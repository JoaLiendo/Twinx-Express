"""Casos de uso del inventario físico (V1.4).

Un inventario compara lo CONTADO contra el stock que el sistema tenía al momento del conteo
(`stock_esperado` + `version_stock`, tomados juntos dentro de una transacción `BEGIN IMMEDIATE`).
REGLA CRÍTICA: el inventario jamás sobrescribe silenciosamente movimientos de stock posteriores
al conteo. Al confirmar se valida, para TODAS las líneas contadas (también las de diferencia
cero), que el stock y la versión actuales sigan siendo los del conteo; si alguna cambió (venta,
compra, ajuste, anulación, incluso si el stock volvió al mismo número) la confirmación se rechaza
completa, sin ajustes parciales, y se pide recontar. No hay libro de movimientos que permita
reconstruir con seguridad qué ocurrió entre el conteo y la confirmación, así que ante la duda
prevalece la seguridad.

Permisos (validados acá además de las rutas): OWNER crea, confirma, cancela y consulta el
detalle; OWNER y CASHIER cuentan y ven las líneas a ciegas (sin esperado ni diferencia).
"""

import logging

from db.conexion import ConexionBD, obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import inventarios as repositorio_inventarios
from db.repositorios import productos as repositorio_productos
from db.repositorios import usuarios as repositorio_usuarios
from domain.inventario import (
    ESTADO_ABIERTO,
    ESTADO_CANCELADO,
    ESTADO_CONFIRMADO,
    DetalleInventario,
    Inventario,
    LineaConteo,
    ResultadoConfirmacion,
    ResumenInventario,
    validar_cantidad_contada,
)
from domain.usuario import exigir_rol
from excepciones import (
    MENSAJE_FORMULARIO_REENVIADO_CON_OTROS_DATOS,
    ClaveIdempotenciaReutilizadaError,
    DatosInvalidosError,
    InventarioDesactualizadoError,
    InventarioNoAbiertoError,
    InventarioNoEncontradoError,
    InventarioSinConteosError,
    ProductoFueraDeInventarioError,
    ProductoNoEncontradoError,
)
from services import servicio_stock

logger = logging.getLogger(__name__)

_SOLO_OWNER = frozenset({"OWNER"})
_OWNER_Y_CASHIER = frozenset({"OWNER", "CASHIER"})
_MAXIMO_NOMBRES_EN_MENSAJE = 5


def _exigir_usuario(conexion: ConexionBD, usuario_id: int, roles: frozenset[str]) -> None:
    exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), roles)


def _inventario_abierto_o_error(conexion: ConexionBD, inventario_id: int) -> Inventario:
    inventario = repositorio_inventarios.obtener_inventario_en_conexion(conexion, inventario_id)
    if inventario is None:
        raise InventarioNoEncontradoError(f"No existe un inventario con id {inventario_id}.")
    if inventario.estado != ESTADO_ABIERTO:
        raise InventarioNoAbiertoError(
            f"El inventario #{inventario_id} ya está {inventario.estado.lower()}: no admite más cambios."
        )
    return inventario


def _resolver_productos(conexion: ConexionBD, producto_ids: list[int] | None) -> list[int]:
    """Todos los productos activos, o la selección manual (sin repetidos). Un producto inactivo solo
    entra si todavía tiene stock."""
    if producto_ids is None:
        return repositorio_inventarios.listar_ids_productos_activos_en_conexion(conexion)
    resueltos: list[int] = []
    for producto_id in dict.fromkeys(producto_ids):
        producto = repositorio_productos.obtener_por_id_en_conexion_incluyendo_inactivos(conexion, producto_id)
        if producto is None:
            raise ProductoNoEncontradoError(f"No existe un producto con id {producto_id}.")
        if not producto.activo and producto.stock_actual <= 0:
            raise DatosInvalidosError(
                f"'{producto.nombre}' está inactivo y sin stock: no puede incluirse en un inventario."
            )
        resueltos.append(producto_id)
    return resueltos


def crear_inventario(
    usuario_id: int, producto_ids: list[int] | None = None, observaciones: str | None = None
) -> Inventario:
    """Abre un inventario con las líneas fijadas: `producto_ids=None` toma todos los productos activos;
    una lista es la selección manual (puede incluir inactivos con stock).

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        InventarioAbiertoExistenteError: si ya hay un inventario abierto.
        DatosInvalidosError: si no queda ningún producto o uno inactivo no tiene stock.
        ProductoNoEncontradoError: si algún producto no existe.
    """
    observaciones = (observaciones or "").strip() or None
    with obtener_conexion(inmediata=True) as conexion:
        _exigir_usuario(conexion, usuario_id, _SOLO_OWNER)
        ids = _resolver_productos(conexion, producto_ids)
        if not ids:
            raise DatosInvalidosError("El inventario no tiene ningún producto para contar.")
        inventario = repositorio_inventarios.crear_inventario_en_conexion(conexion, usuario_id, observaciones)
        repositorio_inventarios.agregar_lineas_en_conexion(conexion, inventario.id, ids)
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, "INVENTARIO_CREADO", "INVENTARIO", inventario.id,
            f"Inventario #{inventario.id} iniciado con {len(ids)} producto(s)",
        )
    logger.info("Inventario #%s creado por usuario %s (%s productos)", inventario.id, usuario_id, len(ids))
    return inventario


def obtener_inventario_abierto(usuario_id: int) -> Inventario | None:
    """El inventario abierto, si hay uno (lo consultan OWNER y CASHIER)."""
    with obtener_conexion() as conexion:
        _exigir_usuario(conexion, usuario_id, _OWNER_Y_CASHIER)
    return repositorio_inventarios.obtener_abierto()


def listar_lineas_para_conteo(inventario_id: int, usuario_id: int) -> list[LineaConteo]:
    """Líneas del inventario para contar, a ciegas: sin stock esperado ni diferencia.

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER o CASHIER activo.
        InventarioNoEncontradoError: si el inventario no existe.
    """
    with obtener_conexion() as conexion:
        _exigir_usuario(conexion, usuario_id, _OWNER_Y_CASHIER)
        if repositorio_inventarios.obtener_inventario_en_conexion(conexion, inventario_id) is None:
            raise InventarioNoEncontradoError(f"No existe un inventario con id {inventario_id}.")
    return repositorio_inventarios.listar_lineas_para_conteo(inventario_id)


def registrar_conteo(inventario_id: int, producto_id: int, cantidad_contada: int, usuario_id: int) -> None:
    """Registra (o reemplaza) el conteo de un producto. En una única transacción `BEGIN IMMEDIATE`
    toma el stock esperado, su versión, el costo vigente y la cantidad contada, con quién y cuándo.
    No devuelve el esperado ni la diferencia: el conteo es a ciegas.

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER o CASHIER activo.
        InventarioNoEncontradoError / InventarioNoAbiertoError: según el estado del inventario.
        ProductoFueraDeInventarioError: si el producto no es una línea del inventario.
        DatosInvalidosError: si la cantidad no es un entero >= 0.
    """
    cantidad = validar_cantidad_contada(cantidad_contada)
    with obtener_conexion(inmediata=True) as conexion:
        _exigir_usuario(conexion, usuario_id, _OWNER_Y_CASHIER)
        _inventario_abierto_o_error(conexion, inventario_id)
        estado = repositorio_inventarios.obtener_estado_producto_en_conexion(conexion, producto_id)
        registrado = estado is not None and repositorio_inventarios.registrar_conteo_en_conexion(
            conexion,
            inventario_id,
            producto_id,
            estado.stock_actual,
            estado.version_stock,
            cantidad,
            estado.precio_costo_centavos,
            usuario_id,
        )
        if not registrado:
            raise ProductoFueraDeInventarioError(
                f"El producto {producto_id} no forma parte del inventario #{inventario_id}."
            )
    logger.info(
        "Conteo registrado: inventario=%s producto=%s cantidad=%s usuario=%s",
        inventario_id, producto_id, cantidad, usuario_id,
    )


def _mensaje_desactualizados(nombres: list[str]) -> str:
    visibles = ", ".join(nombres[:_MAXIMO_NOMBRES_EN_MENSAJE])
    resto = len(nombres) - _MAXIMO_NOMBRES_EN_MENSAJE
    detalle = f"{visibles} y {resto} más" if resto > 0 else visibles
    return (
        f"El stock cambió después del conteo en: {detalle}. "
        "No se confirmó nada: volvé a contar esos productos y confirmá de nuevo."
    )


def _resultado(conexion: ConexionBD, inventario: Inventario) -> ResultadoConfirmacion:
    total, contadas, con_ajuste = repositorio_inventarios.contar_lineas_en_conexion(conexion, inventario.id)
    return ResultadoConfirmacion(
        inventario=inventario,
        ajustes_generados=con_ajuste,
        lineas_sin_diferencia=contadas - con_ajuste,
        lineas_sin_contar=total - contadas,
    )


def confirmar_inventario(
    inventario_id: int, usuario_id: int, clave_idempotencia: str | None = None
) -> ResultadoConfirmacion:
    """Confirma el inventario: valida versiones de TODAS las líneas contadas, genera un ajuste
    RECUENTO por cada diferencia, audita y cierra, todo en una única transacción.

    Las líneas sin contar no se tocan (jamás se interpretan como cero). Una línea contada sin
    diferencia no genera ajuste, pero igualmente valida su versión. Si alguna línea contada ya no
    coincide con el stock/versión del conteo se rechaza toda la confirmación.

    Idempotencia: el estado (una segunda confirmación ve el inventario cerrado), la clave de
    idempotencia (un reenvío devuelve el resultado ya obtenido) y el índice único de ajuste por
    (inventario, producto).

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        InventarioNoEncontradoError / InventarioNoAbiertoError: según el estado del inventario.
        InventarioSinConteosError: si no hay ninguna línea contada.
        InventarioDesactualizadoError: si el stock de algún producto contado cambió después del conteo.
        ClaveIdempotenciaReutilizadaError: si la clave ya se usó en otro inventario.
    """
    with obtener_conexion(inmediata=True) as conexion:
        _exigir_usuario(conexion, usuario_id, _SOLO_OWNER)
        if clave_idempotencia is not None:
            previo = repositorio_inventarios.obtener_por_clave_idempotencia_en_conexion(conexion, clave_idempotencia)
            if previo is not None:
                if previo.id != inventario_id:
                    raise ClaveIdempotenciaReutilizadaError(MENSAJE_FORMULARIO_REENVIADO_CON_OTROS_DATOS)
                return _resultado(conexion, previo)

        _inventario_abierto_o_error(conexion, inventario_id)
        contadas = repositorio_inventarios.listar_lineas_contadas_en_conexion(conexion, inventario_id)
        if not contadas:
            raise InventarioSinConteosError("El inventario no tiene ninguna línea contada para confirmar.")

        desactualizadas = [linea.producto_nombre for linea in contadas if linea.desactualizada]
        if desactualizadas:
            raise InventarioDesactualizadoError(_mensaje_desactualizados(desactualizadas), desactualizadas)

        for linea in contadas:
            diferencia = linea.cantidad_contada - linea.stock_esperado
            if diferencia == 0:
                continue
            producto = repositorio_productos.obtener_por_id_en_conexion_incluyendo_inactivos(
                conexion, linea.producto_id
            )
            ajuste = servicio_stock.aplicar_ajuste_en_conexion(
                conexion,
                producto,
                diferencia,
                "RECUENTO",
                usuario_id,
                observaciones=f"Inventario #{inventario_id}",
                inventario_id=inventario_id,
            )
            repositorio_inventarios.asignar_ajuste_en_conexion(conexion, linea.linea_id, ajuste.id)

        inventario = repositorio_inventarios.cerrar_inventario_en_conexion(
            conexion, inventario_id, ESTADO_CONFIRMADO, usuario_id, clave_idempotencia
        )
        resultado = _resultado(conexion, inventario)
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, "INVENTARIO_CONFIRMADO", "INVENTARIO", inventario_id,
            f"Inventario #{inventario_id} confirmado: {resultado.ajustes_generados} ajuste(s), "
            f"{resultado.lineas_sin_diferencia} sin diferencia, {resultado.lineas_sin_contar} sin contar",
        )
    logger.info(
        "Inventario #%s confirmado por usuario %s: %s ajustes", inventario_id, usuario_id, resultado.ajustes_generados
    )
    return resultado


def cancelar_inventario(inventario_id: int, usuario_id: int) -> Inventario:
    """Cancela un inventario abierto sin tocar el stock. No se reabre: para corregir se cancela y se
    inicia uno nuevo.

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        InventarioNoEncontradoError / InventarioNoAbiertoError: según el estado del inventario.
    """
    with obtener_conexion(inmediata=True) as conexion:
        _exigir_usuario(conexion, usuario_id, _SOLO_OWNER)
        _inventario_abierto_o_error(conexion, inventario_id)
        inventario = repositorio_inventarios.cerrar_inventario_en_conexion(
            conexion, inventario_id, ESTADO_CANCELADO, usuario_id
        )
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, "INVENTARIO_CANCELADO", "INVENTARIO", inventario_id,
            f"Inventario #{inventario_id} cancelado sin modificar el stock",
        )
    logger.info("Inventario #%s cancelado por usuario %s", inventario_id, usuario_id)
    return inventario


def listar_inventarios(usuario_id: int) -> list[ResumenInventario]:
    """Historial de inventarios, el más reciente primero (solo OWNER)."""
    with obtener_conexion() as conexion:
        _exigir_usuario(conexion, usuario_id, _SOLO_OWNER)
    return repositorio_inventarios.listar_resumen()


def obtener_detalle(inventario_id: int, usuario_id: int) -> DetalleInventario:
    """Inventario con esperado, contado, diferencia, costo, ajuste asociado y quién contó (solo OWNER).

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        InventarioNoEncontradoError: si el inventario no existe.
    """
    with obtener_conexion() as conexion:
        _exigir_usuario(conexion, usuario_id, _SOLO_OWNER)
    detalle = repositorio_inventarios.obtener_detalle(inventario_id)
    if detalle is None:
        raise InventarioNoEncontradoError(f"No existe un inventario con id {inventario_id}.")
    return detalle
