"""Casos de uso de precios (V1.2): historial de cambios y actualización masiva.

La escritura del historial ocurre dentro de cada operación que cambia un
precio (edición de producto, compra, actualización masiva, importación),
en su misma transacción -- ver `db.repositorios.historial_precios`.
"""

import logging

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import historial_precios as repositorio_historial_precios
from db.repositorios import lotes_precios as repositorio_lotes
from db.repositorios import productos as repositorio_productos
from domain.dinero import centavos_a_texto
from domain.historial_precio import CambioPrecio
from domain.precios_masivos import (
    ALCANCE_SIN_CATEGORIA,
    ALCANCE_TODOS,
    ESTADO_OK,
    LONGITUD_MAXIMA_CLAVE,
    CriterioActualizacion,
    PropuestaPrecio,
    categoria_del_alcance,
    evaluar,
    validar_alcance,
    validar_seleccion,
)
from excepciones import (
    MENSAJE_FORMULARIO_REENVIADO_CON_OTROS_DATOS,
    ClaveIdempotenciaReutilizadaError,
    DatosInvalidosError,
    ProductoNoEncontradoError,
)

logger = logging.getLogger(__name__)


def _productos_del_alcance(alcance: str, conexion=None):
    alcance = validar_alcance(alcance)
    if alcance == ALCANCE_TODOS:
        return repositorio_productos.listar_activos_con_precio(conexion=conexion)
    if alcance == ALCANCE_SIN_CATEGORIA:
        return repositorio_productos.listar_activos_con_precio(sin_categoria=True, conexion=conexion)
    return repositorio_productos.listar_activos_con_precio(
        categoria_id=categoria_del_alcance(alcance), conexion=conexion
    )


def listar_historial_de_producto(producto_id: int) -> list[CambioPrecio]:
    """Cambios de precio (venta y costo) de un producto, el más reciente primero."""
    return repositorio_historial_precios.listar_por_producto(producto_id)


def proponer_actualizacion(criterio: CriterioActualizacion, alcance: str) -> list[PropuestaPrecio]:
    """Vista previa: para cada producto del alcance, el precio que resultaría
    de aplicar el criterio. No escribe nada."""
    propuestas = []
    for producto in _productos_del_alcance(alcance):
        nuevo, estado = evaluar(producto.precio_venta_centavos, criterio)
        propuestas.append(
            PropuestaPrecio(
                producto_id=producto.id,
                codigo_barras=producto.codigo_barras,
                nombre=producto.nombre,
                precio_actual_centavos=producto.precio_venta_centavos,
                precio_nuevo_centavos=nuevo,
                estado=estado,
            )
        )
    return propuestas


def aplicar_actualizacion(
    usuario_id: int,
    criterio: CriterioActualizacion,
    alcance: str,
    precios_esperados: dict[int, int],
    clave_idempotencia: str | None = None,
) -> repositorio_lotes.LotePrecios:
    """Aplica la actualización masiva a los productos elegidos, todo o nada.

    `precios_esperados` mapea `producto_id -> precio de venta que el usuario
    vio en la vista previa`. El precio nuevo lo recalcula siempre el
    servidor (nunca se confía en uno recibido). Dentro de una única
    transacción `BEGIN IMMEDIATE`: se rechaza la operación completa si algún
    producto ya no existe o no está activo, si su precio cambió desde la
    vista previa, o si el criterio no daría un precio positivo y distinto del
    actual; si todo es válido se actualiza cada precio, se registra cada cambio
    en el historial con el lote y se crea el lote. Cualquier error revierte
    todo: no quedan cambios parciales.

    Con `clave_idempotencia`, un reenvío devuelve el lote ya aplicado (si el
    pedido es el mismo) sin volver a aplicar nada.

    Raises:
        DatosInvalidosError: si no hay productos o algún producto no es aplicable.
    """
    # La confirmación no confía en nada de lo que validó la vista previa: el
    # criterio se valida al construirse, y acá el alcance, la selección y la clave.
    validar_alcance(alcance)
    validar_seleccion(precios_esperados)
    if clave_idempotencia is not None and not (0 < len(clave_idempotencia) <= LONGITUD_MAXIMA_CLAVE):
        raise DatosInvalidosError("La clave de la operación no es válida.")

    with obtener_conexion(inmediata=True) as conexion:
        if clave_idempotencia is not None:
            existente = repositorio_lotes.obtener_por_clave_idempotencia_en_conexion(conexion, clave_idempotencia)
            if existente is not None:
                productos_del_lote = repositorio_historial_precios.listar_productos_del_lote_en_conexion(
                    conexion, existente.id
                )
                if (existente.criterio, existente.alcance, productos_del_lote) != (
                    criterio,
                    alcance,
                    set(precios_esperados),
                ):
                    raise ClaveIdempotenciaReutilizadaError(MENSAJE_FORMULARIO_REENVIADO_CON_OTROS_DATOS)
                return existente

        ids_del_alcance = {p.id for p in _productos_del_alcance(alcance, conexion)}
        cambios: list[tuple[int, int, int]] = []
        for producto_id, precio_esperado in sorted(precios_esperados.items()):
            producto = repositorio_productos.obtener_por_id_en_conexion(conexion, producto_id)
            if producto is None:
                raise ProductoNoEncontradoError(f"El producto {producto_id} ya no existe o está inactivo.")
            if producto.id not in ids_del_alcance:
                raise DatosInvalidosError(
                    f"'{producto.nombre}' no pertenece al alcance elegido o no tiene precio configurado."
                )
            if producto.precio_venta_centavos != precio_esperado:
                raise DatosInvalidosError(
                    f"El precio de '{producto.nombre}' cambió desde la vista previa: revisá la actualización de nuevo."
                )
            nuevo, estado = evaluar(producto.precio_venta_centavos, criterio)
            if estado != ESTADO_OK:
                raise DatosInvalidosError(
                    f"'{producto.nombre}' no se puede actualizar con este criterio: el precio nuevo "
                    "no sería positivo o no cambiaría."
                )
            cambios.append((producto.id, producto.precio_venta_centavos, nuevo))

        lote_id = repositorio_lotes.crear_lote_en_conexion(
            conexion, usuario_id, criterio, alcance, len(cambios), clave_idempotencia
        )
        for producto_id, _anterior, nuevo in cambios:
            # Único punto de escritura: cambia el precio y registra el historial juntos.
            repositorio_productos.cambiar_precio_en_conexion(
                conexion, producto_id, "VENTA", nuevo, usuario_id, "MASIVA", lote_id
            )
        valor_texto = (
            f"{centavos_a_texto(criterio.valor)} %"
            if criterio.tipo == "PORCENTAJE"
            else f"${centavos_a_texto(criterio.valor)}"
        )
        repositorio_auditoria.registrar_en_conexion(
            conexion,
            usuario_id,
            "PRECIOS_ACTUALIZACION_MASIVA",
            "LOTE_PRECIOS",
            lote_id,
            f"{'Aumentar' if criterio.direccion == 'AUMENTAR' else 'Disminuir'} {valor_texto} a {len(cambios)} producto(s)",
        )

    logger.info(
        "Actualización masiva de precios: lote=%s productos=%s tipo=%s usuario_id=%s",
        lote_id, len(cambios), criterio.tipo, usuario_id,
    )
    return repositorio_lotes.obtener_por_id(lote_id)


def listar_lotes_recientes(limite: int = 10) -> list[repositorio_lotes.LotePrecios]:
    """Últimas actualizaciones masivas aplicadas, la más reciente primero."""
    return repositorio_lotes.listar_recientes(limite)
