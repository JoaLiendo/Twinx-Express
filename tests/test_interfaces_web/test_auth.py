"""Pruebas de interfaces.web.auth (Fase 2D): resolver el usuario
autenticado a partir de un `Request` y la dependencia `requiere_rol`.

No usa `httpx`/`TestClient`: un `starlette.requests.Request` se puede
construir directamente a partir de un scope ASGI mínimo, sin levantar
ningún servidor (ver `_request_con_cookie`). Reutiliza
`services.servicio_auth` para login real contra una base de datos
temporal (ver tests/conftest.py) — no se duplica lógica de sesiones.
"""

import pytest
from starlette.requests import Request

from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from excepciones import NoAutenticadoError, PermisoDenegadoError
from interfaces.web.auth import NOMBRE_COOKIE_SESION, obtener_usuario_actual, requiere_rol
from services import servicio_auth


def _request_con_cookie(token: str | None) -> Request:
    headers = []
    if token is not None:
        headers = [(b"cookie", f"{NOMBRE_COOKIE_SESION}={token}".encode())]
    return Request({"type": "http", "headers": headers})


def _crear_usuario(rol: str, *, activo: bool = True, nombre_usuario: str = "usuario_test"):
    return repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre_usuario,
            nombre_completo="Usuario de prueba",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol=rol,
        )
    )


def _login(nombre_usuario: str) -> str:
    return servicio_auth.iniciar_sesion(nombre_usuario, "clave-correcta-123").token


class TestObtenerUsuarioActual:
    def test_request_sin_cookie_devuelve_none(self, base_datos_temporal):
        request = _request_con_cookie(None)

        assert obtener_usuario_actual(request) is None

    def test_request_con_token_inexistente_devuelve_none(self, base_datos_temporal):
        request = _request_con_cookie("token-que-no-existe")

        assert obtener_usuario_actual(request) is None

    def test_request_con_token_valido_devuelve_el_usuario_correcto(self, base_datos_temporal):
        usuario = _crear_usuario("OWNER")
        token = _login(usuario.nombre_usuario)

        encontrado = obtener_usuario_actual(_request_con_cookie(token))

        assert encontrado is not None
        assert encontrado.id == usuario.id
        assert encontrado.rol == "OWNER"

    def test_usuario_desactivado_no_se_considera_autenticado(self, base_datos_temporal):
        usuario = _crear_usuario("CASHIER")
        token = _login(usuario.nombre_usuario)
        assert obtener_usuario_actual(_request_con_cookie(token)) is not None  # todavía activo

        with obtener_conexion() as conexion:
            conexion.execute("UPDATE usuarios SET activo = 0 WHERE id = ?", (usuario.id,))

        assert obtener_usuario_actual(_request_con_cookie(token)) is None


class TestRequiereRol:
    def test_usuario_owner_pasa_la_dependencia_de_owner(self, base_datos_temporal):
        usuario = _crear_usuario("OWNER", nombre_usuario="ana")
        token = _login(usuario.nombre_usuario)
        dependencia = requiere_rol("OWNER")

        resultado = dependencia(_request_con_cookie(token))

        assert resultado.id == usuario.id

    def test_usuario_cashier_pasa_una_dependencia_que_permite_cashier(self, base_datos_temporal):
        usuario = _crear_usuario("CASHIER", nombre_usuario="carlos")
        token = _login(usuario.nombre_usuario)
        dependencia = requiere_rol("OWNER", "CASHIER")

        resultado = dependencia(_request_con_cookie(token))

        assert resultado.id == usuario.id

    def test_usuario_cashier_es_rechazado_por_dependencia_de_owner(self, base_datos_temporal):
        usuario = _crear_usuario("CASHIER", nombre_usuario="carlos")
        token = _login(usuario.nombre_usuario)
        dependencia = requiere_rol("OWNER")

        with pytest.raises(PermisoDenegadoError):
            dependencia(_request_con_cookie(token))

    def test_sin_autenticar_lanza_no_autenticado_no_permiso_denegado(self, base_datos_temporal):
        dependencia = requiere_rol("OWNER")

        with pytest.raises(NoAutenticadoError):
            dependencia(_request_con_cookie(None))

    def test_usuario_inactivo_es_tratado_como_no_autenticado_por_requiere_rol(
        self, base_datos_temporal
    ):
        usuario = _crear_usuario("OWNER")
        token = _login(usuario.nombre_usuario)
        with obtener_conexion() as conexion:
            conexion.execute("UPDATE usuarios SET activo = 0 WHERE id = ?", (usuario.id,))

        with pytest.raises(NoAutenticadoError):
            requiere_rol("OWNER")(_request_con_cookie(token))
