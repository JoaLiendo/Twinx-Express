"""Pruebas de los invariantes de negocio de domain.categoria.

No dependen de base de datos: validan que la entidad se autovalide al
construirse (mismo enfoque que tests/test_dominio.py).
"""

import pytest

from domain.categoria import Categoria
from excepciones import DatosInvalidosError


def test_categoria_valida_se_crea_sin_error():
    categoria = Categoria(nombre="Bebidas")
    assert categoria.nombre == "Bebidas"
    assert categoria.activa is True


def test_categoria_puede_crearse_inactiva():
    assert Categoria(nombre="Bebidas", activa=False).activa is False


def test_rechaza_nombre_vacio():
    with pytest.raises(DatosInvalidosError):
        Categoria(nombre="")


def test_rechaza_nombre_solo_espacios():
    with pytest.raises(DatosInvalidosError):
        Categoria(nombre="   ")
