"""Casos de uso del módulo de Reportes: agrega datos ya persistidos de
`ventas`/`detalle_venta` sobre un rango de fechas, sin recalcular ni
inferir nada que la base no almacene.

Reportes V2 agrega rentabilidad (margen bruto/porcentual, costo y margen
por producto) y ventas por vendedor, apoyándose en
`detalle_venta.costo_unitario_centavos` y `ventas.usuario_id`. Ninguno de
los dos campos existe para ventas anteriores a esa migración: quedan
`NULL`, y este módulo nunca los trata como `0` -- se excluyen de los
cálculos de costo/margen/vendedor, pero siguen contando en las métricas
generales del reporte (`cantidad_ventas`/`total_facturado_centavos`),
que no dependen de ninguno de los dos campos.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from db.repositorios import caja as repositorio_caja
from db.repositorios import clientes as repositorio_clientes
from db.repositorios import compras as repositorio_compras
from db.repositorios import ventas as repositorio_ventas
from domain.reportes_operativos import CobranzaDeCliente, ComprasDeProveedor, DeudorCuenta, ResumenSesionCaja
from domain.venta import ProductoMasVendido, ResumenVenta, Venta
from excepciones import DatosInvalidosError

LIMITE_PRODUCTOS_MAS_VENDIDOS = 10

# Ventana por defecto cuando no se pide un rango explícito: últimos 7
# días (incluye hoy). Un vistazo rápido de la operación reciente, no
# todo el historial -- quien necesite otro período lo elige con el filtro.
DIAS_RANGO_POR_DEFECTO = 7


@dataclass
class VentasPorDia:
    """Ventas de un día puntual dentro del período del reporte."""

    fecha: str
    cantidad_ventas: int
    total_centavos: int


@dataclass
class VentasPorMedioPago:
    """Ventas de un `tipo_pago` puntual dentro del período del reporte."""

    tipo_pago: str
    cantidad_ventas: int
    total_centavos: int


@dataclass
class ResumenRentabilidad:
    """Margen bruto del período (Reportes V2), calculado solo sobre las
    líneas de venta con `costo_unitario_centavos` conocido.

    `margen_porcentual` es el margen bruto **sobre el precio de venta**
    (`margen_bruto_centavos / venta_total_con_costo_conocido_centavos * 100`,
    "de cada $100 vendidos, cuántos son ganancia") -- deliberadamente
    NO es el markup sobre costo (`margen_bruto / costo_total`, "cuánto
    más caro vendo que lo que me costó"), que es una pregunta distinta.
    Es el único `float` de este módulo, y a propósito: es un cociente de
    exposición que se calcula una única vez a partir de enteros ya
    exactos, nunca se acumula ni se vuelve a usar en otro cálculo -- no
    es la clase de operación que la regla "nunca floats para dinero"
    busca evitar (esa regla protege valores monetarios acumulados/
    persistidos, no un cociente de display).

    `None` (no `0.0`) cuando no hay ninguna facturación con costo
    conocido en el período: "sin datos" y "margen de 0%" son cosas
    distintas, y confundirlas sería mostrar una cifra falsa.

    `cantidad_ventas_sin_costo_historico` cuenta ventas completas
    excluidas de este cálculo (ventas anteriores a que este campo
    existiera, o registradas sin costo conocido) -- se expone para que
    el reporte pueda avisar de forma transparente que el margen no
    cubre el 100% de `ReporteVentas.cantidad_ventas`.
    """

    costo_total_centavos: int
    margen_bruto_centavos: int
    margen_porcentual: float | None
    cantidad_ventas_sin_costo_historico: int


@dataclass
class VentasPorUsuario:
    """Ventas de un vendedor puntual dentro del período (Reportes V2).

    Incluye usuarios desactivados (`usuario_activo=False`): un usuario
    nunca se borra físicamente, y su historial de ventas ya ocurrido no
    depende de si sigue activo hoy (ver `db.repositorios.usuarios`).
    """

    usuario_id: int
    nombre_completo: str
    usuario_activo: bool
    cantidad_ventas: int
    total_vendido_centavos: int


@dataclass
class AnulacionesPorMotivo:
    """Anulaciones de un `motivo_anulacion` puntual dentro del período
    del reporte (Visibilidad de Anulaciones)."""

    motivo: str
    cantidad: int
    monto_centavos: int


@dataclass
class ResumenAnulaciones:
    """Resumen de ventas `ANULADA` del período (Visibilidad de
    Anulaciones): cuántas se anularon, por cuánto y por qué motivo.

    Es información puramente de auditoría/trazabilidad, separada de los
    KPIs operativos del resto de `ReporteVentas` -- `cantidad`/
    `monto_total_centavos` nunca suman ni restan sobre `cantidad_ventas`/
    `total_facturado_centavos` (esos dos siguen calculándose solo sobre
    ventas `ACTIVA`, sin ningún cambio). `monto_total_centavos` es la
    suma de `total_centavos` original de cada venta anulada -- ese
    campo no se toca al anular (ver `services.servicio_ventas.anular_venta`),
    así que siempre representa el monto real de la venta que se
    deshizo, nunca un valor recalculado.
    """

    cantidad: int
    monto_total_centavos: int
    por_motivo: list[AnulacionesPorMotivo] = field(default_factory=list)


@dataclass
class ReporteVentas:
    """Resumen de ventas para un período `[fecha_desde, fecha_hasta]`
    (ambos límites inclusive, texto "YYYY-MM-DD").

    `ticket_promedio_centavos` es `total_facturado_centavos // cantidad_ventas`
    (0 si no hubo ventas): aritmética entera, igual criterio que el
    resto del proyecto para montos en centavos (ver `domain.dinero`).

    `rentabilidad`/`ventas_por_usuario` (Reportes V2) pueden cubrir menos
    ventas que `cantidad_ventas`: una venta sin costo histórico o sin
    usuario asociado sigue contando acá, pero queda fuera de esos dos
    cálculos (ver `ResumenRentabilidad`/`VentasPorUsuario`).
    """

    fecha_desde: str
    fecha_hasta: str
    cantidad_ventas: int
    total_facturado_centavos: int
    ticket_promedio_centavos: int
    rentabilidad: ResumenRentabilidad
    resumen_anulaciones: ResumenAnulaciones
    ventas_por_medio_pago: list[VentasPorMedioPago] = field(default_factory=list)
    evolucion_por_dia: list[VentasPorDia] = field(default_factory=list)
    productos_mas_vendidos: list[ProductoMasVendido] = field(default_factory=list)
    ventas_por_usuario: list[VentasPorUsuario] = field(default_factory=list)


def _rango_por_defecto() -> tuple[str, str]:
    hoy = date.today()
    desde = hoy - timedelta(days=DIAS_RANGO_POR_DEFECTO - 1)
    return desde.isoformat(), hoy.isoformat()


def _agrupar_por_medio_pago(ventas: list[Venta]) -> list[VentasPorMedioPago]:
    cantidad_por_tipo: dict[str, int] = {}
    total_por_tipo: dict[str, int] = {}
    for venta in ventas:
        cantidad_por_tipo[venta.tipo_pago] = cantidad_por_tipo.get(venta.tipo_pago, 0) + 1
        total_por_tipo[venta.tipo_pago] = total_por_tipo.get(venta.tipo_pago, 0) + venta.total_centavos

    return [
        VentasPorMedioPago(
            tipo_pago=tipo_pago,
            cantidad_ventas=cantidad_por_tipo[tipo_pago],
            total_centavos=total_por_tipo[tipo_pago],
        )
        for tipo_pago in sorted(total_por_tipo, key=lambda t: total_por_tipo[t], reverse=True)
    ]


def _agrupar_por_motivo_anulacion(anuladas: list[ResumenVenta]) -> list[AnulacionesPorMotivo]:
    """Agrupa ventas `ANULADA` por `motivo_anulacion`, mayor a menor
    monto -- mismo criterio que `_agrupar_por_medio_pago`. `motivo` nunca
    es `None` acá: toda venta con `estado == 'ANULADA'` tiene un motivo
    persistido (ver `services.servicio_ventas.anular_venta`)."""
    cantidad_por_motivo: dict[str, int] = {}
    monto_por_motivo: dict[str, int] = {}
    for venta in anuladas:
        motivo = venta.motivo_anulacion
        cantidad_por_motivo[motivo] = cantidad_por_motivo.get(motivo, 0) + 1
        monto_por_motivo[motivo] = monto_por_motivo.get(motivo, 0) + venta.total_centavos

    return [
        AnulacionesPorMotivo(
            motivo=motivo,
            cantidad=cantidad_por_motivo[motivo],
            monto_centavos=monto_por_motivo[motivo],
        )
        for motivo in sorted(monto_por_motivo, key=lambda m: monto_por_motivo[m], reverse=True)
    ]


def _agrupar_por_dia(ventas: list[Venta]) -> list[VentasPorDia]:
    """Agrupa por el componente de fecha de `Venta.fecha` (los primeros
    10 caracteres de "YYYY-MM-DD HH:MM:SS", mismo formato que graba
    SQLite vía `datetime('now', 'localtime')`)."""
    cantidad_por_dia: dict[str, int] = {}
    total_por_dia: dict[str, int] = {}
    for venta in ventas:
        clave_fecha = venta.fecha[:10]
        cantidad_por_dia[clave_fecha] = cantidad_por_dia.get(clave_fecha, 0) + 1
        total_por_dia[clave_fecha] = total_por_dia.get(clave_fecha, 0) + venta.total_centavos

    return [
        VentasPorDia(fecha=fecha, cantidad_ventas=cantidad_por_dia[fecha], total_centavos=total_por_dia[fecha])
        for fecha in sorted(total_por_dia)
    ]


def generar_reporte_ventas(fecha_desde: str | None = None, fecha_hasta: str | None = None) -> ReporteVentas:
    """Arma el reporte de ventas del período pedido.

    Si no se pasa alguno de los dos límites, usa el rango por defecto
    (últimos `DIAS_RANGO_POR_DEFECTO` días) para ambos -- un rango a
    medio especificar (solo desde, o solo hasta) sería ambiguo, así que
    se trata igual que "no se pidió ningún rango" en vez de adivinar el
    límite que falta.
    """
    if fecha_desde is None or fecha_hasta is None:
        fecha_desde, fecha_hasta = _rango_por_defecto()

    ventas_del_periodo = repositorio_ventas.listar_en_rango(fecha_desde, fecha_hasta)
    productos_mas_vendidos = repositorio_ventas.listar_productos_mas_vendidos_en_rango(
        fecha_desde, fecha_hasta, limite=LIMITE_PRODUCTOS_MAS_VENDIDOS
    )

    cantidad_ventas = len(ventas_del_periodo)
    total_facturado_centavos = sum(venta.total_centavos for venta in ventas_del_periodo)
    ticket_promedio_centavos = total_facturado_centavos // cantidad_ventas if cantidad_ventas else 0

    return ReporteVentas(
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        cantidad_ventas=cantidad_ventas,
        total_facturado_centavos=total_facturado_centavos,
        ticket_promedio_centavos=ticket_promedio_centavos,
        rentabilidad=_calcular_rentabilidad(fecha_desde, fecha_hasta),
        resumen_anulaciones=_calcular_resumen_anulaciones(fecha_desde, fecha_hasta),
        ventas_por_medio_pago=_agrupar_por_medio_pago(ventas_del_periodo),
        evolucion_por_dia=_agrupar_por_dia(ventas_del_periodo),
        productos_mas_vendidos=productos_mas_vendidos,
        ventas_por_usuario=_listar_ventas_por_usuario(fecha_desde, fecha_hasta),
    )


def _calcular_rentabilidad(fecha_desde: str, fecha_hasta: str) -> ResumenRentabilidad:
    costo_total_centavos, venta_total_con_costo_centavos, cantidad_ventas_sin_costo_historico = (
        repositorio_ventas.calcular_rentabilidad_en_rango(fecha_desde, fecha_hasta)
    )
    margen_bruto_centavos = venta_total_con_costo_centavos - costo_total_centavos
    margen_porcentual = (
        margen_bruto_centavos / venta_total_con_costo_centavos * 100
        if venta_total_con_costo_centavos > 0
        else None
    )
    return ResumenRentabilidad(
        costo_total_centavos=costo_total_centavos,
        margen_bruto_centavos=margen_bruto_centavos,
        margen_porcentual=margen_porcentual,
        cantidad_ventas_sin_costo_historico=cantidad_ventas_sin_costo_historico,
    )


def _calcular_resumen_anulaciones(fecha_desde: str, fecha_hasta: str) -> ResumenAnulaciones:
    """Resumen de ventas `ANULADA` del período (Visibilidad de
    Anulaciones).

    Usa `repositorio_ventas.listar_resumen(..., estado="ANULADA")` --
    deliberadamente NO `listar_en_rango`/`calcular_rentabilidad_en_rango`/
    `listar_productos_mas_vendidos_en_rango`/`listar_ventas_por_usuario_en_rango`,
    que filtran `estado = 'ACTIVA'` a propósito y cuya semántica no debe
    tocarse: este resumen es información aparte, no un ajuste de esos
    cuatro cálculos.
    """
    anuladas = repositorio_ventas.listar_resumen(fecha_desde, fecha_hasta, estado="ANULADA")
    return ResumenAnulaciones(
        cantidad=len(anuladas),
        monto_total_centavos=sum(venta.total_centavos for venta in anuladas),
        por_motivo=_agrupar_por_motivo_anulacion(anuladas),
    )


def _listar_ventas_por_usuario(fecha_desde: str, fecha_hasta: str) -> list[VentasPorUsuario]:
    filas = repositorio_ventas.listar_ventas_por_usuario_en_rango(fecha_desde, fecha_hasta)
    return [
        VentasPorUsuario(
            usuario_id=usuario_id,
            nombre_completo=nombre_completo,
            usuario_activo=activo,
            cantidad_ventas=cantidad_ventas,
            total_vendido_centavos=total_vendido_centavos,
        )
        for usuario_id, nombre_completo, activo, cantidad_ventas, total_vendido_centavos in filas
    ]


# --- Reportes operativos (V1.6-C): caja por sesión, compras por proveedor y cuenta corriente -------------
# Todos agregan en SQL (ver `db/repositorios`) y son de solo lectura.

# Ventana por defecto de los reportes operativos con período: últimos 30 días (incluye hoy).
DIAS_RANGO_POR_DEFECTO_OPERATIVO = 30

_FECHA_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass
class ReporteCaja:
    fecha_desde: str
    fecha_hasta: str
    sesiones: list[ResumenSesionCaja]


@dataclass
class ReporteCompras:
    fecha_desde: str
    fecha_hasta: str
    proveedor_id: int | None
    filas: list[ComprasDeProveedor]

    @property
    def total_compras(self) -> int:
        return sum(fila.cantidad_compras for fila in self.filas)

    @property
    def total_unidades(self) -> int:
        return sum(fila.unidades for fila in self.filas)

    @property
    def total_centavos(self) -> int:
        return sum(fila.total_centavos for fila in self.filas)


@dataclass
class ReporteDeuda:
    filas: list[DeudorCuenta]

    @property
    def total_centavos(self) -> int:
        return sum(fila.saldo_centavos for fila in self.filas)


@dataclass
class ReporteCobranzas:
    fecha_desde: str
    fecha_hasta: str
    filas: list[CobranzaDeCliente]

    @property
    def total_cobros(self) -> int:
        return sum(fila.cantidad_cobros for fila in self.filas)

    @property
    def total_centavos(self) -> int:
        return sum(fila.total_centavos for fila in self.filas)


def _limites_del_periodo(fecha_desde: str | None, fecha_hasta: str | None) -> tuple[str, str, str, str]:
    """`(desde_efectivo, hasta_efectivo, inicio, fin_exclusivo)` de un período de días completos.

    Sin alguno de los dos límites usa el rango por defecto para ambos (misma convención que los demás
    reportes). `inicio` y `fin_exclusivo` (el día siguiente a `hasta`) son texto comparable con las
    columnas de fecha/hora, así el filtro no aplica `date()` a la columna.

    Raises:
        DatosInvalidosError: si una fecha no es `AAAA-MM-DD` válida o `desde` es posterior a `hasta`.
    """
    if not fecha_desde or not fecha_hasta:
        hoy = date.today()
        fecha_desde = (hoy - timedelta(days=DIAS_RANGO_POR_DEFECTO_OPERATIVO - 1)).isoformat()
        fecha_hasta = hoy.isoformat()
    try:
        if not (_FECHA_ISO.fullmatch(fecha_desde) and _FECHA_ISO.fullmatch(fecha_hasta)):
            raise ValueError
        desde, hasta = date.fromisoformat(fecha_desde), date.fromisoformat(fecha_hasta)
        fin_exclusivo = (hasta + timedelta(days=1)).isoformat()
    except (ValueError, OverflowError):
        raise DatosInvalidosError("Las fechas del reporte deben tener el formato AAAA-MM-DD y ser válidas.") from None
    if desde > hasta:
        raise DatosInvalidosError("La fecha desde no puede ser posterior a la fecha hasta.")
    return fecha_desde, fecha_hasta, fecha_desde, fin_exclusivo


def generar_reporte_caja(fecha_desde: str | None = None, fecha_hasta: str | None = None) -> ReporteCaja:
    """Resumen por sesión de caja (abiertas en el período), con la misma fórmula de efectivo esperado
    que el arqueo y el cierre. No modifica ninguna caja."""
    desde, hasta, inicio, fin = _limites_del_periodo(fecha_desde, fecha_hasta)
    return ReporteCaja(desde, hasta, repositorio_caja.resumir_sesiones(inicio, fin))


def generar_reporte_compras(
    fecha_desde: str | None = None, fecha_hasta: str | None = None, proveedor_id: int | None = None
) -> ReporteCompras:
    """Compras del período agrupadas por proveedor (cantidad, unidades y total), opcionalmente de un solo
    proveedor."""
    desde, hasta, inicio, fin = _limites_del_periodo(fecha_desde, fecha_hasta)
    return ReporteCompras(desde, hasta, proveedor_id, repositorio_compras.resumir_por_proveedor(inicio, fin, proveedor_id))


def generar_reporte_deuda() -> ReporteDeuda:
    """Deuda actual de cuenta corriente por cliente (solo saldos positivos, activos o inactivos)."""
    return ReporteDeuda(repositorio_clientes.listar_deudores())


def generar_reporte_cobranzas(fecha_desde: str | None = None, fecha_hasta: str | None = None) -> ReporteCobranzas:
    """Cobros de cuenta corriente del período agrupados por cliente (solo `COBRO`)."""
    desde, hasta, inicio, fin = _limites_del_periodo(fecha_desde, fecha_hasta)
    return ReporteCobranzas(desde, hasta, repositorio_clientes.resumir_cobranzas(inicio, fin))
