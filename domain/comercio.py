"""Datos comerciales que se imprimen en el ticket (V1.2).

Solo texto libre y corto que el dueño configura: nombre del comercio,
dirección, teléfono y una leyenda de pie. No hay ningún dato fiscal (CUIT,
condición frente al IVA, número de comprobante): el ticket sigue siendo un
comprobante interno, no una factura.
"""

from dataclasses import dataclass

from excepciones import DatosInvalidosError

NOMBRE_POR_DEFECTO = "Kiosco"

LIMITES = {"nombre": 60, "direccion": 100, "telefono": 30, "pie": 120}
CLAVES_CONFIGURACION = {
    "nombre": "comercio_nombre",
    "direccion": "comercio_direccion",
    "telefono": "comercio_telefono",
    "pie": "ticket_pie",
}
ETIQUETAS = {"nombre": "nombre", "direccion": "dirección", "telefono": "teléfono", "pie": "leyenda de pie"}


def _limpiar(campo: str, texto: str | None) -> str:
    """Una sola línea, sin espacios sobrantes ni caracteres de control, con el
    largo máximo del campo."""
    limpio = " ".join(("".join(c for c in (texto or "") if c.isprintable() or c.isspace())).split())
    if len(limpio) > LIMITES[campo]:
        raise DatosInvalidosError(
            f"El campo {ETIQUETAS[campo]} admite hasta {LIMITES[campo]} caracteres (tiene {len(limpio)})."
        )
    return limpio


@dataclass(frozen=True)
class DatosComercio:
    """Datos del comercio para el ticket. Un campo vacío no se imprime; el
    nombre vacío se reemplaza por `NOMBRE_POR_DEFECTO`."""

    nombre: str = NOMBRE_POR_DEFECTO
    direccion: str = ""
    telefono: str = ""
    pie: str = ""

    def __post_init__(self) -> None:
        for campo in LIMITES:
            object.__setattr__(self, campo, _limpiar(campo, getattr(self, campo)))
        if not self.nombre:
            object.__setattr__(self, "nombre", NOMBRE_POR_DEFECTO)
