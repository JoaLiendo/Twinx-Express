"""RC4: cambiar o resetear una contraseña cierra todas las sesiones abiertas de ese usuario (una sesión
ya abierta no puede sobrevivir hasta 12 h a un cambio de credenciales) y queda auditado sin la contraseña."""

import pytest

from db.repositorios import auditoria as repositorio_auditoria
from excepciones import CredencialesInvalidasError
from services import servicio_auth, servicio_usuarios
from tests import utilidades_fiscales as uf

_CLAVE = "clave-correcta-123"


@pytest.fixture
def usuarios(base_datos_temporal):
    return {"owner": uf.crear_owner("duenio"), "cajero": uf.crear_cashier("cajero")}


def _sesion(nombre):
    return servicio_auth.iniciar_sesion(nombre, _CLAVE).token


def test_reset_por_owner_cierra_todas_las_sesiones_del_usuario_y_solo_las_suyas(usuarios):
    del_cajero = [_sesion("cajero"), _sesion("cajero")]
    del_owner = _sesion("duenio")

    servicio_usuarios.resetear_password(usuarios["cajero"], "otra-clave-456", actor_id=usuarios["owner"])

    assert all(servicio_auth.obtener_usuario_de_token(t) is None for t in del_cajero)
    assert servicio_auth.obtener_usuario_de_token(del_owner) is not None
    assert servicio_auth.iniciar_sesion("cajero", "otra-clave-456") is not None
    resumen = repositorio_auditoria.listar(accion="USUARIO_PASSWORD_RESETEADA")[0].resumen
    assert "2 sesión(es) cerrada(s)" in resumen and "otra-clave" not in resumen


def test_cambio_propio_cierra_todas_las_sesiones_incluida_la_actual_y_se_audita(usuarios):
    actual, otra_pc = _sesion("cajero"), _sesion("cajero")
    del_owner = _sesion("duenio")

    servicio_usuarios.cambiar_password_propia(usuarios["cajero"], _CLAVE, "clave-nueva-789")

    assert servicio_auth.obtener_usuario_de_token(actual) is None
    assert servicio_auth.obtener_usuario_de_token(otra_pc) is None
    assert servicio_auth.obtener_usuario_de_token(del_owner) is not None
    [entrada] = repositorio_auditoria.listar(accion="USUARIO_PASSWORD_CAMBIADA")
    assert entrada.usuario_id == usuarios["cajero"]
    assert "clave-nueva" not in entrada.resumen and _CLAVE not in entrada.resumen and "pbkdf2" not in entrada.resumen


def test_cambio_propio_con_la_clave_actual_incorrecta_no_cierra_nada_ni_audita(usuarios):
    token = _sesion("cajero")

    with pytest.raises(CredencialesInvalidasError):
        servicio_usuarios.cambiar_password_propia(usuarios["cajero"], "equivocada", "clave-nueva-789")

    assert servicio_auth.obtener_usuario_de_token(token) is not None
    assert repositorio_auditoria.listar(accion="USUARIO_PASSWORD_CAMBIADA") == []
