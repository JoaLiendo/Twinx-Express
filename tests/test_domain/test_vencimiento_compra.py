"""V1.10-C: regla pura de la fecha de vencimiento de una compra (sin base de datos)."""

import pytest

from domain.compra import validar_fecha_vencimiento
from excepciones import DatosInvalidosError

COMPRA = "2026-03-10 14:30:00"


def test_contado_sin_vencimiento_es_valido():
    validar_fecha_vencimiento("CONTADO", COMPRA, None)


def test_contado_con_vencimiento_se_rechaza():
    with pytest.raises(DatosInvalidosError, match="contado"):
        validar_fecha_vencimiento("CONTADO", COMPRA, "2026-04-10")


def test_credito_sin_vencimiento_es_valido():
    validar_fecha_vencimiento("CREDITO", COMPRA, None)


def test_credito_con_vencimiento_el_mismo_dia_de_la_compra_es_valido():
    validar_fecha_vencimiento("CREDITO", COMPRA, "2026-03-10")


def test_credito_con_vencimiento_posterior_es_valido():
    validar_fecha_vencimiento("CREDITO", COMPRA, "2026-03-11")


def test_credito_con_vencimiento_anterior_a_la_compra_se_rechaza():
    with pytest.raises(DatosInvalidosError, match="anterior"):
        validar_fecha_vencimiento("CREDITO", COMPRA, "2026-03-09")


def test_la_fecha_de_compra_puede_venir_solo_como_dia():
    validar_fecha_vencimiento("CREDITO", "2026-03-10", "2026-03-10")


@pytest.mark.parametrize("texto", ["2026-02-30", "2026-13-01", "2026-00-10", "2025-02-29"])
def test_fecha_inexistente_se_rechaza(texto):
    with pytest.raises(DatosInvalidosError, match="AAAA-MM-DD"):
        validar_fecha_vencimiento("CREDITO", COMPRA, texto)


@pytest.mark.parametrize(
    "texto", ["", " ", "10/04/2026", "2026-4-1", "20260410", "2026-04-10 ", "2026-04-10T00:00", "abc", "２０２６-０４-１０"]
)
def test_formato_invalido_se_rechaza(texto):
    with pytest.raises(DatosInvalidosError, match="AAAA-MM-DD"):
        validar_fecha_vencimiento("CREDITO", COMPRA, texto)


def test_no_hay_maximo_artificial_de_dias():
    validar_fecha_vencimiento("CREDITO", COMPRA, "2027-03-11")  # > 365 días
    validar_fecha_vencimiento("CREDITO", COMPRA, "9999-12-31")
