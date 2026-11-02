"""Clasificación de cada producto respecto de los «Otros Impuestos Nacionales Indirectos» (V1.16-H).

El Régimen de Transparencia Fiscal al Consumidor (Ley 27.743, RG ARCA/AFIP 5614/2024) exige mostrar, junto
al IVA Contenido, el importe de los otros impuestos nacionales indirectos que inciden en el precio. Twinx
no calcula ninguno de esos impuestos (internos, específicos por unidad, etc.): por eso no afirma un valor
que no conoce. Cada producto queda en uno de tres estados:

- SIN_CLASIFICAR: nadie lo revisó (todo producto histórico nace así). Bloquea donde rige la transparencia.
- NO_APLICA: el dueño confirmó que no lleva otros impuestos nacionales indirectos. Aporta $ 0,00.
- REQUIERE_TRATAMIENTO: está alcanzado por un impuesto que Twinx todavía no sabe calcular. Bloquea siempre:
  nunca se lo reemplaza por cero.

Dos causas de bloqueo distintas (ver `a_exigir`):

A. Transparencia: la RG 5614/2024 exige el importe «de tratarse de las operaciones detalladas en el
   artículo 99 de la Ley 27.743», o sea operaciones con consumidores finales: la letra del comprobante no
   alcanza. Clases A y B conservan la regla de V1.16-H (todo producto confirmado); la clase C la aplica solo
   si el receptor es consumidor final, porque un emisor C factura también a inscriptos, monotributistas y
   exentos.
B. Cálculo: un producto REQUIERE_TRATAMIENTO lleva un tributo que Twinx no calcula, así que no puede afirmar
   que `ImpTrib` sea 0 ni que el total sea todo neto. Bloquea con cualquier receptor.
"""

from collections.abc import Iterable

from excepciones import DatosInvalidosError, FiscalizacionBloqueadaError

SIN_CLASIFICAR = "SIN_CLASIFICAR"
NO_APLICA = "NO_APLICA"
REQUIERE_TRATAMIENTO = "REQUIERE_TRATAMIENTO"

CLASIFICACIONES = {
    SIN_CLASIFICAR: "Sin clasificar",
    NO_APLICA: "No aplica",
    REQUIERE_TRATAMIENTO: "Requiere tratamiento especial",
}

ETIQUETA_DOCUMENTO = "Otros Impuestos Nacionales Indirectos"

MENSAJE_REQUIERE_TRATAMIENTO = (
    "Este producto requiere tratamiento de impuestos nacionales indirectos todavía no configurado."
)


def validar_clasificacion(valor: str) -> str:
    """Raises: DatosInvalidosError si `valor` no es una de las tres clasificaciones."""
    if valor not in CLASIFICACIONES:
        raise DatosInvalidosError(f"Clasificación de otros impuestos nacionales indirectos inválida: {valor!r}.")
    return valor


def aplica_transparencia(clase: str, receptor_consumidor_final: bool) -> bool:
    """`True` si el comprobante debe conocer los otros impuestos nacionales indirectos de todos sus productos
    por el régimen de transparencia (causa A del docstring del módulo)."""
    return clase != "C" or receptor_consumidor_final


def a_exigir(
    productos: Iterable[tuple[str, str]], clase: str, receptor_consumidor_final: bool
) -> list[tuple[str, str]]:
    """Los productos (`(nombre, clasificación)`) que deben estar resueltos para fiscalizar: todos si rige la
    transparencia; si no, solo los REQUIERE_TRATAMIENTO (causa B). Fuera de la transparencia SIN_CLASIFICAR no
    bloquea: ninguna otra obligación exige ese importe y Twinx informa `ImpTrib` 0 sin `Tributos`."""
    productos = list(productos)
    if aplica_transparencia(clase, receptor_consumidor_final):
        return productos
    return [(nombre, clasificacion) for nombre, clasificacion in productos if clasificacion == REQUIERE_TRATAMIENTO]


def motivos_de_bloqueo(productos: Iterable[tuple[str, str]]) -> list[str]:
    """Un motivo por producto (`(nombre, clasificación)`) que impide fiscalizar, sin repetir nombres."""
    sin_clasificar = sorted({nombre for nombre, clasificacion in productos if clasificacion == SIN_CLASIFICAR})
    especiales = sorted({nombre for nombre, clasificacion in productos if clasificacion == REQUIERE_TRATAMIENTO})
    return [
        f"Clasificá los otros impuestos nacionales indirectos de «{nombre}»." for nombre in sin_clasificar
    ] + [f"«{nombre}»: {MENSAJE_REQUIERE_TRATAMIENTO}" for nombre in especiales]


def exigir_conocidos(productos: Iterable[tuple[str, str]]) -> None:
    """Bloquea si algún producto no está confirmado como NO_APLICA.

    Raises:
        FiscalizacionBloqueadaError: con un motivo por producto pendiente o que requiere tratamiento.
    """
    productos = list(productos)
    motivos = motivos_de_bloqueo(productos)
    if not motivos:
        return
    if any(clasificacion == REQUIERE_TRATAMIENTO for _, clasificacion in productos):
        mensaje = "Hay productos con impuestos nacionales indirectos que Twinx todavía no calcula: no se fiscaliza."
    else:
        mensaje = "Hay productos sin clasificar sus otros impuestos nacionales indirectos: clasificalos antes de fiscalizar."
    raise FiscalizacionBloqueadaError(mensaje, motivos)


def importe_centavos(clasificaciones: Iterable[str]) -> int | None:
    """Importe de otros impuestos nacionales indirectos de un conjunto de líneas: 0 si todas son NO_APLICA;
    `None` si alguna no lo es (comprobante histórico anterior a V1.16-H: Twinx no afirma un valor)."""
    clasificaciones = list(clasificaciones)
    if clasificaciones and all(clasificacion == NO_APLICA for clasificacion in clasificaciones):
        return 0
    return None
