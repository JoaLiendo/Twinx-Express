"""Casos de uso de los pedidos de clientes (migración 051): tomar, consultar, cancelar y preparar la
conversión en venta. Un pedido nunca mueve stock ni dinero; concretarlo es registrar una venta en el POS
con `pedido_id` (ver `servicio_ventas.registrar_venta`), que valida stock y precios en ese momento y deja
el pedido CONVERTIDO en la misma transacción.
"""

import logging
from dataclasses import dataclass
from datetime import date

from db.conexion import ConexionBD, obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import clientes as repositorio_clientes
from db.repositorios import pedidos as repositorio_pedidos
from db.repositorios import productos as repositorio_productos
from db.repositorios.pedidos import FiltrosPedidos
from domain.dinero import centavos_a_texto_localizado
from domain.pedido import (
    ESTADO_PENDIENTE,
    ETIQUETAS_ESTADO_PEDIDO,
    ItemPedido,
    LineaPedido,
    Pedido,
    validar_datos_pedido,
    validar_motivo_cancelacion,
)
from excepciones import (
    MENSAJE_FORMULARIO_REENVIADO_CON_OTROS_DATOS,
    ClaveIdempotenciaReutilizadaError,
    ClienteInactivoError,
    ClienteNoEncontradoError,
    DatosInvalidosError,
    PedidoNoEncontradoError,
    PedidoNoPendienteError,
    ProductoNoEncontradoError,
)

logger = logging.getLogger(__name__)

PEDIDOS_POR_PAGINA = 50


def registrar_pedido(
    *,
    usuario_id: int,
    items: list[ItemPedido],
    cliente_id: int | None = None,
    contacto_nombre: str | None = None,
    contacto_telefono: str | None = None,
    notas: str | None = None,
    fecha_entrega: str | None = None,
    clave_idempotencia: str | None = None,
    hoy: date | None = None,
) -> Pedido:
    """Toma un pedido. Congela el precio vigente de cada producto como referencia (no es el precio de
    venta: ese se decide al cobrar). No reserva ni descuenta stock. Un reenvío con la misma clave devuelve
    el pedido ya tomado.

    Raises:
        DatosInvalidosError, ClienteNoEncontradoError, ClienteInactivoError, ProductoNoEncontradoError,
        ClaveIdempotenciaReutilizadaError.
    """
    datos = validar_datos_pedido(
        cliente_id=cliente_id,
        contacto_nombre=contacto_nombre,
        contacto_telefono=contacto_telefono,
        notas=notas,
        fecha_entrega=fecha_entrega,
        items=items,
        hoy=hoy,
    )
    with obtener_conexion(inmediata=True) as conexion:
        if clave_idempotencia:
            existente = repositorio_pedidos.obtener_por_clave_idempotencia_en_conexion(conexion, clave_idempotencia)
            if existente is not None:
                lineas = {(l.producto_id, l.cantidad) for l in repositorio_pedidos.listar_lineas_en_conexion(conexion, existente.id)}
                if lineas != {(i.producto_id, i.cantidad) for i in datos.items} or existente.cliente_id != datos.cliente_id:
                    raise ClaveIdempotenciaReutilizadaError(MENSAJE_FORMULARIO_REENVIADO_CON_OTROS_DATOS)
                return existente
        if datos.cliente_id is not None:
            cliente = repositorio_clientes.obtener_por_id_en_conexion(conexion, datos.cliente_id)
            if cliente is None:
                raise ClienteNoEncontradoError(f"No existe un cliente con id {datos.cliente_id}.")
            if not cliente.activo:
                raise ClienteInactivoError(f"El cliente '{cliente.nombre}' está inactivo: no puede tomar pedidos.")
        precios: dict[int, int] = {}
        for item in datos.items:
            producto = repositorio_productos.obtener_por_id_en_conexion(conexion, item.producto_id)
            if producto is None:
                raise ProductoNoEncontradoError(f"No existe un producto activo con id {item.producto_id}.")
            precios[item.producto_id] = producto.precio_venta_centavos
        pedido = repositorio_pedidos.registrar_en_conexion(conexion, datos, usuario_id, precios, clave_idempotencia or None)
        repositorio_auditoria.registrar_en_conexion(
            conexion,
            usuario_id,
            "PEDIDO_TOMADO",
            "PEDIDO",
            pedido.id,
            f"Pedido #{pedido.id} para {pedido.para_quien}: {pedido.cantidad_lineas} línea(s), "
            f"${centavos_a_texto_localizado(pedido.total_al_pedir_centavos)} a precios del momento",
        )
    logger.info("Pedido tomado: #%s (%s líneas)", pedido.id, pedido.cantidad_lineas)
    return pedido


def obtener_pedido(pedido_id: int) -> Pedido:
    pedido = repositorio_pedidos.obtener_por_id(pedido_id)
    if pedido is None:
        raise PedidoNoEncontradoError(f"No existe un pedido con id {pedido_id}.")
    return pedido


def listar_lineas(pedido_id: int) -> list[LineaPedido]:
    return repositorio_pedidos.listar_lineas(pedido_id)


def cancelar_pedido(pedido_id: int, *, usuario_id: int, motivo: str | None) -> Pedido:
    """Cancela un pedido pendiente con motivo (auditado). No hay nada que devolver: nunca movió stock ni dinero.

    Raises:
        PedidoNoEncontradoError, PedidoNoPendienteError, DatosInvalidosError.
    """
    motivo_valido = validar_motivo_cancelacion(motivo)
    with obtener_conexion(inmediata=True) as conexion:
        pedido = repositorio_pedidos.obtener_por_id_en_conexion(conexion, pedido_id)
        if pedido is None:
            raise PedidoNoEncontradoError(f"No existe un pedido con id {pedido_id}.")
        if not pedido.pendiente:
            raise PedidoNoPendienteError(f"El pedido {pedido_id} ya está {pedido.estado_etiqueta.lower()}.")
        if not repositorio_pedidos.cancelar_en_conexion(conexion, pedido_id, usuario_id, motivo_valido):
            raise PedidoNoPendienteError(f"El pedido {pedido_id} ya no está pendiente.")
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, "PEDIDO_CANCELADO", "PEDIDO", pedido_id, f"Pedido #{pedido_id} cancelado: {motivo_valido}"
        )
        cancelado = repositorio_pedidos.obtener_por_id_en_conexion(conexion, pedido_id)
    assert cancelado is not None
    return cancelado


def convertir_en_conexion(conexion: ConexionBD, pedido_id: int, venta_id: int, usuario_id: int | None) -> None:
    """Deja el pedido CONVERTIDO con su venta, dentro de la transacción de la venta (la llama
    `servicio_ventas.registrar_venta`). Un pedido que ya no está pendiente aborta la venta entera: nunca
    se ejecutan dos ventas por el mismo pedido.

    Raises:
        PedidoNoEncontradoError, PedidoNoPendienteError.
    """
    pedido = repositorio_pedidos.obtener_por_id_en_conexion(conexion, pedido_id)
    if pedido is None:
        raise PedidoNoEncontradoError(f"No existe un pedido con id {pedido_id}.")
    if not pedido.pendiente:
        raise PedidoNoPendienteError(
            f"El pedido #{pedido_id} ya está {pedido.estado_etiqueta.lower()}"
            + (f" (venta #{pedido.venta_id})" if pedido.venta_id else "")
            + ": no se puede vender dos veces."
        )
    if not repositorio_pedidos.marcar_convertido_en_conexion(conexion, pedido_id, venta_id):
        raise PedidoNoPendienteError(f"El pedido #{pedido_id} ya no está pendiente.")
    repositorio_auditoria.registrar_en_conexion(
        conexion, usuario_id, "PEDIDO_CONVERTIDO", "PEDIDO", pedido_id, f"Pedido #{pedido_id} convertido en la venta #{venta_id}"
    )


# --- Consultas -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ListadoPedidos:
    pedidos: list[Pedido]
    pagina: int
    total_paginas: int
    cantidad: int
    pendientes: int
    filtros: FiltrosPedidos


def filtros_del_listado(estado: str | None, texto: str | None, cliente_id: int | None = None) -> FiltrosPedidos:
    if estado and estado not in ETIQUETAS_ESTADO_PEDIDO:
        raise DatosInvalidosError("Estado de pedido desconocido.")
    return FiltrosPedidos(estado or None, (texto or "").strip()[:100] or None, cliente_id)


def listar_pedidos(filtros: FiltrosPedidos, pagina: int = 1) -> ListadoPedidos:
    cantidad = repositorio_pedidos.contar(filtros)
    total_paginas = max(1, -(-cantidad // PEDIDOS_POR_PAGINA))
    pagina = min(max(1, pagina), total_paginas)
    pedidos = repositorio_pedidos.listar(filtros, PEDIDOS_POR_PAGINA, (pagina - 1) * PEDIDOS_POR_PAGINA)
    return ListadoPedidos(pedidos, pagina, total_paginas, cantidad, repositorio_pedidos.contar_pendientes(), filtros)


def contar_pendientes() -> int:
    return repositorio_pedidos.contar_pendientes()


def listar_de_cliente(cliente_id: int) -> list[Pedido]:
    return repositorio_pedidos.listar_de_cliente(cliente_id)


@dataclass(frozen=True)
class PrecargaPOS:
    """Lo que el POS recibe para vender un pedido: líneas vendibles hoy y avisos de lo que cambió."""

    pedido: Pedido
    lineas: list[LineaPedido]
    avisos: list[str]

    @property
    def items_vendibles(self) -> list[dict]:
        return [
            {"producto_id": l.producto_id, "cantidad": l.cantidad}
            for l in self.lineas
            if l.producto_activo and l.precio_vigente_centavos > 0
        ]


def preparar_precarga_pos(pedido_id: int) -> PrecargaPOS:
    """Pedido pendiente listo para el POS. No cambia nada: el POS vuelve a validar stock y precio al cobrar.

    Raises:
        PedidoNoEncontradoError, PedidoNoPendienteError.
    """
    pedido = obtener_pedido(pedido_id)
    if pedido.estado != ESTADO_PENDIENTE:
        raise PedidoNoPendienteError(f"El pedido #{pedido_id} ya está {pedido.estado_etiqueta.lower()}: no se puede vender.")
    lineas = listar_lineas(pedido_id)
    avisos = []
    for linea in lineas:
        if not linea.producto_activo:
            avisos.append(f"{linea.producto_nombre} está dado de baja: no se cargó al carrito.")
        elif linea.precio_vigente_centavos <= 0:
            avisos.append(f"{linea.producto_nombre} no tiene precio configurado: no se cargó al carrito.")
        elif linea.sin_stock_suficiente:
            avisos.append(f"{linea.producto_nombre}: se pidieron {linea.cantidad} y hay {linea.stock_actual} en stock.")
        elif linea.precio_cambio:
            avisos.append(
                f"{linea.producto_nombre}: el precio cambió de ${centavos_a_texto_localizado(linea.precio_unitario_centavos)}"
                f" a ${centavos_a_texto_localizado(linea.precio_vigente_centavos)}; rige el precio actual."
            )
    return PrecargaPOS(pedido, lineas, avisos)
