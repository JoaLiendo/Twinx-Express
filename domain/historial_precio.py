"""Entidad del historial de cambios de precio de un producto (V1.2).

Cada `CambioPrecio` es un hecho ya ocurrido: el precio de venta o de costo
de un producto pasó de un valor a otro, quién lo hizo y por qué camino.
Solo se registran cambios reales (valor nuevo distinto del anterior).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CambioPrecio:
    """Un cambio de precio registrado, con el nombre del usuario ya resuelto
    (`None` si el cambio no tuvo usuario autenticado)."""

    id: int
    producto_id: int
    fecha: str
    campo: str
    precio_anterior_centavos: int
    precio_nuevo_centavos: int
    origen: str
    usuario_nombre_completo: str | None = None

    @property
    def diferencia_centavos(self) -> int:
        return self.precio_nuevo_centavos - self.precio_anterior_centavos
