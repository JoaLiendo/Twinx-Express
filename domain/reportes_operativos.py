"""Filas de los reportes operativos (caja por sesión, compras por proveedor, cuenta corriente, rotación,
pérdidas de stock).

Son proyecciones de solo lectura armadas con consultas agregadas en `db/`: nunca modifican una caja,
una compra ni una cuenta. Los importes son centavos (`int`).
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class VentasDeSesionPorMedio:
    """Ventas activas de una sesión de caja con un mismo medio de pago. `efectivo_centavos` es lo que
    entró en efectivo: el total de EFECTIVO, el contado de las ventas a cuenta mixtas (V1.14-C), 0 si no."""

    tipo_pago: str
    cantidad_ventas: int
    total_centavos: int
    efectivo_centavos: int


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
        return sum(venta.efectivo_centavos for venta in self.ventas_por_medio)

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


ESTADO_SIN_VENTAS = "SIN_VENTAS"
ESTADO_CON_VENTAS = "CON_VENTAS"


@dataclass(frozen=True)
class ProductoRotacion:
    """Producto con stock y su movimiento de ventas en un período (reporte de rotación).

    `unidades_vendidas` cuenta solo ventas activas dentro del período; `fecha_ultima_venta` es la última
    venta activa de toda la historia (`None` si nunca se vendió). `valor_stock_centavos` es
    `stock_actual * costo_unitario_centavos`, la misma valorización del inventario.
    """

    producto_id: int
    codigo_barras: str
    nombre: str
    activo: bool
    stock_actual: int
    costo_unitario_centavos: int
    unidades_vendidas: int
    fecha_ultima_venta: str | None
    dias_desde_ultima_venta: int | None

    @property
    def valor_stock_centavos(self) -> int:
        return self.stock_actual * self.costo_unitario_centavos

    @property
    def estado(self) -> str:
        return ESTADO_CON_VENTAS if self.unidades_vendidas > 0 else ESTADO_SIN_VENTAS


@dataclass(frozen=True)
class FilaPerdida:
    """Un ajuste de pérdida de stock (V1.11-D): `cantidad` son las unidades perdidas (positivo).

    `costo_unitario_centavos` es el snapshot histórico del ajuste; `None` = costo histórico desconocido
    (ajustes anteriores a V1.11-A): esa fila no se valoriza nunca, ni con el costo actual. Si el ajuste
    tiene una reversa (`reversa_id`), quedó neutralizado: se muestra, pero no es una pérdida vigente.
    """

    ajuste_id: int
    fecha: str
    producto_id: int
    codigo_barras: str
    producto_nombre: str
    motivo: str
    cantidad: int
    costo_unitario_centavos: int | None
    usuario_nombre_completo: str | None
    observaciones: str | None
    reversa_id: int | None = None
    reversa_fecha: str | None = None
    reversa_usuario_nombre_completo: str | None = None

    @property
    def revertida(self) -> bool:
        return self.reversa_id is not None

    @property
    def costo_conocido(self) -> bool:
        return self.costo_unitario_centavos is not None

    @property
    def perdida_centavos(self) -> int | None:
        return None if self.costo_unitario_centavos is None else self.cantidad * self.costo_unitario_centavos


@dataclass(frozen=True)
class ResumenPerdidas:
    """Totales de pérdidas VIGENTES (sin las revertidas). `valor_conocido_centavos` suma solo filas con
    costo histórico conocido; las de costo desconocido se cuentan aparte y nunca se valorizan. `motivo`
    es `None` en el total general."""

    motivo: str | None
    filas_vigentes: int
    cantidad: int
    valor_conocido_centavos: int
    filas_costo_desconocido: int
    cantidad_costo_desconocido: int
    filas_revertidas: int


def resumir_perdidas(filas: list[FilaPerdida], motivo: str | None = None) -> ResumenPerdidas:
    vigentes = [fila for fila in filas if not fila.revertida]
    desconocidas = [fila for fila in vigentes if not fila.costo_conocido]
    return ResumenPerdidas(
        motivo=motivo,
        filas_vigentes=len(vigentes),
        cantidad=sum(fila.cantidad for fila in vigentes),
        valor_conocido_centavos=sum(fila.perdida_centavos for fila in vigentes if fila.costo_conocido),
        filas_costo_desconocido=len(desconocidas),
        cantidad_costo_desconocido=sum(fila.cantidad for fila in desconocidas),
        filas_revertidas=len(filas) - len(vigentes),
    )


def resumir_perdidas_por_motivo(filas: list[FilaPerdida]) -> list[ResumenPerdidas]:
    """Un resumen por motivo presente, de mayor a menor cantidad perdida vigente."""
    motivos = sorted({fila.motivo for fila in filas})
    resumenes = [resumir_perdidas([f for f in filas if f.motivo == motivo], motivo) for motivo in motivos]
    return sorted(resumenes, key=lambda resumen: (-resumen.cantidad, resumen.motivo))


@dataclass(frozen=True)
class ReportePerdidas:
    fecha_desde: str
    fecha_hasta: str
    producto_id: int | None
    motivo: str | None
    filas: list[FilaPerdida]
    total: ResumenPerdidas
    por_motivo: list[ResumenPerdidas]
