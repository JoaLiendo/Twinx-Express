"""Pruebas HTTP end-to-end de gestión de usuarios/empleados (Etapa B.2
del MVP): listado, alta, edición, cambio de rol, activar/desactivar,
reset de contraseña por un OWNER, y autocambio de la propia contraseña
por cualquier usuario autenticado.

Complementan (no reemplazan) `tests/test_services/test_servicio_usuarios.py`
(reglas de negocio puras) y `tests/test_interfaces_web/test_auth.py`
(el guardrail genérico `requiere_rol`, ya usado sin cambios acá).
"""

from urllib.parse import unquote

from domain.usuario import Usuario
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth, servicio_usuarios

from ._asgi_cliente import solicitud


def _crear_usuario_y_loguearse(rol: str, nombre_usuario: str) -> str:
    from db.repositorios import usuarios as repositorio_usuarios

    repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre_usuario,
            nombre_completo="Usuario de prueba",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol=rol,
        )
    )
    return servicio_auth.iniciar_sesion(nombre_usuario, "clave-correcta-123").token


def _cookies_owner(nombre_usuario: str = "ana") -> dict[str, str]:
    return {NOMBRE_COOKIE_SESION: _crear_usuario_y_loguearse("OWNER", nombre_usuario)}


def _por_nombre_usuario(nombre_usuario: str) -> Usuario:
    return next(u for u in servicio_usuarios.listar_todos() if u.nombre_usuario == nombre_usuario)


class TestListado:
    def test_owner_puede_ver_el_listado(self, base_datos_temporal):
        cookies = _cookies_owner()
        servicio_usuarios.crear_usuario("carlos", "Carlos Cajero", "clave-correcta-123", "CASHIER")

        respuesta = solicitud("GET", "/empleados", cookies=cookies)

        assert respuesta.status == 200
        assert "Carlos Cajero" in respuesta.texto
        assert "@carlos" in respuesta.texto

    def test_cashier_recibe_403_al_intentar_acceder(self, base_datos_temporal):
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")

        respuesta = solicitud("GET", "/empleados", cookies={NOMBRE_COOKIE_SESION: token})

        assert respuesta.status == 403

    def test_sin_sesion_redirige_a_login(self, base_datos_temporal):
        respuesta = solicitud("GET", "/empleados")

        assert respuesta.status == 303
        assert respuesta.header("location").startswith("/login")


class TestCrear:
    def test_owner_puede_crear_usuario(self, base_datos_temporal):
        cookies = _cookies_owner()

        respuesta = solicitud(
            "POST",
            "/empleados/nuevo",
            cookies=cookies,
            formulario={
                "nombre_usuario": "carlos",
                "nombre_completo": "Carlos Cajero",
                "password": "clave-correcta-123",
                "rol": "CASHIER",
            },
        )

        assert respuesta.status == 303
        assert "tipo=success" in respuesta.header("location")
        creado = _por_nombre_usuario("carlos")
        assert creado.rol == "CASHIER"
        assert creado.activo is True

    def test_cashier_recibe_403_al_intentar_crear(self, base_datos_temporal):
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")

        respuesta = solicitud(
            "POST",
            "/empleados/nuevo",
            cookies={NOMBRE_COOKIE_SESION: token},
            formulario={
                "nombre_usuario": "otro",
                "nombre_completo": "Otro",
                "password": "clave-correcta-123",
                "rol": "CASHIER",
            },
        )

        assert respuesta.status == 403

    def test_nombre_usuario_duplicado_se_muestra_como_error(self, base_datos_temporal):
        cookies = _cookies_owner("ana")

        respuesta = solicitud(
            "POST",
            "/empleados/nuevo",
            cookies=cookies,
            formulario={
                "nombre_usuario": "ana",
                "nombre_completo": "Otra Ana",
                "password": "clave-correcta-123",
                "rol": "CASHIER",
            },
        )

        assert respuesta.status == 303
        assert "tipo=error" in respuesta.header("location")
        assert "nombre de usuario" in unquote(respuesta.header("location")).lower()


class TestEditar:
    def test_owner_puede_editar_nombre_completo(self, base_datos_temporal):
        cookies = _cookies_owner()
        cajero = servicio_usuarios.crear_usuario("carlos", "Carlos Cajero", "clave-correcta-123", "CASHIER")

        respuesta = solicitud(
            "POST",
            f"/empleados/{cajero.id}/editar",
            cookies=cookies,
            formulario={"nombre_completo": "Carlos Actualizado"},
        )

        assert respuesta.status == 303
        assert "tipo=success" in respuesta.header("location")
        actualizado = servicio_usuarios.obtener_por_id(cajero.id)
        assert actualizado.nombre_completo == "Carlos Actualizado"

    def test_nombre_usuario_sigue_siendo_inmutable(self, base_datos_temporal):
        """La ruta de edición no declara `nombre_usuario` como campo:
        aunque el formulario lo envíe, no hay ningún camino de código
        que lo use para modificar la columna."""
        cookies = _cookies_owner()
        cajero = servicio_usuarios.crear_usuario("carlos", "Carlos Cajero", "clave-correcta-123", "CASHIER")

        respuesta = solicitud(
            "POST",
            f"/empleados/{cajero.id}/editar",
            cookies=cookies,
            formulario={"nombre_completo": "Carlos Actualizado", "nombre_usuario": "otro_nombre"},
        )

        assert respuesta.status == 303
        actualizado = servicio_usuarios.obtener_por_id(cajero.id)
        assert actualizado.nombre_usuario == "carlos"

    def test_cashier_recibe_403_al_intentar_editar(self, base_datos_temporal):
        servicio_usuarios.crear_usuario("ana", "Ana Owner", "clave-correcta-123", "OWNER")
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")
        cajero = _por_nombre_usuario("carlos")

        respuesta = solicitud(
            "POST",
            f"/empleados/{cajero.id}/editar",
            cookies={NOMBRE_COOKIE_SESION: token},
            formulario={"nombre_completo": "Hackeado"},
        )

        assert respuesta.status == 403
        assert servicio_usuarios.obtener_por_id(cajero.id).nombre_completo == "Usuario de prueba"


class TestCambiarRol:
    def test_owner_puede_cambiar_rol(self, base_datos_temporal):
        cookies = _cookies_owner()
        cajero = servicio_usuarios.crear_usuario("carlos", "Carlos Cajero", "clave-correcta-123", "CASHIER")

        respuesta = solicitud(
            "POST", f"/empleados/{cajero.id}/rol", cookies=cookies, formulario={"rol": "OWNER"}
        )

        assert respuesta.status == 303
        assert "tipo=success" in respuesta.header("location")
        assert servicio_usuarios.obtener_por_id(cajero.id).rol == "OWNER"

    def test_bloquea_convertir_al_ultimo_owner_activo_en_cashier(self, base_datos_temporal):
        cookies = _cookies_owner("ana")
        unico_owner = servicio_usuarios.obtener_por_id(1)

        respuesta = solicitud(
            "POST",
            f"/empleados/{unico_owner.id}/rol",
            cookies=cookies,
            formulario={"rol": "CASHIER"},
        )

        assert respuesta.status == 303
        assert "tipo=error" in respuesta.header("location")
        assert "owner" in unquote(respuesta.header("location")).lower()
        assert servicio_usuarios.obtener_por_id(unico_owner.id).rol == "OWNER"


class TestActivarDesactivar:
    def test_owner_puede_desactivar_y_activar(self, base_datos_temporal):
        cookies = _cookies_owner()
        cajero = servicio_usuarios.crear_usuario("carlos", "Carlos Cajero", "clave-correcta-123", "CASHIER")

        respuesta = solicitud("POST", f"/empleados/{cajero.id}/desactivar", cookies=cookies)
        assert respuesta.status == 303
        assert "tipo=success" in respuesta.header("location")
        assert servicio_usuarios.obtener_por_id(cajero.id).activo is False

        respuesta = solicitud("POST", f"/empleados/{cajero.id}/activar", cookies=cookies)
        assert respuesta.status == 303
        assert servicio_usuarios.obtener_por_id(cajero.id).activo is True

    def test_bloquea_desactivar_al_ultimo_owner_activo(self, base_datos_temporal):
        cookies = _cookies_owner("ana")
        unico_owner = servicio_usuarios.obtener_por_id(1)

        respuesta = solicitud("POST", f"/empleados/{unico_owner.id}/desactivar", cookies=cookies)

        assert respuesta.status == 303
        assert "tipo=error" in respuesta.header("location")
        assert "owner" in unquote(respuesta.header("location")).lower()
        assert servicio_usuarios.obtener_por_id(unico_owner.id).activo is True

    def test_no_existe_eliminacion_fisica(self, base_datos_temporal):
        """No hay ninguna ruta de baja física: `/eliminar` sobre un
        usuario ni siquiera existe como endpoint registrado."""
        cookies = _cookies_owner()
        cajero = servicio_usuarios.crear_usuario("carlos", "Carlos Cajero", "clave-correcta-123", "CASHIER")

        respuesta = solicitud("POST", f"/empleados/{cajero.id}/eliminar", cookies=cookies)

        assert respuesta.status == 404
        assert servicio_usuarios.obtener_por_id(cajero.id) is not None


class TestResetearPassword:
    def test_owner_puede_resetear_password_de_otro_usuario(self, base_datos_temporal):
        cookies = _cookies_owner()
        cajero = servicio_usuarios.crear_usuario("carlos", "Carlos Cajero", "clave-correcta-123", "CASHIER")

        respuesta = solicitud(
            "POST",
            f"/empleados/{cajero.id}/resetear-password",
            cookies=cookies,
            formulario={"password_nueva": "clave-nueva-456"},
        )

        assert respuesta.status == 303
        assert "tipo=success" in respuesta.header("location")
        assert servicio_auth.iniciar_sesion("carlos", "clave-nueva-456") is not None

    def test_cashier_recibe_403_al_intentar_resetear(self, base_datos_temporal):
        servicio_usuarios.crear_usuario("ana", "Ana Owner", "clave-correcta-123", "OWNER")
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")
        objetivo = servicio_usuarios.obtener_por_id(1)

        respuesta = solicitud(
            "POST",
            f"/empleados/{objetivo.id}/resetear-password",
            cookies={NOMBRE_COOKIE_SESION: token},
            formulario={"password_nueva": "clave-nueva-456"},
        )

        assert respuesta.status == 403


class TestCambiarPasswordPropia:
    def test_cashier_puede_cambiar_su_propia_password(self, base_datos_temporal):
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")

        respuesta = solicitud(
            "POST",
            "/mi-cuenta/password",
            cookies={NOMBRE_COOKIE_SESION: token},
            formulario={"password_actual": "clave-correcta-123", "password_nueva": "clave-nueva-456"},
        )

        assert respuesta.status == 303
        assert "tipo=success" in respuesta.header("location")
        assert servicio_auth.iniciar_sesion("carlos", "clave-nueva-456") is not None

    def test_owner_puede_cambiar_su_propia_password(self, base_datos_temporal):
        token = _crear_usuario_y_loguearse("OWNER", "ana")

        respuesta = solicitud(
            "POST",
            "/mi-cuenta/password",
            cookies={NOMBRE_COOKIE_SESION: token},
            formulario={"password_actual": "clave-correcta-123", "password_nueva": "clave-nueva-456"},
        )

        assert respuesta.status == 303
        assert servicio_auth.iniciar_sesion("ana", "clave-nueva-456") is not None

    def test_password_actual_incorrecta_se_muestra_como_error(self, base_datos_temporal):
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")

        respuesta = solicitud(
            "POST",
            "/mi-cuenta/password",
            cookies={NOMBRE_COOKIE_SESION: token},
            formulario={"password_actual": "clave-equivocada", "password_nueva": "clave-nueva-456"},
        )

        assert respuesta.status == 303
        assert "tipo=error" in respuesta.header("location")
        # La contraseña original sigue funcionando: no hubo cambio parcial.
        assert servicio_auth.iniciar_sesion("carlos", "clave-correcta-123") is not None

    def test_formulario_de_cambio_propio_es_accesible_sin_ser_owner(self, base_datos_temporal):
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")

        respuesta = solicitud("GET", "/mi-cuenta/password", cookies={NOMBRE_COOKIE_SESION: token})

        assert respuesta.status == 200
