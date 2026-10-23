"""Repositorio de acceso a datos de categorías de productos (Fase 3C, árbol en V1.11-E).

Contiene únicamente consultas SQL parametrizadas. No contiene reglas
de negocio: la validación de datos ocurre en `domain.categoria`.
"""

import sqlite3

from db.conexion import obtener_conexion
from domain.categoria import Categoria
from excepciones import CategoriaNoEncontradaError, DatosInvalidosError, ErrorBaseDatos, NombreCategoriaDuplicadoError

_COLUMNAS = "id, nombre, activa, fecha_creacion, categoria_padre_id"


def _fila_a_categoria(fila: sqlite3.Row) -> Categoria:
    """Mapea una fila de la tabla `categorias` a la entidad de dominio."""
    return Categoria(
        id=fila["id"],
        nombre=fila["nombre"],
        activa=bool(fila["activa"]),
        fecha_creacion=fila["fecha_creacion"],
        categoria_padre_id=fila["categoria_padre_id"],
    )


def _traducir_integridad(error: ErrorBaseDatos, nombre: str) -> ErrorBaseDatos | DatosInvalidosError:
    """Traduce las violaciones de integridad propias de `categorias`: el UNIQUE del nombre, y
    las guardias del árbol (FK del padre, trigger anti-ciclos) que el servicio ya valida antes
    -- si igual llegan acá, es una carrera o un llamador que no validó: dato inválido."""
    if not isinstance(error.__cause__, sqlite3.IntegrityError):
        return error
    if "UNIQUE" in str(error.__cause__):
        return NombreCategoriaDuplicadoError(f"Ya existe una categoría con el nombre '{nombre}'.")
    return DatosInvalidosError("La categoría superior elegida no es válida.")


def crear_categoria(categoria: Categoria) -> Categoria:
    """Inserta una nueva categoría y devuelve la entidad con su id asignado.

    Traduce la violación de UNIQUE sobre `nombre` en
    `NombreCategoriaDuplicadoError`.
    """
    consulta = f"""
        INSERT INTO categorias (nombre, categoria_padre_id)
        VALUES (?, ?)
        RETURNING {_COLUMNAS}
    """
    try:
        with obtener_conexion() as conexion:
            fila = conexion.execute(consulta, (categoria.nombre, categoria.categoria_padre_id)).fetchone()
    except ErrorBaseDatos as error:
        traducido = _traducir_integridad(error, categoria.nombre)
        if traducido is error:
            raise
        raise traducido from error
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


def listar_todas() -> list[Categoria]:
    """Devuelve todas las categorías (activas e inactivas), ordenadas
    por nombre (para la pantalla de gestión de categorías)."""
    consulta = f"SELECT {_COLUMNAS} FROM categorias ORDER BY nombre"
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta).fetchall()
    return [_fila_a_categoria(fila) for fila in filas]


def eliminar_categoria(categoria_id: int) -> bool:
    """Elimina una categoría activa. Devuelve `True` si la baja fue lógica.

    Intenta primero un `DELETE` físico. Si hay productos o subcategorías
    que la referencian, las FK `ON DELETE RESTRICT` lo impiden
    (`sqlite3.IntegrityError`): en ese caso se la desactiva
    (`activa = 0`) en vez de borrarla, para no romper la relación con
    lo que ya la tiene asignada.
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


def actualizar(categoria_id: int, nombre: str, categoria_padre_id: int | None) -> Categoria:
    """Actualiza el nombre y la categoría superior de una categoría existente.

    Traduce la violación de UNIQUE sobre `nombre` en
    `NombreCategoriaDuplicadoError`, igual que `crear_categoria`. No
    exige que la categoría esté activa: también se puede corregir el
    nombre de una dada de baja lógica.
    """
    consulta = f"""
        UPDATE categorias SET nombre = ?, categoria_padre_id = ? WHERE id = ?
        RETURNING {_COLUMNAS}
    """
    try:
        with obtener_conexion() as conexion:
            fila = conexion.execute(consulta, (nombre, categoria_padre_id, categoria_id)).fetchone()
    except ErrorBaseDatos as error:
        traducido = _traducir_integridad(error, nombre)
        if traducido is error:
            raise
        raise traducido from error
    if fila is None:
        raise CategoriaNoEncontradaError(f"No existe una categoría con id {categoria_id}.")
    return _fila_a_categoria(fila)


def reactivar_categoria(categoria_id: int) -> Categoria:
    """Revierte una baja lógica (`activa` de 0 a 1)."""
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
