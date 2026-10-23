"""Repositorio de partidas de stock y su libro de movimientos (migración 032, V1.12). Solo SQL parametrizado.

Las escrituras reciben la conexión: siempre ocurren dentro de la transacción de la operación que mueve
stock (venta, compra, ajuste, alta), nunca solas.
"""

import sqlite3
from collections.abc import Iterable

from db.conexion import obtener_conexion
from domain.partida import Asignacion, Partida

_COLUMNAS = (
    "id, producto_id, origen, cantidad_original, cantidad_disponible, fecha_vencimiento,"
    " costo_unitario_centavos, fecha_ingreso, detalle_compra_id, ajuste_id, detalle_venta_id"
)


def _fila_a_partida(fila: sqlite3.Row) -> Partida:
    return Partida(**{clave: fila[clave] for clave in fila.keys()})


def fecha_de_hoy_en_conexion(conexion: sqlite3.Connection) -> str:
    """El "hoy" de FEFO y de los vencimientos, del mismo reloj que las fechas que persiste la base."""
    return conexion.execute("SELECT date('now', 'localtime')").fetchone()[0]


def crear_en_conexion(
    conexion: sqlite3.Connection,
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
    """Alta de una partida con todo su disponible. La fecha de ingreso es la de la base (ahora)."""
    fila = conexion.execute(
        f"""
        INSERT INTO partidas_stock
            (producto_id, origen, detalle_compra_id, ajuste_id, detalle_venta_id, cantidad_original,
             cantidad_disponible, fecha_vencimiento, costo_unitario_centavos, fecha_ingreso)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
        RETURNING {_COLUMNAS}
        """,
        (producto_id, origen, detalle_compra_id, ajuste_id, detalle_venta_id, cantidad, cantidad,
         fecha_vencimiento, costo_unitario_centavos),
    ).fetchone()
    return _fila_a_partida(fila)


def listar_de_producto_en_conexion(conexion: sqlite3.Connection, producto_id: int) -> list[Partida]:
    filas = conexion.execute(
        f"SELECT {_COLUMNAS} FROM partidas_stock WHERE producto_id = ? ORDER BY id", (producto_id,)
    ).fetchall()
    return [_fila_a_partida(fila) for fila in filas]


def listar_de_producto(producto_id: int) -> list[Partida]:
    with obtener_conexion() as conexion:
        return listar_de_producto_en_conexion(conexion, producto_id)


def registrar_movimientos_en_conexion(
    conexion: sqlite3.Connection,
    asignaciones: Iterable[Asignacion],
    signo: int,
    *,
    detalle_venta_id: int | None = None,
    ajuste_id: int | None = None,
    detalle_compra_id: int | None = None,
) -> None:
    """Un movimiento por asignación, con `delta = signo * cantidad` y la causa indicada. El trigger de la
    032 aplica cada uno al disponible de su partida (y la base rechaza consumir de más)."""
    conexion.executemany(
        """
        INSERT INTO movimientos_partida (partida_id, delta, detalle_venta_id, ajuste_id, detalle_compra_id, criterio)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (a.partida_id, signo * a.cantidad, detalle_venta_id, ajuste_id, detalle_compra_id, a.criterio)
            for a in asignaciones
        ],
    )


def netos_por_partida_de_causa_en_conexion(
    conexion: sqlite3.Connection,
    *,
    detalle_venta_id: int | None = None,
    ajuste_id: int | None = None,
) -> dict[int, int]:
    """Suma de deltas por partida de todos los movimientos de una causa (para revertirla exactamente)."""
    if (detalle_venta_id is None) == (ajuste_id is None):
        raise ValueError("Indicar exactamente una causa.")
    columna, valor = ("detalle_venta_id", detalle_venta_id) if detalle_venta_id is not None else ("ajuste_id", ajuste_id)
    filas = conexion.execute(
        f"SELECT partida_id, SUM(delta) AS neto FROM movimientos_partida WHERE {columna} = ?"
        " GROUP BY partida_id ORDER BY partida_id",
        (valor,),
    ).fetchall()
    return {fila["partida_id"]: fila["neto"] for fila in filas if fila["neto"] != 0}


def ids_partidas_de_detalle_compra_en_conexion(conexion: sqlite3.Connection, detalle_compra_id: int) -> list[int]:
    filas = conexion.execute(
        "SELECT id FROM partidas_stock WHERE detalle_compra_id = ? ORDER BY id", (detalle_compra_id,)
    ).fetchall()
    return [fila["id"] for fila in filas]


def ingresos_por_producto_de_compra_en_conexion(
    conexion: sqlite3.Connection, compra_id: int
) -> dict[int, list[tuple[int, str]]]:
    """Por producto, `(cantidad_original, vencimiento o "")` de las partidas que ingresó una compra, ordenado:
    el contenido de vencimientos con el que se registró (para comparar un reintento idempotente)."""
    filas = conexion.execute(
        """
        SELECT s.producto_id, s.cantidad_original, COALESCE(s.fecha_vencimiento, '') AS vence
        FROM partidas_stock s JOIN detalle_compra d ON d.id = s.detalle_compra_id
        WHERE d.compra_id = ?
        """,
        (compra_id,),
    ).fetchall()
    resultado: dict[int, list[tuple[int, str]]] = {}
    for fila in filas:
        resultado.setdefault(fila["producto_id"], []).append((fila["cantidad_original"], fila["vence"]))
    return {producto_id: sorted(valores) for producto_id, valores in resultado.items()}


def lineas_de_venta_en_conexion(conexion: sqlite3.Connection, venta_id: int) -> list[tuple[int, int, int]]:
    """`(detalle_venta_id, producto_id, cantidad)` de cada línea, en el orden en que se registraron."""
    filas = conexion.execute(
        "SELECT id, producto_id, cantidad FROM detalle_venta WHERE venta_id = ? ORDER BY id", (venta_id,)
    ).fetchall()
    return [(fila["id"], fila["producto_id"], fila["cantidad"]) for fila in filas]


def lineas_de_compra_en_conexion(conexion: sqlite3.Connection, compra_id: int) -> list[tuple[int, int, int, int]]:
    """`(detalle_compra_id, producto_id, cantidad, costo_unitario_centavos)` de cada línea, por id."""
    filas = conexion.execute(
        "SELECT id, producto_id, cantidad, costo_unitario_centavos FROM detalle_compra WHERE compra_id = ? ORDER BY id",
        (compra_id,),
    ).fetchall()
    return [(fila["id"], fila["producto_id"], fila["cantidad"], fila["costo_unitario_centavos"]) for fila in filas]


def productos_no_conciliados_en_conexion(
    conexion: sqlite3.Connection, producto_ids: Iterable[int] | None = None
) -> list[tuple[int, int, int]]:
    """`(producto_id, stock_actual, suma_de_partidas)` de cada producto cuyo stock no coincide con sus
    partidas; todos los productos si `producto_ids` es `None`."""
    filtro, parametros = "", []
    if producto_ids is not None:
        ids = sorted(set(producto_ids))
        if not ids:
            return []
        filtro = f"WHERE p.id IN ({', '.join('?' for _ in ids)})"
        parametros = ids
    filas = conexion.execute(
        f"""
        SELECT p.id, p.stock_actual, COALESCE(SUM(s.cantidad_disponible), 0) AS en_partidas
        FROM productos p LEFT JOIN partidas_stock s ON s.producto_id = p.id
        {filtro}
        GROUP BY p.id
        HAVING p.stock_actual <> COALESCE(SUM(s.cantidad_disponible), 0)
        ORDER BY p.id
        """,
        parametros,
    ).fetchall()
    return [(fila["id"], fila["stock_actual"], fila["en_partidas"]) for fila in filas]


def productos_no_conciliados() -> list[tuple[int, int, int]]:
    with obtener_conexion() as conexion:
        return productos_no_conciliados_en_conexion(conexion)
