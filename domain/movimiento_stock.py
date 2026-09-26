"""Movimientos de stock de un producto (kardex, V1.7-C).

Son proyecciones de solo lectura armadas con los eventos reales que ya cambian el stock (ventas,
anulaciones de venta y de compra, compras y ajustes de stock, incluidos los recuentos de inventario
físico): no existe una tabla de movimientos. Un movimiento anulado NO desaparece: se muestra la
operación original y, aparte, su reversión en la fecha de anulación.
"""

from dataclasses import dataclass, field

TIPO_VENTA = "VENTA"
TIPO_ANULACION_VENTA = "ANULACION_VENTA"
TIPO_COMPRA = "COMPRA"
TIPO_ANULACION_COMPRA = "ANULACION_COMPRA"
TIPO_AJUSTE = "AJUSTE"
TIPO_RECUENTO = "RECUENTO"


@dataclass(frozen=True)
class MovimientoStock:
    """Un cambio de stock. `cantidad` lleva signo (entrada positiva, salida negativa) y `saldo` es el stock
    reconstruido después del movimiento. `saldo_verificado` solo aplica a los ajustes, que guardan su propio
    `stock_resultante`: `True` si el saldo reconstruido lo confirma, `False` si no coincide, `None` si el
    movimiento no guarda un saldo con el cual contrastar."""

    fecha: str
    tipo: str
    referencia: str
    cantidad: int
    saldo: int
    descripcion: str
    saldo_verificado: bool | None = None

    @property
    def entrada(self) -> int:
        return max(self.cantidad, 0)

    @property
    def salida(self) -> int:
        return max(-self.cantidad, 0)


@dataclass(frozen=True)
class Kardex:
    """Movimientos de un producto en un período, con sus saldos.

    `saldo_inicial` es siempre RECONSTRUIDO (no es un dato persistido): `stock_actual` menos todos los
    movimientos desde el inicio del período. Incluye el stock con el que se dio de alta el producto, que el
    sistema no registra como movimiento. `saldo_final` es el saldo al terminar el período; sin filtros
    coincide con `stock_actual`.
    """

    producto_id: int
    codigo_barras: str
    nombre: str
    activo: bool
    stock_actual: int
    fecha_desde: str | None
    fecha_hasta: str | None
    saldo_inicial: int
    saldo_final: int
    movimientos: list[MovimientoStock] = field(default_factory=list)

    @property
    def total_entradas(self) -> int:
        return sum(movimiento.entrada for movimiento in self.movimientos)

    @property
    def total_salidas(self) -> int:
        return sum(movimiento.salida for movimiento in self.movimientos)
