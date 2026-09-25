"""Filas de los reportes operativos (caja por sesión, compras por proveedor, cuenta corriente).

Son proyecciones de solo lectura armadas con consultas agregadas en `db/`: nunca modifican una caja,
una compra ni una cuenta. Los importes son centavos (`int`).
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class VentasDeSesionPorMedio:
    """Ventas activas de una sesión de caja con un mismo medio de pago."""

    tipo_pago: str
    cantidad_ventas: int
    total_centavos: int


@dataclass(frozen=True)
class ResumenSesionCaja:
    """Una sesión de caja con lo que entró y salió de ella.

    `apertura_centavos`, `ingresos_centavos` (manuales), `cobranzas_centavos` (cobros de cuenta
    corriente, que también son ingresos de caja) y `egresos_centavos` salen de los movimientos de la
    sesión; `contado_centavos` y `diferencia_centavos` son `None` mientras está abierta. El efectivo
    esperado usa la misma fórmula que el arqueo y el cierre (`services.servicio_caja`): apertura +
    ingresos + cobranzas + ventas en efectivo - egresos.
    """

    sesion_id: int
    estado: str
    origen: str
    fecha_apertura: str
    fecha_cierre: str | None
    usuario_apertura: str | None
    usuario_cierre: str | None
    fondo_centavos: int | None
    apertura_centavos: int
    ingresos_centavos: int
    cobranzas_centavos: int
    egresos_centavos: int
    contado_centavos: int | None
    diferencia_centavos: int | None
    ventas_por_medio: list[VentasDeSesionPorMedio] = field(default_factory=list)

    @property
    def total_ventas_centavos(self) -> int:
        return sum(venta.total_centavos for venta in self.ventas_por_medio)

    @property
    def efectivo_ventas_centavos(self) -> int:
        return sum(venta.total_centavos for venta in self.ventas_por_medio if venta.tipo_pago == "EFECTIVO")

    @property
    def efectivo_esperado_centavos(self) -> int:
        return (
            self.apertura_centavos
            + self.ingresos_centavos
            + self.cobranzas_centavos
            + self.efectivo_ventas_centavos
            - self.egresos_centavos
        )


@dataclass(frozen=True)
class ComprasDeProveedor:
    """Compras de un proveedor en un período."""

    proveedor_id: int
    proveedor_nombre: str
    cantidad_compras: int
    unidades: int
    total_centavos: int


@dataclass(frozen=True)
class DeudorCuenta:
    """Cliente con saldo positivo en su cuenta corriente."""

    cliente_id: int
    cliente_nombre: str
    activo: bool
    saldo_centavos: int


@dataclass(frozen=True)
class CobranzaDeCliente:
    """Cobros (`COBRO`) de un cliente en un período."""

    cliente_id: int
    cliente_nombre: str
    cantidad_cobros: int
    total_centavos: int
