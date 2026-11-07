"""Entidades y cálculos puros de la analítica (V1.17).

Todo lo que hay acá es de solo lectura: proyecciones armadas con consultas agregadas de `db/` y
cálculos deterministas sobre ellas. Ninguna cifra se inventa: cuando un dato histórico no existe
(costo de referencia de una venta vieja, costo real de una existencia heredada, precio anterior a que
existiera el historial) se cuenta aparte como "desconocido" y nunca se reemplaza por el valor vigente.

Las tres fuentes de costo que se distinguen (ver `docs/V1.12_PARTIDAS.md`):

- **Costo de referencia**: `detalle_venta.costo_unitario_centavos`, el costo vigente al vender.
- **Costo real conocido**: costo de las partidas que consumió cada línea (libro de movimientos).
- **Costo desconocido**: unidades sin costo de referencia o sin partida con costo.

Los porcentajes son `float` de exposición calculados una vez a partir de enteros exactos (mismo
criterio que `services.servicio_reportes.ResumenRentabilidad`); nunca se acumulan.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from domain.credito import (
    ESTADO_CRITICA,
    ESTADO_DEMORADO,
    ESTADO_NORMAL,
    ESTADO_REGULAR,
    ETIQUETAS_ESTADO,
    estado_por_antiguedad,
)

GRANULARIDAD_DIA = "dia"
GRANULARIDAD_SEMANA = "semana"
GRANULARIDAD_MES = "mes"
GRANULARIDADES = (GRANULARIDAD_DIA, GRANULARIDAD_SEMANA, GRANULARIDAD_MES)

CAMPO_VENTA = "VENTA"
CAMPO_COSTO = "COSTO"

# Un cambio de precio es "relevante" cuando su variación absoluta supera este porcentaje.
UMBRAL_CAMBIO_RELEVANTE_POR_DEFECTO = 10

# Meses distintos con ventas necesarios para presentar la comparación estacional.
MESES_MINIMOS_ESTACIONALIDAD = 12

# Un proveedor "domina" una categoría cuando concentra al menos este porcentaje de lo comprado en ella.
UMBRAL_DEPENDENCIA_PORCENTAJE = 80

# Días de venta que la reposición por rotación intenta cubrir.
DIAS_COBERTURA_REPOSICION = 14
DIAS_ROTACION_REPOSICION = 30


def porcentaje(parte: int, total: int) -> float | None:
    """`parte / total * 100`, o `None` cuando el total es 0 (sin datos no es lo mismo que 0 %)."""
    return parte / total * 100 if total else None


def variacion_porcentual(inicial: int, final: int) -> float | None:
    """Variación de `inicial` a `final` en porcentaje; `None` si no hay base (inicial 0)."""
    return (final - inicial) / inicial * 100 if inicial else None


def clave_periodo(fecha: str, granularidad: str) -> str:
    """Clave de agrupación de una fecha `AAAA-MM-DD`: el día, la semana ISO (`AAAA-Wnn`) o el mes."""
    if granularidad == GRANULARIDAD_DIA:
        return fecha[:10]
    if granularidad == GRANULARIDAD_MES:
        return fecha[:7]
    if granularidad == GRANULARIDAD_SEMANA:
        anio, semana, _ = date.fromisoformat(fecha[:10]).isocalendar()
        return f"{anio}-W{semana:02d}"
    raise ValueError(f"Granularidad desconocida: {granularidad!r}")


def periodo_anterior(fecha_desde: str, fecha_hasta: str) -> tuple[str, str]:
    """El período inmediatamente anterior, de la misma cantidad de días (para comparar)."""
    desde, hasta = date.fromisoformat(fecha_desde), date.fromisoformat(fecha_hasta)
    dias = (hasta - desde).days + 1
    return (desde - timedelta(days=dias)).isoformat(), (desde - timedelta(days=1)).isoformat()


def mismo_periodo_anio_anterior(fecha_desde: str, fecha_hasta: str) -> tuple[str, str]:
    """Las mismas fechas del año anterior (29 de febrero cae al 28)."""

    def un_anio_atras(texto: str) -> str:
        valor = date.fromisoformat(texto)
        try:
            return valor.replace(year=valor.year - 1).isoformat()
        except ValueError:
            return valor.replace(year=valor.year - 1, day=28).isoformat()

    return un_anio_atras(fecha_desde), un_anio_atras(fecha_hasta)


# --- Ventas y margen ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class LineaVentaDia:
    """Agregado de un producto en un día: unidades, facturación y las tres bases de costo.

    `unidades_con_costo_ref` / `costo_ref_centavos` / `facturado_con_costo_ref_centavos` cubren solo las
    líneas con costo de referencia conocido. `unidades_costo_real` / `costo_real_centavos` cubren solo
    las unidades que salieron de partidas con costo. `unidades_comparables` /
    `costo_ref_comparable_centavos` / `costo_real_comparable_centavos` son las unidades con AMBOS costos
    conocidos, valorizadas a cada base, para medir la diferencia sobre exactamente las mismas unidades.
    """

    fecha: str
    producto_id: int
    producto_nombre: str
    categoria_id: int | None
    categoria_nombre: str | None
    unidades: int
    facturado_centavos: int
    unidades_con_costo_ref: int
    costo_ref_centavos: int
    facturado_con_costo_ref_centavos: int
    unidades_costo_real: int
    costo_real_centavos: int
    unidades_comparables: int
    costo_ref_comparable_centavos: int
    costo_real_comparable_centavos: int


@dataclass
class ResumenMargen:
    """Margen bruto de un grupo (producto, categoría o período). Margen bruto = facturado - costo; no es
    ganancia neta (no descuenta gastos, impuestos ni pérdidas)."""

    clave: str
    etiqueta: str
    unidades: int = 0
    facturado_centavos: int = 0
    unidades_con_costo_ref: int = 0
    costo_ref_centavos: int = 0
    facturado_con_costo_ref_centavos: int = 0
    unidades_costo_real: int = 0
    costo_real_centavos: int = 0
    unidades_comparables: int = 0
    costo_ref_comparable_centavos: int = 0
    costo_real_comparable_centavos: int = 0
    categoria_id: int | None = None

    def sumar(self, linea: LineaVentaDia) -> None:
        self.unidades += linea.unidades
        self.facturado_centavos += linea.facturado_centavos
        self.unidades_con_costo_ref += linea.unidades_con_costo_ref
        self.costo_ref_centavos += linea.costo_ref_centavos
        self.facturado_con_costo_ref_centavos += linea.facturado_con_costo_ref_centavos
        self.unidades_costo_real += linea.unidades_costo_real
        self.costo_real_centavos += linea.costo_real_centavos
        self.unidades_comparables += linea.unidades_comparables
        self.costo_ref_comparable_centavos += linea.costo_ref_comparable_centavos
        self.costo_real_comparable_centavos += linea.costo_real_comparable_centavos

    @property
    def margen_ref_centavos(self) -> int | None:
        """Margen bruto sobre la porción con costo de referencia conocido; `None` sin esa porción."""
        if self.unidades_con_costo_ref == 0:
            return None
        return self.facturado_con_costo_ref_centavos - self.costo_ref_centavos

    @property
    def margen_ref_porcentual(self) -> float | None:
        margen = self.margen_ref_centavos
        return None if margen is None else porcentaje(margen, self.facturado_con_costo_ref_centavos)

    @property
    def unidades_sin_costo_ref(self) -> int:
        return self.unidades - self.unidades_con_costo_ref

    @property
    def unidades_sin_costo_real(self) -> int:
        return self.unidades - self.unidades_costo_real

    @property
    def diferencia_real_vs_ref_centavos(self) -> int | None:
        """Costo real menos costo de referencia sobre las mismas unidades (positivo: la mercadería
        vendida costó más que la referencia). `None` si no hay unidades comparables."""
        if self.unidades_comparables == 0:
            return None
        return self.costo_real_comparable_centavos - self.costo_ref_comparable_centavos

    @property
    def diferencia_real_vs_ref_porcentual(self) -> float | None:
        diferencia = self.diferencia_real_vs_ref_centavos
        return None if diferencia is None else porcentaje(diferencia, self.costo_ref_comparable_centavos)


@dataclass(frozen=True)
class ResumenVentasPeriodo:
    """Ventas (tickets) de una clave de período."""

    clave: str
    cantidad_ventas: int
    unidades: int
    total_centavos: int

    @property
    def ticket_promedio_centavos(self) -> int:
        return self.total_centavos // self.cantidad_ventas if self.cantidad_ventas else 0


@dataclass(frozen=True)
class VentasDia:
    """Fila base de ventas de un día (tickets, unidades y total), tal como la agrega `db/`."""

    fecha: str
    cantidad_ventas: int
    unidades: int
    total_centavos: int


def agrupar_ventas(filas: list[VentasDia], granularidad: str) -> list[ResumenVentasPeriodo]:
    """Suma las filas diarias por la clave de período elegida, en orden cronológico."""
    acumulado: dict[str, list[int]] = {}
    for fila in filas:
        clave = clave_periodo(fila.fecha, granularidad)
        valores = acumulado.setdefault(clave, [0, 0, 0])
        valores[0] += fila.cantidad_ventas
        valores[1] += fila.unidades
        valores[2] += fila.total_centavos
    return [ResumenVentasPeriodo(clave, *acumulado[clave]) for clave in sorted(acumulado)]


@dataclass(frozen=True)
class ComparacionPeriodos:
    """Un período contra otro: totales y variación porcentual (`None` si el anterior no tuvo ventas)."""

    etiqueta: str
    fecha_desde: str
    fecha_hasta: str
    cantidad_ventas: int
    unidades: int
    total_centavos: int
    cantidad_ventas_anterior: int
    unidades_anterior: int
    total_anterior_centavos: int

    @property
    def variacion_total(self) -> float | None:
        return variacion_porcentual(self.total_anterior_centavos, self.total_centavos)

    @property
    def variacion_ventas(self) -> float | None:
        return variacion_porcentual(self.cantidad_ventas_anterior, self.cantidad_ventas)

    @property
    def variacion_unidades(self) -> float | None:
        return variacion_porcentual(self.unidades_anterior, self.unidades)


@dataclass(frozen=True)
class MesEstacional:
    """Un mes del año a lo largo de los años con ventas: promedio mensual, no pronóstico."""

    mes: int
    anios: int
    total_centavos: int
    cantidad_ventas: int

    @property
    def promedio_centavos(self) -> int:
        return self.total_centavos // self.anios if self.anios else 0


@dataclass(frozen=True)
class Estacionalidad:
    meses_con_ventas: int
    por_mes: list[MesEstacional]

    @property
    def suficiente(self) -> bool:
        return self.meses_con_ventas >= MESES_MINIMOS_ESTACIONALIDAD


def calcular_estacionalidad(ventas_por_mes: list[tuple[str, int, int]]) -> Estacionalidad:
    """`ventas_por_mes`: `(AAAA-MM, cantidad_ventas, total_centavos)` de toda la historia. Agrupa por mes
    del año y promedia entre los años en que ese mes tuvo ventas: una comparación histórica, nunca una
    proyección."""
    anios_por_mes: dict[int, set[int]] = {}
    total_por_mes: dict[int, int] = {}
    cantidad_por_mes: dict[int, int] = {}
    for mes_texto, cantidad, total in ventas_por_mes:
        anio, mes = int(mes_texto[:4]), int(mes_texto[5:7])
        anios_por_mes.setdefault(mes, set()).add(anio)
        total_por_mes[mes] = total_por_mes.get(mes, 0) + total
        cantidad_por_mes[mes] = cantidad_por_mes.get(mes, 0) + cantidad
    return Estacionalidad(
        meses_con_ventas=len(ventas_por_mes),
        por_mes=[
            MesEstacional(mes, len(anios_por_mes[mes]), total_por_mes[mes], cantidad_por_mes[mes])
            for mes in sorted(anios_por_mes)
        ],
    )


def agrupar_margen(lineas: list[LineaVentaDia], criterio: str) -> list[ResumenMargen]:
    """Resume el margen por `producto`, `categoria` o una granularidad de período, de mayor a menor
    facturación (los períodos, en orden cronológico)."""
    grupos: dict[str, ResumenMargen] = {}
    for linea in lineas:
        if criterio == "producto":
            clave, etiqueta, categoria_id = str(linea.producto_id), linea.producto_nombre, linea.categoria_id
        elif criterio == "categoria":
            clave = str(linea.categoria_id) if linea.categoria_id is not None else ""
            etiqueta, categoria_id = linea.categoria_nombre or "Sin categoría", linea.categoria_id
        else:
            clave = etiqueta = clave_periodo(linea.fecha, criterio)
            categoria_id = None
        grupo = grupos.get(clave)
        if grupo is None:
            grupo = grupos[clave] = ResumenMargen(clave=clave, etiqueta=etiqueta, categoria_id=categoria_id)
        grupo.sumar(linea)
    if criterio in GRANULARIDADES:
        return [grupos[clave] for clave in sorted(grupos)]
    return sorted(grupos.values(), key=lambda g: (-g.facturado_centavos, g.etiqueta.casefold()))


# --- Precios y costos --------------------------------------------------------------------------------


@dataclass(frozen=True)
class EvolucionPrecioProducto:
    """Cambios de un campo (VENTA o COSTO) de un producto dentro del período: del valor anterior al
    primer cambio hasta el valor del último, y cuántos cambios hubo. El valor vigente es el actual del
    producto (puede diferir del final si cambió después del período)."""

    producto_id: int
    producto_nombre: str
    categoria_id: int | None
    categoria_nombre: str | None
    campo: str
    cambios: int
    inicial_centavos: int
    final_centavos: int
    vigente_centavos: int

    @property
    def variacion_centavos(self) -> int:
        return self.final_centavos - self.inicial_centavos

    @property
    def variacion_porcentual(self) -> float | None:
        return variacion_porcentual(self.inicial_centavos, self.final_centavos)

    def es_relevante(self, umbral: float) -> bool:
        variacion = self.variacion_porcentual
        return variacion is not None and abs(variacion) >= umbral


@dataclass(frozen=True)
class CambioPrecioListado:
    """Un cambio de precio del historial con el nombre del producto resuelto (listado de la analítica)."""

    id: int
    fecha: str
    producto_id: int
    producto_nombre: str
    campo: str
    precio_anterior_centavos: int
    precio_nuevo_centavos: int
    origen: str
    usuario_nombre_completo: str | None

    @property
    def variacion_porcentual(self) -> float | None:
        return variacion_porcentual(self.precio_anterior_centavos, self.precio_nuevo_centavos)


@dataclass(frozen=True)
class CambiosPorPeriodo:
    """Cantidad de cambios de precio de un período, separados por campo y dirección."""

    clave: str
    subas_venta: int
    bajas_venta: int
    subas_costo: int
    bajas_costo: int


@dataclass(frozen=True)
class ProductoConCostoSinPrecio:
    """El costo subió en el período y el precio de venta no cambió: margen erosionado a revisar."""

    producto_id: int
    producto_nombre: str
    variacion_costo_porcentual: float
    costo_inicial_centavos: int
    costo_final_centavos: int
    precio_venta_centavos: int


def costos_subieron_sin_precio(evoluciones: list[EvolucionPrecioProducto]) -> list[ProductoConCostoSinPrecio]:
    """Productos cuyo costo subió en el período sin ningún cambio de precio de venta en el mismo lapso."""
    con_cambio_de_venta = {e.producto_id for e in evoluciones if e.campo == CAMPO_VENTA}
    resultado = []
    for evolucion in evoluciones:
        variacion = evolucion.variacion_porcentual
        if evolucion.campo != CAMPO_COSTO or variacion is None or variacion <= 0:
            continue
        if evolucion.producto_id in con_cambio_de_venta:
            continue
        resultado.append(
            ProductoConCostoSinPrecio(
                producto_id=evolucion.producto_id,
                producto_nombre=evolucion.producto_nombre,
                variacion_costo_porcentual=variacion,
                costo_inicial_centavos=evolucion.inicial_centavos,
                costo_final_centavos=evolucion.final_centavos,
                precio_venta_centavos=evolucion.vigente_centavos,
            )
        )
    return sorted(resultado, key=lambda p: -p.variacion_costo_porcentual)


# --- Deuda -------------------------------------------------------------------------------------------

ORDEN_ESTADOS_DEUDA = (ESTADO_NORMAL, ESTADO_REGULAR, ESTADO_DEMORADO, ESTADO_CRITICA)


@dataclass(frozen=True)
class ObligacionPendiente:
    """Saldo pendiente de una venta a cuenta y su fecha de origen (`obligaciones_cliente`)."""

    cliente_id: int
    cliente_nombre: str
    cliente_activo: bool
    saldo_pendiente_centavos: int
    fecha_origen: str


@dataclass(frozen=True)
class DeudaCliente:
    """Deuda de un cliente repartida por antigüedad, con el estado objetivo de su obligación más vieja y
    los hechos de crédito ya registrados (strikes, cierre abierto). No evalúa ni registra nada nuevo."""

    cliente_id: int
    cliente_nombre: str
    activo: bool
    saldo_centavos: int
    por_estado: dict[str, int]
    dias_antiguedad: int
    estado: str
    strikes: int
    credito_cerrado: bool

    @property
    def estado_etiqueta(self) -> str:
        return ETIQUETAS_ESTADO[self.estado]


@dataclass(frozen=True)
class AnalisisDeudaClientes:
    hoy: date
    clientes: list[DeudaCliente]

    @property
    def total_centavos(self) -> int:
        return sum(cliente.saldo_centavos for cliente in self.clientes)

    def total_por_estado(self, estado: str) -> int:
        return sum(cliente.por_estado.get(estado, 0) for cliente in self.clientes)

    def clientes_en(self, estado: str) -> list[DeudaCliente]:
        return [cliente for cliente in self.clientes if cliente.estado == estado]


def analizar_deuda_clientes(
    obligaciones: list[ObligacionPendiente], strikes: dict[int, int], cerrados: set[int], hoy: date
) -> AnalisisDeudaClientes:
    """Reparte cada obligación pendiente en el tramo de antigüedad que le corresponde HOY, con los mismos
    umbrales que el estado de crédito (`domain.credito.estado_por_antiguedad`). Mayor deuda primero."""
    por_cliente: dict[int, list[ObligacionPendiente]] = {}
    for obligacion in obligaciones:
        por_cliente.setdefault(obligacion.cliente_id, []).append(obligacion)
    clientes = []
    for cliente_id, pendientes in por_cliente.items():
        por_estado: dict[str, int] = {}
        mayor_antiguedad = 0
        for obligacion in pendientes:
            dias = (hoy - date.fromisoformat(obligacion.fecha_origen[:10])).days
            mayor_antiguedad = max(mayor_antiguedad, dias)
            estado = estado_por_antiguedad(dias)
            por_estado[estado] = por_estado.get(estado, 0) + obligacion.saldo_pendiente_centavos
        primera = pendientes[0]
        clientes.append(
            DeudaCliente(
                cliente_id=cliente_id,
                cliente_nombre=primera.cliente_nombre,
                activo=primera.cliente_activo,
                saldo_centavos=sum(o.saldo_pendiente_centavos for o in pendientes),
                por_estado=por_estado,
                dias_antiguedad=mayor_antiguedad,
                estado=estado_por_antiguedad(mayor_antiguedad),
                strikes=strikes.get(cliente_id, 0),
                credito_cerrado=cliente_id in cerrados,
            )
        )
    clientes.sort(key=lambda c: (-c.saldo_centavos, c.cliente_nombre.casefold()))
    return AnalisisDeudaClientes(hoy, clientes)


@dataclass(frozen=True)
class SaldoMensual:
    """Saldo de una cartera (clientes o proveedores) al cierre de cada mes, reconstruido acumulando el
    libro real de movimientos."""

    mes: str
    cargos_centavos: int
    pagos_centavos: int
    saldo_centavos: int


def evolucion_de_saldo(movimientos_por_mes: list[tuple[str, int, int]]) -> list[SaldoMensual]:
    """`movimientos_por_mes`: `(AAAA-MM, cargos, pagos)` en orden cronológico; el saldo acumula mes a mes."""
    saldo = 0
    resultado = []
    for mes, cargos, pagos in movimientos_por_mes:
        saldo += cargos - pagos
        resultado.append(SaldoMensual(mes, cargos, pagos, saldo))
    return resultado


# --- Proveedores -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CostoProductoProveedor:
    """Primer y último costo pagado a un proveedor por un producto dentro del período."""

    proveedor_id: int
    producto_id: int
    producto_nombre: str
    compras: int
    costo_inicial_centavos: int
    costo_final_centavos: int

    @property
    def variacion_porcentual(self) -> float | None:
        return variacion_porcentual(self.costo_inicial_centavos, self.costo_final_centavos)


@dataclass(frozen=True)
class CompraCategoriaProveedor:
    """Lo comprado a un proveedor en una categoría durante el período."""

    proveedor_id: int
    categoria_id: int | None
    categoria_nombre: str | None
    total_centavos: int


@dataclass(frozen=True)
class DependenciaCategoria:
    categoria_id: int | None
    categoria_nombre: str
    total_centavos: int
    porcentaje: float

    @property
    def alta(self) -> bool:
        return self.porcentaje >= UMBRAL_DEPENDENCIA_PORCENTAJE


@dataclass(frozen=True)
class AnalisisProveedor:
    proveedor_id: int
    nombre: str
    activo: bool
    cantidad_compras: int
    unidades: int
    total_centavos: int
    participacion: float | None
    calificacion: int | None
    evaluaciones: int
    reclamos_total: int
    reclamos_abiertos: int
    saldo_centavos: int
    costos_subieron: int
    costos_bajaron: int
    costos: list[CostoProductoProveedor]
    dependencias: list[DependenciaCategoria]

    @property
    def requiere_seguimiento(self) -> bool:
        """Hechos registrados que justifican mirar al proveedor: calificación baja, reclamos abiertos o
        dependencia alta en alguna categoría. No es una predicción."""
        return (
            (self.calificacion is not None and self.calificacion <= 2)
            or self.reclamos_abiertos > 0
            or any(dependencia.alta for dependencia in self.dependencias)
        )


def calcular_dependencias(
    compras: list[CompraCategoriaProveedor],
) -> dict[int, list[DependenciaCategoria]]:
    """Por proveedor, su participación en cada categoría que le compró (sobre el total comprado en esa
    categoría a todos los proveedores)."""
    total_por_categoria: dict[int | None, int] = {}
    for compra in compras:
        total_por_categoria[compra.categoria_id] = total_por_categoria.get(compra.categoria_id, 0) + compra.total_centavos
    resultado: dict[int, list[DependenciaCategoria]] = {}
    for compra in compras:
        total = total_por_categoria[compra.categoria_id]
        participacion = porcentaje(compra.total_centavos, total) or 0.0
        resultado.setdefault(compra.proveedor_id, []).append(
            DependenciaCategoria(
                categoria_id=compra.categoria_id,
                categoria_nombre=compra.categoria_nombre or "Sin categoría",
                total_centavos=compra.total_centavos,
                porcentaje=participacion,
            )
        )
    for dependencias in resultado.values():
        dependencias.sort(key=lambda d: (-d.porcentaje, d.categoria_nombre.casefold()))
    return resultado


# --- Reposición --------------------------------------------------------------------------------------


def sugerencia_por_rotacion(stock_actual: int, unidades_vendidas: int, dias_observados: int, dias_cobertura: int) -> int | None:
    """Unidades a comprar para cubrir `dias_cobertura` días al ritmo de venta observado, descontando el
    stock actual. `None` cuando no hubo ventas en el período observado (sin ritmo no hay sugerencia)."""
    if unidades_vendidas <= 0 or dias_observados <= 0:
        return None
    necesarias = -(-unidades_vendidas * dias_cobertura // dias_observados)  # techo de la división
    return max(0, necesarias - stock_actual)


def dias_de_cobertura(stock_actual: int, unidades_vendidas: int, dias_observados: int) -> int | None:
    """Días que dura el stock al ritmo de venta observado; `None` sin ventas en el período."""
    if unidades_vendidas <= 0 or dias_observados <= 0:
        return None
    return stock_actual * dias_observados // unidades_vendidas
