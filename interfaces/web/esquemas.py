"""Esquemas Pydantic de la API JSON que usa el POS (ver `rutas/ventas.py`).

Son la forma de los datos que cruzan la frontera HTTP, no entidades de
dominio: la validación de negocio real vive en `domain/` y corre recién
cuando estos datos se pasan a `services/`.
"""

from pydantic import BaseModel, Field


class ItemVentaEntrada(BaseModel):
    producto_id: int
    cantidad: int = Field(gt=0)


class VentaEntrada(BaseModel):
    items: list[ItemVentaEntrada]
    tipo_pago: str
    # Fase 5A: obligatoria en la API web -- sin ella no hay protección
    # posible contra ventas duplicadas por reintento/doble envío (ver
    # services.servicio_ventas.registrar_venta). No se exige formato
    # UUID estricto: el contrato es "una cadena que el cliente controla
    # y reutiliza igual en cualquier reintento del mismo intento de cobro".
    clave_idempotencia: str = Field(min_length=1, max_length=100)


class VentaSalida(BaseModel):
    id: int
    fecha: str
    total_centavos: int
    tipo_pago: str
