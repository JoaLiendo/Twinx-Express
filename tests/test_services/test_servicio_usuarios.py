"""Pruebas de services.servicio_usuarios (Etapa B.1 del MVP): CRUD de
usuarios, inmutabilidad de `nombre_usuario`, baja lógica, cambio de rol,
reset/autocambio de contraseña, y la regla de "nunca sin OWNER activo".

La autorización por rol (que un CASHIER no pueda invocar estas
operaciones) no se prueba acá: ese guardrail es
`interfaces.web.auth.requiere_rol`, ya cubierto en
`tests/test_interfaces_web/test_auth.py::TestRequiereRol::test_usuario_cashier_es_rechazado_por_dependencia_de_owner`
-- es el mismo mecanismo, ya en uso, que protegerá las rutas reales de
`interfaces/web/rutas/empleados.py` cuando se implementen (Etapa B.2).
`servicio_usuarios`, como el resto de `services/`, no conoce roles de
quien lo llama.
"""

import pytest

from excepciones import (
    CredencialesInvalidasError,
    DatosInvalidosError,
    NombreUsuarioDuplicadoError,
    UltimoOwnerActivoError,
    UsuarioNoEncontradoError,
)
from services import servicio_auth, servicio_usuarios


def _crear_owner(base_datos_temporal, nombre_usuario="ana", **overrides):
    datos = dict(
        nombre_usuario=nombre_usuario,
        nombre_completo="Ana Owner",
        password="clave-correcta-123",
        rol="OWNER",
    )
    datos.update(overrides)
    return servicio_usuarios.crear_usuario(**datos)


def _crear_cashier(base_datos_temporal, nombre_usuario="carlos", **overrides):
    datos = dict(
        nombre_usuario=nombre_usuario,
        nombre_completo="Carlos Cajero",
        password="clave-correcta-123",
        rol="CASHIER",
    )
    datos.update(overrides)
    return servicio_usuarios.crear_usuario(**datos)


class TestCrearUsuario:
    def test_crea_usuario_y_hashea_la_password(self, base_datos_temporal):
        usuario = _crear_owner(base_datos_temporal)

        assert usuario.id is not None
        assert usuario.nombre_usuario == "ana"
        assert usuario.rol == "OWNER"
        assert usuario.activo is True
        assert usuario.password_hash != "clave-correcta-123"
        assert usuario.password_hash.startswith("pbkdf2_sha256$")

    def test_nombre_usuario_duplicado_lanza_error(self, base_datos_temporal):
        _crear_owner(base_datos_temporal, nombre_usuario="ana")

        with pytest.raises(NombreUsuarioDuplicadoError):
            _crear_cashier(base_datos_temporal, nombre_usuario="ana")

    def test_nombre_completo_vacio_lanza_error(self, base_datos_temporal):
        with pytest.raises(DatosInvalidosError):
            _crear_owner(base_datos_temporal, nombre_completo="   ")


class TestEditarDatos:
    def test_edita_nombre_completo(self, base_datos_temporal):
        usuario = _crear_owner(base_datos_temporal)

        actualizado = servicio_usuarios.editar_datos(usuario.id, "Ana Propietaria")

        assert actualizado.nombre_completo == "Ana Propietaria"

    def test_nombre_usuario_no_es_modificable(self, base_datos_temporal):
        """editar_datos no acepta `nombre_usuario`: no existe ningún
        parámetro ni código posible que lo cambie."""
        usuario = _crear_owner(base_datos_temporal, nombre_usuario="ana")

        actualizado = servicio_usuarios.editar_datos(usuario.id, "Ana Propietaria")

        assert actualizado.nombre_usuario == "ana"

    def test_usuario_inexistente_lanza_error(self, base_datos_temporal):
        with pytest.raises(UsuarioNoEncontradoError):
            servicio_usuarios.editar_datos(9999, "Nombre Cualquiera")

    def test_nombre_completo_vacio_lanza_error(self, base_datos_temporal):
        usuario = _crear_owner(base_datos_temporal)

        with pytest.raises(DatosInvalidosError):
            servicio_usuarios.editar_datos(usuario.id, "")


class TestCambiarRol:
    def test_cambia_rol_de_cashier_a_owner(self, base_datos_temporal):
        _crear_owner(base_datos_temporal, nombre_usuario="ana")
        cajero = _crear_cashier(base_datos_temporal, nombre_usuario="carlos")

        actualizado = servicio_usuarios.cambiar_rol(cajero.id, "OWNER")

        assert actualizado.rol == "OWNER"

    def test_cambia_rol_de_owner_a_cashier_si_hay_otro_owner_activo(self, base_datos_temporal):
        owner_1 = _crear_owner(base_datos_temporal, nombre_usuario="ana")
        _crear_owner(base_datos_temporal, nombre_usuario="beto")

        actualizado = servicio_usuarios.cambiar_rol(owner_1.id, "CASHIER")

        assert actualizado.rol == "CASHIER"

    def test_bloquea_convertir_al_ultimo_owner_activo_en_cashier(self, base_datos_temporal):
        unico_owner = _crear_owner(base_datos_temporal)

        with pytest.raises(UltimoOwnerActivoError):
            servicio_usuarios.cambiar_rol(unico_owner.id, "CASHIER")

        # No se aplicó ningún cambio parcial.
        assert servicio_usuarios.obtener_por_id(unico_owner.id).rol == "OWNER"

    def test_usuario_inexistente_lanza_error(self, base_datos_temporal):
        with pytest.raises(UsuarioNoEncontradoError):
            servicio_usuarios.cambiar_rol(9999, "OWNER")


class TestActivarDesactivar:
    def test_desactiva_cashier(self, base_datos_temporal):
        _crear_owner(base_datos_temporal, nombre_usuario="ana")
        cajero = _crear_cashier(base_datos_temporal)

        actualizado = servicio_usuarios.desactivar(cajero.id)

        assert actualizado.activo is False

    def test_reactiva_usuario(self, base_datos_temporal):
        _crear_owner(base_datos_temporal, nombre_usuario="ana")
        cajero = _crear_cashier(base_datos_temporal)
        servicio_usuarios.desactivar(cajero.id)

        actualizado = servicio_usuarios.activar(cajero.id)

        assert actualizado.activo is True

    def test_desactiva_owner_si_hay_otro_owner_activo(self, base_datos_temporal):
        owner_1 = _crear_owner(base_datos_temporal, nombre_usuario="ana")
        _crear_owner(base_datos_temporal, nombre_usuario="beto")

        actualizado = servicio_usuarios.desactivar(owner_1.id)

        assert actualizado.activo is False

    def test_bloquea_desactivar_al_ultimo_owner_activo(self, base_datos_temporal):
        unico_owner = _crear_owner(base_datos_temporal)

        with pytest.raises(UltimoOwnerActivoError):
            servicio_usuarios.desactivar(unico_owner.id)

        assert servicio_usuarios.obtener_por_id(unico_owner.id).activo is True

    def test_desactivar_un_owner_ya_inactivo_no_cuenta_como_ultimo(self, base_datos_temporal):
        """Un OWNER ya inactivo no bloquea nada: no es "el último activo"."""
        owner_1 = _crear_owner(base_datos_temporal, nombre_usuario="ana")
        owner_2 = _crear_owner(base_datos_temporal, nombre_usuario="beto")
        servicio_usuarios.desactivar(owner_2.id)

        # owner_1 sigue siendo el único activo: desactivarlo debe bloquearse.
        with pytest.raises(UltimoOwnerActivoError):
            servicio_usuarios.desactivar(owner_1.id)

    def test_usuario_inexistente_lanza_error(self, base_datos_temporal):
        with pytest.raises(UsuarioNoEncontradoError):
            servicio_usuarios.desactivar(9999)


class TestResetearPassword:
    def test_owner_resetea_password_de_otro_usuario(self, base_datos_temporal):
        _crear_owner(base_datos_temporal, nombre_usuario="ana")
        cajero = _crear_cashier(base_datos_temporal)

        servicio_usuarios.resetear_password(cajero.id, "clave-nueva-456")

        sesion = servicio_auth.iniciar_sesion(cajero.nombre_usuario, "clave-nueva-456")
        assert sesion is not None

    def test_password_anterior_deja_de_funcionar_tras_el_reset(self, base_datos_temporal):
        _crear_owner(base_datos_temporal, nombre_usuario="ana")
        cajero = _crear_cashier(base_datos_temporal)

        servicio_usuarios.resetear_password(cajero.id, "clave-nueva-456")

        with pytest.raises(CredencialesInvalidasError):
            servicio_auth.iniciar_sesion(cajero.nombre_usuario, "clave-correcta-123")

    def test_usuario_inexistente_lanza_error(self, base_datos_temporal):
        with pytest.raises(UsuarioNoEncontradoError):
            servicio_usuarios.resetear_password(9999, "clave-nueva-456")


class TestCambiarPasswordPropia:
    def test_cambia_la_propia_password_con_la_actual_correcta(self, base_datos_temporal):
        owner = _crear_owner(base_datos_temporal)

        servicio_usuarios.cambiar_password_propia(owner.id, "clave-correcta-123", "clave-nueva-456")

        sesion = servicio_auth.iniciar_sesion(owner.nombre_usuario, "clave-nueva-456")
        assert sesion is not None

    def test_rechaza_si_la_password_actual_es_incorrecta(self, base_datos_temporal):
        owner = _crear_owner(base_datos_temporal)

        with pytest.raises(CredencialesInvalidasError):
            servicio_usuarios.cambiar_password_propia(owner.id, "clave-equivocada", "clave-nueva-456")

    def test_password_anterior_deja_de_funcionar_tras_el_autocambio(self, base_datos_temporal):
        owner = _crear_owner(base_datos_temporal)

        servicio_usuarios.cambiar_password_propia(owner.id, "clave-correcta-123", "clave-nueva-456")

        with pytest.raises(CredencialesInvalidasError):
            servicio_auth.iniciar_sesion(owner.nombre_usuario, "clave-correcta-123")

    def test_no_cambia_nada_si_la_password_actual_es_incorrecta(self, base_datos_temporal):
        owner = _crear_owner(base_datos_temporal)

        with pytest.raises(CredencialesInvalidasError):
            servicio_usuarios.cambiar_password_propia(owner.id, "clave-equivocada", "clave-nueva-456")

        # La contraseña original sigue funcionando: no hubo cambio parcial.
        sesion = servicio_auth.iniciar_sesion(owner.nombre_usuario, "clave-correcta-123")
        assert sesion is not None
