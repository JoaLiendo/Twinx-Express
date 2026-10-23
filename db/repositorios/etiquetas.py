"""Repositorio de etiquetas de productos (migración 030, V1.11-E). Solo SQL parametrizado."""

import sqlite3
from collections.abc import Iterable

from db.conexion import obtener_conexion
from domain.etiqueta import Etiqueta
from excepciones import ErrorBaseDatos, EtiquetaNoEncontradaError, NombreEtiquetaDuplicadoError

_COLUMNAS = "id, nombre, fecha_creacion"


def _fila_a_etiqueta(fila: sqlite3.Row) -> Etiqueta:
    return Etiqueta(id=fila["id"], nombre=fila["nombre"], fecha_creacion=fila["fecha_creacion"])


def _duplicado(error: ErrorBaseDatos, nombre: str) -> ErrorBaseDatos | NombreEtiquetaDuplicadoError:
    if isinstance(error.__cause__, sqlite3.IntegrityError):
        return NombreEtiquetaDuplicadoError(f"Ya existe una etiqueta con el nombre '{nombre}'.")
    return error


def crear(etiqueta: Etiqueta) -> Etiqueta:
    """Inserta una etiqueta. El nombre es único sin distinguir mayúsculas (COLLATE NOCASE)."""
    try:
        with obtener_conexion() as conexion:
            fila = conexion.execute(
                f"INSERT INTO etiquetas (nombre) VALUES (?) RETURNING {_COLUMNAS}", (etiqueta.nombre,)
            ).fetchone()
    except ErrorBaseDatos as error:
        traducido = _duplicado(error, etiqueta.nombre)
        if traducido is error:
            raise
        raise traducido from error
    return _fila_a_etiqueta(fila)


def renombrar(etiqueta_id: int, nombre: str) -> Etiqueta:
    try:
        with obtener_conexion() as conexion:
            fila = conexion.execute(
                f"UPDATE etiquetas SET nombre = ? WHERE id = ? RETURNING {_COLUMNAS}", (nombre, etiqueta_id)
            ).fetchone()
    except ErrorBaseDatos as error:
        traducido = _duplicado(error, nombre)
        if traducido is error:
            raise
        raise traducido from error
    if fila is None:
        raise EtiquetaNoEncontradaError(f"No existe una etiqueta con id {etiqueta_id}.")
    return _fila_a_etiqueta(fila)


def eliminar(etiqueta_id: int) -> None:
    """Borra la etiqueta; sus asignaciones a productos se van en cascada."""
    with obtener_conexion() as conexion:
        cursor = conexion.execute("DELETE FROM etiquetas WHERE id = ?", (etiqueta_id,))
    if cursor.rowcount == 0:
        raise EtiquetaNoEncontradaError(f"No existe una etiqueta con id {etiqueta_id}.")


def listar() -> list[Etiqueta]:
    with obtener_conexion() as conexion:
        filas = conexion.execute(f"SELECT {_COLUMNAS} FROM etiquetas ORDER BY nombre COLLATE NOCASE").fetchall()
    return [_fila_a_etiqueta(fila) for fila in filas]


def contar_productos_por_etiqueta() -> dict[int, int]:
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            "SELECT etiqueta_id, COUNT(*) AS n FROM producto_etiquetas GROUP BY etiqueta_id"
        ).fetchall()
    return {fila["etiqueta_id"]: fila["n"] for fila in filas}


def ids_por_producto() -> dict[int, list[int]]:
    """Etiquetas de cada producto que tenga alguna (una sola consulta para todo el listado)."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            "SELECT pe.producto_id, pe.etiqueta_id FROM producto_etiquetas pe"
            " JOIN etiquetas e ON e.id = pe.etiqueta_id ORDER BY e.nombre COLLATE NOCASE"
        ).fetchall()
    resultado: dict[int, list[int]] = {}
    for fila in filas:
        resultado.setdefault(fila["producto_id"], []).append(fila["etiqueta_id"])
    return resultado


def ids_de_producto(producto_id: int) -> list[int]:
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            "SELECT etiqueta_id FROM producto_etiquetas WHERE producto_id = ? ORDER BY etiqueta_id", (producto_id,)
        ).fetchall()
    return [fila["etiqueta_id"] for fila in filas]


def reemplazar_de_producto_en_conexion(
    conexion: sqlite3.Connection, producto_id: int, etiqueta_ids: Iterable[int]
) -> bool:
    """Deja al producto exactamente con `etiqueta_ids`, dentro de la transacción recibida.
    Devuelve `True` si algo cambió."""
    nuevas = set(etiqueta_ids)
    actuales = {
        fila["etiqueta_id"]
        for fila in conexion.execute(
            "SELECT etiqueta_id FROM producto_etiquetas WHERE producto_id = ?", (producto_id,)
        ).fetchall()
    }
    if nuevas == actuales:
        return False
    conexion.executemany(
        "DELETE FROM producto_etiquetas WHERE producto_id = ? AND etiqueta_id = ?",
        [(producto_id, etiqueta_id) for etiqueta_id in actuales - nuevas],
    )
    conexion.executemany(
        "INSERT INTO producto_etiquetas (producto_id, etiqueta_id) VALUES (?, ?)",
        [(producto_id, etiqueta_id) for etiqueta_id in sorted(nuevas - actuales)],
    )
    return True
