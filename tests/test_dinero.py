"""Pruebas de domain.dinero: conversión exacta entre texto decimal y centavos.

No dependen de base de datos: son pruebas unitarias puras.
"""

import pytest

from domain.dinero import (
    centavos_a_texto,
    centavos_a_texto_localizado,
    texto_a_centavos,
)
from excepciones import DatosInvalidosError


class TestTextoACentavos:
    @pytest.mark.parametrize(
        "texto, centavos_esperados",
        [
            ("150.50", 15050),
            ("150,50", 15050),  # coma como separador decimal
            ("0.10", 10),
            ("0.01", 1),
            ("0", 0),
            ("10", 1000),
            ("  99.99  ", 9999),  # espacios alrededor
            ("1234567.89", 123456789),
        ],
    )
    def test_convierte_texto_valido_a_centavos_exactos(self, texto, centavos_esperados):
        assert texto_a_centavos(texto) == centavos_esperados

    def test_redondea_al_centavo_mas_cercano_hacia_arriba(self):
        assert texto_a_centavos("150.505") == 15051

    def test_no_acumula_error_de_precision_binaria(self):
        # El caso clásico que rompe con float: 0.1 + 0.2 != 0.3
        total_centavos = texto_a_centavos("0.10") + texto_a_centavos("0.20")
        assert total_centavos == texto_a_centavos("0.30")

    @pytest.mark.parametrize("texto_invalido", ["", "   ", "abc", "12.34.56", "$100"])
    def test_rechaza_texto_invalido(self, texto_invalido):
        with pytest.raises(DatosInvalidosError):
            texto_a_centavos(texto_invalido)


class TestCentavosATexto:
    @pytest.mark.parametrize(
        "centavos, texto_esperado",
        [
            (15050, "150.50"),
            (10, "0.10"),
            (1, "0.01"),
            (0, "0.00"),
            (100, "1.00"),
            (-1550, "-15.50"),
        ],
    )
    def test_convierte_centavos_a_texto(self, centavos, texto_esperado):
        assert centavos_a_texto(centavos) == texto_esperado

    @pytest.mark.parametrize("texto", ["0.00", "1.00", "150.50", "9999.99"])
    def test_es_inverso_de_texto_a_centavos(self, texto):
        assert centavos_a_texto(texto_a_centavos(texto)) == texto


class TestCentavosATextoLocalizado:
    @pytest.mark.parametrize(
        "centavos, texto_esperado",
        [
            (170000, "1.700,00"),
            (15050, "150,50"),
            (10, "0,10"),
            (0, "0,00"),
            (100, "1,00"),
            (123456789, "1.234.567,89"),
            (-1550, "-15,50"),
        ],
    )
    def test_convierte_centavos_a_texto_con_formato_argentino(self, centavos, texto_esperado):
        assert centavos_a_texto_localizado(centavos) == texto_esperado
