"""Repositorio de documentación fiscal declarada de proveedores (migración 036). Solo SQL parametrizado;
las filas son inmutables (las protege la base)."""

import sqlite3

from db.conexion import obtener_conexion
from domain.documento_proveedor import DocumentoProveedor

_SELECT = """
    SELECT d.id, d.proveedor_id, d.tipo, d.fecha_documento, d.referencia, d.observacion, d.usuario_id,
           u.nombre_completo AS usuario_nombre_completo, d.fecha_registro
    FROM documentos_proveedor d LEFT JOIN usuarios u ON u.id = d.usuario_id
"""


def _fila_a_documento(fila: sqlite3.Row) -> DocumentoProveedor:
    return DocumentoProveedor(**dict(fila))


def obtener_por_clave_en_conexion(conexion: sqlite3.Connection, clave_idempotencia: str) -> DocumentoProveedor | None:
    fila = conexion.execute(f"{_SELECT} WHERE d.clave_idempotencia = ?", (clave_idempotencia,)).fetchone()
    return _fila_a_documento(fila) if fila is not None else None


def registrar_en_conexion(
    conexion: sqlite3.Connection,
    proveedor_id: int,
    tipo: str,
    fecha_documento: str,
    referencia: str | None,
    observacion: str | None,
    usuario_id: int,
    clave_idempotencia: str | None,
) -> DocumentoProveedor:
    cursor = conexion.execute(
        """
        INSERT INTO documentos_proveedor (proveedor_id, tipo, fecha_documento, referencia, observacion, usuario_id,
                                          clave_idempotencia)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (proveedor_id, tipo, fecha_documento, referencia, observacion, usuario_id, clave_idempotencia),
    )
    return _fila_a_documento(conexion.execute(f"{_SELECT} WHERE d.id = ?", (cursor.lastrowid,)).fetchone())


def listar_de_proveedor(proveedor_id: int) -> list[DocumentoProveedor]:
    """Documentos del proveedor, del más reciente (por fecha del documento, luego por registro) al más antiguo."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            f"{_SELECT} WHERE d.proveedor_id = ? ORDER BY d.fecha_documento DESC, d.id DESC", (proveedor_id,)
        ).fetchall()
    return [_fila_a_documento(fila) for fila in filas]
