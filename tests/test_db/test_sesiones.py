"""Pruebas de integración de db.repositorios.sesiones contra una base de
datos SQLite real (temporal y aislada, ver tests/conftest.py).

Fase 2B: solo prueban persistencia (crear/leer una sesión y la
integridad referencial con `usuarios`). La lógica de cuándo crear,
expirar o invalidar una sesión en el login/logout es de fase 2C y no
se prueba acá.
"""

import pytest

from db.repositorios import sesiones as repositorio_sesiones
from db.repositorios import usuarios as repositorio_usuarios
from domain.sesion import Sesion
from domain.usuario import Usuario
from excepciones import ErrorBaseDatos


def _usuario_persistido(base_datos_temporal, **overrides):
    datos = dict(
        nombre_usuario="ana",
        nombre_completo="Ana Owner",
        password_hash="pbkdf2_sha256$600000$" + "a" * 32 + "$" + "b" * 64,
        rol="OWNER",
    )
    datos.update(overrides)
    return repositorio_usuarios.crear_usuario(Usuario(**datos))


def _sesion(usuario_id, **overrides):
    datos = dict(
        token="token-" + "x" * 40,
        usuario_id=usuario_id,
        fecha_expiracion="2026-12-31 23:59:59",
    )
    datos.update(overrides)
    return Sesion(**datos)


def test_crear_sesion_persiste_y_devuelve_fecha_creacion(base_datos_temporal):
    usuario = _usuario_persistido(base_datos_temporal)

    creada = repositorio_sesiones.crear_sesion(_sesion(usuario.id))

    assert creada.token == "token-" + "x" * 40
    assert creada.usuario_id == usuario.id
    assert creada.fecha_creacion is not None


def test_obtener_por_token_devuelve_sesion_existente(base_datos_temporal):
    usuario = _usuario_persistido(base_datos_temporal)
    repositorio_sesiones.crear_sesion(_sesion(usuario.id, token="token-abc"))

    encontrada = repositorio_sesiones.obtener_por_token("token-abc")

    assert encontrada is not None
    assert encontrada.usuario_id == usuario.id
    assert encontrada.fecha_expiracion == "2026-12-31 23:59:59"


def test_obtener_por_token_inexistente_devuelve_none(base_datos_temporal):
    assert repositorio_sesiones.obtener_por_token("no-existe") is None


def test_sesion_requiere_usuario_existente(base_datos_temporal):
    """Integridad referencial: no se puede crear una sesión para un
    usuario_id que no existe (FK con `usuarios`, ver migración 003)."""
    with pytest.raises(ErrorBaseDatos):
        repositorio_sesiones.crear_sesion(_sesion(usuario_id=9999))
