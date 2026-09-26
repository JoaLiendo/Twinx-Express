"""Validación de rangos de fechas de filtros (`AAAA-MM-DD`), sin efectos secundarios."""

import re
from datetime import date, timedelta

from excepciones import DatosInvalidosError

_FECHA_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


def _fecha_valida(texto: str, mensaje: str) -> date:
    try:
        if not _FECHA_ISO.fullmatch(texto):
            raise ValueError
        return date.fromisoformat(texto)
    except ValueError:
        raise DatosInvalidosError(mensaje) from None


def limites_de_fechas(
    fecha_desde: str | None, fecha_hasta: str | None, mensaje: str
) -> tuple[str | None, str | None]:
    """`(inicio, fin_exclusivo)` de un filtro de días completos, como texto comparable con las columnas de
    fecha/hora (`fecha >= inicio AND fecha < fin_exclusivo`, sin aplicar `date()` a la columna, así el índice
    de fecha sirve). `None` deja el extremo abierto; un texto vacío equivale a `None`.

    Raises:
        DatosInvalidosError: con `mensaje` si una fecha no es `AAAA-MM-DD` válida, o con otro mensaje si
            `desde` es posterior a `hasta`.
    """
    desde = _fecha_valida(fecha_desde, mensaje) if fecha_desde else None
    hasta = _fecha_valida(fecha_hasta, mensaje) if fecha_hasta else None
    if desde and hasta and desde > hasta:
        raise DatosInvalidosError("La fecha desde no puede ser posterior a la fecha hasta.")
    try:
        fin_exclusivo = (hasta + timedelta(days=1)).isoformat() if hasta else None
    except OverflowError:
        raise DatosInvalidosError(mensaje) from None
    return (desde.isoformat() if desde else None), fin_exclusivo
