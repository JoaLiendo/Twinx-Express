"""Casos de uso de ingreso de mercadería / compras (Fase 4B).

`registrar_compra` es la única operación que incrementa stock por
compra: hace todo (validación de proveedor/productos, alta de la
compra y su detalle, incremento de stock, actualización del costo
vigente) dentro de una misma transacción de `db.conexion.obtener_conexion`,
de forma que ante cualquier error no quede ningún cambio a medias --
mismo patrón que `services.servicio_ventas.registrar_venta`.

Las compras son inmutables en esta fase: no hay `actualizar_compra` ni
`eliminar_compra` acá, a propósito (ver auditoría de Fase 4).
"""

import logging

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import compras as repositorio_compras
from db.repositorios import productos as repositorio_productos
from db.repositorios import proveedores as repositorio_proveedores
from domain.compra import Compra, DetalleCompra, ItemCompra, LineaDetalleCompra, ResumenCompra
from domain.dinero import centavos_a_texto
from excepciones import (
    MENSAJE_FORMULARIO_REENVIADO_CON_OTROS_DATOS,
    ClaveIdempotenciaReutilizadaError,
    DatosInvalidosError,
    ProductoDuplicadoEnCompraError,
    ProductoNoEncontradoError,
    ProveedorNoEncontradoError,
)

logger = logging.getLogger(__name__)


def registrar_compra(
    proveedor_id: int,
    usuario_id: int,
    items: list[ItemCompra],
    observaciones: str | None = None,
    clave_idempotencia: str | None = None,
) -> Compra:
    """Registra un ingreso de mercadería con sus líneas de detalle,
    incrementa el stock de cada producto y actualiza su costo vigente.

    Cada `ItemCompra` ya valida sus propios invariantes al construirse
    (cantidad > 0, costo >= 0: ver `domain.compra.ItemCompra`). Acá se
    valida, en orden y dentro de una única transacción: que el
    proveedor exista y esté activo, que cada producto exista y esté
    activo, y que ningún producto se repita dentro de la misma compra.
    El subtotal de cada línea y el total de la compra los calcula
    siempre el servidor (`cantidad * costo_unitario_centavos`, y la
    suma de esos subtotales): nunca se confía en un total recibido del
    cliente.

    Si cualquier paso falla, no se persiste ni la compra, ni su
    detalle, ni ningún incremento de stock o actualización de costo:
    `obtener_conexion` revierte toda la transacción.

    Args:
        proveedor_id: id de un proveedor activo (ver `services.servicio_proveedores`).
        usuario_id: id del usuario que registra el ingreso.
        items: líneas de compra (producto_id + cantidad + costo unitario).
            No puede estar vacío ni repetir un mismo `producto_id`.
        observaciones: texto libre opcional.
        clave_idempotencia: identificador que el formulario genera una vez
            y reenvía igual ante cualquier reintento (V1.1): un reenvío
            devuelve la compra ya registrada sin duplicar compra, stock ni
            costo.

    Raises:
        DatosInvalidosError: si `items` está vacío.
        ProveedorNoEncontradoError: si `proveedor_id` no corresponde a
            ningún proveedor activo.
        ProductoNoEncontradoError: si algún `producto_id` no corresponde
            a ningún producto activo.
        ProductoDuplicadoEnCompraError: si un mismo producto aparece en
            más de una línea.
    """
    if not items:
        raise DatosInvalidosError("Una compra debe tener al menos un ítem.")

    # `inmediata=True`: mismo motivo que en `servicio_ventas.registrar_venta`
    # (ver auditoría de concurrencia, Stage D) -- esta transacción lee el
    # stock/costo vigente de cada producto y después escribe en base a esa
    # lectura; sin el lock tomado desde el inicio, dos compras concurrentes
    # del mismo producto podrían perder un incremento de stock.
    with obtener_conexion(inmediata=True) as conexion:
        if clave_idempotencia is not None:
            existente = repositorio_compras.obtener_por_clave_idempotencia_en_conexion(
                conexion, clave_idempotencia
            )
            if existente is not None:
                lineas_originales = repositorio_compras.listar_lineas_en_conexion(conexion, existente.id)
                lineas_pedidas = sorted(
                    (item.producto_id, item.cantidad, item.costo_unitario_centavos) for item in items
                )
                if (existente.proveedor_id, existente.observaciones, lineas_originales) != (
                    proveedor_id,
                    observaciones,
                    lineas_pedidas,
                ):
                    raise ClaveIdempotenciaReutilizadaError(MENSAJE_FORMULARIO_REENVIADO_CON_OTROS_DATOS)
                return existente

        proveedor = repositorio_proveedores.obtener_por_id_en_conexion(conexion, proveedor_id)
        if proveedor is None:
            raise ProveedorNoEncontradoError(f"No existe un proveedor activo con id {proveedor_id}.")

        productos_por_id = {}
        for item in items:
            producto = repositorio_productos.obtener_por_id_en_conexion(conexion, item.producto_id)
            if producto is None:
                raise ProductoNoEncontradoError(f"No existe un producto activo con id {item.producto_id}.")
            productos_por_id[item.producto_id] = producto

        ids_producto = [item.producto_id for item in items]
        if len(ids_producto) != len(set(ids_producto)):
            raise ProductoDuplicadoEnCompraError(
                "Un producto no puede aparecer más de una vez en la misma compra."
            )

        # Aritmética entera exacta: sin redondeos ni errores de precisión binaria.
        items_con_subtotal = [
            (item, item.cantidad * item.costo_unitario_centavos) for item in items
        ]
        total_centavos = sum(subtotal for _, subtotal in items_con_subtotal)

        compra = repositorio_compras.registrar_compra_con_detalle(
            conexion,
            proveedor_id,
            usuario_id,
            observaciones,
            total_centavos,
            items_con_subtotal,
            clave_idempotencia=clave_idempotencia,
        )

        for item in items:
            producto = productos_por_id[item.producto_id]
            producto.actualizar_stock(producto.stock_actual + item.cantidad)
            repositorio_productos.actualizar_stock_en_conexion(conexion, producto.id, producto.stock_actual)
            # El costo vigente cambia con cada compra: `actualizar_costo_en_conexion` lo
            # actualiza y, si difiere del anterior, lo registra en el historial de precios
            # (misma transacción que el ingreso de stock).
            repositorio_productos.actualizar_costo_en_conexion(
                conexion, producto.id, item.costo_unitario_centavos, usuario_id, "COMPRA"
            )

        repositorio_auditoria.registrar_en_conexion(
            conexion,
            usuario_id,
            "COMPRA_REGISTRADA",
            "COMPRA",
            compra.id,
            f"Proveedor {proveedor.nombre}: {len(items)} línea(s), total ${centavos_a_texto(total_centavos)}",
        )

    logger.info(
        "Compra registrada: id=%s proveedor_id=%s total_centavos=%s items=%s",
        compra.id, compra.proveedor_id, compra.total_centavos, len(items),
    )
    return compra


def obtener_por_id(compra_id: int) -> Compra | None:
    """Busca una compra por id (pantalla de detalle)."""
    return repositorio_compras.obtener_por_id(compra_id)


def listar_todas() -> list[Compra]:
    """Devuelve todas las compras, más recientes primero (listado)."""
    return repositorio_compras.listar_todas()


def listar_detalle(compra_id: int) -> list[DetalleCompra]:
    """Devuelve las líneas de una compra (pantalla de detalle)."""
    return repositorio_compras.listar_detalle(compra_id)


def listar_resumen(
    proveedor_id: int | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
) -> list[ResumenCompra]:
    """Historial de compras con proveedor/usuario/cantidad de líneas ya
    resueltos (Fase 4C, ver `db.repositorios.compras.listar_resumen`)."""
    return repositorio_compras.listar_resumen(proveedor_id, fecha_desde, fecha_hasta)


def obtener_resumen_por_id(compra_id: int) -> ResumenCompra | None:
    """Encabezado resuelto de una compra para la pantalla de detalle."""
    return repositorio_compras.obtener_resumen_por_id(compra_id)


def listar_detalle_con_producto(compra_id: int) -> list[LineaDetalleCompra]:
    """Líneas de una compra con nombre y unidad de medida del producto
    ya resueltos (pantalla de detalle, Fase 4C)."""
    return repositorio_compras.listar_detalle_con_producto(compra_id)
