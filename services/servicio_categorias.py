"""Casos de uso de gestión de categorías de productos (Fase 3C).

Orquesta `domain.categoria` y `db.repositorios.categorias`. Es CRUD
simple: no hay ninguna regla de negocio más allá de "el nombre no
puede repetirse" y "no se puede borrar físicamente una categoría en
uso" — ambas ya las traduce el repositorio.
"""

import logging

from db.repositorios import categorias as repositorio_categorias
from domain.categoria import Categoria
from excepciones import CategoriaNoEncontradaError

logger = logging.getLogger(__name__)


def crear_categoria(nombre: str) -> Categoria:
    """Da de alta una nueva categoría.

    Raises:
        DatosInvalidosError: si el nombre está vacío.
        NombreCategoriaDuplicadoError: si ya existe una categoría con ese nombre.
    """
    categoria = Categoria(nombre=nombre)
    categoria_creada = repositorio_categorias.crear_categoria(categoria)
    logger.info("Categoría creada: %s (id=%s)", categoria_creada.nombre, categoria_creada.id)
    return categoria_creada


def obtener_por_id(categoria_id: int) -> Categoria | None:
    """Busca una categoría por id."""
    return repositorio_categorias.obtener_por_id(categoria_id)


def obtener_por_nombre(nombre: str) -> Categoria | None:
    """Busca una categoría por nombre exacto."""
    return repositorio_categorias.obtener_por_nombre(nombre)


def listar_activas() -> list[Categoria]:
    """Devuelve las categorías activas (para selects de productos y filtros)."""
    return repositorio_categorias.listar_activas()


def listar_todas() -> list[Categoria]:
    """Devuelve todas las categorías, activas e inactivas (pantalla de gestión)."""
    return repositorio_categorias.listar_todas()


def actualizar_categoria(categoria_id: int, nombre: str) -> Categoria:
    """Actualiza el nombre de una categoría existente.

    Raises:
        CategoriaNoEncontradaError: si no existe una categoría con ese id.
        DatosInvalidosError: si el nombre está vacío.
        NombreCategoriaDuplicadoError: si ya existe otra categoría con ese nombre.
    """
    categoria_actual = repositorio_categorias.obtener_por_id(categoria_id)
    if categoria_actual is None:
        raise CategoriaNoEncontradaError(f"No existe una categoría con id {categoria_id}.")
    # Reconstruye la entidad con el nombre nuevo para reutilizar la
    # validación de dominio (no vacío) antes de persistir.
    Categoria(
        nombre=nombre,
        id=categoria_actual.id,
        activa=categoria_actual.activa,
        fecha_creacion=categoria_actual.fecha_creacion,
    )
    categoria_actualizada = repositorio_categorias.actualizar_nombre(categoria_id, nombre)
    logger.info("Categoría actualizada: %s (id=%s)", categoria_actualizada.nombre, categoria_id)
    return categoria_actualizada


def eliminar_categoria(categoria_id: int) -> bool:
    """Da de baja una categoría. Devuelve `True` si la baja fue lógica.

    Si ningún producto la usa, se elimina físicamente; si algún
    producto la referencia, se desactiva (baja lógica) para no romper
    esa relación (ver `db.repositorios.categorias.eliminar_categoria`).

    Raises:
        CategoriaNoEncontradaError: si no existe una categoría activa con ese id.
    """
    baja_logica = repositorio_categorias.eliminar_categoria(categoria_id)
    logger.info("Categoría id=%s dada de baja (baja_logica=%s)", categoria_id, baja_logica)
    return baja_logica


def reactivar_categoria(categoria_id: int) -> Categoria:
    """Reactiva una categoría dada de baja lógica.

    Raises:
        CategoriaNoEncontradaError: si no existe una categoría inactiva con ese id.
    """
    categoria = repositorio_categorias.reactivar_categoria(categoria_id)
    logger.info("Categoría reactivada: %s (id=%s)", categoria.nombre, categoria.id)
    return categoria
