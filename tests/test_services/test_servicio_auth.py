"""Pruebas de services.servicio_auth (Fase 2C): hashing, verificación,
login, resolución de usuario por token, expiración y logout.

Todo esto es lógica de servicio pura: no hay rutas HTTP, cookies ni
`Request` todavía (eso es fase 2D). Usa `base_datos_temporal` (ver
tests/conftest.py) para las operaciones que tocan `usuarios`/`sesiones`.
"""

import pytest

from db.repositorios import sesiones as repositorio_sesiones
from db.repositorios import usuarios as repositorio_usuarios
from db.conexion import obtener_conexion
from domain.usuario import Usuario
from excepciones import CredencialesInvalidasError, DatosInvalidosError, UsuarioInactivoError
from services import servicio_auth


def _crear_usuario(base_datos_temporal, **overrides):
    datos = dict(
        nombre_usuario="ana",
        nombre_completo="Ana Owner",
        password_hash=servicio_auth.hashear_password("clave-correcta-123"),
        rol="OWNER",
    )
    datos.update(overrides)
    return repositorio_usuarios.crear_usuario(Usuario(**datos))


def _desactivar_usuario(usuario_id: int) -> None:
    """Solo para tests: cambia `activo` directo por SQL (no hay CRUD de
    usuarios activo/inactivo todavía — eso es un caso de uso de fase
    posterior, no de autenticación)."""
    with obtener_conexion() as conexion:
        conexion.execute("UPDATE usuarios SET activo = 0 WHERE id = ?", (usuario_id,))


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


class TestHashearPassword:
    def test_genera_formato_autodescriptivo_con_cuatro_partes(self):
        resultado = servicio_auth.hashear_password("clave-correcta-123")

        partes = resultado.split("$")
        assert len(partes) == 4
        algoritmo, iteraciones, salt_hex, hash_hex = partes
        assert algoritmo == "pbkdf2_sha256"
        assert iteraciones.isdigit()
        bytes.fromhex(salt_hex)
        bytes.fromhex(hash_hex)

    def test_usa_iteraciones_por_defecto_documentadas(self):
        resultado = servicio_auth.hashear_password("clave-correcta-123")
        iteraciones = int(resultado.split("$")[1])
        assert iteraciones == servicio_auth.ITERACIONES_PBKDF2
        assert servicio_auth.ITERACIONES_PBKDF2 >= 600_000

    def test_dos_hashes_de_la_misma_password_son_diferentes(self):
        hash_1 = servicio_auth.hashear_password("clave-correcta-123")
        hash_2 = servicio_auth.hashear_password("clave-correcta-123")

        assert hash_1 != hash_2  # salt aleatorio distinto en cada llamada

    def test_password_original_no_aparece_en_el_hash_resultante(self):
        resultado = servicio_auth.hashear_password("clave-correcta-123")
        assert "clave-correcta-123" not in resultado

    def test_rechaza_password_vacia(self):
        with pytest.raises(DatosInvalidosError):
            servicio_auth.hashear_password("")

    def test_acepta_cantidad_de_iteraciones_explicita(self):
        resultado = servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000)
        assert resultado.split("$")[1] == "1000"


# ---------------------------------------------------------------------------
# Verificación
# ---------------------------------------------------------------------------


class TestVerificarPassword:
    def setup_method(self):
        # Iteraciones bajas: estos tests no ejercitan el costo de hashing,
        # así que no hace falta pagar los ~200ms de las 600000 reales.
        self.hash_valido = servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000)

    def test_password_correcta_devuelve_true(self):
        assert servicio_auth.verificar_password("clave-correcta-123", self.hash_valido) is True

    def test_password_incorrecta_devuelve_false(self):
        assert servicio_auth.verificar_password("otra-clave", self.hash_valido) is False

    def test_hash_con_formato_invalido_devuelve_false_sin_lanzar(self):
        assert servicio_auth.verificar_password("clave-correcta-123", "esto-no-es-un-hash") is False

    def test_hash_con_partes_de_mas_devuelve_false_sin_lanzar(self):
        corrupto = self.hash_valido + "$sobrante"
        assert servicio_auth.verificar_password("clave-correcta-123", corrupto) is False

    def test_hash_con_salt_no_hexadecimal_devuelve_false_sin_lanzar(self):
        algoritmo, iteraciones, _salt, hash_hex = self.hash_valido.split("$")
        corrupto = f"{algoritmo}${iteraciones}$no-es-hex${hash_hex}"
        assert servicio_auth.verificar_password("clave-correcta-123", corrupto) is False

    def test_hash_con_iteraciones_no_numericas_devuelve_false_sin_lanzar(self):
        algoritmo, _iteraciones, salt, hash_hex = self.hash_valido.split("$")
        corrupto = f"{algoritmo}$mil${salt}${hash_hex}"
        assert servicio_auth.verificar_password("clave-correcta-123", corrupto) is False

    def test_hash_con_algoritmo_desconocido_devuelve_false_sin_lanzar(self):
        _algoritmo, iteraciones, salt, hash_hex = self.hash_valido.split("$")
        corrupto = f"md5${iteraciones}${salt}${hash_hex}"
        assert servicio_auth.verificar_password("clave-correcta-123", corrupto) is False

    def test_hash_vacio_devuelve_false_sin_lanzar(self):
        assert servicio_auth.verificar_password("clave-correcta-123", "") is False

    def test_salt_distinto_no_valida_la_misma_password(self):
        otro_hash = servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000)
        algoritmo, iteraciones, _salt_original, hash_hex_original = self.hash_valido.split("$")
        _algoritmo, _iteraciones, salt_ajeno, _hash_hex_ajeno = otro_hash.split("$")

        # Mismo hash_hex, pero con el salt de otra sesión de hashing: no debe validar.
        hibrido = f"{algoritmo}${iteraciones}${salt_ajeno}${hash_hex_original}"
        assert servicio_auth.verificar_password("clave-correcta-123", hibrido) is False


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


class TestIniciarSesion:
    def test_credenciales_correctas_devuelve_sesion(self, base_datos_temporal):
        usuario = _crear_usuario(base_datos_temporal)

        sesion = servicio_auth.iniciar_sesion("ana", "clave-correcta-123")

        assert sesion.usuario_id == usuario.id
        assert sesion.token
        assert sesion.fecha_expiracion is not None

    def test_usuario_inexistente_lanza_credenciales_invalidas(self, base_datos_temporal):
        with pytest.raises(CredencialesInvalidasError):
            servicio_auth.iniciar_sesion("no-existe", "cualquier-cosa")

    def test_password_incorrecta_lanza_credenciales_invalidas(self, base_datos_temporal):
        _crear_usuario(base_datos_temporal)

        with pytest.raises(CredencialesInvalidasError):
            servicio_auth.iniciar_sesion("ana", "clave-incorrecta")

    def test_usuario_inactivo_lanza_usuario_inactivo(self, base_datos_temporal):
        usuario = _crear_usuario(base_datos_temporal, activo=False)

        with pytest.raises(UsuarioInactivoError):
            servicio_auth.iniciar_sesion("ana", "clave-correcta-123")

    def test_mensaje_es_identico_para_usuario_inexistente_y_password_incorrecta(
        self, base_datos_temporal
    ):
        _crear_usuario(base_datos_temporal)

        with pytest.raises(CredencialesInvalidasError) as error_inexistente:
            servicio_auth.iniciar_sesion("no-existe", "cualquier-cosa")
        with pytest.raises(CredencialesInvalidasError) as error_password:
            servicio_auth.iniciar_sesion("ana", "clave-incorrecta")

        assert str(error_inexistente.value) == str(error_password.value)

    def test_tokens_son_distintos_entre_sesiones_sucesivas(self, base_datos_temporal):
        _crear_usuario(base_datos_temporal)

        sesion_1 = servicio_auth.iniciar_sesion("ana", "clave-correcta-123")
        sesion_2 = servicio_auth.iniciar_sesion("ana", "clave-correcta-123")

        assert sesion_1.token != sesion_2.token


# ---------------------------------------------------------------------------
# Resolución de usuario por token / expiración
# ---------------------------------------------------------------------------


class TestObtenerUsuarioDeToken:
    def test_token_valido_devuelve_el_usuario(self, base_datos_temporal):
        usuario = _crear_usuario(base_datos_temporal)
        sesion = servicio_auth.iniciar_sesion("ana", "clave-correcta-123")

        encontrado = servicio_auth.obtener_usuario_de_token(sesion.token)

        assert encontrado is not None
        assert encontrado.id == usuario.id

    def test_token_inexistente_devuelve_none(self, base_datos_temporal):
        assert servicio_auth.obtener_usuario_de_token("token-que-no-existe") is None

    def test_token_expirado_devuelve_none(self, base_datos_temporal):
        _crear_usuario(base_datos_temporal)
        sesion = servicio_auth.iniciar_sesion("ana", "clave-correcta-123", duracion_segundos=-1)

        assert servicio_auth.obtener_usuario_de_token(sesion.token) is None

    def test_token_expirado_se_limpia_al_detectarse(self, base_datos_temporal):
        _crear_usuario(base_datos_temporal)
        sesion = servicio_auth.iniciar_sesion("ana", "clave-correcta-123", duracion_segundos=-1)

        servicio_auth.obtener_usuario_de_token(sesion.token)

        assert repositorio_sesiones.obtener_por_token(sesion.token) is None

    def test_usuario_desactivado_despues_de_iniciar_sesion_invalida_el_token(
        self, base_datos_temporal
    ):
        usuario = _crear_usuario(base_datos_temporal)
        sesion = servicio_auth.iniciar_sesion("ana", "clave-correcta-123")
        assert servicio_auth.obtener_usuario_de_token(sesion.token) is not None  # todavía válida

        _desactivar_usuario(usuario.id)

        assert servicio_auth.obtener_usuario_de_token(sesion.token) is None

    def test_usuario_eliminado_devuelve_none(self, base_datos_temporal):
        usuario = _crear_usuario(base_datos_temporal)
        sesion = servicio_auth.iniciar_sesion("ana", "clave-correcta-123")

        with obtener_conexion() as conexion:
            conexion.execute("DELETE FROM usuarios WHERE id = ?", (usuario.id,))

        assert servicio_auth.obtener_usuario_de_token(sesion.token) is None


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


class TestCerrarSesion:
    def test_invalida_el_token(self, base_datos_temporal):
        _crear_usuario(base_datos_temporal)
        sesion = servicio_auth.iniciar_sesion("ana", "clave-correcta-123")

        servicio_auth.cerrar_sesion(sesion.token)

        assert servicio_auth.obtener_usuario_de_token(sesion.token) is None

    def test_repetido_sobre_el_mismo_token_no_rompe(self, base_datos_temporal):
        _crear_usuario(base_datos_temporal)
        sesion = servicio_auth.iniciar_sesion("ana", "clave-correcta-123")

        servicio_auth.cerrar_sesion(sesion.token)
        servicio_auth.cerrar_sesion(sesion.token)  # no debe lanzar

    def test_sobre_token_inexistente_no_rompe(self, base_datos_temporal):
        servicio_auth.cerrar_sesion("token-que-jamas-existio")  # no debe lanzar


# ---------------------------------------------------------------------------
# Sesiones múltiples (decisión: SÍ se permiten, ver docstring del módulo)
# ---------------------------------------------------------------------------


class TestSesionesMultiples:
    def test_un_usuario_puede_tener_dos_sesiones_simultaneas_validas(self, base_datos_temporal):
        _crear_usuario(base_datos_temporal)

        sesion_1 = servicio_auth.iniciar_sesion("ana", "clave-correcta-123")
        sesion_2 = servicio_auth.iniciar_sesion("ana", "clave-correcta-123")

        assert servicio_auth.obtener_usuario_de_token(sesion_1.token) is not None
        assert servicio_auth.obtener_usuario_de_token(sesion_2.token) is not None

    def test_cerrar_una_sesion_no_afecta_a_la_otra(self, base_datos_temporal):
        _crear_usuario(base_datos_temporal)
        sesion_1 = servicio_auth.iniciar_sesion("ana", "clave-correcta-123")
        sesion_2 = servicio_auth.iniciar_sesion("ana", "clave-correcta-123")

        servicio_auth.cerrar_sesion(sesion_1.token)

        assert servicio_auth.obtener_usuario_de_token(sesion_1.token) is None
        assert servicio_auth.obtener_usuario_de_token(sesion_2.token) is not None


# ---------------------------------------------------------------------------
# Protección contra fuerza bruta (límite de intentos por usuario)
# ---------------------------------------------------------------------------

_CLAVE_OK = "clave-correcta-123"
_CLAVE_MAL = "clave-incorrecta"


def _crear_usuario_rapido(base_datos_temporal, **overrides):
    """Como `_crear_usuario`, pero con pocas iteraciones en el hash real
    del usuario para que los tests con muchos intentos no tarden."""
    overrides.setdefault("password_hash", servicio_auth.hashear_password(_CLAVE_OK, iteraciones=1000))
    return _crear_usuario(base_datos_temporal, **overrides)


def _fallar(nombre_usuario: str, veces: int) -> None:
    for _ in range(veces):
        with pytest.raises(CredencialesInvalidasError):
            servicio_auth.iniciar_sesion(nombre_usuario, _CLAVE_MAL)


class TestLimiteDeIntentos:
    def test_bajo_el_umbral_la_password_correcta_sigue_funcionando(self, base_datos_temporal):
        _crear_usuario_rapido(base_datos_temporal)
        _fallar("ana", 4)

        assert servicio_auth.iniciar_sesion("ana", _CLAVE_OK).token

    def test_tras_cinco_fallos_la_password_correcta_es_rechazada_con_el_mismo_error(
        self, base_datos_temporal
    ):
        _crear_usuario_rapido(base_datos_temporal)
        with pytest.raises(CredencialesInvalidasError) as error_normal:
            servicio_auth.iniciar_sesion("ana", _CLAVE_MAL)
        _fallar("ana", 4)

        with pytest.raises(CredencialesInvalidasError) as error_bloqueado:
            servicio_auth.iniciar_sesion("ana", _CLAVE_OK)

        assert str(error_bloqueado.value) == str(error_normal.value)

    def test_al_expirar_el_bloqueo_la_password_correcta_funciona(self, base_datos_temporal, reloj_falso):
        _crear_usuario_rapido(base_datos_temporal)
        _fallar("ana", 5)

        reloj_falso.avanzar(300)

        assert servicio_auth.iniciar_sesion("ana", _CLAVE_OK).token

    def test_intentos_durante_el_bloqueo_no_lo_extienden(self, base_datos_temporal, reloj_falso):
        _crear_usuario_rapido(base_datos_temporal)
        _fallar("ana", 5)
        reloj_falso.avanzar(299)
        _fallar("ana", 3)  # rechazados por el bloqueo vigente

        reloj_falso.avanzar(1)

        assert servicio_auth.iniciar_sesion("ana", _CLAVE_OK).token

    def test_un_login_exitoso_reinicia_el_contador(self, base_datos_temporal):
        _crear_usuario_rapido(base_datos_temporal)
        _fallar("ana", 4)
        servicio_auth.iniciar_sesion("ana", _CLAVE_OK)

        _fallar("ana", 4)  # si no se hubiera reiniciado, ya estaría bloqueada

        assert servicio_auth.iniciar_sesion("ana", _CLAVE_OK).token

    def test_el_bloqueo_de_un_usuario_no_afecta_a_otro(self, base_datos_temporal):
        _crear_usuario_rapido(base_datos_temporal)
        _crear_usuario_rapido(base_datos_temporal, nombre_usuario="beto", rol="CASHIER")
        _fallar("ana", 5)

        assert servicio_auth.iniciar_sesion("beto", _CLAVE_OK).token

    def test_fallos_espaciados_mas_alla_de_la_ventana_no_bloquean(self, base_datos_temporal, reloj_falso):
        _crear_usuario_rapido(base_datos_temporal)
        _fallar("ana", 4)
        reloj_falso.avanzar(900)
        _fallar("ana", 4)

        assert servicio_auth.iniciar_sesion("ana", _CLAVE_OK).token


class TestNombresInexistentes:
    def test_no_crean_estado_en_el_limitador(self, base_datos_temporal, limitador_login_aislado, monkeypatch):
        _crear_usuario_rapido(base_datos_temporal)
        # Sin PBKDF2 real: acá solo importa que no se cree estado.
        monkeypatch.setattr(servicio_auth, "verificar_password", lambda *_: False)

        for i in range(300):
            with pytest.raises(CredencialesInvalidasError):
                servicio_auth.iniciar_sesion(f"inventado-{i}", _CLAVE_MAL)

        assert limitador_login_aislado._estados == {}

    def test_nunca_se_bloquean(self, base_datos_temporal, monkeypatch):
        monkeypatch.setattr(servicio_auth, "verificar_password", lambda *_: False)

        for _ in range(20):
            with pytest.raises(CredencialesInvalidasError):
                servicio_auth.iniciar_sesion("no-existe", _CLAVE_MAL)

    def test_solo_los_usuarios_existentes_ocupan_estado(self, base_datos_temporal, limitador_login_aislado):
        usuario = _crear_usuario_rapido(base_datos_temporal)
        _fallar("ana", 2)

        assert list(limitador_login_aislado._estados) == [usuario.id]


class TestUsuarioInactivoYLimite:
    def test_inactivo_sigue_lanzando_usuario_inactivo_bajo_el_umbral(self, base_datos_temporal):
        _crear_usuario_rapido(base_datos_temporal, activo=False)

        with pytest.raises(UsuarioInactivoError):
            servicio_auth.iniciar_sesion("ana", _CLAVE_OK)

    def test_inactivo_tambien_se_bloquea_y_pasa_a_error_generico(self, base_datos_temporal):
        _crear_usuario_rapido(base_datos_temporal, activo=False)
        for _ in range(5):
            with pytest.raises(UsuarioInactivoError):
                servicio_auth.iniciar_sesion("ana", _CLAVE_OK)

        with pytest.raises(CredencialesInvalidasError):
            servicio_auth.iniciar_sesion("ana", _CLAVE_OK)


class TestParidadDeCostoPbkdf2:
    """Todo camino de rechazo hace exactamente una verificación PBKDF2,
    para que el tiempo de respuesta no distinga los casos."""

    @pytest.fixture
    def llamadas(self, monkeypatch):
        registradas = []
        original = servicio_auth.verificar_password

        def espia(password, password_hash):
            registradas.append(password_hash)
            return original(password, password_hash)

        monkeypatch.setattr(servicio_auth, "verificar_password", espia)
        return registradas

    def test_usuario_inexistente_verifica_contra_el_senuelo(self, base_datos_temporal, llamadas):
        with pytest.raises(CredencialesInvalidasError):
            servicio_auth.iniciar_sesion("no-existe", _CLAVE_MAL)

        assert llamadas == [servicio_auth._HASH_SENUELO]

    def test_password_incorrecta_verifica_una_vez_contra_el_hash_real(self, base_datos_temporal, llamadas):
        usuario = _crear_usuario_rapido(base_datos_temporal)

        with pytest.raises(CredencialesInvalidasError):
            servicio_auth.iniciar_sesion("ana", _CLAVE_MAL)

        assert llamadas == [usuario.password_hash]

    def test_usuario_inactivo_verifica_una_vez_contra_el_senuelo(self, base_datos_temporal, llamadas):
        _crear_usuario_rapido(base_datos_temporal, activo=False)

        with pytest.raises(UsuarioInactivoError):
            servicio_auth.iniciar_sesion("ana", _CLAVE_OK)

        assert llamadas == [servicio_auth._HASH_SENUELO]

    def test_usuario_bloqueado_verifica_una_vez_contra_el_senuelo_y_no_contra_el_real(
        self, base_datos_temporal, llamadas
    ):
        _crear_usuario_rapido(base_datos_temporal)
        _fallar("ana", 5)
        llamadas.clear()

        with pytest.raises(CredencialesInvalidasError):
            servicio_auth.iniciar_sesion("ana", _CLAVE_OK)

        assert llamadas == [servicio_auth._HASH_SENUELO]


class TestLoggingDeLogin:
    def test_fallo_de_usuario_existente_registra_solo_el_id(self, base_datos_temporal, caplog):
        usuario = _crear_usuario_rapido(base_datos_temporal)

        with caplog.at_level("INFO"):
            _fallar("ana", 1)

        registro = next(r for r in caplog.records if "Login fallido" in r.getMessage())
        assert registro.levelname == "WARNING"
        assert f"usuario_id={usuario.id}" in registro.getMessage()
        assert "1/5" in registro.getMessage()
        assert "ana" not in registro.getMessage()

    def test_el_quinto_fallo_registra_el_bloqueo(self, base_datos_temporal, caplog):
        usuario = _crear_usuario_rapido(base_datos_temporal)

        with caplog.at_level("INFO"):
            _fallar("ana", 5)

        bloqueos = [r for r in caplog.records if "bloqueado temporalmente" in r.getMessage()]
        assert len(bloqueos) == 1
        assert bloqueos[0].levelname == "WARNING"
        assert f"usuario_id={usuario.id}" in bloqueos[0].getMessage()

    def test_intento_durante_bloqueo_se_registra(self, base_datos_temporal, caplog):
        usuario = _crear_usuario_rapido(base_datos_temporal)
        _fallar("ana", 5)
        caplog.clear()

        with caplog.at_level("INFO"):
            _fallar("ana", 1)

        registro = next(r for r in caplog.records if "durante bloqueo" in r.getMessage())
        assert registro.levelname == "WARNING"
        assert f"usuario_id={usuario.id}" in registro.getMessage()

    def test_exito_que_limpia_contador_se_registra_como_info(self, base_datos_temporal, caplog):
        usuario = _crear_usuario_rapido(base_datos_temporal)
        _fallar("ana", 2)

        with caplog.at_level("INFO"):
            servicio_auth.iniciar_sesion("ana", _CLAVE_OK)

        registro = next(r for r in caplog.records if "contador de fallos reiniciado" in r.getMessage())
        assert registro.levelname == "INFO"
        assert f"usuario_id={usuario.id}" in registro.getMessage()
        assert "2 fallos previos" in registro.getMessage()

    def test_login_exitoso_sin_fallos_previos_no_registra_nada(self, base_datos_temporal, caplog):
        _crear_usuario_rapido(base_datos_temporal)

        with caplog.at_level("INFO", logger=servicio_auth.logger.name):
            servicio_auth.iniciar_sesion("ana", _CLAVE_OK)

        assert caplog.records == []

    def test_usuario_inexistente_no_registra_el_nombre(self, base_datos_temporal, caplog):
        with caplog.at_level("INFO"):
            with pytest.raises(CredencialesInvalidasError):
                servicio_auth.iniciar_sesion("nombre-secreto-xyz", _CLAVE_MAL)

        assert any(r.levelname == "WARNING" for r in caplog.records)
        assert "nombre-secreto-xyz" not in caplog.text

    def test_ningun_log_contiene_contrasenas_hashes_ni_nombres_de_usuario(self, base_datos_temporal, caplog):
        usuario = _crear_usuario_rapido(base_datos_temporal)

        with caplog.at_level("DEBUG"):
            _fallar("ana", 6)

        assert _CLAVE_MAL not in caplog.text
        assert _CLAVE_OK not in caplog.text
        assert usuario.password_hash not in caplog.text
        assert "ana" not in caplog.text
