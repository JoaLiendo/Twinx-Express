"""Casos de uso del módulo de Reportes: agrega datos ya persistidos de
`ventas`/`detalle_venta` sobre un rango de fechas, sin recalcular ni
inferir nada que la base no almacene.

Deliberadamente NO calcula márgenes, ganancias ni costo histórico:
`detalle_venta` congela el precio de venta al momento de cada venta,
pero no el costo del producto en ese momento.
`productos.precio_costo_centavos` es el costo *actual*, no el vigente
en la fecha de cada venta -- usarlo para un período pasado daría un
margen incorrecto si el costo cambió desde entonces (algo frecuente:
`services.servicio_compras` actualiza ese costo en cada compra nueva).
Mostrar esa cifra igual, aunque fuera técnicamente posible, sería un
dato incorrecto disfrazado de reporte. Requeriría guardar
`costo_unitario_centavos` en `detalle_venta` (como ya hace
`detalle_compra`) -- un cambio de esquema fuera de alcance de esta fase.

Tampoco reporta por vendedor/cajero: a diferencia de `compras` (que sí
tiene `usuario_id`), `ventas` no registra quién la cobró -- no hay
forma de saber eso con el modelo actual.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from db.repositorios import ventas as repositorio_ventas
from domain.venta import ProductoMasVendido, Venta

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
class ReporteVentas:
    """Resumen de ventas para un período `[fecha_desde, fecha_hasta]`
    (ambos límites inclusive, texto "YYYY-MM-DD").

    `ticket_promedio_centavos` es `total_facturado_centavos // cantidad_ventas`
    (0 si no hubo ventas): aritmética entera, igual criterio que el
    resto del proyecto para montos en centavos (ver `domain.dinero`).
    """

    fecha_desde: str
    fecha_hasta: str
    cantidad_ventas: int
    total_facturado_centavos: int
    ticket_promedio_centavos: int
    ventas_por_medio_pago: list[VentasPorMedioPago] = field(default_factory=list)
    evolucion_por_dia: list[VentasPorDia] = field(default_factory=list)
    productos_mas_vendidos: list[ProductoMasVendido] = field(default_factory=list)


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
        ventas_por_medio_pago=_agrupar_por_medio_pago(ventas_del_periodo),
        evolucion_por_dia=_agrupar_por_dia(ventas_del_periodo),
        productos_mas_vendidos=productos_mas_vendidos,
    )
