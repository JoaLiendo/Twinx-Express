"""V1.5-A: `texto_a_centavos` rechaza de forma controlada lo no finito y lo fuera de rango, y conserva el
comportamiento de siempre para los valores válidos. El límite es el que ya usaba el proyecto para productos
(`MAXIMO_ENTERO`, 64 bits de SQLite), ahora definido en un único lugar."""

import pytest

from domain import dinero, producto
from domain.dinero import MAXIMO_ENTERO, texto_a_centavos
from excepciones import DatosInvalidosError

MAXIMO_TEXTO = "92233720368547758.07"  # MAXIMO_ENTERO centavos


def test_el_limite_es_el_de_siempre_y_esta_definido_en_un_solo_lugar():
    assert MAXIMO_ENTERO == 2**63 - 1
    assert producto.MAXIMO_ENTERO is dinero.MAXIMO_ENTERO


@pytest.mark.parametrize(
    "texto",
    ["NaN", "nan", "-NaN", "sNaN", "-sNaN", "Infinity", "inf", "-Infinity", "-inf", "  NaN  "],
)
def test_los_valores_no_finitos_se_rechazan(texto):
    with pytest.raises(DatosInvalidosError, match="Monto inválido"):
        texto_a_centavos(texto)


@pytest.mark.parametrize(
    "texto",
    ["1E+9999999", "-1E+9999999", "1e19", "1e400", "9" * 30, "1" + "0" * 400, "1e20", "9.9e18"],
)
def test_los_exponentes_y_magnitudes_enormes_se_rechazan_sin_fallar_al_cuantizar(texto):
    with pytest.raises(DatosInvalidosError, match="fuera del rango"):
        texto_a_centavos(texto)


def test_frontera_maxima_exacta_acepta_y_una_unidad_minima_mas_rechaza():
    assert texto_a_centavos(MAXIMO_TEXTO) == MAXIMO_ENTERO
    for texto in ("92233720368547758.08", "92233720368547758.075", "92233720368547759", "92233720368547758,08"):
        with pytest.raises(DatosInvalidosError, match="fuera del rango"):
            texto_a_centavos(texto)


def test_el_redondeo_se_aplica_antes_de_validar_el_limite():
    assert texto_a_centavos("92233720368547758.074") == MAXIMO_ENTERO  # redondea hacia abajo al máximo
    with pytest.raises(DatosInvalidosError):
        texto_a_centavos("92233720368547758.075")  # redondea hacia arriba: supera el máximo


def test_la_frontera_negativa_es_simetrica_y_el_signo_no_se_valida_aca():
    assert texto_a_centavos("-" + MAXIMO_TEXTO) == -MAXIMO_ENTERO
    assert texto_a_centavos("-5") == -500  # cada campo decide si admite negativos
    for texto in ("-92233720368547758.08", "-1E+9999999", "-1e19"):
        with pytest.raises(DatosInvalidosError):
            texto_a_centavos(texto)


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("0", 0),
        ("0,00", 0),
        ("0.004", 0),
        ("0.005", 1),
        ("0.01", 1),
        ("150", 15000),
        ("150.5", 15050),
        ("150,50", 15050),
        ("  1.999  ", 200),
        ("1e2", 10000),
        ("1E-2", 1),
        ("1e-9999999", 0),  # exponente negativo enorme: es 0, no un error
        ("0." + "1" * 500, 11),  # cientos de decimales: redondea sin perder exactitud
        ("1234567.89", 123456789),
        ("1234567.8949999999999999999999999999999", 123456789),  # el redondeo es exacto con cualquier cantidad de dígitos
    ],
)
def test_los_valores_validos_conservan_su_comportamiento(texto, esperado):
    assert texto_a_centavos(texto) == esperado


@pytest.mark.parametrize("texto", ["", "   ", "abc", "1.2.3", "12abc", "--5", "1,2,3"])
def test_texto_vacio_o_mal_formado_sigue_siendo_un_error_controlado(texto):
    with pytest.raises(DatosInvalidosError, match="Monto inválido"):
        texto_a_centavos(texto)


def test_el_mensaje_no_repite_un_texto_gigante():
    with pytest.raises(DatosInvalidosError) as error:
        texto_a_centavos("9" * 5000)

    assert len(str(error.value)) < 200
