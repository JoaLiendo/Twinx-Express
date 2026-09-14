"""Pruebas de los invariantes de negocio de domain.sesion.

No dependen de base de datos: validan que la entidad se autovalide al
construirse. La lógica de cuándo crear/expirar/invalidar una sesión
(fase 2C) no se prueba acá.
"""

import pytest

from domain.sesion import Sesion
from excepciones import DatosInvalidosError


def _crear(**overrides):
    datos = dict(
        token="a" * 43,
        usuario_id=1,
        fecha_expiracion="2026-09-13 12:00:00",
    )
    datos.update(overrides)
    return Sesion(**datos)


def test_sesion_valida_se_crea_sin_error():
    sesion = _crear()
    assert sesion.usuario_id == 1
    assert sesion.fecha_expiracion == "2026-09-13 12:00:00"


def test_rechaza_token_vacio():
    with pytest.raises(DatosInvalidosError):
        _crear(token="   ")


def test_rechaza_usuario_id_invalido():
    with pytest.raises(DatosInvalidosError):
        _crear(usuario_id=0)


def test_rechaza_usuario_id_negativo():
    with pytest.raises(DatosInvalidosError):
        _crear(usuario_id=-5)


def test_rechaza_fecha_expiracion_vacia():
    with pytest.raises(DatosInvalidosError):
        _crear(fecha_expiracion="")
