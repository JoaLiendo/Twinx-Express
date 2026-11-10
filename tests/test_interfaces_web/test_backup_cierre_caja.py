"""V1.15-B: backup automático al cerrar la caja.

Invariante 5: el cierre de caja nunca se revierte por un fallo del backup
(filesystem); el fallo queda auditado y visible hasta un backup correcto.
"""

import threading
from urllib.parse import unquote

import pytest

import services.servicio_backup as modulo_backup
from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from domain.backup import TIPO_CIERRE_CAJA
from domain.usuario import Usuario
from excepciones import ErrorBackup
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import formato_backup, servicio_auth, servicio_caja
from services.control_escrituras import ControlEscrituras, control_escrituras

from ._asgi_cliente import solicitud


@pytest.fixture(autouse=True)
def _control_escrituras_limpio():
    yield
    if control_escrituras.backup_activo:
        control_escrituras.finalizar_backup()


def _login(rol: str, nombre: str) -> tuple[Usuario, dict[str, str]]:
    usuario = repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre,
            nombre_completo="Usuario de prueba",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol=rol,
        )
    )
    return usuario, {NOMBRE_COOKIE_SESION: servicio_auth.iniciar_sesion(nombre, "clave-correcta-123").token}


def _backups(directorio):
    return sorted(directorio.glob("*.zip")) if directorio.exists() else []


def _acciones_backup():
    with obtener_conexion() as conexion:
        return [f["accion"] for f in conexion.execute("SELECT accion FROM auditoria WHERE entidad = 'BACKUP' ORDER BY id")]


def _cerrar(cookies, monto="1000"):
    return solicitud("POST", "/caja/cerrar", cookies=cookies, formulario={"monto_final": monto})


def _toast(respuesta):
    return unquote(respuesta.header("location"))


@pytest.fixture
def caja_abierta_por_owner(base_datos_temporal):
    usuario, cookies = _login("OWNER", "duenio")
    servicio_caja.abrir_caja(100_000, usuario_id=usuario.id)
    return usuario, cookies


def test_cierre_normal_genera_backup_de_cierre_validado(caja_abierta_por_owner, backups_aislados):
    usuario, cookies = caja_abierta_por_owner

    respuesta = _cerrar(cookies)

    assert respuesta.status == 303
    assert "tipo=success" in respuesta.header("location")
    assert "Backup guardado" in _toast(respuesta)
    [ruta] = _backups(backups_aislados)
    info = formato_backup.validar_backup(ruta)
    assert info.manifiesto.tipo == TIPO_CIERRE_CAJA
    assert info.manifiesto.sesion_caja_id is not None
    assert _acciones_backup() == ["BACKUP_CIERRE_CAJA_OK"]
    assert servicio_caja.consultar_estado() is False


def test_el_backup_contiene_la_caja_ya_cerrada(caja_abierta_por_owner, backups_aislados, tmp_path):
    _, cookies = caja_abierta_por_owner
    _cerrar(cookies)

    destino = tmp_path / "extraido"
    destino.mkdir()
    formato_backup.validar_backup(_backups(backups_aislados)[0], destino)
    import sqlite3

    conexion = sqlite3.connect(destino / "data" / "kiosco.db")
    try:
        estado = conexion.execute("SELECT estado FROM sesiones_caja ORDER BY id DESC LIMIT 1").fetchone()[0]
    finally:
        conexion.close()
    assert estado == "CERRADA"


def test_doble_envio_no_duplica_el_backup(caja_abierta_por_owner, backups_aislados):
    _, cookies = caja_abierta_por_owner

    primera = _cerrar(cookies)
    segunda = _cerrar(cookies)

    assert "tipo=success" in primera.header("location")
    assert "tipo=error" in segunda.header("location")
    assert len(_backups(backups_aislados)) == 1


def test_reintento_del_backup_de_la_misma_sesion_reutiliza_el_existente(caja_abierta_por_owner, backups_aislados):
    _, cookies = caja_abierta_por_owner
    _cerrar(cookies)
    [ruta] = _backups(backups_aislados)
    sesion_id = formato_backup.validar_backup(ruta).manifiesto.sesion_caja_id

    resultado = modulo_backup.backup_de_cierre_de_caja(sesion_id, ControlEscrituras())

    assert resultado.ok and resultado.ya_existia and resultado.ruta == ruta
    assert len(_backups(backups_aislados)) == 1


def test_reintento_con_backup_previo_corrupto_genera_uno_nuevo(caja_abierta_por_owner, backups_aislados):
    _, cookies = caja_abierta_por_owner
    _cerrar(cookies)
    [ruta] = _backups(backups_aislados)
    sesion_id = formato_backup.validar_backup(ruta).manifiesto.sesion_caja_id
    datos = bytearray(ruta.read_bytes())
    datos[200] ^= 0xFF
    ruta.write_bytes(bytes(datos))

    resultado = modulo_backup.backup_de_cierre_de_caja(sesion_id, ControlEscrituras())

    assert resultado.ok and not resultado.ya_existia and resultado.ruta != ruta


def _crear_que_falla(*_args, **_kwargs):
    raise ErrorBackup("No hay espacio suficiente en el disco para guardar el backup.")


def test_si_el_backup_falla_la_caja_queda_cerrada_y_se_avisa(caja_abierta_por_owner, backups_aislados, monkeypatch):
    _, cookies = caja_abierta_por_owner
    monkeypatch.setattr(modulo_backup, "crear_backup", _crear_que_falla)

    respuesta = _cerrar(cookies)

    assert respuesta.status == 303
    assert "tipo=warning" in respuesta.header("location")
    assert "no se pudo guardar el backup" in _toast(respuesta)
    assert servicio_caja.consultar_estado() is False
    assert _backups(backups_aislados) == []
    assert _acciones_backup() == ["BACKUP_ERROR"]


def test_fallo_inesperado_del_backup_no_rompe_el_cierre(caja_abierta_por_owner, monkeypatch):
    _, cookies = caja_abierta_por_owner

    def explota(*_args, **_kwargs):
        raise RuntimeError("fallo inesperado")

    monkeypatch.setattr(modulo_backup, "crear_backup", explota)

    respuesta = _cerrar(cookies)

    assert respuesta.status == 303
    assert "tipo=warning" in respuesta.header("location")
    assert servicio_caja.consultar_estado() is False
    assert _acciones_backup() == ["BACKUP_ERROR"]


def test_la_alerta_sigue_visible_en_la_apertura_siguiente_hasta_un_backup_correcto(
    caja_abierta_por_owner, monkeypatch
):
    _, cookies = caja_abierta_por_owner
    crear_real = modulo_backup.crear_backup
    monkeypatch.setattr(modulo_backup, "crear_backup", _crear_que_falla)
    _cerrar(cookies)
    monkeypatch.setattr(modulo_backup, "crear_backup", crear_real)

    _, cookies_cajero = _login("CASHIER", "cajero")
    solicitud("POST", "/caja/abrir", cookies=cookies_cajero, formulario={"monto_inicial": "1000"})
    panel_cajero = solicitud("GET", "/caja", cookies=cookies_cajero).texto
    assert 'id="alerta-backup"' in panel_cajero
    assert "Avisá al dueño" in panel_cajero

    panel_owner = solicitud("GET", "/backup", cookies=cookies).texto
    assert 'id="alerta-backup"' in panel_owner

    assert solicitud("POST", "/backup/crear", cookies=cookies).status == 303
    assert 'id="alerta-backup"' not in solicitud("GET", "/caja", cookies=cookies).texto
    assert modulo_backup.alerta_de_backup() is None


def test_cierre_rechazado_mientras_hay_un_backup_en_curso(caja_abierta_por_owner):
    _, cookies = caja_abierta_por_owner
    control_escrituras.iniciar_backup()
    try:
        respuesta = _cerrar(cookies)
    finally:
        control_escrituras.finalizar_backup()

    assert "tipo=error" in respuesta.header("location")
    assert servicio_caja.consultar_estado() is True
    assert control_escrituras.escrituras_de_negocio_en_curso == 0


def test_el_backup_de_cierre_espera_a_un_backup_en_curso_en_vez_de_perderse(base_datos_temporal):
    control = ControlEscrituras()
    control.iniciar_backup()
    liberado = threading.Timer(0.3, control.finalizar_backup)
    liberado.start()

    resultado = modulo_backup.backup_de_cierre_de_caja(1, control)

    liberado.join()
    assert resultado.ok


def test_backup_manual_concurrente_rechaza_el_segundo_sin_auditarlo_como_error(base_datos_temporal):
    control = ControlEscrituras()
    control.iniciar_backup()
    try:
        from excepciones import BackupEnCursoError

        with pytest.raises(BackupEnCursoError):
            modulo_backup.ejecutar_backup("MANUAL", control)
    finally:
        control.finalizar_backup()

    assert _acciones_backup() == []


def test_backup_manual_auditado(base_datos_temporal):
    resultado = modulo_backup.ejecutar_backup("MANUAL", ControlEscrituras(), usuario_id=None)

    assert resultado.ok
    assert _acciones_backup() == ["BACKUP_MANUAL_OK"]


def test_el_panel_nombra_en_castellano_el_tipo_del_ultimo_backup(caja_abierta_por_owner):
    """Auditoría visual del cierre: el panel mostraba el valor interno («cierre caja», «automatico»)."""
    _, cookies = caja_abierta_por_owner
    _cerrar(cookies)
    html = solicitud("GET", "/backup", cookies=cookies).texto
    assert "(cierre de caja)" in html and "(cierre caja)" not in html
