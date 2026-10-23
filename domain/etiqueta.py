"""Entidad Etiqueta de producto (V1.11-E).

Marca libre e independiente de las categorías ("Frío", "Sin TACC", "Oferta"): un producto puede
tener varias. Es plana a propósito (no forma un árbol) y no dispara ninguna automatización.
"""

from dataclasses import dataclass

from excepciones import DatosInvalidosError

LONGITUD_MAXIMA_NOMBRE = 40


@dataclass
class Etiqueta:
    """Una etiqueta, con el nombre ya normalizado (espacios de más quitados)."""

    nombre: str
    id: int | None = None
    fecha_creacion: str | None = None

    def __post_init__(self) -> None:
        self.nombre = " ".join((self.nombre or "").split())
        if not self.nombre:
            raise DatosInvalidosError("El nombre de la etiqueta no puede estar vacío.")
        if len(self.nombre) > LONGITUD_MAXIMA_NOMBRE:
            raise DatosInvalidosError(f"El nombre de la etiqueta admite hasta {LONGITUD_MAXIMA_NOMBRE} caracteres.")
