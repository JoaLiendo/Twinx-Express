"""Pruebas de interfaces.web.utilidades.contexto_base (Fase 2D).

`contexto_base` ahora recibe `Request`, resuelve el usuario actual una
sola vez y expone la navegación ya filtrada por su rol. No usa
`httpx`/`TestClient`: un `Request` se construye directo (ver
tests/test_interfaces_web/test_auth.py, mismo enfoque).
"""

from starlette.requests import Request

from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from interfaces.web.navegacion import NAV_ITEMS
from interfaces.web.utilidades import contexto_base
from services import servicio_auth


def _request_con_cookie(token: str | None) -> Request:
    headers = []
    if token is not None:
        headers = [(b"cookie", f"{NOMBRE_COOKIE_SESION}={token}".encode())]
    return Request({"type": "http", "headers": headers})


def _crear_usuario_y_loguearse(rol: str, nombre_usuario: str) -> str:
    repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre_usuario,
            nombre_completo="Usuario de prueba",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol=rol,
        )
    )
    return servicio_auth.iniciar_sesion(nombre_usuario, "clave-correcta-123").token


def _hrefs(items):
    return {item["href"] for item in items}


def test_sin_sesion_usuario_actual_es_none_y_nav_completa(base_datos_temporal):
    contexto = contexto_base(_request_con_cookie(None))

    assert contexto["usuario_actual"] is None
    assert _hrefs(contexto["nav_items"]) == _hrefs(NAV_ITEMS)
    assert "caja_abierta" in contexto


def test_con_sesion_owner_expone_el_usuario_y_nav_completa(base_datos_temporal):
    token = _crear_usuario_y_loguearse("OWNER", "ana")

    contexto = contexto_base(_request_con_cookie(token))

    assert contexto["usuario_actual"] is not None
    assert contexto["usuario_actual"].rol == "OWNER"
    assert _hrefs(contexto["nav_items"]) == _hrefs(NAV_ITEMS)


def test_con_sesion_cashier_expone_el_usuario_y_nav_restringida(base_datos_temporal):
    token = _crear_usuario_y_loguearse("CASHIER", "carlos")

    contexto = contexto_base(_request_con_cookie(token))

    assert contexto["usuario_actual"].rol == "CASHIER"
    assert _hrefs(contexto["nav_items"]) == {"/", "/ventas", "/caja", "/productos"}
