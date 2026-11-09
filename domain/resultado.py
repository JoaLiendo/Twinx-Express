"""Resultado operativo estimado del negocio (cierre operativo, bloque A3).

Resultado operativo estimado = margen bruto (sobre el costo de referencia) − gastos operativos vigentes del
período. Es una estimación y se presenta como tal: no descuenta impuestos ni ajustes contables, no incluye
las unidades vendidas sin costo conocido ni las pérdidas de stock, y la mercadería se reconoce por su costo
al vender (no por las compras), así que ninguna compra se cuenta dos veces contra los gastos.
"""

from dataclasses import dataclass

from domain.analitica import GRANULARIDADES, LineaVentaDia, ResumenMargen, clave_periodo, porcentaje


@dataclass(frozen=True)
class AnulacionesDia:
    """Ventas de un día que terminaron anuladas (incluida la cancelación por nota de crédito)."""

    fecha: str
    cantidad: int
    total_centavos: int


@dataclass
class ResultadoPeriodo:
    """Resultado de un período (o del total): ventas, margen bruto, anulaciones y gastos."""

    clave: str
    etiqueta: str
    margen: ResumenMargen = None  # type: ignore[assignment]  # se completa en __post_init__
    anulaciones_cantidad: int = 0
    anulaciones_centavos: int = 0
    gastos_centavos: int = 0

    def __post_init__(self) -> None:
        if self.margen is None:
            self.margen = ResumenMargen(clave=self.clave, etiqueta=self.etiqueta)

    @property
    def facturado_centavos(self) -> int:
        return self.margen.facturado_centavos

    @property
    def costo_conocido_centavos(self) -> int:
        """Costo de referencia de las unidades que lo tienen (la base del margen bruto)."""
        return self.margen.costo_ref_centavos

    @property
    def unidades_sin_costo(self) -> int:
        return self.margen.unidades_sin_costo_ref

    @property
    def margen_bruto_centavos(self) -> int | None:
        return self.margen.margen_ref_centavos

    @property
    def resultado_operativo_centavos(self) -> int | None:
        """`None` cuando no hay ninguna unidad con costo conocido: sin margen no hay resultado que estimar
        (los gastos se muestran igual, por separado)."""
        margen = self.margen_bruto_centavos
        if margen is None:
            return None if self.facturado_centavos else -self.gastos_centavos
        return margen - self.gastos_centavos

    @property
    def resultado_operativo_porcentual(self) -> float | None:
        resultado = self.resultado_operativo_centavos
        if resultado is None or not self.margen.facturado_con_costo_ref_centavos:
            return None
        return porcentaje(resultado, self.margen.facturado_con_costo_ref_centavos)

    @property
    def incompleto(self) -> bool:
        """Hay unidades vendidas sin costo conocido: el margen (y el resultado) están subestimados o
        sobreestimados según el costo que falta; se avisa en vez de inventarlo."""
        return self.unidades_sin_costo > 0


def agrupar_resultado(
    lineas: list[LineaVentaDia],
    anulaciones: list[AnulacionesDia],
    gastos_por_dia: list[tuple[str, int]],
    granularidad: str,
) -> tuple[ResultadoPeriodo, list[ResultadoPeriodo]]:
    """`(total, períodos en orden cronológico)`. Un período con gastos pero sin ventas (o al revés) aparece igual."""
    if granularidad not in GRANULARIDADES:
        raise ValueError(f"Granularidad desconocida: {granularidad!r}")
    total = ResultadoPeriodo(clave="total", etiqueta="Total del período")
    periodos: dict[str, ResultadoPeriodo] = {}

    def periodo(fecha: str) -> ResultadoPeriodo:
        clave = clave_periodo(fecha, granularidad)
        grupo = periodos.get(clave)
        if grupo is None:
            grupo = periodos[clave] = ResultadoPeriodo(clave=clave, etiqueta=clave)
        return grupo

    for linea in lineas:
        total.margen.sumar(linea)
        periodo(linea.fecha).margen.sumar(linea)
    for anulacion in anulaciones:
        for grupo in (total, periodo(anulacion.fecha)):
            grupo.anulaciones_cantidad += anulacion.cantidad
            grupo.anulaciones_centavos += anulacion.total_centavos
    for fecha, importe in gastos_por_dia:
        total.gastos_centavos += importe
        periodo(fecha).gastos_centavos += importe
    return total, [periodos[clave] for clave in sorted(periodos)]
