"""V1.15-D: segundo destino opcional para copias.

Invariante 3: un fallo del segundo destino nunca destruye el primario.
"""

import shutil
from datetime import datetime
from urllib.parse import unquote

import pytest

import config
import services.servicio_backup as modulo_backup
from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from excepciones import DatosInvalidosError
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import formato_backup, servicio_auth, servicio_destino_secundario
from services.control_escrituras import ControlEscrituras
from services.formato_backup import hash_archivo
from tests.test_interfaces_web._asgi_cliente import solicitud


@pytest.fixture
def owner(base_datos_temporal):
    usuario = repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario="duenio",
            nombre_completo="Dueño",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol="OWNER",
        )
    )
    return usuario, {NOMBRE_COOKIE_SESION: servicio_auth.iniciar_sesion("duenio", "clave-correcta-123").token}


@pytest.fixture
def externo(tmp_path):
    carpeta = tmp_path / "Disco externo ñ"
    carpeta.mkdir()
    return carpeta


def _acciones():
    with obtener_conexion() as conexion:
        return [f["accion"] for f in conexion.execute("SELECT accion FROM auditoria ORDER BY id")]


def _configurar(externo, usuario):
    servicio_destino_secundario.guardar_destino_secundario(str(externo), usuario.id)


def test_sin_segundo_destino_funciona_normalmente(owner, backups_aislados):
    resultado = modulo_backup.ejecutar_backup("MANUAL", ControlEscrituras())

    assert resultado.ok
    assert modulo_backup.estado_backups().destino_secundario is None
    assert "BACKUP_SECUNDARIO_OK" not in _acciones()


def test_copia_verificada_al_segundo_destino(owner, externo):
    _configurar(externo, owner[0])

    resultado = modulo_backup.ejecutar_backup("MANUAL", ControlEscrituras())

    copia = externo / resultado.ruta.name
    assert copia.is_file()
    assert hash_archivo(copia) == hash_archivo(resultado.ruta)
    assert formato_backup.validar_backup(copia).manifiesto is not None
    assert not list(externo.glob(".*"))
    assert _acciones()[-1] == "BACKUP_SECUNDARIO_OK"
    estado = modulo_backup.estado_backups()
    assert estado.ultima_copia_secundaria is not None and estado.error_secundario is None


def test_destino_desconectado_no_afecta_al_primario(owner, externo):
    _configurar(externo, owner[0])
    shutil.rmtree(externo)

    resultado = modulo_backup.ejecutar_backup("MANUAL", ControlEscrituras())

    assert resultado.ok and resultado.ruta.is_file()
    formato_backup.validar_backup(resultado.ruta)
    assert _acciones()[-2:] == ["BACKUP_MANUAL_OK", "BACKUP_SECUNDARIO_ERROR"]
    assert modulo_backup.estado_backups().error_secundario is not None
    assert modulo_backup.alerta_de_backup() is None  # el primario está bien


def test_copia_corrupta_se_descarta_y_el_primario_sigue(owner, externo, monkeypatch):
    _configurar(externo, owner[0])

    def copia_que_corrompe(origen, destino):
        destino.write_bytes(b"basura")

    monkeypatch.setattr(servicio_destino_secundario.shutil, "copyfile", copia_que_corrompe)

    resultado = modulo_backup.ejecutar_backup("MANUAL", ControlEscrituras())

    assert resultado.ruta.is_file()
    assert list(externo.iterdir()) == []
    assert _acciones()[-1] == "BACKUP_SECUNDARIO_ERROR"


def test_sin_espacio_en_el_destino(owner, externo, monkeypatch):
    _configurar(externo, owner[0])
    monkeypatch.setattr(
        servicio_destino_secundario.shutil, "disk_usage", lambda _r: shutil._ntuple_diskusage(10, 10, 0)
    )

    resultado = modulo_backup.ejecutar_backup("MANUAL", ControlEscrituras())

    assert resultado.ok
    assert _acciones()[-1] == "BACKUP_SECUNDARIO_ERROR"


def test_permiso_denegado_durante_la_copia(owner, externo, monkeypatch):
    _configurar(externo, owner[0])

    def copia_sin_permiso(*_args):
        raise PermissionError(13, "Acceso denegado")

    monkeypatch.setattr(servicio_destino_secundario.shutil, "copyfile", copia_sin_permiso)

    resultado = modulo_backup.ejecutar_backup("MANUAL", ControlEscrituras())

    assert resultado.ruta.is_file()
    assert _acciones()[-1] == "BACKUP_SECUNDARIO_ERROR"


def test_retencion_tambien_en_el_destino_sin_tocar_archivos_ajenos(owner, externo):
    _configurar(externo, owner[0])
    (externo / "fotos_familia.zip").write_bytes(b"ajeno")
    for dia in range(1, 20):
        ruta = modulo_backup.crear_backup(
            modulo_backup.DIRECTORIO_BACKUPS, ControlEscrituras(), tipo="CIERRE_CAJA", ahora=datetime(2026, 1, dia, 21)
        )
        modulo_backup.copiar_a_destino_secundario(ruta)

    nombres = {p.name for p in externo.iterdir()}
    assert "fotos_familia.zip" in nombres
    primarios = {p.name for p in modulo_backup.DIRECTORIO_BACKUPS.glob("*.zip")}
    assert nombres - {"fotos_familia.zip"} == primarios  # misma política en ambos destinos
    assert len(primarios) < 19


@pytest.mark.parametrize(
    "texto",
    ["relativa\\carpeta", "Z:\\no\\existe\\seguro_12345", "x" * 500],
)
def test_rutas_invalidas(owner, texto):
    with pytest.raises(DatosInvalidosError):
        servicio_destino_secundario.guardar_destino_secundario(texto, owner[0].id)


def test_no_puede_ser_la_carpeta_de_datos_ni_la_principal(owner, monkeypatch, tmp_path):
    datos = tmp_path / "data"
    datos.mkdir()
    monkeypatch.setattr(config, "DIRECTORIO_DATA", datos)
    modulo_backup.DIRECTORIO_BACKUPS.mkdir(parents=True, exist_ok=True)

    for prohibida in (datos, modulo_backup.DIRECTORIO_BACKUPS, modulo_backup.DIRECTORIO_BACKUPS.parent):
        with pytest.raises(DatosInvalidosError):
            servicio_destino_secundario.guardar_destino_secundario(str(prohibida), owner[0].id)


def test_vacio_desactiva_y_se_audita(owner, externo):
    _configurar(externo, owner[0])
    servicio_destino_secundario.guardar_destino_secundario("", owner[0].id)

    assert servicio_destino_secundario.obtener_destino_secundario() is None
    assert _acciones().count("CONFIGURACION_BACKUP_CAMBIADA") == 2


def test_pagina_de_backup_muestra_falta_de_redundancia_y_permite_configurar(owner, externo):
    _, cookies = owner

    pagina = solicitud("GET", "/backup", cookies=cookies).texto
    assert 'id="backup-sin-redundancia"' in pagina

    respuesta = solicitud("POST", "/backup/destino-secundario", cookies=cookies, formulario={"destino": str(externo)})
    assert "tipo=success" in respuesta.header("location")

    pagina = solicitud("GET", "/backup", cookies=cookies).texto
    assert 'id="backup-sin-redundancia"' not in pagina
    assert str(externo) in pagina


def test_ruta_invalida_por_web_da_error_claro(owner):
    _, cookies = owner

    respuesta = solicitud("POST", "/backup/destino-secundario", cookies=cookies, formulario={"destino": "Z:\\nada\\x"})

    assert "tipo=error" in respuesta.header("location")
    assert "no existe" in unquote(respuesta.header("location"))


def test_cajero_no_puede_configurar_el_segundo_destino(base_datos_temporal, externo):
    repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario="cajero",
            nombre_completo="Cajero",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol="CASHIER",
        )
    )
    cookies = {NOMBRE_COOKIE_SESION: servicio_auth.iniciar_sesion("cajero", "clave-correcta-123").token}

    respuesta = solicitud("POST", "/backup/destino-secundario", cookies=cookies, formulario={"destino": str(externo)})

    assert respuesta.status in (303, 403)
    assert servicio_destino_secundario.obtener_destino_secundario() is None
