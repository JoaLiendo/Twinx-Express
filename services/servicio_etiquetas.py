"""Casos de uso de etiquetas de productos (V1.11-E).

CRUD simple: las etiquetas son marcas libres sin reglas de negocio. Su asignación a un producto
ocurre dentro del alta/edición del producto (ver `services.servicio_stock`), en la misma
transacción y auditada como parte de esa edición.
"""

import logging
from collections.abc import Iterable

from db.repositorios import etiquetas as repositorio_etiquetas
from domain.etiqueta import Etiqueta
from excepciones import DatosInvalidosError

logger = logging.getLogger(__name__)


def crear_etiqueta(nombre: str) -> Etiqueta:
    """Raises: DatosInvalidosError, NombreEtiquetaDuplicadoError."""
    etiqueta = repositorio_etiquetas.crear(Etiqueta(nombre=nombre))
    logger.info("Etiqueta creada: %s (id=%s)", etiqueta.nombre, etiqueta.id)
    return etiqueta


def renombrar_etiqueta(etiqueta_id: int, nombre: str) -> Etiqueta:
    """Raises: DatosInvalidosError, NombreEtiquetaDuplicadoError, EtiquetaNoEncontradaError."""
    etiqueta = repositorio_etiquetas.renombrar(etiqueta_id, Etiqueta(nombre=nombre).nombre)
    logger.info("Etiqueta renombrada: %s (id=%s)", etiqueta.nombre, etiqueta.id)
    return etiqueta


def eliminar_etiqueta(etiqueta_id: int) -> None:
    """Borra la etiqueta y la quita de todos sus productos. Raises: EtiquetaNoEncontradaError."""
    repositorio_etiquetas.eliminar(etiqueta_id)
    logger.info("Etiqueta id=%s eliminada", etiqueta_id)


def listar() -> list[Etiqueta]:
    return repositorio_etiquetas.listar()


def contar_productos_por_etiqueta() -> dict[int, int]:
    return repositorio_etiquetas.contar_productos_por_etiqueta()


def ids_por_producto() -> dict[int, list[int]]:
    return repositorio_etiquetas.ids_por_producto()


def ids_de_producto(producto_id: int) -> list[int]:
    return repositorio_etiquetas.ids_de_producto(producto_id)


def validar_ids(etiqueta_ids: Iterable[int]) -> frozenset[int]:
    """Las etiquetas elegidas en un formulario deben existir (evita que la FK falle con un error
    técnico). Raises: DatosInvalidosError."""
    elegidas = frozenset(etiqueta_ids)
    existentes = {etiqueta.id for etiqueta in repositorio_etiquetas.listar()}
    if not elegidas <= existentes:
        raise DatosInvalidosError("Una de las etiquetas elegidas ya no existe.")
    return elegidas
