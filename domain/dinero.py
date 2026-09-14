"""Utilidades para trabajar con dinero como enteros en centavos.

Todo el dominio y la persistencia manejan montos de dinero como `int`
en centavos (nunca `float`): un `float` acumula errores de precisión
binaria en operaciones financieras (ej. `0.1 + 0.2 != 0.3`), algo
inaceptable para precios, totales de venta o movimientos de caja.

Este módulo concentra la única conversión permitida entre esa
representación interna (centavos) y el texto decimal que ve un
usuario (ej. "150.50"). Se usa `Decimal` para esa conversión puntual,
nunca `float`.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from excepciones import DatosInvalidosError

CENTAVOS_POR_UNIDAD = 100


def texto_a_centavos(texto: str) -> int:
    """Convierte un texto decimal (ej. "150.5", "150,50") a centavos (15050).

    Redondea al centavo más cercano (mitad hacia arriba) de forma
    explícita con `Decimal`, en vez de heredar el redondeo implícito
    de un `float`.
    """
    texto_normalizado = texto.strip().replace(",", ".")
    try:
        valor = Decimal(texto_normalizado)
    except InvalidOperation as error:
        raise DatosInvalidosError(f"Monto inválido: {texto!r}.") from error

    centavos = (valor * CENTAVOS_POR_UNIDAD).to_integral_value(rounding=ROUND_HALF_UP)
    return int(centavos)


def centavos_a_texto(centavos: int) -> str:
    """Convierte centavos (15050) a texto decimal con dos decimales ("150.50")."""
    signo = "-" if centavos < 0 else ""
    centavos_absolutos = abs(centavos)
    unidades, resto = divmod(centavos_absolutos, CENTAVOS_POR_UNIDAD)
    return f"{signo}{unidades}.{resto:02d}"


def centavos_a_texto_localizado(centavos: int) -> str:
    """Como `centavos_a_texto`, pero con separador de miles ('.') y coma
    decimal (',') para presentación en pantalla (ej. 150000 -> "1.500,00").

    Reutiliza `centavos_a_texto` (nunca reimplementa la conversión):
    solo reformatea el mismo texto para lectura. Por eso mismo, este
    formato es exclusivamente de solo lectura -- nunca usarlo como
    `value=` de un `<input>` editable, porque `texto_a_centavos` no
    puede volver a parsearlo (ver `interfaces.web.plantillas`, filtro
    `dinero_ar` vs. `dinero`).
    """
    texto = centavos_a_texto(centavos)
    signo = ""
    if texto.startswith("-"):
        signo = "-"
        texto = texto[1:]
    entero, _, decimales = texto.partition(".")
    entero_con_miles = f"{int(entero):,}".replace(",", ".")
    return f"{signo}{entero_con_miles},{decimales}"
