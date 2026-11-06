"""V1.17: la política de contraseñas vista desde los cuatro formularios reales."""

from urllib.parse import unquote

import pytest

from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth, servicio_usuarios

from ._asgi_cliente import solicitud

_CLAVE = "clave-correcta-123"
_CORTA = "corta-123"
_MENSAJE = "al menos 10 caracteres"


def _crear(nombre: str, rol: str) -> Usuario:
    return repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre,
            nombre_completo=f"Nombre {nombre}",
            password_hash=servicio_auth.hashear_password(_CLAVE, iteraciones=1000),
            rol=rol,
        )
    )


def _cookies(nombre: str) -> dict[str, str]:
    return {NOMBRE_COOKIE_SESION: servicio_auth.iniciar_sesion(nombre, _CLAVE).token}


@pytest.fixture
def owner(base_datos_temporal):
    usuario = _crear("duenio", "OWNER")
    return usuario, _cookies("duenio")


def test_primer_owner_con_clave_corta_ve_el_motivo_sin_que_se_repita_la_clave(base_datos_temporal):
    respuesta = solicitud(
        "POST", "/configuracion-inicial",
        formulario={"nombre_completo": "Dueño", "nombre_usuario": "dueno", "password": _CORTA},
    )

    assert respuesta.status == 200
    assert _MENSAJE in respuesta.texto
    assert _CORTA not in respuesta.texto
    assert servicio_usuarios.hay_algun_owner_activo() is False


def test_alta_de_empleado_con_clave_corta_avisa_y_no_crea(owner):
    respuesta = solicitud(
        "POST", "/empleados/nuevo", cookies=owner[1],
        formulario={"nombre_usuario": "carlos", "nombre_completo": "Carlos", "password": _CORTA, "rol": "CASHIER"},
    )

    assert respuesta.status == 303
    assert _MENSAJE in unquote(respuesta.header("location"))
    assert _CORTA not in unquote(respuesta.header("location"))
    assert [u.nombre_usuario for u in servicio_usuarios.listar_todos()] == ["duenio"]


def test_reset_con_clave_corta_avisa_y_conserva_hash_y_sesion(owner):
    cajero = _crear("cajero", "CASHIER")
    token = servicio_auth.iniciar_sesion("cajero", _CLAVE).token

    respuesta = solicitud(
        "POST", f"/empleados/{cajero.id}/resetear-password", cookies=owner[1], formulario={"password_nueva": _CORTA}
    )

    assert respuesta.status == 303
    assert _MENSAJE in unquote(respuesta.header("location"))
    assert repositorio_usuarios.obtener_por_id(cajero.id).password_hash == cajero.password_hash
    assert servicio_auth.obtener_usuario_de_token(token) is not None


def test_cambio_propio_con_clave_corta_no_cierra_la_sesion(owner):
    usuario, cookies = owner

    respuesta = solicitud(
        "POST", "/mi-cuenta/password", cookies=cookies, formulario={"password_actual": _CLAVE, "password_nueva": _CORTA}
    )

    assert respuesta.status == 303
    assert "password_cambiada" not in respuesta.header("location")
    assert _MENSAJE in unquote(respuesta.header("location"))
    assert repositorio_usuarios.obtener_por_id(usuario.id).password_hash == usuario.password_hash
    assert servicio_auth.obtener_usuario_de_token(cookies[NOMBRE_COOKIE_SESION]) is not None


@pytest.mark.parametrize("ruta", ["/empleados/nuevo", "/mi-cuenta/password"])
def test_los_formularios_anuncian_la_regla(owner, ruta):
    texto = solicitud("GET", ruta, cookies=owner[1]).texto

    assert 'minlength="10"' in texto
    assert 'maxlength="128"' in texto
    assert "Mínimo 10 caracteres" in texto


def test_el_formulario_de_reset_anuncia_la_regla(owner):
    cajero = _crear("cajero", "CASHIER")

    texto = solicitud("GET", f"/empleados/{cajero.id}/editar", cookies=owner[1]).texto

    assert 'minlength="10"' in texto
    assert "Mínimo 10 caracteres" in texto


def test_el_formulario_del_primer_owner_anuncia_la_regla(base_datos_temporal):
    texto = solicitud("GET", "/configuracion-inicial").texto

    assert 'minlength="10"' in texto
    assert "Mínimo 10 caracteres" in texto
