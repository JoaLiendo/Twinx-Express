"""Casos de uso de partidas de stock (V1.12). Diseño e invariantes: `docs/V1.12_PARTIDAS.md`.

Toda operación que cambia `productos.stock_actual` (alta con stock, compra y su anulación, venta y su
anulación, ajustes) pasa por acá DENTRO de su propia transacción: registra el ingreso o los movimientos
de partida equivalentes y, antes de terminar, `verificar_conciliacion_en_conexion` exige que el stock de
cada producto tocado siga siendo exactamente la suma de sus partidas. Si no, se lanza
`StockNoConciliadoError` y la operación completa se revierte: nunca puede quedar una venta registrada con
partidas sin descontar, ni partidas descontadas de una venta que falló.
"""

import logging
from collections.abc import Iterable

from db.conexion import ConexionBD
from db.repositorios import partidas as repositorio_partidas
from domain.partida import (
    CRITERIO_ELEGIDA,
    CRITERIO_REVERSION,
    ORIGEN_AJUSTE,
    ORIGEN_ANULACION_VENTA,
    Asignacion,
    Partida,
    asignar_consumo,
)
from excepciones import StockNoConciliadoError

logger = logging.getLogger(__name__)


def registrar_ingreso_en_conexion(
    conexion: ConexionBD,
    producto_id: int,
    origen: str,
    cantidad: int,
    *,
    detalle_compra_id: int | None = None,
    ajuste_id: int | None = None,
    detalle_venta_id: int | None = None,
    fecha_vencimiento: str | None = None,
    costo_unitario_centavos: int | None = None,
) -> Partida:
    """Crea la partida de un ingreso de stock, con su causa. Los datos que no se conocen van `None`."""
    return repositorio_partidas.crear_en_conexion(
        conexion,
        producto_id,
        origen,
        cantidad,
        detalle_compra_id=detalle_compra_id,
        ajuste_id=ajuste_id,
        detalle_venta_id=detalle_venta_id,
        fecha_vencimiento=fecha_vencimiento,
        costo_unitario_centavos=costo_unitario_centavos,
    )


def consumir_en_conexion(
    conexion: ConexionBD,
    producto_id: int,
    cantidad: int,
    *,
    detalle_venta_id: int | None = None,
    ajuste_id: int | None = None,
    detalle_compra_id: int | None = None,
    partida_elegida_id: int | None = None,
    preferidas: Iterable[int] = (),
) -> list[Asignacion]:
    """Descuenta `cantidad` de las partidas del producto y deja un movimiento por partida con la causa.

    `preferidas` (en orden) se consumen primero con criterio `ELEGIDA`: la causa determina la partida
    (ej. anular una compra descuenta primero lo que esa compra ingresó). El resto se asigna con
    `domain.partida.asignar_consumo` (elegida por el usuario, única o FEFO).

    Raises:
        StockInsuficienteError: si las partidas no alcanzan.
    """
    partidas = repositorio_partidas.listar_de_producto_en_conexion(conexion, producto_id)
    hoy = repositorio_partidas.fecha_de_hoy_en_conexion(conexion)
    asignaciones: list[Asignacion] = []
    pendiente = cantidad
    por_id = {partida.id: partida for partida in partidas}
    for partida_id in preferidas:
        partida = por_id.get(partida_id)
        if partida is None or partida.cantidad_disponible == 0 or pendiente == 0:
            continue
        tomada = min(pendiente, partida.cantidad_disponible)
        asignaciones.append(Asignacion(partida.id, tomada, CRITERIO_ELEGIDA))
        pendiente -= tomada
    if pendiente:
        usadas = {a.partida_id: a.cantidad for a in asignaciones}
        restantes = [
            Partida(**{**vars(p), "cantidad_disponible": p.cantidad_disponible - usadas.get(p.id, 0)})
            for p in partidas
        ]
        asignaciones += asignar_consumo(restantes, pendiente, hoy, partida_elegida_id)
    repositorio_partidas.registrar_movimientos_en_conexion(
        conexion, asignaciones, -1,
        detalle_venta_id=detalle_venta_id, ajuste_id=ajuste_id, detalle_compra_id=detalle_compra_id,
    )
    return asignaciones


def revertir_causa_en_conexion(
    conexion: ConexionBD,
    *,
    detalle_venta_id: int | None = None,
    ajuste_original_id: int | None = None,
    ajuste_reversa_id: int | None = None,
) -> int:
    """Deshace exactamente los movimientos de una causa anterior sobre las mismas partidas (criterio
    `REVERSION`) y devuelve el delta total aplicado (0 si la causa no tenía movimientos, ej. un ajuste
    anterior a V1.12).

    - Anular una línea de venta: `detalle_venta_id` (los movimientos nuevos llevan la misma línea).
    - Revertir un ajuste: `ajuste_original_id`; los nuevos llevan `ajuste_reversa_id`.
    """
    if detalle_venta_id is not None:
        netos = repositorio_partidas.netos_por_partida_de_causa_en_conexion(conexion, detalle_venta_id=detalle_venta_id)
    else:
        netos = repositorio_partidas.netos_por_partida_de_causa_en_conexion(conexion, ajuste_id=ajuste_original_id)
    asignaciones = [Asignacion(partida_id, -neto, CRITERIO_REVERSION) for partida_id, neto in netos.items()]
    repositorio_partidas.registrar_movimientos_en_conexion(
        conexion, asignaciones, 1, detalle_venta_id=detalle_venta_id, ajuste_id=ajuste_reversa_id
    )
    return sum(a.cantidad for a in asignaciones)


def devolver_linea_de_venta_en_conexion(
    conexion: ConexionBD, detalle_venta_id: int, producto_id: int, cantidad: int
) -> None:
    """Al anular una línea de venta, devuelve sus unidades a las mismas partidas de las que salieron. Una
    venta anterior a V1.12 no tiene movimientos: lo que falte vuelve como una partida
    `ANULACION_VENTA` sin vencimiento ni costo (no se sabe de qué partida salió)."""
    devuelto = revertir_causa_en_conexion(conexion, detalle_venta_id=detalle_venta_id)
    if devuelto < cantidad:
        registrar_ingreso_en_conexion(
            conexion, producto_id, ORIGEN_ANULACION_VENTA, cantidad - devuelto, detalle_venta_id=detalle_venta_id
        )


def aplicar_ajuste_en_conexion(
    conexion: ConexionBD,
    producto_id: int,
    delta: int,
    ajuste_id: int,
    origen_ingreso: str,
    *,
    reversa_de_id: int | None = None,
    costo_unitario_centavos: int | None = None,
    partida_elegida_id: int | None = None,
) -> None:
    """Refleja un ajuste de stock en las partidas.

    - Una reversa deshace los movimientos de su ajuste original; si el original es anterior a V1.12 (sin
      movimientos), se trata como un ajuste nuevo del mismo signo.
    - `delta < 0`: consume (partida elegida, única o FEFO).
    - `delta > 0`: nueva partida de `origen_ingreso` (sin vencimiento: no se conoce), con el costo
      indicado (el snapshot real del ajuste, o `None`).
    """
    if reversa_de_id is not None:
        if revertir_causa_en_conexion(conexion, ajuste_original_id=reversa_de_id, ajuste_reversa_id=ajuste_id):
            return
        origen_ingreso = ORIGEN_AJUSTE
    if delta < 0:
        consumir_en_conexion(conexion, producto_id, -delta, ajuste_id=ajuste_id, partida_elegida_id=partida_elegida_id)
    else:
        registrar_ingreso_en_conexion(
            conexion, producto_id, origen_ingreso, delta, ajuste_id=ajuste_id,
            costo_unitario_centavos=costo_unitario_centavos,
        )


def verificar_conciliacion_en_conexion(conexion: ConexionBD, producto_ids: Iterable[int]) -> None:
    """Exige `stock_actual = SUM(partidas disponibles)` para cada producto indicado.

    Raises:
        StockNoConciliadoError: si alguno no concilia (la transacción del llamador se revierte).
    """
    no_conciliados = repositorio_partidas.productos_no_conciliados_en_conexion(conexion, producto_ids)
    if no_conciliados:
        logger.error("Stock no conciliado con partidas (producto, stock, partidas): %s", no_conciliados)
        raise StockNoConciliadoError(
            "El stock no coincide con sus partidas: se canceló la operación para no dejar datos inconsistentes."
        )


def listar_partidas(producto_id: int) -> list[Partida]:
    return repositorio_partidas.listar_de_producto(producto_id)


def productos_no_conciliados() -> list[tuple[int, int, int]]:
    """Diagnóstico de solo lectura: `(producto_id, stock, suma_de_partidas)` de los que no concilian."""
    return repositorio_partidas.productos_no_conciliados()
