"""V1.17: política mínima de contraseñas. Una sola regla para el primer OWNER, el alta de empleados, el reset
por OWNER y el cambio propio; rechazar nunca modifica el hash, nunca revoca sesiones y nunca audita."""

import logging

import pytest

from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import LARGO_MAXIMO_PASSWORD, LARGO_MINIMO_PASSWORD, Usuario, validar_password
from excepciones import CredencialesInvalidasError, DatosInvalidosError
from services import servicio_auth, servicio_usuarios

_CLAVE = "clave-correcta-123"
_CORTA = "corta-123"
_MENSAJE_CORTA = "al menos 10 caracteres"


class TestRegla:
    @pytest.mark.parametrize(
        "password",
        [
            "a" * LARGO_MINIMO_PASSWORD,
            "a" * LARGO_MAXIMO_PASSWORD,
            "todo en minusculas con espacios",
            "1234567890",
            "!@#$%^&*()_+",
            "ñandú-año-2026",
        ],
    )
    def test_acepta_sin_exigir_combinaciones(self, password):
        validar_password(password)

    @pytest.mark.parametrize("password", ["", " " * 12, "\t" * 12])
    def test_rechaza_vacia_o_solo_espacios(self, password):
        with pytest.raises(DatosInvalidosError, match="vacía"):
            validar_password(password)

    def test_rechaza_un_caracter_menos_que_el_minimo(self):
        with pytest.raises(DatosInvalidosError, match=_MENSAJE_CORTA):
            validar_password("a" * (LARGO_MINIMO_PASSWORD - 1))

    def test_rechaza_un_caracter_mas_que_el_maximo(self):
        with pytest.raises(DatosInvalidosError, match="no puede superar los 128"):
            validar_password("a" * (LARGO_MAXIMO_PASSWORD + 1))

    def test_el_mensaje_nunca_incluye_la_contrasena(self):
        with pytest.raises(DatosInvalidosError) as error:
            validar_password(_CORTA)
        assert _CORTA not in str(error.value)


def _usuario_existente(nombre: str, rol: str = "CASHIER") -> Usuario:
    return repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre,
            nombre_completo=f"Nombre {nombre}",
            password_hash=servicio_auth.hashear_password(_CLAVE, iteraciones=1000),
            rol=rol,
        )
    )


def _hash_de(usuario_id: int) -> str:
    return repositorio_usuarios.obtener_por_id(usuario_id).password_hash


class TestAlta:
    def test_crear_usuario_rechaza_y_no_crea_nada(self, base_datos_temporal):
        with pytest.raises(DatosInvalidosError, match=_MENSAJE_CORTA):
            servicio_usuarios.crear_usuario("carlos", "Carlos", _CORTA, "CASHIER")
        assert servicio_usuarios.listar_todos() == []

    def test_crear_primer_owner_rechaza_y_no_crea_nada(self, base_datos_temporal):
        with pytest.raises(DatosInvalidosError, match=_MENSAJE_CORTA):
            servicio_usuarios.crear_primer_owner("ana", "Ana", _CORTA)
        assert servicio_usuarios.hay_algun_owner_activo() is False

    def test_crear_usuario_acepta_el_largo_minimo(self, base_datos_temporal):
        creado = servicio_usuarios.crear_usuario("carlos", "Carlos", "a" * LARGO_MINIMO_PASSWORD, "CASHIER")
        assert servicio_auth.verificar_password("a" * LARGO_MINIMO_PASSWORD, creado.password_hash)


class TestResetPorOwner:
    def test_rechazado_no_cambia_hash_ni_revoca_sesiones_ni_audita(self, base_datos_temporal):
        owner, cajero = _usuario_existente("duenio", "OWNER"), _usuario_existente("cajero")
        hash_previo = _hash_de(cajero.id)
        token = servicio_auth.iniciar_sesion("cajero", _CLAVE).token

        with pytest.raises(DatosInvalidosError, match=_MENSAJE_CORTA):
            servicio_usuarios.resetear_password(cajero.id, _CORTA, actor_id=owner.id)

        assert _hash_de(cajero.id) == hash_previo
        assert servicio_auth.obtener_usuario_de_token(token) is not None
        assert repositorio_auditoria.listar(accion="USUARIO_PASSWORD_RESETEADA") == []


class TestCambioPropio:
    def test_rechazado_no_cambia_hash_ni_revoca_sesiones_ni_audita(self, base_datos_temporal):
        cajero = _usuario_existente("cajero")
        hash_previo = _hash_de(cajero.id)
        token = servicio_auth.iniciar_sesion("cajero", _CLAVE).token

        with pytest.raises(DatosInvalidosError, match=_MENSAJE_CORTA):
            servicio_usuarios.cambiar_password_propia(cajero.id, _CLAVE, _CORTA)

        assert _hash_de(cajero.id) == hash_previo
        assert servicio_auth.obtener_usuario_de_token(token) is not None
        assert repositorio_auditoria.listar(accion="USUARIO_PASSWORD_CAMBIADA") == []

    def test_la_clave_actual_incorrecta_se_informa_antes_que_la_politica(self, base_datos_temporal):
        cajero = _usuario_existente("cajero")

        with pytest.raises(CredencialesInvalidasError):
            servicio_usuarios.cambiar_password_propia(cajero.id, "equivocada", _CORTA)


class TestCuentasExistentes:
    def test_una_cuenta_con_clave_corta_anterior_a_la_politica_sigue_entrando(self, base_datos_temporal):
        repositorio_usuarios.crear_usuario(
            Usuario(
                nombre_usuario="vieja",
                nombre_completo="Cuenta vieja",
                password_hash=servicio_auth.hashear_password("1234", iteraciones=1000),
                rol="OWNER",
            )
        )

        assert servicio_auth.iniciar_sesion("vieja", "1234") is not None


def test_un_rechazo_no_deja_la_contrasena_en_logs(base_datos_temporal, caplog):
    cajero = _usuario_existente("cajero")

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(DatosInvalidosError):
            servicio_usuarios.cambiar_password_propia(cajero.id, _CLAVE, _CORTA)

    assert _CORTA not in caplog.text
    assert "pbkdf2" not in caplog.text
