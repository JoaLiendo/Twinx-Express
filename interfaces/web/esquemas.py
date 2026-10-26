"""Esquemas Pydantic de la API JSON que usa el POS (ver `rutas/ventas.py`).

Son la forma de los datos que cruzan la frontera HTTP, no entidades de
dominio: la validación de negocio real vive en `domain/` y corre recién
cuando estos datos se pasan a `services/`.
"""

from pydantic import BaseModel, Field, StrictInt


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
    # 021: cliente de una venta a cuenta. Solo lo manda el POS con CUENTA_CORRIENTE; el servicio valida
    # que exista, esté activo y que una venta a cuenta lo tenga.
    cliente_id: int | None = None
    # V1.12-D: autorización para vender mercadería vencida. Un OWNER logueado la confirma con
    # `autorizar_vencidos`; en el puesto de un CASHIER hacen falta usuario y contraseña de un OWNER.
    autorizar_vencidos: bool = False
    autorizacion_usuario: str | None = Field(default=None, max_length=100)
    autorizacion_password: str | None = Field(default=None, max_length=200)
    # V1.14-C: efectivo cobrado en el momento en una venta a cuenta mixta (el servicio valida el rango) y
    # quién compra a nombre de una empresa: un autorizado vigente o una autorización excepcional.
    pago_contado_centavos: StrictInt = 0
    comprador_autorizado_id: int | None = None
    comprador_excepcional_nombre: str | None = Field(default=None, max_length=120)
    comprador_excepcional_metodo: str | None = Field(default=None, max_length=20)
    comprador_excepcional_detalle: str | None = Field(default=None, max_length=250)

    @property
    def indica_comprador(self) -> bool:
        return self.comprador_autorizado_id is not None or any(
            (self.comprador_excepcional_nombre, self.comprador_excepcional_metodo, self.comprador_excepcional_detalle)
        )


class VentaSalida(BaseModel):
    id: int
    fecha: str
    total_centavos: int
    tipo_pago: str
    pago_contado_centavos: int = 0
    credito_centavos: int = 0


class ConteoEntrada(BaseModel):
    """Conteo de un producto del inventario físico (V1.4). `StrictInt`: solo un entero JSON, sin
    coerción de texto, decimales ni booleanos; el servicio valida que no sea negativo."""

    cantidad: StrictInt


class ConteoSalida(BaseModel):
    """Lo único que vuelve al contador: el producto y lo que contó. Nunca el stock esperado ni la
    diferencia (conteo a ciegas)."""

    producto_id: int
    cantidad_contada: int
