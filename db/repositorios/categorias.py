"""Repositorio de acceso a datos de categorías de productos (Fase 3C).

Contiene únicamente consultas SQL parametrizadas. No contiene reglas
de negocio: la validación de datos ocurre en `domain.categoria.Categoria`.
"""

import sqlite3

from db.conexion import obtener_conexion
from domain.categoria import Categoria
from excepciones import CategoriaNoEncontradaError, ErrorBaseDatos, NombreCategoriaDuplicadoError

_COLUMNAS = "id, nombre, activa, fecha_creacion"


def _fila_a_categoria(fila: sqlite3.Row) -> Categoria:
    """Mapea una fila de la tabla `categorias` a la entidad de dominio."""
    return Categoria(
        id=fila["id"],
        nombre=fila["nombre"],
        activa=bool(fila["activa"]),
        fecha_creacion=fila["fecha_creacion"],
    )


def crear_categoria(categoria: Categoria) -> Categoria:
    """Inserta una nueva categoría y devuelve la entidad con su id asignado.

    Traduce la violación de UNIQUE sobre `nombre` en
    `NombreCategoriaDuplicadoError`.
    """
    consulta = f"""
        INSERT INTO categorias (nombre)
        VALUES (?)
        RETURNING {_COLUMNAS}
    """
    try:
        with obtener_conexion() as conexion:
            fila = conexion.execute(consulta, (categoria.nombre,)).fetchone()
    except ErrorBaseDatos as error:
        if isinstance(error.__cause__, sqlite3.IntegrityError):
            raise NombreCategoriaDuplicadoError(
                f"Ya existe una categoría con el nombre '{categoria.nombre}'."
            ) from error
        raise
    return _fila_a_categoria(fila)


def obtener_por_id(categoria_id: int) -> Categoria | None:
    """Busca una categoría por id, o `None` si no existe."""
    consulta = f"SELECT {_COLUMNAS} FROM categorias WHERE id = ?"
    with obtener_conexion() as conexion:
        fila = conexion.execute(consulta, (categoria_id,)).fetchone()
    return _fila_a_categoria(fila) if fila is not None else None


def obtener_por_nombre(nombre: str) -> Categoria | None:
    """Busca una categoría por nombre exacto, o `None` si no existe."""
    consulta = f"SELECT {_COLUMNAS} FROM categorias WHERE nombre = ?"
    with obtener_conexion() as conexion:
        fila = conexion.execute(consulta, (nombre,)).fetchone()
    return _fila_a_categoria(fila) if fila is not None else None


def listar_activas() -> list[Categoria]:
    """Devuelve las categorías activas, ordenadas por nombre (para
    selects de alta/edición de productos y filtros de listado)."""
    consulta = f"SELECT {_COLUMNAS} FROM categorias WHERE activa = 1 ORDER BY nombre"
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta).fetchall()
    return [_fila_a_categoria(fila) for fila in filas]


def listar_todas() -> list[Categoria]:
    """Devuelve todas las categorías (activas e inactivas), ordenadas
    por nombre (para la pantalla de gestión de categorías)."""
    consulta = f"SELECT {_COLUMNAS} FROM categorias ORDER BY nombre"
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta).fetchall()
    return [_fila_a_categoria(fila) for fila in filas]


def eliminar_categoria(categoria_id: int) -> bool:
    """Elimina una categoría activa. Devuelve `True` si la baja fue lógica.

    Intenta primero un `DELETE` físico. Si hay productos que la
    referencian, la FK `ON DELETE RESTRICT` de `productos.categoria_id`
    lo impide (`sqlite3.IntegrityError`): en ese caso se la desactiva
    (`activa = 0`) en vez de borrarla, para no romper la relación con
    los productos que ya la tienen asignada.
    """
    try:
        with obtener_conexion() as conexion:
            cursor = conexion.execute(
                "DELETE FROM categorias WHERE id = ? AND activa = 1", (categoria_id,)
            )
            fue_eliminada = cursor.rowcount > 0
        baja_logica = False
    except ErrorBaseDatos as error:
        if not isinstance(error.__cause__, sqlite3.IntegrityError):
            raise
        with obtener_conexion() as conexion:
            cursor = conexion.execute(
                "UPDATE categorias SET activa = 0 WHERE id = ? AND activa = 1", (categoria_id,)
            )
            fue_eliminada = cursor.rowcount > 0
        baja_logica = True

    if not fue_eliminada:
        raise CategoriaNoEncontradaError(f"No existe una categoría activa con id {categoria_id}.")
    return baja_logica


def actualizar_nombre(categoria_id: int, nombre: str) -> Categoria:
    """Actualiza únicamente el nombre de una categoría existente.

    Traduce la violación de UNIQUE sobre `nombre` en
    `NombreCategoriaDuplicadoError`, igual que `crear_categoria`. No
    exige que la categoría esté activa: también se puede corregir el
    nombre de una dada de baja lógica.
    """
    consulta = f"""
        UPDATE categorias SET nombre = ? WHERE id = ?
        RETURNING {_COLUMNAS}
    """
    try:
        with obtener_conexion() as conexion:
            fila = conexion.execute(consulta, (nombre, categoria_id)).fetchone()
    except ErrorBaseDatos as error:
        if isinstance(error.__cause__, sqlite3.IntegrityError):
            raise NombreCategoriaDuplicadoError(
                f"Ya existe una categoría con el nombre '{nombre}'."
            ) from error
        raise
    if fila is None:
        raise CategoriaNoEncontradaError(f"No existe una categoría con id {categoria_id}.")
    return _fila_a_categoria(fila)


def reactivar_categoria(categoria_id: int) -> Categoria:
    """Revierte una baja lógica (`activa` de 0 a 1).

    Dejada preparada para cuando exista una UI de reactivación (no es
    parte de esta fase, ver Fase 3C).
    """
    consulta = f"""
        UPDATE categorias
        SET activa = 1
        WHERE id = ? AND activa = 0
        RETURNING {_COLUMNAS}
    """
    with obtener_conexion() as conexion:
        fila = conexion.execute(consulta, (categoria_id,)).fetchone()
    if fila is None:
        raise CategoriaNoEncontradaError(f"No existe una categoría inactiva con id {categoria_id}.")
    return _fila_a_categoria(fila)
