"""Casos de uso de gestión de categorías de productos (Fase 3C, árbol en V1.11-E).

Orquesta `domain.categoria` y `db.repositorios.categorias`. Además de
"el nombre no puede repetirse" y "no se puede borrar físicamente una
categoría en uso" (ambas ya las traduce el repositorio), valida la
ubicación en el árbol (`ArbolCategorias.validar_ubicacion`) y que una
baja o reactivación no deje subcategorías activas colgando de una
categoría inactiva.
"""

import logging

from db.repositorios import categorias as repositorio_categorias
from domain.categoria import ArbolCategorias, Categoria, CategoriaEnArbol
from excepciones import CategoriaNoEncontradaError, DatosInvalidosError

logger = logging.getLogger(__name__)

_MISMA_CATEGORIA_SUPERIOR = object()


def obtener_arbol() -> ArbolCategorias:
    """Árbol con todas las categorías, activas e inactivas (rutas, filtros con subcategorías)."""
    return ArbolCategorias(repositorio_categorias.listar_todas())


def opciones_de_categoria_superior(categoria_id: int | None = None) -> list[CategoriaEnArbol]:
    """Categorías activas donde puede quedar `categoria_id` (o una nueva, si es `None`) sin
    formar un ciclo ni superar los niveles admitidos, en orden de árbol."""
    arbol = obtener_arbol()
    opciones = []
    for opcion in arbol.en_orden(solo_activas=True):
        try:
            arbol.validar_ubicacion(categoria_id, opcion.categoria.id)
        except DatosInvalidosError:
            continue
        opciones.append(opcion)
    return opciones


def crear_categoria(nombre: str, categoria_padre_id: int | None = None) -> Categoria:
    """Da de alta una nueva categoría, principal o dentro de `categoria_padre_id`.

    Raises:
        DatosInvalidosError: si el nombre está vacío o la ubicación no es válida.
        NombreCategoriaDuplicadoError: si ya existe una categoría con ese nombre.
    """
    categoria = Categoria(nombre=nombre, categoria_padre_id=categoria_padre_id)
    obtener_arbol().validar_ubicacion(None, categoria_padre_id)
    categoria_creada = repositorio_categorias.crear_categoria(categoria)
    logger.info(
        "Categoría creada: %s (id=%s, superior=%s)",
        categoria_creada.nombre,
        categoria_creada.id,
        categoria_creada.categoria_padre_id,
    )
    return categoria_creada


def obtener_por_id(categoria_id: int) -> Categoria | None:
    """Busca una categoría por id."""
    return repositorio_categorias.obtener_por_id(categoria_id)


def obtener_por_nombre(nombre: str) -> Categoria | None:
    """Busca una categoría por nombre exacto."""
    return repositorio_categorias.obtener_por_nombre(nombre)


def listar_todas() -> list[Categoria]:
    """Devuelve todas las categorías, activas e inactivas (pantalla de gestión)."""
    return repositorio_categorias.listar_todas()


def actualizar_categoria(
    categoria_id: int,
    nombre: str,
    categoria_padre_id: int | None | object = _MISMA_CATEGORIA_SUPERIOR,
) -> Categoria:
    """Actualiza el nombre y, si se indica, la categoría superior (`None` = principal).

    Sin `categoria_padre_id` la categoría queda donde estaba: así ningún llamador la mueve a
    principal por omisión.

    Raises:
        CategoriaNoEncontradaError: si no existe una categoría con ese id.
        DatosInvalidosError: si el nombre está vacío o la nueva ubicación no es válida.
        NombreCategoriaDuplicadoError: si ya existe otra categoría con ese nombre.
    """
    arbol = obtener_arbol()
    categoria_actual = arbol.obtener(categoria_id)
    if categoria_actual is None:
        raise CategoriaNoEncontradaError(f"No existe una categoría con id {categoria_id}.")
    if categoria_padre_id is _MISMA_CATEGORIA_SUPERIOR:
        categoria_padre_id = categoria_actual.categoria_padre_id
    # Reconstruye la entidad con el nombre nuevo para reutilizar la
    # validación de dominio (no vacío) antes de persistir.
    Categoria(nombre=nombre, id=categoria_id, categoria_padre_id=categoria_padre_id)
    if categoria_padre_id != categoria_actual.categoria_padre_id:
        arbol.validar_ubicacion(categoria_id, categoria_padre_id)
    categoria_actualizada = repositorio_categorias.actualizar(categoria_id, nombre, categoria_padre_id)
    logger.info(
        "Categoría actualizada: %s (id=%s, superior=%s)",
        categoria_actualizada.nombre,
        categoria_id,
        categoria_actualizada.categoria_padre_id,
    )
    return categoria_actualizada


def eliminar_categoria(categoria_id: int) -> bool:
    """Da de baja una categoría. Devuelve `True` si la baja fue lógica.

    Si ningún producto ni subcategoría la usa, se elimina físicamente; si no, se desactiva
    (baja lógica) para no romper esa relación (ver
    `db.repositorios.categorias.eliminar_categoria`). No se da de baja una categoría con
    subcategorías activas: quedarían ocultas sin que nadie lo haya pedido.

    Raises:
        CategoriaNoEncontradaError: si no existe una categoría activa con ese id.
        DatosInvalidosError: si tiene subcategorías activas.
    """
    arbol = obtener_arbol()
    activas = [hija.nombre for hija in arbol.subcategorias(categoria_id) if hija.activa]
    if activas:
        raise DatosInvalidosError(
            f"Primero dá de baja o mové sus subcategorías: {', '.join(activas)}."
        )
    baja_logica = repositorio_categorias.eliminar_categoria(categoria_id)
    logger.info("Categoría id=%s dada de baja (baja_logica=%s)", categoria_id, baja_logica)
    return baja_logica


def reactivar_categoria(categoria_id: int) -> Categoria:
    """Reactiva una categoría dada de baja lógica.

    Raises:
        CategoriaNoEncontradaError: si no existe una categoría inactiva con ese id.
        DatosInvalidosError: si su categoría superior sigue dada de baja.
    """
    arbol = obtener_arbol()
    categoria = arbol.obtener(categoria_id)
    if categoria is not None and categoria.categoria_padre_id is not None:
        superior = arbol.obtener(categoria.categoria_padre_id)
        if superior is not None and not superior.activa:
            raise DatosInvalidosError(f"Primero reactivá la categoría '{superior.nombre}'.")
    categoria = repositorio_categorias.reactivar_categoria(categoria_id)
    logger.info("Categoría reactivada: %s (id=%s)", categoria.nombre, categoria.id)
    return categoria
