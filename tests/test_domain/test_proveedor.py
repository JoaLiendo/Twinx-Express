"""Pruebas de los invariantes de negocio de domain.proveedor (Fase 4A).

No dependen de base de datos: validan que la entidad se autovalide al
construirse (mismo enfoque que tests/test_domain/test_categoria.py).
"""

import pytest

from domain.proveedor import Proveedor
from excepciones import DatosInvalidosError


def test_proveedor_valido_se_crea_sin_error():
    proveedor = Proveedor(nombre="Distribuidora SA")
    assert proveedor.nombre == "Distribuidora SA"
    assert proveedor.activo is True


def test_proveedor_puede_crearse_inactivo():
    assert Proveedor(nombre="Distribuidora SA", activo=False).activo is False


def test_campos_de_contacto_son_opcionales():
    proveedor = Proveedor(nombre="Distribuidora SA")
    assert proveedor.contacto_nombre is None
    assert proveedor.telefono is None
    assert proveedor.email is None
    assert proveedor.direccion is None
    assert proveedor.notas is None


def test_proveedor_admite_datos_de_contacto_completos():
    proveedor = Proveedor(
        nombre="Distribuidora SA",
        contacto_nombre="Juan Pérez",
        telefono="1122334455",
        email="ventas@distribuidora.com",
        direccion="Av. Siempre Viva 742",
        notas="Entrega los martes",
    )
    assert proveedor.contacto_nombre == "Juan Pérez"
    assert proveedor.telefono == "1122334455"
    assert proveedor.email == "ventas@distribuidora.com"
    assert proveedor.direccion == "Av. Siempre Viva 742"
    assert proveedor.notas == "Entrega los martes"


def test_rechaza_nombre_vacio():
    with pytest.raises(DatosInvalidosError):
        Proveedor(nombre="")


def test_rechaza_nombre_solo_espacios():
    with pytest.raises(DatosInvalidosError):
        Proveedor(nombre="   ")
