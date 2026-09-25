"""V1.5-A: el porcentaje de una actualización masiva rechaza valores no finitos y exponentes gigantes de forma
controlada (antes, `1E+9999999` desbordaba `Decimal` y terminaba en un error del servidor)."""

import pytest

from domain.precios_masivos import PORCENTAJE_MAXIMO_BASIS_POINTS, porcentaje_a_basis_points
from excepciones import DatosInvalidosError


@pytest.mark.parametrize(
    "texto", ["NaN", "sNaN", "Infinity", "-Infinity", "1E+9999999", "1e400", "1000.01", "1001", "9" * 40, "0", "-5", "", "abc"]
)
def test_porcentajes_hostiles_o_fuera_de_rango_se_rechazan_de_forma_controlada(texto):
    with pytest.raises(DatosInvalidosError):
        porcentaje_a_basis_points(texto)


def test_frontera_del_porcentaje_y_valores_validos_no_cambian():
    assert porcentaje_a_basis_points("1000") == PORCENTAJE_MAXIMO_BASIS_POINTS
    assert porcentaje_a_basis_points("10") == 1000
    assert porcentaje_a_basis_points("10,25") == 1025
    assert porcentaje_a_basis_points("0.01") == 1
    with pytest.raises(DatosInvalidosError, match="2 decimales"):
        porcentaje_a_basis_points("10.001")
    with pytest.raises(DatosInvalidosError, match="mayor a cero"):
        porcentaje_a_basis_points("1E-9999999")  # exponente negativo enorme: se redondea a 0, no es un porcentaje
