"""Reglas fiscales fechadas: qué clase de comprobante corresponde y cuándo identificar al receptor.

Cada regla normativa con un valor que cambia en el tiempo vive acá como `ReglaFechada`, con su fuente y
fecha de consulta. Ningún otro módulo repite esos valores.
"""

from dataclasses import dataclass
from datetime import date

from domain.fiscal import tablas_arca
from excepciones import FiscalizacionBloqueadaError

CONDICIONES_IVA_EMISOR = {
    "RESPONSABLE_INSCRIPTO": "Responsable inscripto",
    "MONOTRIBUTO": "Monotributo",
    "EXENTO": "Exento",
}

HABILITACIONES_CLASE_A = {
    "NORMAL": "Clase A habitual",
    "OPERACION_SUJETA_A_RETENCION": "Clase A con leyenda «OPERACIÓN SUJETA A RETENCIÓN»",
    "PAGO_EN_CBU_INFORMADA": "Clase A con leyenda «PAGO EN CBU INFORMADA»",
}


@dataclass(frozen=True)
class ReglaFechada:
    """Un valor normativo vigente desde `vigente_desde` (inclusive) hasta la regla siguiente."""

    vigente_desde: date
    valor: int
    fuente: str


# Importe (en centavos) a partir del cual una operación con consumidor final exige identificarlo
# (CUIT/CUIL/CDI o DNI). Fuente: RG ARCA 5700/2025 (B.O. 27/05/2025), que lo fija en $10.000.000;
# confirmado en https://www.afip.gob.ar/fe/emision-autorizacion/datos-comprobantes.asp ("igual o
# superior a $10.000.000"), consultado el 01/11/2026.
UMBRAL_IDENTIFICACION_CONSUMIDOR_FINAL = (
    ReglaFechada(
        vigente_desde=date(2025, 5, 27),
        valor=1_000_000_000,
        fuente="RG ARCA 5700/2025 (B.O. 27/05/2025); consultado 01/11/2026",
    ),
)


def regla_vigente(reglas: tuple[ReglaFechada, ...], fecha: date) -> ReglaFechada:
    """La regla aplicable a `fecha`.

    Raises:
        FiscalizacionBloqueadaError: si `fecha` es anterior a toda regla conocida (Twinx no adivina).
    """
    aplicables = [regla for regla in reglas if regla.vigente_desde <= fecha]
    if not aplicables:
        raise FiscalizacionBloqueadaError(f"No hay una regla fiscal conocida para la fecha {fecha.isoformat()}.")
    return max(aplicables, key=lambda regla: regla.vigente_desde)


def consumidor_final_requiere_identificacion(importe_total_centavos: int, fecha: date) -> bool:
    """`True` si la operación iguala o supera el umbral vigente para identificar al consumidor final."""
    return importe_total_centavos >= regla_vigente(UMBRAL_IDENTIFICACION_CONSUMIDOR_FINAL, fecha).valor


def resolver_clase(condicion_emisor: str, condicion_receptor: str, habilitacion_clase_a: str = "NORMAL") -> str:
    """Clase del comprobante (A, B o C): la decide la condición de emisor y receptor, nunca el operador.

    - Emisor monotributista o exento: siempre C.
    - Emisor responsable inscripto: A para receptores inscriptos o monotributistas, B para consumidor
      final o exento.

    La ex clase M ya no existe (RG ARCA 5762/2025, vigente desde 01/12/2025): la reemplaza la clase A con
    leyenda «OPERACIÓN SUJETA A RETENCIÓN» o «PAGO EN CBU INFORMADA». Twinx V1.16 no emite esa variante:
    si el emisor está habilitado así, se bloquea en vez de emitir una A común (nunca se convierte en silencio).

    Raises:
        FiscalizacionBloqueadaError: condición desconocida o clase A con leyenda.
    """
    if condicion_emisor not in CONDICIONES_IVA_EMISOR:
        raise FiscalizacionBloqueadaError(f"Condición frente al IVA del emisor desconocida: {condicion_emisor!r}.")
    if condicion_receptor not in tablas_arca.CONDICION_IVA_RECEPTOR:
        raise FiscalizacionBloqueadaError(f"Condición frente al IVA del receptor desconocida: {condicion_receptor!r}.")
    if condicion_emisor in ("MONOTRIBUTO", "EXENTO"):
        return "C"
    if condicion_receptor in ("RESPONSABLE_INSCRIPTO", "MONOTRIBUTO"):
        if habilitacion_clase_a != "NORMAL":
            raise FiscalizacionBloqueadaError(
                f"ARCA habilitó al emisor para {HABILITACIONES_CLASE_A.get(habilitacion_clase_a, habilitacion_clase_a)}"
                " (RG 5762/2025, ex clase M). Twinx V1.16 no emite esa clase: emití el comprobante por otro medio"
                " habilitado por ARCA."
            )
        return "A"
    return "B"


def tipo_comprobante(clase: str, rol: str) -> int:
    """Código ARCA del comprobante para la clase y el rol (factura o nota)."""
    return tablas_arca.TIPO_POR_CLASE_Y_ROL[(clase, rol)]
