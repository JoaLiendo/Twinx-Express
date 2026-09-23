"""Pruebas HTTP end-to-end de login/logout (Fase 2E).

Usa el cliente ASGI mínimo de `_asgi_cliente.py` (sin `httpx`, ver su
docstring) para golpear la app real: rutas, dependencias de FastAPI y
cookies tal como los vería un navegador. Reutiliza `services.servicio_auth`
para crear usuarios/verificar sesiones — no duplica esa lógica acá.
"""

from http.cookies import SimpleCookie

from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth

from ._asgi_cliente import solicitud


def _crear_usuario(rol: str, *, nombre_usuario: str = "ana", activo: bool = True):
    return repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre_usuario,
            nombre_completo="Usuario de prueba",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol=rol,
            activo=activo,
        )
    )


def _login_directo(nombre_usuario: str) -> str:
    """Crea una sesión real vía servicio_auth (sin pasar por HTTP) para
    tests que necesitan partir de "ya hay una sesión válida"."""
    return servicio_auth.iniciar_sesion(nombre_usuario, "clave-correcta-123").token


def _cookie_de(respuesta, nombre: str) -> SimpleCookie:
    set_cookie = respuesta.header("set-cookie")
    assert set_cookie is not None, "la respuesta no trae Set-Cookie"
    cookie = SimpleCookie()
    cookie.load(set_cookie)
    assert nombre in cookie
    return cookie


class TestFormularioLogin:
    def test_get_login_responde_200_con_formulario(self, base_datos_temporal):
        # Requiere que exista un OWNER: sin ninguno, /login redirige a
        # /configuracion-inicial (ver auditoría de Stage D).
        _crear_usuario("OWNER")

        respuesta = solicitud("GET", "/login")

        assert respuesta.status == 200
        assert "nombre_usuario" in respuesta.texto
        assert 'type="password"' in respuesta.texto

    def test_usuario_ya_autenticado_es_redirigido_al_dashboard(self, base_datos_temporal):
        _crear_usuario("OWNER")
        token = _login_directo("ana")

        respuesta = solicitud("GET", "/login", cookies={NOMBRE_COOKIE_SESION: token})

        assert respuesta.status == 303
        assert respuesta.header("location") == "/"


class TestProcesarLogin:
    def test_credenciales_correctas_crea_sesion_y_fija_cookie(self, base_datos_temporal):
        _crear_usuario("OWNER")

        respuesta = solicitud(
            "POST", "/login", formulario={"nombre_usuario": "ana", "password": "clave-correcta-123"}
        )

        assert respuesta.status == 303
        cookie = _cookie_de(respuesta, NOMBRE_COOKIE_SESION)
        token = cookie[NOMBRE_COOKIE_SESION].value
        assert servicio_auth.obtener_usuario_de_token(token) is not None

    def test_credenciales_incorrectas_muestra_error_generico(self, base_datos_temporal):
        _crear_usuario("OWNER")

        respuesta = solicitud(
            "POST", "/login", formulario={"nombre_usuario": "ana", "password": "clave-mala"}
        )

        assert respuesta.status == 200
        assert respuesta.header("set-cookie") is None
        assert "incorrect" in respuesta.texto.lower()

    def test_usuario_inactivo_muestra_el_mismo_error_generico(self, base_datos_temporal):
        _crear_usuario("OWNER", activo=False)

        respuesta_inactivo = solicitud(
            "POST", "/login", formulario={"nombre_usuario": "ana", "password": "clave-correcta-123"}
        )

        _crear_usuario("CASHIER", nombre_usuario="carla")
        respuesta_incorrecta = solicitud(
            "POST", "/login", formulario={"nombre_usuario": "carla", "password": "clave-mala"}
        )

        assert respuesta_inactivo.status == 200
        assert respuesta_inactivo.header("set-cookie") is None

        # Mismo texto de error visible para ambos casos: no se filtra que
        # 'ana' existe pero está inactiva.
        def _extraer_mensaje(html: str) -> str:
            inicio = html.index("mensaje-error")
            return html[inicio : inicio + 200]

        assert _extraer_mensaje(respuesta_inactivo.texto) == _extraer_mensaje(respuesta_incorrecta.texto)

    def test_usuario_inexistente_muestra_el_mismo_error_generico_que_password_incorrecta(
        self, base_datos_temporal
    ):
        _crear_usuario("OWNER")

        respuesta_inexistente = solicitud(
            "POST", "/login", formulario={"nombre_usuario": "no-existe", "password": "cualquiera"}
        )
        respuesta_incorrecta = solicitud(
            "POST", "/login", formulario={"nombre_usuario": "ana", "password": "clave-mala"}
        )

        assert respuesta_inexistente.status == respuesta_incorrecta.status == 200
        assert respuesta_inexistente.header("set-cookie") is None


class TestCookieDeSesion:
    def test_cookie_contiene_unicamente_el_token(self, base_datos_temporal):
        _crear_usuario("OWNER")

        respuesta = solicitud(
            "POST", "/login", formulario={"nombre_usuario": "ana", "password": "clave-correcta-123"}
        )

        cookie = _cookie_de(respuesta, NOMBRE_COOKIE_SESION)
        valor = cookie[NOMBRE_COOKIE_SESION].value
        assert "ana" not in valor
        assert "OWNER" not in valor

    def test_cookie_es_httponly_samesite_lax_y_path_raiz(self, base_datos_temporal):
        _crear_usuario("OWNER")

        respuesta = solicitud(
            "POST", "/login", formulario={"nombre_usuario": "ana", "password": "clave-correcta-123"}
        )

        cookie = _cookie_de(respuesta, NOMBRE_COOKIE_SESION)
        morsel = cookie[NOMBRE_COOKIE_SESION]
        assert morsel["httponly"] is True
        assert morsel["samesite"].lower() == "lax"
        assert morsel["path"] == "/"

    def test_max_age_de_la_cookie_coincide_con_la_duracion_de_sesion(self, base_datos_temporal):
        _crear_usuario("OWNER")

        respuesta = solicitud(
            "POST", "/login", formulario={"nombre_usuario": "ana", "password": "clave-correcta-123"}
        )

        cookie = _cookie_de(respuesta, NOMBRE_COOKIE_SESION)
        morsel = cookie[NOMBRE_COOKIE_SESION]
        assert int(morsel["max-age"]) == servicio_auth.DURACION_SESION_SEGUNDOS


class TestLogout:
    def test_elimina_la_sesion(self, base_datos_temporal):
        _crear_usuario("OWNER")
        token = _login_directo("ana")

        solicitud("POST", "/logout", cookies={NOMBRE_COOKIE_SESION: token})

        assert servicio_auth.obtener_usuario_de_token(token) is None

    def test_elimina_la_cookie_y_redirige_a_login(self, base_datos_temporal):
        _crear_usuario("OWNER")
        token = _login_directo("ana")

        respuesta = solicitud("POST", "/logout", cookies={NOMBRE_COOKIE_SESION: token})

        assert respuesta.status == 303
        assert respuesta.header("location") == "/login"
        cookie = _cookie_de(respuesta, NOMBRE_COOKIE_SESION)
        morsel = cookie[NOMBRE_COOKIE_SESION]
        assert morsel.value == "" or int(morsel["max-age"]) <= 0

    def test_funciona_sin_cookie_valida(self, base_datos_temporal):
        respuesta = solicitud("POST", "/logout")

        assert respuesta.status == 303
        assert respuesta.header("location") == "/login"

    def test_funciona_con_token_inexistente(self, base_datos_temporal):
        respuesta = solicitud("POST", "/logout", cookies={NOMBRE_COOKIE_SESION: "token-que-no-existe"})

        assert respuesta.status == 303


class TestBloqueoTemporalPorIntentos:
    """El bloqueo por fuerza bruta no debe ser visible desde HTTP: mismo
    estado, mismo texto y sin cookie, exista o no la cuenta."""

    @staticmethod
    def _post(nombre_usuario: str, password: str):
        return solicitud("POST", "/login", formulario={"nombre_usuario": nombre_usuario, "password": password})

    @staticmethod
    def _mensaje(html: str) -> str:
        inicio = html.index("mensaje-error")
        return html[inicio : inicio + 200]

    def test_tras_cinco_fallos_la_password_correcta_recibe_el_mismo_error_generico(self, base_datos_temporal):
        _crear_usuario("OWNER")
        respuesta_normal = self._post("ana", "clave-mala")
        for _ in range(4):
            self._post("ana", "clave-mala")

        respuesta_bloqueada = self._post("ana", "clave-correcta-123")

        assert respuesta_bloqueada.status == 200
        assert respuesta_bloqueada.header("set-cookie") is None
        assert self._mensaje(respuesta_bloqueada.texto) == self._mensaje(respuesta_normal.texto)

    def test_la_respuesta_bloqueada_es_indistinguible_de_un_usuario_inexistente(self, base_datos_temporal):
        _crear_usuario("OWNER")
        for _ in range(5):
            self._post("ana", "clave-mala")

        respuesta_bloqueada = self._post("ana", "clave-correcta-123")
        respuesta_inexistente = self._post("no-existe", "clave-correcta-123")

        assert respuesta_bloqueada.status == respuesta_inexistente.status == 200
        assert respuesta_bloqueada.header("set-cookie") is None
        assert respuesta_inexistente.header("set-cookie") is None
        assert self._mensaje(respuesta_bloqueada.texto) == self._mensaje(respuesta_inexistente.texto)

    def test_la_respuesta_no_menciona_el_bloqueo(self, base_datos_temporal):
        _crear_usuario("OWNER")
        for _ in range(5):
            self._post("ana", "clave-mala")

        texto = self._post("ana", "clave-correcta-123").texto.lower()

        for palabra in ("bloque", "demasiados", "intentos", "esper"):
            assert palabra not in texto

    def test_no_se_crea_sesion_mientras_esta_bloqueado(self, base_datos_temporal):
        _crear_usuario("OWNER")
        for _ in range(5):
            self._post("ana", "clave-mala")

        self._post("ana", "clave-correcta-123")

        with obtener_conexion() as conexion:
            cantidad = conexion.execute("SELECT COUNT(*) FROM sesiones").fetchone()[0]
        assert cantidad == 0

    def test_al_expirar_el_bloqueo_el_login_correcto_redirige_y_fija_cookie(
        self, base_datos_temporal, reloj_falso
    ):
        _crear_usuario("OWNER")
        for _ in range(5):
            self._post("ana", "clave-mala")
        reloj_falso.avanzar(300)

        respuesta = self._post("ana", "clave-correcta-123")

        assert respuesta.status == 303
        _cookie_de(respuesta, NOMBRE_COOKIE_SESION)
