"""V1.17: errores 500/422/503 amigables para quien navega, sin tocar el contrato JSON de `/api/`."""

import logging
import sqlite3
from urllib.parse import unquote

import pytest

import db.conexion as modulo_conexion
import services.servicio_auditoria as modulo_auditoria
import services.servicio_caja as modulo_caja
from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from excepciones import DatosInvalidosError, ErrorBaseDatos
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth
from services.control_escrituras import control_escrituras

from ._asgi_cliente import solicitud

_NAVEGADOR = {"accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
_DETALLE_TECNICO = 'SELECT clave FROM usuarios en C:\\Users\\dueno\\kiosco.db: "token-secreto"'
_MENSAJE_500 = "Algo salió mal. La operación no pudo completarse."
_MENSAJE_422 = "Hay datos inválidos o incompletos."
_MENSAJE_503 = "El servicio no está disponible temporalmente."
_MENSAJE_BACKUP = "Hay un backup en curso. Probá de nuevo en unos segundos."


@pytest.fixture(autouse=True)
def _sin_backup_colgado():
    yield
    if control_escrituras.backup_activo:
        control_escrituras.finalizar_backup()


@pytest.fixture
def cookies(base_datos_temporal):
    repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario="duenio",
            nombre_completo="Dueño",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol="OWNER",
        )
    )
    return {NOMBRE_COOKIE_SESION: servicio_auth.iniciar_sesion("duenio", "clave-correcta-123").token}


def _romper(*_args, **_kwargs):
    raise RuntimeError(_DETALLE_TECNICO)


class Test500:
    def test_navegacion_ve_una_pagina_amigable_con_el_codigo_correcto(self, cookies, monkeypatch):
        monkeypatch.setattr(modulo_auditoria, "listar_pagina", _romper)

        respuesta = solicitud("GET", "/auditoria", cookies=cookies, headers=_NAVEGADOR, tolerar_error_servidor=True)

        assert respuesta.status == 500
        assert respuesta.header("content-type").startswith("text/html")
        assert _MENSAJE_500 in respuesta.texto
        assert "Internal Server Error" not in respuesta.texto

    def test_no_muestra_traza_sql_rutas_ni_secretos(self, cookies, monkeypatch):
        monkeypatch.setattr(modulo_auditoria, "listar_pagina", _romper)

        texto = solicitud("GET", "/auditoria", cookies=cookies, headers=_NAVEGADOR, tolerar_error_servidor=True).texto

        for filtrado in ("Traceback", "RuntimeError", "SELECT", "kiosco.db", "token-secreto", "C:\\"):
            assert filtrado not in texto

    def test_la_pagina_no_depende_de_la_base_que_pudo_causar_el_error(self, cookies, monkeypatch):
        monkeypatch.setattr(modulo_caja, "consultar_estado", _romper)

        respuesta = solicitud("GET", "/auditoria", cookies=cookies, headers=_NAVEGADOR, tolerar_error_servidor=True)

        assert respuesta.status == 500
        assert _MENSAJE_500 in respuesta.texto

    def test_deja_el_detalle_tecnico_en_el_log(self, cookies, monkeypatch, caplog):
        monkeypatch.setattr(modulo_auditoria, "listar_pagina", _romper)

        with caplog.at_level(logging.ERROR, logger="interfaces.web.app"):
            solicitud("GET", "/auditoria?msg=dato", cookies=cookies, headers=_NAVEGADOR, tolerar_error_servidor=True)

        assert "GET /auditoria" in caplog.text
        assert "RuntimeError" in caplog.text
        assert "msg=dato" not in caplog.text

    def test_la_api_responde_json_aunque_acepte_html(self, cookies, monkeypatch):
        import services.servicio_clientes as modulo_clientes

        monkeypatch.setattr(modulo_clientes, "buscar_clientes_activos_para_venta", _romper)

        respuesta = solicitud(
            "GET", "/api/clientes/buscar?q=ana", cookies=cookies, headers=_NAVEGADOR, tolerar_error_servidor=True
        )

        assert respuesta.status == 500
        assert respuesta.json() == {"error": _MENSAJE_500}


class Test422:
    def test_navegacion_ve_una_pagina_amigable(self, base_datos_temporal):
        respuesta = solicitud("POST", "/login", formulario={"nombre_usuario": "ana"}, headers=_NAVEGADOR)

        assert respuesta.status == 422
        assert respuesta.header("content-type").startswith("text/html")
        assert _MENSAJE_422 in respuesta.texto
        assert '"detail"' not in respuesta.texto

    def test_fuera_de_la_navegacion_no_devuelve_el_detalle_crudo(self, base_datos_temporal):
        respuesta = solicitud("POST", "/login", formulario={"password": "valor-enviado-secreto"})

        assert respuesta.status == 422
        assert respuesta.json() == {"error": "Los datos enviados no son válidos."}

    def test_la_api_conserva_su_json(self, cookies):
        respuesta = solicitud("POST", "/api/ventas", cookies=cookies, json_body={"items": "no-es-una-lista"}, headers=_NAVEGADOR)

        assert respuesta.status == 422
        assert respuesta.json() == {"error": "Los datos enviados no son válidos."}


class Test503:
    def test_navegacion_ve_una_pagina_con_el_motivo(self, base_datos_temporal):
        control_escrituras.iniciar_backup()

        respuesta = solicitud("POST", "/login", formulario={"nombre_usuario": "x", "password": "y"}, headers=_NAVEGADOR)

        assert respuesta.status == 503
        assert respuesta.header("content-type").startswith("text/html")
        assert _MENSAJE_503 in respuesta.texto
        assert _MENSAJE_BACKUP in respuesta.texto

    def test_la_api_conserva_su_json(self, cookies):
        control_escrituras.iniciar_backup()

        respuesta = solicitud("POST", "/api/ventas", cookies=cookies, json_body={}, headers=_NAVEGADOR)

        assert respuesta.status == 503
        assert respuesta.json() == {"error": _MENSAJE_BACKUP}

    def test_un_cliente_que_no_pide_html_sigue_recibiendo_json(self, base_datos_temporal):
        control_escrituras.iniciar_backup()

        respuesta = solicitud("POST", "/login", formulario={"nombre_usuario": "x", "password": "y"})

        assert respuesta.status == 503
        assert respuesta.json() == {"error": _MENSAJE_BACKUP}


class TestErroresDeNegocio:
    def test_siguen_siendo_un_aviso_en_la_pagina_de_origen(self, cookies, monkeypatch):
        def rechazar(*_args, **_kwargs):
            raise DatosInvalidosError("El dato de negocio no es válido.")

        monkeypatch.setattr(modulo_auditoria, "listar_pagina", rechazar)

        respuesta = solicitud("GET", "/auditoria", cookies=cookies, headers=_NAVEGADOR)

        assert respuesta.status == 303
        assert "El dato de negocio no es válido." in unquote(respuesta.header("location"))

    def test_en_la_api_conservan_el_mensaje_y_el_422(self, cookies, monkeypatch):
        def rechazar(*_args, **_kwargs):
            raise DatosInvalidosError("El dato de negocio no es válido.")

        import services.servicio_clientes as modulo_clientes

        monkeypatch.setattr(modulo_clientes, "buscar_clientes_activos_para_venta", rechazar)

        respuesta = solicitud("GET", "/api/clientes/buscar?q=ana", cookies=cookies)

        assert respuesta.status == 422
        assert respuesta.json() == {"error": "El dato de negocio no es válido."}


def _ejecutar_en_base(*sentencias: str):
    def ejecutar(*_args, **_kwargs):
        with modulo_conexion.obtener_conexion() as conexion:
            for sentencia in sentencias:
                conexion.execute(sentencia)

    return ejecutar


_MENSAJE_BASE = "Error al operar sobre la base de datos."
_TABLA_INEXISTENTE = ("SELECT * FROM tabla_que_no_existe",)
_UNICO_DUPLICADO = (
    "CREATE TEMP TABLE repetida (dato TEXT UNIQUE)",
    "INSERT INTO repetida VALUES ('a')",
    "INSERT INTO repetida VALUES ('a')",
)
_TRIGGER_DE_NEGOCIO = (
    "CREATE TEMP TABLE protegida (dato TEXT)",
    "CREATE TEMP TRIGGER no_tocar BEFORE INSERT ON protegida BEGIN SELECT RAISE(ABORT, 'Ese dato no se modifica.'); END",
    "INSERT INTO protegida VALUES ('a')",
)


class TestErrorDeBase:
    def _aviso(self, cookies, monkeypatch, sentencias) -> str:
        monkeypatch.setattr(modulo_auditoria, "listar_pagina", _ejecutar_en_base(*sentencias))
        respuesta = solicitud("GET", "/auditoria", cookies=cookies, headers=_NAVEGADOR)
        assert respuesta.status == 303
        return unquote(respuesta.header("location"))

    def test_una_tabla_inexistente_no_llega_a_la_pantalla(self, cookies, monkeypatch):
        aviso = self._aviso(cookies, monkeypatch, _TABLA_INEXISTENTE)

        assert _MENSAJE_BASE in aviso
        assert "tabla_que_no_existe" not in aviso
        assert "no such table" not in aviso

    def test_una_restriccion_no_revela_tabla_ni_columna(self, cookies, monkeypatch):
        aviso = self._aviso(cookies, monkeypatch, _UNICO_DUPLICADO)

        assert _MENSAJE_BASE in aviso
        assert "repetida" not in aviso
        assert "UNIQUE" not in aviso

    def test_el_aviso_en_espanol_de_un_trigger_se_conserva(self, cookies, monkeypatch):
        assert "Ese dato no se modifica." in self._aviso(cookies, monkeypatch, _TRIGGER_DE_NEGOCIO)

    def test_en_la_api_tampoco_llega_el_texto_de_sqlite(self, cookies, monkeypatch):
        import services.servicio_clientes as modulo_clientes

        monkeypatch.setattr(modulo_clientes, "buscar_clientes_activos_para_venta", _ejecutar_en_base(*_TABLA_INEXISTENTE))

        respuesta = solicitud("GET", "/api/clientes/buscar?q=ana", cookies=cookies)

        assert respuesta.status == 422
        assert respuesta.json() == {"error": _MENSAJE_BASE}

    def test_la_excepcion_conserva_el_detalle_tecnico_para_diagnostico(self, base_datos_temporal):
        with pytest.raises(ErrorBaseDatos) as error:
            _ejecutar_en_base(*_TABLA_INEXISTENTE)()

        assert "no such table: tabla_que_no_existe" in str(error.value)

    def test_el_detalle_queda_en_el_log(self, base_datos_temporal, caplog):
        with caplog.at_level(logging.ERROR, logger="db.conexion"):
            with pytest.raises(ErrorBaseDatos):
                _ejecutar_en_base(*_TABLA_INEXISTENTE)()

        assert "tabla_que_no_existe" in caplog.text

    def test_la_base_ocupada_conserva_su_mensaje_propio(self, base_datos_temporal):
        with pytest.raises(ErrorBaseDatos) as error:
            with modulo_conexion.obtener_conexion():
                raise sqlite3.OperationalError("database is locked")

        assert error.value.mensaje_usuario == modulo_conexion._MENSAJE_BASE_DATOS_OCUPADA
