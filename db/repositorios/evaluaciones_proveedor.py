"""Repositorio de evaluaciones de proveedores (migración 034). Solo SQL parametrizado; las filas son
inmutables (las protege la base), así que acá solo se insertan y se leen."""

import sqlite3
from collections.abc import Iterable

from db.conexion import obtener_conexion
from domain.evaluacion_proveedor import CRITERIOS, EvaluacionProveedor

# Los nombres de criterio son también los de sus columnas (en minúsculas): lista fija, no dato del usuario.
_COLUMNAS_CRITERIOS = [criterio.lower() for criterio in CRITERIOS]
_SELECT = f"""
    SELECT e.id, e.proveedor_id, e.estrellas, e.comentario, e.usuario_id, u.nombre_completo AS usuario_nombre_completo,
           e.fecha, {", ".join(f"e.{columna}" for columna in _COLUMNAS_CRITERIOS)}
    FROM evaluaciones_proveedor e
    LEFT JOIN usuarios u ON u.id = e.usuario_id
"""


def _fila_a_evaluacion(fila: sqlite3.Row) -> EvaluacionProveedor:
    return EvaluacionProveedor(
        id=fila["id"],
        proveedor_id=fila["proveedor_id"],
        estrellas=fila["estrellas"],
        comentario=fila["comentario"],
        usuario_id=fila["usuario_id"],
        usuario_nombre_completo=fila["usuario_nombre_completo"],
        fecha=fila["fecha"],
        criterios={
            criterio: fila[criterio.lower()] for criterio in CRITERIOS if fila[criterio.lower()] is not None
        },
    )


def registrar_en_conexion(
    conexion: sqlite3.Connection,
    proveedor_id: int,
    estrellas: int,
    comentario: str,
    criterios: dict[str, int],
    usuario_id: int,
    clave_idempotencia: str | None,
) -> EvaluacionProveedor:
    columnas = ["proveedor_id", "estrellas", "comentario", "usuario_id", "clave_idempotencia", *_COLUMNAS_CRITERIOS]
    valores = [proveedor_id, estrellas, comentario, usuario_id, clave_idempotencia]
    valores += [criterios.get(criterio) for criterio in CRITERIOS]
    cursor = conexion.execute(
        f"INSERT INTO evaluaciones_proveedor ({', '.join(columnas)}) VALUES ({', '.join('?' for _ in columnas)})",
        valores,
    )
    return obtener_en_conexion(conexion, cursor.lastrowid)


def obtener_en_conexion(conexion: sqlite3.Connection, evaluacion_id: int) -> EvaluacionProveedor | None:
    fila = conexion.execute(f"{_SELECT} WHERE e.id = ?", (evaluacion_id,)).fetchone()
    return _fila_a_evaluacion(fila) if fila is not None else None


def obtener_por_clave_en_conexion(conexion: sqlite3.Connection, clave_idempotencia: str) -> EvaluacionProveedor | None:
    fila = conexion.execute(f"{_SELECT} WHERE e.clave_idempotencia = ?", (clave_idempotencia,)).fetchone()
    return _fila_a_evaluacion(fila) if fila is not None else None


def listar_de_proveedor(proveedor_id: int) -> list[EvaluacionProveedor]:
    """Historial completo de un proveedor, de la más reciente a la más antigua."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(f"{_SELECT} WHERE e.proveedor_id = ? ORDER BY e.id DESC", (proveedor_id,)).fetchall()
    return [_fila_a_evaluacion(fila) for fila in filas]


def calificaciones_actuales(proveedor_ids: Iterable[int] | None = None) -> dict[int, int]:
    """Estrellas de la evaluación más reciente (mayor id) de cada proveedor evaluado; los que no tienen
    ninguna no aparecen."""
    consulta = """
        SELECT e.proveedor_id, e.estrellas FROM evaluaciones_proveedor e
        WHERE e.id = (SELECT MAX(x.id) FROM evaluaciones_proveedor x WHERE x.proveedor_id = e.proveedor_id)
    """
    parametros: list[int] = []
    if proveedor_ids is not None:
        ids = sorted(set(proveedor_ids))
        if not ids:
            return {}
        consulta += f" AND e.proveedor_id IN ({', '.join('?' for _ in ids)})"
        parametros = ids
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta, parametros).fetchall()
    return {fila["proveedor_id"]: fila["estrellas"] for fila in filas}
