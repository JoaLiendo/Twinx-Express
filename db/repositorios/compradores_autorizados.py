"""Repositorio de compradores autorizados de empresas (migración 037, V1.14-A).

Solo SQL parametrizado. El esquema garantiza el máximo de 2 activos por empresa, que solo una
EMPRESA activa los tenga y que cada período solo admita la baja; el servicio valida antes para dar
un mensaje claro, y lo hace dentro de su `BEGIN IMMEDIATE` (dos altas simultáneas se serializan).
"""

import sqlite3

from db.conexion import obtener_conexion
from domain.cliente import CompradorAutorizado

_SELECT = """
    SELECT a.id, a.cliente_id, a.nombre, a.documento, a.contacto, a.activo, a.fecha_alta, a.fecha_baja,
           ua.nombre_completo AS usuario_alta, ub.nombre_completo AS usuario_baja
    FROM compradores_autorizados a
    JOIN usuarios ua ON ua.id = a.usuario_alta_id
    LEFT JOIN usuarios ub ON ub.id = a.usuario_baja_id
"""


def _fila_a_comprador(fila: sqlite3.Row) -> CompradorAutorizado:
    return CompradorAutorizado(
        id=fila["id"],
        cliente_id=fila["cliente_id"],
        nombre=fila["nombre"],
        documento=fila["documento"],
        contacto=fila["contacto"],
        activo=bool(fila["activo"]),
        fecha_alta=fila["fecha_alta"],
        usuario_alta=fila["usuario_alta"],
        fecha_baja=fila["fecha_baja"],
        usuario_baja=fila["usuario_baja"],
    )


def obtener_en_conexion(conexion: sqlite3.Connection, comprador_id: int) -> CompradorAutorizado | None:
    fila = conexion.execute(f"{_SELECT} WHERE a.id = ?", (comprador_id,)).fetchone()
    return _fila_a_comprador(fila) if fila is not None else None


def contar_activos_en_conexion(conexion: sqlite3.Connection, cliente_id: int) -> int:
    return conexion.execute(
        "SELECT COUNT(*) FROM compradores_autorizados WHERE cliente_id = ? AND activo = 1", (cliente_id,)
    ).fetchone()[0]


def crear_en_conexion(
    conexion: sqlite3.Connection, comprador: CompradorAutorizado, usuario_id: int
) -> CompradorAutorizado:
    """Abre un período de autorización (activo) y lo devuelve completo."""
    nuevo_id = conexion.execute(
        """
        INSERT INTO compradores_autorizados (cliente_id, nombre, documento, contacto, usuario_alta_id)
        VALUES (?, ?, ?, ?, ?)
        RETURNING id
        """,
        (comprador.cliente_id, comprador.nombre, comprador.documento, comprador.contacto, usuario_id),
    ).fetchone()["id"]
    return obtener_en_conexion(conexion, nuevo_id)


def dar_de_baja_en_conexion(conexion: sqlite3.Connection, comprador_id: int, usuario_id: int) -> CompradorAutorizado:
    """Cierra un período activo con fecha y usuario de la baja."""
    conexion.execute(
        """
        UPDATE compradores_autorizados
        SET activo = 0, fecha_baja = datetime('now', 'localtime'), usuario_baja_id = ?
        WHERE id = ? AND activo = 1
        """,
        (usuario_id, comprador_id),
    )
    return obtener_en_conexion(conexion, comprador_id)


def listar_por_cliente_en_conexion(conexion: sqlite3.Connection, cliente_id: int) -> list[CompradorAutorizado]:
    """Todos los períodos de la empresa: los activos primero y luego el historial, más reciente primero."""
    filas = conexion.execute(
        f"{_SELECT} WHERE a.cliente_id = ? ORDER BY a.activo DESC, a.id DESC", (cliente_id,)
    ).fetchall()
    return [_fila_a_comprador(fila) for fila in filas]


def listar_por_cliente(cliente_id: int) -> list[CompradorAutorizado]:
    """Ver `listar_por_cliente_en_conexion`."""
    with obtener_conexion() as conexion:
        return listar_por_cliente_en_conexion(conexion, cliente_id)
