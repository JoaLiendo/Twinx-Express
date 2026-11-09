"""Casos de uso de la analítica (V1.17): ventas, margen bruto, precios y costos, deuda, proveedores y el
resumen ejecutivo del dueño. Todo es de solo lectura y se arma con los datos ya persistidos; nada se
proyecta ni se inventa (ver `domain.analitica`).

Los períodos siguen la convención de los reportes operativos (`servicio_reportes.limites_del_periodo`):
últimos 30 días por defecto, `AAAA-MM-DD` inclusive en ambos extremos. Un filtro por categoría incluye
sus subcategorías, igual que el listado de productos.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from db.repositorios import analitica as repositorio_analitica
from db.repositorios import gastos as repositorio_gastos
from db.repositorios.gastos import TotalCategoria
from db.repositorios import evaluaciones_proveedor as repositorio_evaluaciones
from db.repositorios import proveedores as repositorio_proveedores
from db.repositorios import reclamos_proveedor as repositorio_reclamos
from domain.analitica import (
    CAMPO_COSTO,
    CAMPO_VENTA,
    GRANULARIDAD_DIA,
    GRANULARIDAD_MES,
    GRANULARIDADES,
    ORDEN_ESTADOS_DEUDA,
    UMBRAL_CAMBIO_RELEVANTE_POR_DEFECTO,
    AnalisisDeudaClientes,
    AnalisisProveedor,
    CambioPrecioListado,
    CambiosPorPeriodo,
    ComparacionPeriodos,
    Estacionalidad,
    EvolucionPrecioProducto,
    ProductoConCostoSinPrecio,
    ResumenMargen,
    ResumenVentasPeriodo,
    SaldoMensual,
    agrupar_margen,
    agrupar_ventas,
    analizar_deuda_clientes,
    calcular_dependencias,
    calcular_estacionalidad,
    costos_subieron_sin_precio,
    evolucion_de_saldo,
    mismo_periodo_anio_anterior,
    periodo_anterior,
    porcentaje,
)
from domain.credito import ESTADO_CRITICA, ESTADO_DEMORADO
from domain.resultado import ResultadoPeriodo, agrupar_resultado
from domain.deuda_proveedores import ReporteDeudaProveedores
from domain.reclamo_proveedor import ESTADOS_ABIERTOS
from excepciones import DatosInvalidosError
from services import servicio_categorias, servicio_deuda_proveedores, servicio_reportes, servicio_stock
from services.servicio_reportes import limites_del_periodo

LIMITE_TOP_PRODUCTOS = 10
CRITERIOS_MARGEN = ("producto", "categoria") + GRANULARIDADES
DIAS_RESUMEN_EJECUTIVO = 30


def _ids_de_categoria(categoria_id: int | None) -> set[int] | None:
    """La categoría y sus subcategorías; `None` = sin filtro.

    Raises:
        DatosInvalidosError: si la categoría no existe.
    """
    if categoria_id is None:
        return None
    ids = servicio_categorias.obtener_arbol().ids_con_descendientes(categoria_id)
    if not ids:
        raise DatosInvalidosError("La categoría elegida no existe.")
    return ids


def _validar_opcion(valor: str | None, admitidas: tuple[str, ...], por_defecto: str, nombre: str) -> str:
    if not valor:
        return por_defecto
    if valor not in admitidas:
        raise DatosInvalidosError(f"{nombre} inválida: {valor!r}.")
    return valor


# --- A4. Ventas --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AnalisisVentas:
    fecha_desde: str
    fecha_hasta: str
    granularidad: str
    producto_id: int | None
    categoria_id: int | None
    periodos: list[ResumenVentasPeriodo]
    comparacion_anterior: ComparacionPeriodos
    comparacion_anio_anterior: ComparacionPeriodos
    top_facturacion: list[ResumenMargen]
    top_unidades: list[ResumenMargen]
    estacionalidad: Estacionalidad

    @property
    def cantidad_ventas(self) -> int:
        return self.comparacion_anterior.cantidad_ventas

    @property
    def unidades(self) -> int:
        return self.comparacion_anterior.unidades

    @property
    def total_centavos(self) -> int:
        return self.comparacion_anterior.total_centavos

    @property
    def ticket_promedio_centavos(self) -> int:
        return self.total_centavos // self.cantidad_ventas if self.cantidad_ventas else 0

    @property
    def maximo_periodo_centavos(self) -> int:
        return max((periodo.total_centavos for periodo in self.periodos), default=0)


def _totales_de(fecha_desde: str, fecha_hasta: str, producto_id: int | None, ids: set[int] | None) -> tuple[int, int, int]:
    _, _, inicio, fin = limites_del_periodo(fecha_desde, fecha_hasta)
    filas = repositorio_analitica.ventas_por_dia(inicio, fin, producto_id, ids)
    return (
        sum(fila.cantidad_ventas for fila in filas),
        sum(fila.unidades for fila in filas),
        sum(fila.total_centavos for fila in filas),
    )


def _comparar(
    etiqueta: str, actual: tuple[int, int, int], desde: str, hasta: str, producto_id: int | None, ids: set[int] | None
) -> ComparacionPeriodos:
    anterior = _totales_de(desde, hasta, producto_id, ids)
    return ComparacionPeriodos(etiqueta, desde, hasta, *actual, *anterior)


def analizar_ventas(
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    granularidad: str | None = None,
    producto_id: int | None = None,
    categoria_id: int | None = None,
) -> AnalisisVentas:
    """Ventas activas del período por día/semana/mes, comparadas con el período anterior y con el mismo
    período del año anterior, más los productos de mayor facturación y unidades. La estacionalidad es una
    comparación histórica por mes del año sobre toda la historia (sin filtros), nunca un pronóstico.

    Raises:
        DatosInvalidosError: fechas, granularidad o categoría inválidas.
    """
    granularidad = _validar_opcion(granularidad, GRANULARIDADES, GRANULARIDAD_DIA, "Granularidad")
    desde, hasta, inicio, fin = limites_del_periodo(fecha_desde, fecha_hasta)
    ids = _ids_de_categoria(categoria_id)
    filas = repositorio_analitica.ventas_por_dia(inicio, fin, producto_id, ids)
    actual = (
        sum(fila.cantidad_ventas for fila in filas),
        sum(fila.unidades for fila in filas),
        sum(fila.total_centavos for fila in filas),
    )
    por_producto = agrupar_margen(repositorio_analitica.lineas_de_venta_por_dia(inicio, fin, producto_id, ids), "producto")
    return AnalisisVentas(
        fecha_desde=desde,
        fecha_hasta=hasta,
        granularidad=granularidad,
        producto_id=producto_id,
        categoria_id=categoria_id,
        periodos=agrupar_ventas(filas, granularidad),
        comparacion_anterior=_comparar("Período anterior", actual, *periodo_anterior(desde, hasta), producto_id, ids),
        comparacion_anio_anterior=_comparar(
            "Mismo período del año anterior", actual, *mismo_periodo_anio_anterior(desde, hasta), producto_id, ids
        ),
        top_facturacion=por_producto[:LIMITE_TOP_PRODUCTOS],
        top_unidades=sorted(por_producto, key=lambda g: (-g.unidades, -g.facturado_centavos))[:LIMITE_TOP_PRODUCTOS],
        estacionalidad=calcular_estacionalidad(repositorio_analitica.ventas_por_mes_historico(), date.today().strftime("%Y-%m")),
    )


# --- A2/A3. Margen bruto y costo real --------------------------------------------------------------


@dataclass(frozen=True)
class AnalisisMargen:
    fecha_desde: str
    fecha_hasta: str
    criterio: str
    producto_id: int | None
    categoria_id: int | None
    total: ResumenMargen
    grupos: list[ResumenMargen]

    @property
    def con_margen_negativo(self) -> list[ResumenMargen]:
        return [g for g in self.grupos if g.margen_ref_centavos is not None and g.margen_ref_centavos < 0]


def analizar_margen(
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    criterio: str | None = None,
    producto_id: int | None = None,
    categoria_id: int | None = None,
) -> AnalisisMargen:
    """Margen bruto del período por producto, categoría o período, distinguiendo costo de referencia,
    costo real de partidas y unidades sin costo conocido. Solo ventas activas.

    Raises:
        DatosInvalidosError: fechas, criterio o categoría inválidos.
    """
    criterio = _validar_opcion(criterio, CRITERIOS_MARGEN, "producto", "Agrupación")
    desde, hasta, inicio, fin = limites_del_periodo(fecha_desde, fecha_hasta)
    lineas = repositorio_analitica.lineas_de_venta_por_dia(inicio, fin, producto_id, _ids_de_categoria(categoria_id))
    total = ResumenMargen(clave="total", etiqueta="Total del período")
    for linea in lineas:
        total.sumar(linea)
    return AnalisisMargen(desde, hasta, criterio, producto_id, categoria_id, total, agrupar_margen(lineas, criterio))


# --- A1. Precios y costos ----------------------------------------------------------------------------


@dataclass(frozen=True)
class AnalisisPrecios:
    fecha_desde: str
    fecha_hasta: str
    producto_id: int | None
    categoria_id: int | None
    umbral: float
    evoluciones: list[EvolucionPrecioProducto]
    por_mes: list[CambiosPorPeriodo]
    cambios: list[CambioPrecioListado]
    costos_sin_precio: list[ProductoConCostoSinPrecio]

    @property
    def relevantes(self) -> list[EvolucionPrecioProducto]:
        return sorted(
            (e for e in self.evoluciones if e.es_relevante(self.umbral)),
            key=lambda e: -abs(e.variacion_porcentual or 0),
        )

    @property
    def de_venta(self) -> list[EvolucionPrecioProducto]:
        return [e for e in self.evoluciones if e.campo == CAMPO_VENTA]

    @property
    def de_costo(self) -> list[EvolucionPrecioProducto]:
        return [e for e in self.evoluciones if e.campo == CAMPO_COSTO]


def _umbral(umbral: str | None) -> float:
    if not umbral:
        return float(UMBRAL_CAMBIO_RELEVANTE_POR_DEFECTO)
    try:
        valor = float(umbral.replace(",", "."))
    except ValueError:
        raise DatosInvalidosError("El umbral de cambio relevante debe ser un número.") from None
    if not 0 <= valor <= 1000:
        raise DatosInvalidosError("El umbral de cambio relevante debe estar entre 0 y 1000 %.")
    return valor


def analizar_precios(
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    producto_id: int | None = None,
    categoria_id: int | None = None,
    umbral: str | None = None,
) -> AnalisisPrecios:
    """Evolución de precios de venta y costos a partir del historial real de cambios: por producto
    (inicial → final → vigente), por mes (subas y bajas), el listado de cambios y los productos cuyo costo
    subió sin que cambiara el precio. `umbral` (porcentaje) define qué cambio se considera relevante.

    Raises:
        DatosInvalidosError: fechas, categoría o umbral inválidos.
    """
    desde, hasta, inicio, fin = limites_del_periodo(fecha_desde, fecha_hasta)
    ids = _ids_de_categoria(categoria_id)
    evoluciones = repositorio_analitica.evolucion_de_precios(inicio, fin, producto_id, ids)
    por_mes: dict[str, dict[str, int]] = {}
    for mes, campo, subas, bajas in repositorio_analitica.cambios_de_precio_por_mes(inicio, fin, producto_id, ids):
        valores = por_mes.setdefault(mes, {})
        valores[f"subas_{campo.lower()}"] = subas
        valores[f"bajas_{campo.lower()}"] = bajas
    return AnalisisPrecios(
        fecha_desde=desde,
        fecha_hasta=hasta,
        producto_id=producto_id,
        categoria_id=categoria_id,
        umbral=_umbral(umbral),
        evoluciones=evoluciones,
        por_mes=[
            CambiosPorPeriodo(
                mes,
                valores.get("subas_venta", 0),
                valores.get("bajas_venta", 0),
                valores.get("subas_costo", 0),
                valores.get("bajas_costo", 0),
            )
            for mes, valores in sorted(por_mes.items())
        ],
        cambios=repositorio_analitica.listar_cambios_de_precio(inicio, fin, producto_id, ids),
        costos_sin_precio=costos_subieron_sin_precio(evoluciones),
    )


# --- A6. Deuda ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AnalisisDeuda:
    clientes: AnalisisDeudaClientes
    proveedores: ReporteDeudaProveedores
    evolucion_clientes: list[SaldoMensual]
    evolucion_proveedores: list[SaldoMensual]

    @property
    def estados(self) -> tuple[str, ...]:
        return ORDEN_ESTADOS_DEUDA

    @property
    def proveedores_total_centavos(self) -> int:
        """Misma cifra que el reporte de deuda a proveedores: solo libros consistentes (ver `cantidad_inconsistentes`)."""
        return self.proveedores.total_saldo_centavos

    def proveedores_por_situacion(self, situacion: str) -> int:
        return getattr(self.proveedores, f"total_{situacion}_centavos")


def analizar_deuda(hoy: date | None = None) -> AnalisisDeuda:
    """Cuentas por cobrar y por pagar, siempre separadas: antigüedad de la deuda de clientes con el
    estado objetivo de crédito (sin registrar cierres ni strikes nuevos), deuda a proveedores por
    vencimiento (estimación FIFO del reporte existente) y la evolución mensual de cada saldo
    reconstruida desde su libro. `hoy` es solo para tests."""
    hoy = hoy or date.today()
    strikes, cerrados = repositorio_analitica.strikes_y_cierres_de_credito()
    return AnalisisDeuda(
        clientes=analizar_deuda_clientes(
            repositorio_analitica.obligaciones_pendientes_de_clientes(), strikes, cerrados, hoy
        ),
        proveedores=servicio_deuda_proveedores.generar_reporte_deuda_proveedores(hoy=hoy),
        evolucion_clientes=evolucion_de_saldo(repositorio_analitica.movimientos_cuenta_por_mes()),
        evolucion_proveedores=evolucion_de_saldo(repositorio_analitica.movimientos_proveedor_por_mes()),
    )


# --- A7. Proveedores ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class AnalisisProveedores:
    fecha_desde: str
    fecha_hasta: str
    proveedores: list[AnalisisProveedor]

    @property
    def total_centavos(self) -> int:
        return sum(p.total_centavos for p in self.proveedores)

    @property
    def con_seguimiento(self) -> list[AnalisisProveedor]:
        return [p for p in self.proveedores if p.requiere_seguimiento]


def analizar_proveedores(fecha_desde: str | None = None, fecha_hasta: str | None = None) -> AnalisisProveedores:
    """Todos los proveedores con sus compras del período, participación, calificación actual, reclamos,
    saldo, evolución de costos por producto y dependencia por categoría. Los hechos registrados (una
    calificación, un reclamo) se muestran como tales, sin convertirlos en una predicción.

    Raises:
        DatosInvalidosError: fechas inválidas.
    """
    desde, hasta, inicio, fin = limites_del_periodo(fecha_desde, fecha_hasta)
    compras = {c.proveedor_id: c for c in servicio_reportes.generar_reporte_compras(desde, hasta).filas}
    total_comprado = sum(c.total_centavos for c in compras.values())
    calificaciones = repositorio_evaluaciones.calificaciones_actuales()
    abiertos = repositorio_reclamos.abiertos_por_proveedor(ESTADOS_ABIERTOS)
    contadores = repositorio_analitica.contadores_de_proveedores()
    costos: dict[int, list] = {}
    for costo in repositorio_analitica.costos_por_producto_y_proveedor(inicio, fin):
        costos.setdefault(costo.proveedor_id, []).append(costo)
    dependencias = calcular_dependencias(repositorio_analitica.compras_por_proveedor_y_categoria(inicio, fin))
    proveedores = []
    for proveedor in repositorio_proveedores.listar_todos():
        compra = compras.get(proveedor.id)
        evaluaciones, reclamos_total, saldo = contadores.get(proveedor.id, (0, 0, 0))
        costos_proveedor = costos.get(proveedor.id, [])
        proveedores.append(
            AnalisisProveedor(
                proveedor_id=proveedor.id,
                nombre=proveedor.nombre,
                activo=proveedor.activo,
                cantidad_compras=compra.cantidad_compras if compra else 0,
                unidades=compra.unidades if compra else 0,
                total_centavos=compra.total_centavos if compra else 0,
                participacion=porcentaje(compra.total_centavos, total_comprado) if compra else None,
                calificacion=calificaciones.get(proveedor.id),
                evaluaciones=evaluaciones,
                reclamos_total=reclamos_total,
                reclamos_abiertos=abiertos.get(proveedor.id, 0),
                saldo_centavos=saldo,
                costos_subieron=sum(1 for c in costos_proveedor if c.costo_final_centavos > c.costo_inicial_centavos),
                costos_bajaron=sum(1 for c in costos_proveedor if c.costo_final_centavos < c.costo_inicial_centavos),
                costos=costos_proveedor,
                dependencias=dependencias.get(proveedor.id, []),
            )
        )
    proveedores.sort(key=lambda p: (-p.total_centavos, p.nombre.casefold()))
    return AnalisisProveedores(desde, hasta, proveedores)


# --- A9. Resumen ejecutivo ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionSugerida:
    """Algo que el dueño debería revisar hoy, con la cifra que lo justifica y adónde ir. Es una
    observación sobre datos registrados, no una orden ni una predicción."""

    clave: str
    titulo: str
    detalle: str
    href: str
    cantidad: int
    severidad: str  # 'alerta' | 'advertencia' | 'info'


@dataclass(frozen=True)
class ResumenEjecutivo:
    fecha_desde: str
    fecha_hasta: str
    ventas: AnalisisVentas
    margen: AnalisisMargen
    perdidas_centavos: int
    perdidas_unidades: int
    perdidas_sin_costo: int
    deuda: AnalisisDeuda
    stock_critico: int
    sin_rotacion: int
    valor_inmovilizado_centavos: int
    reposicion_pendiente: int
    decisiones: list[DecisionSugerida]


def resumen_ejecutivo(hoy: date | None = None) -> ResumenEjecutivo:
    """Dashboard avanzado del dueño: los últimos 30 días contra los 30 anteriores, el margen, las pérdidas,
    la deuda, el stock que necesita atención y las decisiones a revisar. Reutiliza los análisis de este
    módulo y los reportes existentes; `hoy` es solo para tests."""
    hoy = hoy or date.today()
    desde = (hoy - timedelta(days=DIAS_RESUMEN_EJECUTIVO - 1)).isoformat()
    hasta = hoy.isoformat()
    ventas = analizar_ventas(desde, hasta)
    margen = analizar_margen(desde, hasta, "producto")
    precios = analizar_precios(desde, hasta)
    perdidas = servicio_reportes.generar_reporte_perdidas(desde, hasta)
    rotacion = servicio_reportes.generar_reporte_rotacion(desde, hasta, hoy=hoy)
    deuda = analizar_deuda(hoy)
    proveedores = analizar_proveedores(desde, hasta)
    reposicion = servicio_stock.listar_reposicion()
    stock_critico = len(servicio_stock.listar_stock_critico())
    decisiones = _decisiones(margen, precios, deuda, proveedores, rotacion.productos_sin_ventas, reposicion)
    return ResumenEjecutivo(
        fecha_desde=desde,
        fecha_hasta=hasta,
        ventas=ventas,
        margen=margen,
        perdidas_centavos=perdidas.total.valor_conocido_centavos,
        perdidas_unidades=perdidas.total.cantidad,
        perdidas_sin_costo=perdidas.total.filas_costo_desconocido,
        deuda=deuda,
        stock_critico=stock_critico,
        sin_rotacion=rotacion.productos_sin_ventas,
        valor_inmovilizado_centavos=rotacion.valor_inmovilizado_centavos,
        reposicion_pendiente=len(reposicion),
        decisiones=decisiones,
    )


def _decisiones(margen, precios, deuda, proveedores, sin_rotacion, reposicion) -> list[DecisionSugerida]:
    candidatas = [
        DecisionSugerida(
            "margen-negativo",
            "Productos vendidos por debajo del costo",
            "Margen bruto de referencia negativo en el período.",
            f"/analitica/margen?fecha_desde={margen.fecha_desde}&fecha_hasta={margen.fecha_hasta}",
            len(margen.con_margen_negativo),
            "alerta",
        ),
        DecisionSugerida(
            "costo-sin-precio",
            "Costos que subieron sin ajustar el precio",
            "El costo aumentó en el período y el precio de venta no cambió.",
            f"/analitica/precios?fecha_desde={precios.fecha_desde}&fecha_hasta={precios.fecha_hasta}",
            len(precios.costos_sin_precio),
            "advertencia",
        ),
        DecisionSugerida(
            "deuda-critica",
            "Clientes con deuda crítica",
            "Obligaciones de 60 días o más: el crédito queda cerrado hasta que se regularicen.",
            "/analitica/deuda",
            len(deuda.clientes.clientes_en(ESTADO_CRITICA)),
            "alerta",
        ),
        DecisionSugerida(
            "deuda-demorada",
            "Clientes demorados",
            "Entre 45 y 59 días: vender a cuenta requiere autorización del dueño.",
            "/analitica/deuda",
            len(deuda.clientes.clientes_en(ESTADO_DEMORADO)),
            "advertencia",
        ),
        DecisionSugerida(
            "proveedores-vencidos",
            "Proveedores con deuda vencida",
            "Saldo estimado vencido según la antigüedad de las compras a crédito.",
            "/reportes/deuda-proveedores?situacion=vencida",
            sum(1 for fila in deuda.proveedores.filas if (fila.vencida_centavos or 0) > 0),
            "alerta",
        ),
        DecisionSugerida(
            "proveedores-seguimiento",
            "Proveedores que requieren seguimiento",
            "Calificación baja, reclamos abiertos o dependencia alta en una categoría.",
            f"/analitica/proveedores?fecha_desde={proveedores.fecha_desde}&fecha_hasta={proveedores.fecha_hasta}",
            len(proveedores.con_seguimiento),
            "advertencia",
        ),
        DecisionSugerida(
            "reposicion",
            "Productos para reponer",
            "Por debajo de su stock mínimo; la reposición sugiere cantidades y explica el cálculo.",
            "/reposicion",
            len(reposicion),
            "advertencia",
        ),
        DecisionSugerida(
            "sin-rotacion",
            "Productos con stock y sin ventas",
            "Stock inmovilizado en el período: revisar precio, liquidación o exhibición.",
            f"/reportes/rotacion?fecha_desde={margen.fecha_desde}&fecha_hasta={margen.fecha_hasta}",
            sin_rotacion,
            "info",
        ),
    ]
    return [decision for decision in candidatas if decision.cantidad > 0]


# --- Resultado operativo estimado (cierre operativo, bloque A3) ---------------------------------------


@dataclass(frozen=True)
class AnalisisResultado:
    fecha_desde: str
    fecha_hasta: str
    granularidad: str
    total: ResultadoPeriodo
    periodos: list[ResultadoPeriodo]
    gastos_por_categoria: list[TotalCategoria]


def analizar_resultado(
    fecha_desde: str | None = None, fecha_hasta: str | None = None, granularidad: str | None = None
) -> AnalisisResultado:
    """Ventas activas, anulaciones, margen bruto (costo de referencia; el real y el desconocido se informan
    aparte), gastos operativos vigentes y resultado operativo estimado, por período y en total. Lee los
    mismos datos que `/analitica/margen` y `/gastos`; no proyecta ni inventa nada.

    Raises:
        DatosInvalidosError: fechas o granularidad inválidas.
    """
    granularidad = _validar_opcion(granularidad, GRANULARIDADES, GRANULARIDAD_MES, "Agrupación")
    desde, hasta, inicio, fin = limites_del_periodo(fecha_desde, fecha_hasta)
    lineas = repositorio_analitica.lineas_de_venta_por_dia(inicio, fin)
    anulaciones = repositorio_analitica.anulaciones_por_dia(inicio, fin)
    gastos = repositorio_gastos.totales_por_dia(inicio, fin)
    total, periodos = agrupar_resultado(lineas, anulaciones, gastos, granularidad)
    return AnalisisResultado(desde, hasta, granularidad, total, periodos, repositorio_gastos.totales_por_categoria(inicio, fin))
