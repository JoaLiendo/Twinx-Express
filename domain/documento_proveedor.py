"""Documentación fiscal declarada de proveedores (V1.13-D).

Solo metadata de lo que el OWNER dice haber recibido o consultado: el sistema no guarda archivos, no
consulta ARCA ni ningún otro servicio y no afirma que un documento esté vigente. CM05 es un tipo más,
no un requisito: cuándo corresponde lo decide el usuario (la lógica fiscal profunda es V1.16).
"""

from dataclasses import dataclass
from datetime import date

from excepciones import DatosInvalidosError

LONGITUD_MAXIMA_REFERENCIA = 100
LONGITUD_MAXIMA_OBSERVACION = 500

TIPOS_DOCUMENTO = {
    "CONSTANCIA_FISCAL": "Constancia fiscal",
    "INGRESOS_BRUTOS": "Ingresos Brutos",
    "CM05": "CM05 (Convenio Multilateral)",
    "OTRO_DOCUMENTO_FISCAL": "Otro documento fiscal",
}


@dataclass(frozen=True)
class DocumentoProveedor:
    id: int
    proveedor_id: int
    tipo: str
    fecha_documento: str
    referencia: str | None
    observacion: str | None
    usuario_id: int
    usuario_nombre_completo: str | None
    fecha_registro: str

    @property
    def tipo_etiqueta(self) -> str:
        return TIPOS_DOCUMENTO[self.tipo]


def _opcional(texto: str | None, maximo: int, que: str) -> str | None:
    limpio = (texto or "").strip()
    if not limpio:
        return None
    if len(limpio) > maximo:
        raise DatosInvalidosError(f"{que} no puede superar los {maximo} caracteres.")
    return limpio


def validar_documento(
    tipo: str, fecha_documento: str, referencia: str | None, observacion: str | None, hoy: date
) -> tuple[str, str | None, str | None]:
    """Valida un documento nuevo: `(fecha AAAA-MM-DD, referencia, observación)` normalizadas.

    Raises:
        DatosInvalidosError: tipo desconocido, fecha inválida o posterior a hoy, textos demasiado largos.
    """
    if tipo not in TIPOS_DOCUMENTO:
        raise DatosInvalidosError(f"Tipo de documento inválido: {tipo!r}.")
    texto_fecha = (fecha_documento or "").strip()
    try:
        fecha = date.fromisoformat(texto_fecha) if len(texto_fecha) == 10 else None
    except ValueError:
        fecha = None
    if fecha is None:
        raise DatosInvalidosError("La fecha del documento debe ser una fecha válida (AAAA-MM-DD).")
    if fecha > hoy:
        raise DatosInvalidosError("La fecha del documento no puede ser posterior a hoy.")
    return (
        fecha.isoformat(),
        _opcional(referencia, LONGITUD_MAXIMA_REFERENCIA, "La referencia"),
        _opcional(observacion, LONGITUD_MAXIMA_OBSERVACION, "La observación"),
    )
