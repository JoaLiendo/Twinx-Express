"""Repositorio de reclamos a proveedores y de sus cambios de estado (migración 035). Solo SQL
parametrizado. El estado de un reclamo nunca se escribe acá directamente: lo aplica la base al
insertar un cambio."""

import sqlite3
from collections.abc import Iterable

from db.conexion import obtener_conexion
from domain.reclamo_proveedor import CambioEstadoReclamo, ConsumoDeAjuste, ReclamoProveedor

_SELECT = """
    SELECT r.id, r.proveedor_id, pr.nombre AS proveedor_nombre, r.tipo, r.estado, r.descripcion,
           r.producto_id, p.nombre AS producto_nombre, r.compra_id, r.detalle_compra_id, r.ajuste_id, r.cantidad,
           r.usuario_id, u.nombre_completo AS usuario_nombre_completo, r.fecha
    FROM reclamos_proveedor r
    JOIN proveedores pr ON pr.id = r.proveedor_id
    LEFT JOIN productos p ON p.id = r.producto_id
    LEFT JOIN usuarios u ON u.id = r.usuario_id
"""


def _fila_a_reclamo(fila: sqlite3.Row, cambios: list[CambioEstadoReclamo] | None = None) -> ReclamoProveedor:
    return ReclamoProveedor(
        id=fila["id"],
        proveedor_id=fila["proveedor_id"],
        proveedor_nombre=fila["proveedor_nombre"],
        tipo=fila["tipo"],
        estado=fila["estado"],
        descripcion=fila["descripcion"],
        producto_id=fila["producto_id"],
        producto_nombre=fila["producto_nombre"],
        compra_id=fila["compra_id"],
        detalle_compra_id=fila["detalle_compra_id"],
        ajuste_id=fila["ajuste_id"],
        cantidad=fila["cantidad"],
        usuario_id=fila["usuario_id"],
        usuario_nombre_completo=fila["usuario_nombre_completo"],
        fecha=fila["fecha"],
        cambios=cambios or [],
    )


_SELECT_CAMBIOS = """
    SELECT c.id, c.reclamo_id, c.estado_anterior, c.estado_nuevo, c.observacion, c.usuario_id,
           u.nombre_completo AS usuario_nombre_completo, c.fecha
    FROM reclamos_proveedor_cambios c LEFT JOIN usuarios u ON u.id = c.usuario_id
"""


def _cambios_en_conexion(conexion: sqlite3.Connection, reclamo_id: int) -> list[CambioEstadoReclamo]:
    filas = conexion.execute(f"{_SELECT_CAMBIOS} WHERE c.reclamo_id = ? ORDER BY c.id", (reclamo_id,)).fetchall()
    return [CambioEstadoReclamo(**dict(fila)) for fila in filas]


def obtener_en_conexion(conexion: sqlite3.Connection, reclamo_id: int) -> ReclamoProveedor | None:
    """El reclamo con todo su historial de cambios (del más antiguo al más reciente)."""
    fila = conexion.execute(f"{_SELECT} WHERE r.id = ?", (reclamo_id,)).fetchone()
    return _fila_a_reclamo(fila, _cambios_en_conexion(conexion, reclamo_id)) if fila is not None else None


def obtener(reclamo_id: int) -> ReclamoProveedor | None:
    with obtener_conexion() as conexion:
        return obtener_en_conexion(conexion, reclamo_id)


def obtener_por_clave_en_conexion(conexion: sqlite3.Connection, clave_idempotencia: str) -> ReclamoProveedor | None:
    fila = conexion.execute("SELECT id FROM reclamos_proveedor WHERE clave_idempotencia = ?", (clave_idempotencia,)).fetchone()
    return obtener_en_conexion(conexion, fila["id"]) if fila is not None else None


def obtener_por_ajuste_en_conexion(conexion: sqlite3.Connection, ajuste_id: int) -> int | None:
    fila = conexion.execute("SELECT id FROM reclamos_proveedor WHERE ajuste_id = ?", (ajuste_id,)).fetchone()
    return fila["id"] if fila is not None else None


def crear_en_conexion(
    conexion: sqlite3.Connection,
    *,
    proveedor_id: int,
    tipo: str,
    descripcion: str,
    producto_id: int | None,
    compra_id: int | None,
    detalle_compra_id: int | None,
    ajuste_id: int | None,
    cantidad: int | None,
    usuario_id: int,
    clave_idempotencia: str | None,
) -> ReclamoProveedor:
    cursor = conexion.execute(
        """
        INSERT INTO reclamos_proveedor (proveedor_id, tipo, descripcion, producto_id, compra_id, detalle_compra_id,
                                        ajuste_id, cantidad, usuario_id, clave_idempotencia)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (proveedor_id, tipo, descripcion, producto_id, compra_id, detalle_compra_id, ajuste_id, cantidad, usuario_id,
         clave_idempotencia),
    )
    return obtener_en_conexion(conexion, cursor.lastrowid)


def obtener_cambio_por_clave_en_conexion(
    conexion: sqlite3.Connection, clave_idempotencia: str
) -> CambioEstadoReclamo | None:
    fila = conexion.execute(f"{_SELECT_CAMBIOS} WHERE c.clave_idempotencia = ?", (clave_idempotencia,)).fetchone()
    return CambioEstadoReclamo(**dict(fila)) if fila is not None else None


def registrar_cambio_en_conexion(
    conexion: sqlite3.Connection,
    reclamo_id: int,
    estado_anterior: str,
    estado_nuevo: str,
    observacion: str,
    usuario_id: int,
    clave_idempotencia: str | None,
) -> None:
    conexion.execute(
        """
        INSERT INTO reclamos_proveedor_cambios (reclamo_id, estado_anterior, estado_nuevo, observacion, usuario_id,
                                                clave_idempotencia)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (reclamo_id, estado_anterior, estado_nuevo, observacion, usuario_id, clave_idempotencia),
    )


def listar_de_proveedor(proveedor_id: int) -> list[ReclamoProveedor]:
    """Reclamos del proveedor, del más reciente al más antiguo (sin su historial de cambios)."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(f"{_SELECT} WHERE r.proveedor_id = ? ORDER BY r.id DESC", (proveedor_id,)).fetchall()
    return [_fila_a_reclamo(fila) for fila in filas]


def abiertos_por_proveedor(estados_abiertos: Iterable[str]) -> dict[int, int]:
    """Cantidad de reclamos abiertos de cada proveedor que tiene alguno."""
    estados = sorted(estados_abiertos)
    filas_sql = f"""
        SELECT proveedor_id, COUNT(*) AS abiertos FROM reclamos_proveedor
        WHERE estado IN ({', '.join('?' for _ in estados)}) GROUP BY proveedor_id
    """
    with obtener_conexion() as conexion:
        filas = conexion.execute(filas_sql, estados).fetchall()
    return {fila["proveedor_id"]: fila["abiertos"] for fila in filas}


def reclamos_de_ajustes(ajuste_ids: Iterable[int]) -> dict[int, int]:
    """Id del reclamo originado por cada ajuste que tiene uno."""
    ids = sorted(set(ajuste_ids))
    if not ids:
        return {}
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            f"SELECT ajuste_id, id FROM reclamos_proveedor WHERE ajuste_id IN ({', '.join('?' for _ in ids)})", ids
        ).fetchall()
    return {fila["ajuste_id"]: fila["id"] for fila in filas}


# --- datos de origen ---------------------------------------------------------------------------------


def consumos_de_ajuste_en_conexion(conexion: sqlite3.Connection, ajuste_id: int) -> list[ConsumoDeAjuste]:
    """Las partidas de las que salió un ajuste (movimientos negativos), con su compra y proveedor si
    vinieron de una compra. Un ajuste anterior a V1.12 no tiene ninguna."""
    filas = conexion.execute(
        """
        SELECT m.criterio, s.origen AS origen_partida, s.detalle_compra_id, d.compra_id, c.proveedor_id
        FROM movimientos_partida m
        JOIN partidas_stock s ON s.id = m.partida_id
        LEFT JOIN detalle_compra d ON d.id = s.detalle_compra_id
        LEFT JOIN compras c ON c.id = d.compra_id
        WHERE m.ajuste_id = ? AND m.delta < 0
        ORDER BY m.id
        """,
        (ajuste_id,),
    ).fetchall()
    return [ConsumoDeAjuste(**dict(fila)) for fila in filas]


def proveedor_de_compra_en_conexion(conexion: sqlite3.Connection, compra_id: int) -> int | None:
    fila = conexion.execute("SELECT proveedor_id FROM compras WHERE id = ?", (compra_id,)).fetchone()
    return fila["proveedor_id"] if fila is not None else None


def linea_de_compra_en_conexion(conexion: sqlite3.Connection, compra_id: int, producto_id: int) -> int | None:
    """La línea de la compra para ese producto (una compra no repite productos), o `None`."""
    fila = conexion.execute(
        "SELECT id FROM detalle_compra WHERE compra_id = ? AND producto_id = ?", (compra_id, producto_id)
    ).fetchone()
    return fila["id"] if fila is not None else None


def producto_existe_en_conexion(conexion: sqlite3.Connection, producto_id: int) -> bool:
    return conexion.execute("SELECT 1 FROM productos WHERE id = ?", (producto_id,)).fetchone() is not None
