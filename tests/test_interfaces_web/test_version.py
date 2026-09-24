"""Identidad de versión de Twinx Express (fuente única: `version.py`)."""

import re
import runpy

from config import RAIZ_PROYECTO
from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth
from version import NOMBRE_APLICACION, VERSION

from ._asgi_cliente import solicitud


def _cookies_de_dueno() -> dict[str, str]:
    repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario="ana",
            nombre_completo="Ana",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol="OWNER",
        )
    )
    return {NOMBRE_COOKIE_SESION: servicio_auth.iniciar_sesion("ana", "clave-correcta-123").token}


def test_la_version_definida_es_1_3_0():
    assert VERSION == "1.3.0"
    assert NOMBRE_APLICACION == "Twinx Express"
    assert re.fullmatch(r"\d+\.\d+\.\d+", VERSION)


def test_el_leeme_del_entregable_declara_la_misma_version():
    leeme = (RAIZ_PROYECTO / "LEEME.txt").read_text(encoding="utf-8")
    assert f"{NOMBRE_APLICACION} {VERSION}" in leeme


def test_version_py_se_puede_leer_sin_importar_el_proyecto():
    """`KioscoApp.spec` la lee así: no debe depender de `config.py` (crea directorios al importarse)."""
    datos = runpy.run_path(str(RAIZ_PROYECTO / "version.py"))
    assert datos["VERSION"] == VERSION


def test_el_login_muestra_la_version(base_datos_temporal):
    _cookies_de_dueno()  # con al menos un OWNER, /login no redirige a /configuracion-inicial

    respuesta = solicitud("GET", "/login")

    assert respuesta.status == 200
    assert f"v{VERSION}" in respuesta.texto


def test_la_interfaz_autenticada_muestra_la_version(base_datos_temporal):
    respuesta = solicitud("GET", "/productos", cookies=_cookies_de_dueno())

    assert respuesta.status == 200
    assert f"v{VERSION}" in respuesta.texto
