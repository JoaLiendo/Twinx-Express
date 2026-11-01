"""V1.15-E: backup PRE_UPDATE obligatorio antes de migrar una base existente.

Invariante 2: un pre-update inválido o fallido impide que las migraciones
empiecen. Se usa el esquema real: una base "vieja" con las primeras
migraciones del proyecto que se actualiza con las restantes (varios saltos).
"""

import shutil
import sqlite3
import threading

import pytest

import config
import db.conexion as modulo_conexion
import services.servicio_backup as modulo_backup
import services.servicio_imagenes as modulo_imagenes
from domain.backup import TIPO_PRE_UPDATE
from excepciones import BackupInvalidoError, DatosDeVersionMasNuevaError, ErrorBackup, ErrorBaseDatos
from services import formato_backup
from services.control_escrituras import ControlEscrituras
from version import VERSION

_REALES = sorted(config.DIRECTORIO_MIGRACIONES.glob("*.sql"))
_VIEJAS = 38


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    datos = tmp_path / "datos"
    (datos / "imagenes_productos").mkdir(parents=True)
    migraciones = tmp_path / "migraciones"
    migraciones.mkdir()
    for ruta in _REALES[:_VIEJAS]:
        shutil.copy(ruta, migraciones / ruta.name)
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", datos / "kiosco.db")
    monkeypatch.setattr(modulo_conexion, "DIRECTORIO_MIGRACIONES", migraciones)
    monkeypatch.setattr(modulo_imagenes, "DIRECTORIO_IMAGENES_PRODUCTOS", datos / "imagenes_productos")
    return {"db": datos / "kiosco.db", "migraciones": migraciones, "backups": tmp_path / "backups"}


def _migrar(entorno, control=None):
    return modulo_backup.migrar_base_datos_con_backup_preventivo(entorno["backups"], control or ControlEscrituras())


def _base_vieja_con_datos(entorno):
    modulo_conexion.inicializar_base_datos()
    with modulo_conexion.obtener_conexion() as conexion:
        conexion.execute(
            "INSERT INTO productos (codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos, stock_actual)"
            " VALUES ('779', 'Alfajor', 100, 250, 12)"
        )


def _agregar_migraciones_nuevas(entorno):
    for ruta in _REALES[_VIEJAS:]:
        shutil.copy(ruta, entorno["migraciones"] / ruta.name)


def _aplicadas(ruta_db):
    conexion = sqlite3.connect(ruta_db)
    try:
        return [f[0] for f in conexion.execute("SELECT nombre_archivo FROM schema_migraciones ORDER BY 1")]
    finally:
        conexion.close()


def _zips(entorno):
    return sorted(entorno["backups"].glob("*.zip")) if entorno["backups"].exists() else []


def _acciones(entorno):
    conexion = sqlite3.connect(entorno["db"])
    try:
        return [f[0] for f in conexion.execute("SELECT accion FROM auditoria WHERE entidad = 'BACKUP' ORDER BY id")]
    finally:
        conexion.close()


def test_base_nueva_no_genera_pre_update_y_registra_la_version(entorno):
    _agregar_migraciones_nuevas(entorno)

    assert _migrar(entorno) is None
    assert _zips(entorno) == []
    assert modulo_backup.version_registrada_en_la_base() == VERSION


def test_actualizacion_real_con_varios_saltos(entorno):
    _base_vieja_con_datos(entorno)
    viejas = _aplicadas(entorno["db"])
    _agregar_migraciones_nuevas(entorno)

    ruta = _migrar(entorno)

    assert _zips(entorno) == [ruta]
    info = formato_backup.validar_backup(ruta)
    assert info.manifiesto.tipo == TIPO_PRE_UPDATE
    assert list(info.manifiesto.migraciones) == viejas  # el esquema de ANTES de migrar
    assert info.manifiesto.version_destino == VERSION
    assert info.manifiesto.version_origen is None  # base anterior a V1.15
    assert _aplicadas(entorno["db"]) == [r.name for r in _REALES]
    assert "PRE_UPDATE_BACKUP_OK" in _acciones(entorno)
    with modulo_conexion.obtener_conexion() as conexion:
        assert conexion.execute("SELECT stock_actual FROM productos WHERE codigo_barras = '779'").fetchone()[0] == 12


def test_segundo_arranque_no_genera_otro_pre_update(entorno):
    _base_vieja_con_datos(entorno)
    _agregar_migraciones_nuevas(entorno)
    _migrar(entorno)

    assert _migrar(entorno) is None
    assert len(_zips(entorno)) == 1


def test_la_version_de_origen_queda_en_el_proximo_pre_update(entorno):
    _base_vieja_con_datos(entorno)
    _migrar(entorno)  # deja registrada la versión actual
    _agregar_migraciones_nuevas(entorno)

    ruta = _migrar(entorno)

    assert formato_backup.validar_backup(ruta).manifiesto.version_origen == VERSION


def test_si_el_backup_falla_las_migraciones_no_empiezan(entorno, monkeypatch):
    _base_vieja_con_datos(entorno)
    viejas = _aplicadas(entorno["db"])
    _agregar_migraciones_nuevas(entorno)

    def crear_que_falla(*_args, **_kwargs):
        raise ErrorBackup("No hay espacio suficiente en el disco para guardar el backup.")

    monkeypatch.setattr(modulo_backup, "crear_backup", crear_que_falla)

    with pytest.raises(ErrorBackup):
        _migrar(entorno)

    assert _aplicadas(entorno["db"]) == viejas
    assert "BACKUP_ERROR" in _acciones(entorno)


def test_si_el_backup_no_valida_las_migraciones_no_empiezan(entorno, monkeypatch):
    _base_vieja_con_datos(entorno)
    viejas = _aplicadas(entorno["db"])
    _agregar_migraciones_nuevas(entorno)

    def validar_que_falla(_ruta):
        raise BackupInvalidoError("integridad simulada")

    monkeypatch.setattr(modulo_backup, "validar_backup", validar_que_falla)

    with pytest.raises(ErrorBackup):
        _migrar(entorno)

    assert _aplicadas(entorno["db"]) == viejas
    assert _zips(entorno) == []


def test_crash_durante_la_migracion_y_reintento(entorno):
    _base_vieja_con_datos(entorno)
    viejas = _aplicadas(entorno["db"])
    _agregar_migraciones_nuevas(entorno)
    rota = entorno["migraciones"] / "999_rota.sql"
    rota.write_text("CREATE TABLE a_medias (id INTEGER); ESTO NO ES SQL;", encoding="utf-8")

    with pytest.raises(ErrorBaseDatos):
        _migrar(entorno)

    [primero] = _zips(entorno)
    assert list(formato_backup.validar_backup(primero).manifiesto.migraciones) == viejas
    assert "999_rota.sql" not in _aplicadas(entorno["db"])

    rota.write_text("CREATE TABLE ya_corregida (id INTEGER);", encoding="utf-8")
    segundo = _migrar(entorno)

    assert segundo is not None and primero.exists()
    assert "999_rota.sql" in _aplicadas(entorno["db"])


def test_base_de_una_version_mas_nueva_no_se_toca(entorno):
    _base_vieja_con_datos(entorno)
    with modulo_conexion.obtener_conexion() as conexion:
        conexion.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES ('999_de_la_version_futura.sql')")
    antes = entorno["db"].read_bytes()

    with pytest.raises(DatosDeVersionMasNuevaError, match="más nueva"):
        _migrar(entorno)

    assert entorno["db"].read_bytes() == antes
    assert _zips(entorno) == []


def test_dos_arranques_simultaneos_dejan_la_base_consistente(entorno):
    """En producción el puerto ocupado impide la segunda instancia antes de
    migrar; aun así, dos intentos a la vez no pueden dejar media migración."""
    _base_vieja_con_datos(entorno)
    _agregar_migraciones_nuevas(entorno)
    control = ControlEscrituras()
    errores = []

    def arrancar():
        try:
            _migrar(entorno, control)
        except ErrorBaseDatos as error:
            errores.append(error)

    hilos = [threading.Thread(target=arrancar) for _ in range(2)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    assert len(errores) <= 1
    assert _aplicadas(entorno["db"]) == [r.name for r in _REALES]
    conexion = sqlite3.connect(entorno["db"])
    try:
        assert conexion.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conexion.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conexion.close()
    for ruta in _zips(entorno):
        formato_backup.validar_backup(ruta)
