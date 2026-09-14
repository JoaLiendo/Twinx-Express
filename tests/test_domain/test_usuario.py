"""Pruebas de los invariantes de negocio de domain.usuario.

No dependen de base de datos: validan que la entidad se autovalide al
construirse, en memoria (mismo enfoque que tests/test_dominio.py).
"""

import pytest

from domain.usuario import Usuario
from excepciones import DatosInvalidosError


def _crear(**overrides):
    datos = dict(
        nombre_usuario="ana",
        nombre_completo="Ana Owner",
        password_hash="pbkdf2_sha256$600000$abc123$def456",
        rol="OWNER",
    )
    datos.update(overrides)
    return Usuario(**datos)


def test_usuario_valido_se_crea_sin_error():
    usuario = _crear()
    assert usuario.nombre_usuario == "ana"
    assert usuario.rol == "OWNER"


def test_usuario_activo_por_defecto():
    usuario = _crear()
    assert usuario.activo is True


def test_usuario_puede_crearse_inactivo():
    usuario = _crear(activo=False)
    assert usuario.activo is False


def test_rechaza_nombre_usuario_vacio():
    with pytest.raises(DatosInvalidosError):
        _crear(nombre_usuario="   ")


def test_rechaza_nombre_completo_vacio():
    with pytest.raises(DatosInvalidosError):
        _crear(nombre_completo="")


def test_rechaza_password_hash_vacio():
    with pytest.raises(DatosInvalidosError):
        _crear(password_hash="")


def test_acepta_rol_owner():
    assert _crear(rol="OWNER").rol == "OWNER"


def test_acepta_rol_cashier():
    assert _crear(rol="CASHIER").rol == "CASHIER"


def test_rechaza_rol_invalido():
    with pytest.raises(DatosInvalidosError):
        _crear(rol="SUPERADMIN")


def test_es_owner_es_true_solo_para_rol_owner():
    assert _crear(rol="OWNER").es_owner is True
    assert _crear(rol="CASHIER").es_owner is False
