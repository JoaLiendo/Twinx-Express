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

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

from excepciones import DatosInvalidosError

CENTAVOS_POR_UNIDAD = 100

# Cota técnica, no comercial: SQLite guarda enteros de 64 bits. Un valor mayor no se puede
# persistir (`OverflowError`), así que se rechaza como dato inválido antes de llegar a la base.
# Rige para montos en centavos (en valor absoluto), cantidades y stock.
MAXIMO_ENTERO = 2**63 - 1

# Un monto con más de 20 dígitos enteros (`adjusted() > 19`) supera `MAXIMO_ENTERO` centavos con
# holgura: se descarta sin operar con él, porque un exponente enorme (`1E+9999999`) haría fallar
# la cuantización. Los montos menores se validan de forma exacta, después de redondear.
_EXPONENTE_MAXIMO_PREVIO = 19


def texto_a_centavos(texto: str) -> int:
    """Convierte un texto decimal (ej. "150.5", "150,50") a centavos (15050).

    Redondea al centavo más cercano (mitad hacia arriba) de forma
    explícita con `Decimal`, en vez de heredar el redondeo implícito
    de un `float`.

    Raises:
        DatosInvalidosError: si el texto no es un número, no es finito (`NaN`, `sNaN`, `Infinity`)
            o el resultado en centavos supera `MAXIMO_ENTERO` en valor absoluto. El signo no se
            valida acá: cada campo decide si admite negativos.
    """
    texto_normalizado = texto.strip().replace(",", ".")
    try:
        valor = Decimal(texto_normalizado)
    except InvalidOperation as error:
        raise DatosInvalidosError(f"Monto inválido: {texto[:40]!r}.") from error
    if not valor.is_finite():
        raise DatosInvalidosError(f"Monto inválido: {texto[:40]!r}.")
    if valor != 0 and valor.adjusted() > _EXPONENTE_MAXIMO_PREVIO:
        raise DatosInvalidosError(f"El monto {texto[:40]!r} está fuera del rango admitido.")

    # Precisión suficiente para que `scaleb` y el redondeo sean exactos con cualquier cantidad de dígitos.
    with localcontext() as contexto:
        contexto.prec = len(valor.as_tuple().digits) + 8
        centavos = int(valor.scaleb(2).to_integral_value(rounding=ROUND_HALF_UP))
    if abs(centavos) > MAXIMO_ENTERO:
        raise DatosInvalidosError(f"El monto {texto[:40]!r} está fuera del rango admitido.")
    return centavos


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
