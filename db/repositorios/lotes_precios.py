"""Repositorio de lotes de actualización masiva de precios (migración 016)."""

import sqlite3
from dataclasses import dataclass

from db.conexion import obtener_conexion
from domain.precios_masivos import CriterioActualizacion


@dataclass(frozen=True)
class LotePrecios:
    """Cabecera de una actualización masiva ya aplicada."""

    id: int
    fecha: str
    usuario_id: int
    usuario_nombre_completo: str | None
    criterio: CriterioActualizacion
    alcance: str
    cantidad_productos: int


_SELECT = """
    SELECT l.id, l.fecha, l.usuario_id, u.nombre_completo AS usuario_nombre_completo,
           l.tipo, l.direccion, l.valor, l.redondeo, l.alcance, l.cantidad_productos
    FROM lotes_precios l
    LEFT JOIN usuarios u ON u.id = l.usuario_id
"""


def _fila_a_lote(fila: sqlite3.Row) -> LotePrecios:
    return LotePrecios(
        id=fila["id"],
        fecha=fila["fecha"],
        usuario_id=fila["usuario_id"],
        usuario_nombre_completo=fila["usuario_nombre_completo"],
        criterio=CriterioActualizacion(
            tipo=fila["tipo"], direccion=fila["direccion"], valor=fila["valor"], redondeo=fila["redondeo"]
        ),
        alcance=fila["alcance"],
        cantidad_productos=fila["cantidad_productos"],
    )


def obtener_por_clave_idempotencia_en_conexion(conexion: sqlite3.Connection, clave: str) -> LotePrecios | None:
    fila = conexion.execute(_SELECT + " WHERE l.clave_idempotencia = ?", (clave,)).fetchone()
    return _fila_a_lote(fila) if fila is not None else None


def crear_lote_en_conexion(
    conexion: sqlite3.Connection,
    usuario_id: int,
    criterio: CriterioActualizacion,
    alcance: str,
    cantidad_productos: int,
    clave_idempotencia: str | None,
) -> int:
    """Inserta la cabecera del lote dentro de la transacción recibida y devuelve su id."""
    fila = conexion.execute(
        """
        INSERT INTO lotes_precios
            (usuario_id, tipo, direccion, valor, redondeo, alcance, cantidad_productos, clave_idempotencia)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            usuario_id,
            criterio.tipo,
            criterio.direccion,
            criterio.valor,
            criterio.redondeo,
            alcance,
            cantidad_productos,
            clave_idempotencia,
        ),
    ).fetchone()
    return fila["id"]


def obtener_por_id(lote_id: int) -> LotePrecios | None:
    with obtener_conexion() as conexion:
        fila = conexion.execute(_SELECT + " WHERE l.id = ?", (lote_id,)).fetchone()
    return _fila_a_lote(fila) if fila is not None else None


def listar_recientes(limite: int = 10) -> list[LotePrecios]:
    with obtener_conexion() as conexion:
        filas = conexion.execute(_SELECT + " ORDER BY l.id DESC LIMIT ?", (limite,)).fetchall()
    return [_fila_a_lote(fila) for fila in filas]
