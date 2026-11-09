"""Entidad y reglas de los pedidos de clientes (migración 051).

Un pedido registra lo que un cliente encarga; no reserva ni descuenta stock, no cobra ni factura. El
único camino para concretarlo es el punto de venta, que valida stock y precios de nuevo en ese momento
(`services.servicio_ventas.registrar_venta` con `pedido_id`).
"""

from dataclasses import dataclass
from datetime import date

from domain.fechas import fecha_de_formulario
from excepciones import DatosInvalidosError

ESTADO_PENDIENTE = "PENDIENTE"
ESTADO_CONVERTIDO = "CONVERTIDO"
ESTADO_CANCELADO = "CANCELADO"
ESTADOS_PEDIDO: tuple[tuple[str, str], ...] = (
    (ESTADO_PENDIENTE, "Pendiente"),
    (ESTADO_CONVERTIDO, "Convertido en venta"),
    (ESTADO_CANCELADO, "Cancelado"),
)
ETIQUETAS_ESTADO_PEDIDO = dict(ESTADOS_PEDIDO)

LARGO_MAXIMO_CONTACTO = 120
LARGO_MAXIMO_TELEFONO = 30
LARGO_MAXIMO_NOTAS = 1000
LARGO_MAXIMO_MOTIVO = 300
MAXIMO_LINEAS = 100
MAXIMO_CANTIDAD = 100_000


@dataclass(frozen=True)
class ItemPedido:
    """Una línea pedida: producto y cantidad (el precio lo congela el servicio al tomar el pedido)."""

    producto_id: int
    cantidad: int

    def __post_init__(self) -> None:
        if self.cantidad <= 0:
            raise DatosInvalidosError("La cantidad de cada línea del pedido debe ser mayor que cero.")
        if self.cantidad > MAXIMO_CANTIDAD:
            raise DatosInvalidosError("La cantidad pedida es demasiado grande.")


@dataclass(frozen=True)
class LineaPedido:
    """Una línea persistida, con el producto resuelto y su precio vigente hoy (para ver si cambió)."""

    id: int
    producto_id: int
    producto_nombre: str
    producto_activo: bool
    cantidad: int
    precio_unitario_centavos: int
    precio_vigente_centavos: int
    stock_actual: int

    @property
    def subtotal_al_pedir_centavos(self) -> int:
        return self.cantidad * self.precio_unitario_centavos

    @property
    def subtotal_vigente_centavos(self) -> int:
        return self.cantidad * self.precio_vigente_centavos

    @property
    def precio_cambio(self) -> bool:
        return self.precio_vigente_centavos != self.precio_unitario_centavos

    @property
    def sin_stock_suficiente(self) -> bool:
        return self.stock_actual < self.cantidad


@dataclass(frozen=True)
class Pedido:
    id: int
    fecha_creacion: str
    usuario_id: int
    usuario_nombre: str | None
    cliente_id: int | None
    cliente_nombre: str | None
    contacto_nombre: str | None
    contacto_telefono: str | None
    notas: str | None
    fecha_entrega: str | None
    estado: str
    venta_id: int | None
    convertido_en: str | None
    cancelado_en: str | None
    cancelado_por_usuario_id: int | None
    cancelado_por_nombre: str | None
    motivo_cancelacion: str | None
    cantidad_lineas: int = 0
    total_al_pedir_centavos: int = 0

    @property
    def pendiente(self) -> bool:
        return self.estado == ESTADO_PENDIENTE

    @property
    def estado_etiqueta(self) -> str:
        return ETIQUETAS_ESTADO_PEDIDO.get(self.estado, self.estado)

    @property
    def para_quien(self) -> str:
        return self.cliente_nombre or self.contacto_nombre or "—"


def _texto_acotado(texto: str | None, largo_maximo: int, nombre: str) -> str | None:
    limpio = " ".join((texto or "").split())
    if not limpio:
        return None
    if len(limpio) > largo_maximo:
        raise DatosInvalidosError(f"{nombre} no puede superar los {largo_maximo} caracteres.")
    return limpio


@dataclass(frozen=True)
class DatosPedido:
    cliente_id: int | None
    contacto_nombre: str | None
    contacto_telefono: str | None
    notas: str | None
    fecha_entrega: str | None
    items: tuple[ItemPedido, ...]


def validar_datos_pedido(
    *,
    cliente_id: int | None,
    contacto_nombre: str | None,
    contacto_telefono: str | None,
    notas: str | None,
    fecha_entrega: str | None,
    items: list[ItemPedido],
    hoy: date | None = None,
) -> DatosPedido:
    """Valida y normaliza un pedido nuevo: hay que saber para quién es (cliente o contacto), qué se pide
    (al menos una línea, sin productos repetidos) y, si se indica, para cuándo (nunca una fecha pasada).

    Raises:
        DatosInvalidosError: ante cualquier dato inválido, con un mensaje para el usuario.
    """
    contacto = _texto_acotado(contacto_nombre, LARGO_MAXIMO_CONTACTO, "El nombre de contacto")
    if cliente_id is None and contacto is None:
        raise DatosInvalidosError("Indicá para quién es el pedido: elegí un cliente o escribí un nombre de contacto.")
    if not items:
        raise DatosInvalidosError("Un pedido debe tener al menos un producto.")
    if len(items) > MAXIMO_LINEAS:
        raise DatosInvalidosError(f"Un pedido admite hasta {MAXIMO_LINEAS} líneas.")
    productos = [item.producto_id for item in items]
    if len(set(productos)) != len(productos):
        raise DatosInvalidosError("Un producto aparece más de una vez en el pedido: unificá sus cantidades.")
    entrega = (fecha_entrega or "").strip() or None
    if entrega is not None:
        valor = fecha_de_formulario(entrega, "La fecha de entrega no es válida.")
        if valor < (hoy or date.today()):
            raise DatosInvalidosError("La fecha de entrega no puede ser anterior a hoy.")
        entrega = valor.isoformat()
    return DatosPedido(
        cliente_id=cliente_id,
        contacto_nombre=contacto,
        contacto_telefono=_texto_acotado(contacto_telefono, LARGO_MAXIMO_TELEFONO, "El teléfono de contacto"),
        notas=_texto_acotado(notas, LARGO_MAXIMO_NOTAS, "Las notas"),
        fecha_entrega=entrega,
        items=tuple(items),
    )


def validar_motivo_cancelacion(motivo: str | None) -> str:
    limpio = _texto_acotado(motivo, LARGO_MAXIMO_MOTIVO, "El motivo")
    if limpio is None:
        raise DatosInvalidosError("Indicá el motivo de la cancelación del pedido.")
    return limpio
