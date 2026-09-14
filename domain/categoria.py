"""Entidad Categoría de producto (Fase 3C).

Agrupa productos para organizar/filtrar el catálogo. Es puramente
organizativa: no tiene ninguna relación con precios, stock ni ventas.
"""

from dataclasses import dataclass

from excepciones import DatosInvalidosError


@dataclass
class Categoria:
    """Una categoría de productos, validada al construirse."""

    nombre: str
    id: int | None = None
    activa: bool = True
    fecha_creacion: str | None = None

    def __post_init__(self) -> None:
        if not self.nombre or not self.nombre.strip():
            raise DatosInvalidosError("El nombre de la categoría no puede estar vacío.")
