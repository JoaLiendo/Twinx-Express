"""CAEA: Código de Autorización Electrónico Anticipado, primera modalidad de contingencia para quien emite
por web service con CAE (RG ARCA 5782; https://www.afip.gob.ar/fe/emision-autorizacion/contingencias.asp,
consultado el 01/11/2026).

- Períodos quincenales: orden 1 = días 1 a 15; orden 2 = día 16 al último del mes.
- Se solicita desde los 5 días corridos anteriores al inicio del período o durante el período.
- Se emite en un punto de venta específico para CAEA (distinto del de CAE).
- Lo emitido se informa desde el día siguiente al inicio del período y hasta el 8.º día corrido posterior
  a su finalización (ARCA devuelve la fecha tope exacta, `FchTopeInf`); un punto de venta CAEA sin
  comprobantes en el período se informa "sin movimiento".
"""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

DIAS_PREVIOS_SOLICITUD = 5


def periodo_de(fecha: date) -> tuple[int, int]:
    """(AAAAMM, orden) del período que contiene `fecha`."""
    return fecha.year * 100 + fecha.month, 1 if fecha.day <= 15 else 2


def rango(periodo: int, orden: int) -> tuple[date, date]:
    anio, mes = divmod(periodo, 100)
    if orden == 1:
        return date(anio, mes, 1), date(anio, mes, 15)
    return date(anio, mes, 16), date(anio, mes, calendar.monthrange(anio, mes)[1])


def siguiente(periodo: int, orden: int) -> tuple[int, int]:
    if orden == 1:
        return periodo, 2
    anio, mes = divmod(periodo, 100)
    return (anio + 1) * 100 + 1 if mes == 12 else periodo + 1, 1


def solicitables(hoy: date) -> list[tuple[int, int]]:
    """Períodos que pueden pedirse hoy: el vigente y, en los 5 días previos a su inicio, el próximo."""
    actual = periodo_de(hoy)
    resultado = [actual]
    proximo = siguiente(*actual)
    if rango(*proximo)[0] - hoy <= timedelta(days=DIAS_PREVIOS_SOLICITUD):
        resultado.append(proximo)
    return resultado


@dataclass(frozen=True)
class CAEA:
    id: int
    ambiente: str
    cuit: str
    codigo: str
    periodo: int
    orden: int
    vigente_desde: str
    vigente_hasta: str
    tope_informe: str
    fecha_proceso: str | None
    fecha_obtencion: str

    def vigente_en(self, fecha: date) -> bool:
        return date.fromisoformat(self.vigente_desde) <= fecha <= date.fromisoformat(self.vigente_hasta)

    def dias_para_informar(self, hoy: date) -> int:
        return (date.fromisoformat(self.tope_informe) - hoy).days

    @property
    def etiqueta(self) -> str:
        return f"{self.periodo // 100}/{self.periodo % 100:02d} · {'1.ª' if self.orden == 1 else '2.ª'} quincena"
