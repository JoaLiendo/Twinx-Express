"""Tests del backup preventivo antes de migrar
(`servicio_backup.migrar_base_datos_con_backup_preventivo`).

Usan un directorio de migraciones propio y pequeño (no el real) para
controlar exactamente qué está aplicado y qué está pendiente, sin simular
la aplicación completa.
"""

import re
import sqlite3
import zipfile
from pathlib import Path

import pytest

import db.conexion as modulo_conexion
import services.servicio_backup as modulo_backup
import services.servicio_restore as modulo_restore
from excepciones import ErrorBackup, ErrorBaseDatos
from services.control_escrituras import ControlEscrituras

_PATRON_NOMBRE_PREVENTIVO = re.compile(r"^KioscoApp_backup_pre_migracion_\d{4}-\d{2}-\d{2}_\d{6}\.zip$")

_MIGRACION_1 = "CREATE TABLE datos (id INTEGER PRIMARY KEY, valor TEXT);"
_MIGRACION_2 = "CREATE TABLE extra (id INTEGER PRIMARY KEY);"


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    """Redirige DB, imágenes y migraciones de `db.conexion` y
    `servicio_backup` a directorios temporales. La DB aún no existe."""
    dir_datos = tmp_path / "datos"
    dir_imagenes = dir_datos / "imagenes_productos"
    dir_imagenes.mkdir(parents=True)
    dir_migraciones = tmp_path / "migraciones"
    dir_migraciones.mkdir()
    ruta_db = dir_datos / "kiosco.db"

    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta_db)
    monkeypatch.setattr(modulo_conexion, "DIRECTORIO_MIGRACIONES", dir_migraciones)
    monkeypatch.setattr(modulo_backup, "RUTA_BASE_DATOS", ruta_db)
    monkeypatch.setattr(modulo_backup, "DIRECTORIO_IMAGENES_PRODUCTOS", dir_imagenes)

    return {"db": ruta_db, "migraciones": dir_migraciones, "backups": tmp_path / "backups"}


def _escribir_migracion(entorno, nombre: str, sql: str) -> None:
    (entorno["migraciones"] / nombre).write_text(sql, encoding="utf-8")


def _tablas(ruta_db: Path) -> set[str]:
    conexion = sqlite3.connect(ruta_db)
    try:
        filas = conexion.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        conexion.close()
    return {fila[0] for fila in filas}


def _zips(entorno) -> list[Path]:
    return sorted(entorno["backups"].glob("*.zip")) if entorno["backups"].exists() else []


def _migrar(entorno):
    return modulo_backup.migrar_base_datos_con_backup_preventivo(entorno["backups"], ControlEscrituras())


def _dejar_base_con_migracion_1_y_una_pendiente(entorno) -> None:
    """DB existente con la migración 001 aplicada (y una fila
    identificable) y la 002 recién agregada, todavía sin aplicar."""
    _escribir_migracion(entorno, "001_datos.sql", _MIGRACION_1)
    modulo_conexion.inicializar_base_datos()
    with modulo_conexion.obtener_conexion() as conexion:
        conexion.execute("INSERT INTO datos (valor) VALUES ('SENTINEL_PRE_MIGRACION')")
    _escribir_migracion(entorno, "002_extra.sql", _MIGRACION_2)


def test_base_nueva_migra_sin_crear_backup_preventivo(entorno):
    _escribir_migracion(entorno, "001_datos.sql", _MIGRACION_1)
    assert not entorno["db"].exists()

    resultado = _migrar(entorno)

    assert resultado is None
    assert _zips(entorno) == []
    assert "datos" in _tablas(entorno["db"])


def test_archivo_de_base_vacio_se_trata_como_instalacion_nueva(entorno):
    _escribir_migracion(entorno, "001_datos.sql", _MIGRACION_1)
    entorno["db"].write_bytes(b"")

    resultado = _migrar(entorno)

    assert resultado is None
    assert _zips(entorno) == []
    assert "datos" in _tablas(entorno["db"])


def test_base_existente_sin_migraciones_pendientes_no_crea_backup(entorno):
    _escribir_migracion(entorno, "001_datos.sql", _MIGRACION_1)
    modulo_conexion.inicializar_base_datos()

    resultado = _migrar(entorno)

    assert resultado is None
    assert _zips(entorno) == []


def test_base_existente_con_migraciones_pendientes_crea_exactamente_un_backup_antes_de_migrar(entorno):
    _dejar_base_con_migracion_1_y_una_pendiente(entorno)

    resultado = _migrar(entorno)

    zips = _zips(entorno)
    assert zips == [resultado]
    assert _PATRON_NOMBRE_PREVENTIVO.match(resultado.name), resultado.name
    assert "extra" in _tablas(entorno["db"])  # la migración pendiente sí se aplicó


def test_el_backup_contiene_la_base_anterior_a_la_migracion(entorno, tmp_path):
    _dejar_base_con_migracion_1_y_una_pendiente(entorno)
    ruta_backup = _migrar(entorno)

    extraido = tmp_path / "extraido"
    with zipfile.ZipFile(ruta_backup) as zf:
        zf.extractall(extraido)

    assert _tablas(extraido / "kiosco.db") >= {"datos"}
    assert "extra" not in _tablas(extraido / "kiosco.db")
    conexion = sqlite3.connect(extraido / "kiosco.db")
    try:
        assert conexion.execute("SELECT valor FROM datos").fetchone()[0] == "SENTINEL_PRE_MIGRACION"
    finally:
        conexion.close()


def test_si_el_backup_falla_no_se_ejecutan_las_migraciones(entorno, monkeypatch):
    _dejar_base_con_migracion_1_y_una_pendiente(entorno)

    def backup_que_falla(*_args, **_kwargs):
        raise ErrorBackup("fallo simulado")

    monkeypatch.setattr(modulo_backup, "crear_backup", backup_que_falla)

    with pytest.raises(ErrorBackup):
        _migrar(entorno)

    assert "extra" not in _tablas(entorno["db"])
    with modulo_conexion.obtener_conexion() as conexion:
        aplicadas = {f["nombre_archivo"] for f in conexion.execute("SELECT nombre_archivo FROM schema_migraciones")}
    assert aplicadas == {"001_datos.sql"}


def test_si_la_migracion_falla_el_backup_preventivo_permanece(entorno):
    _dejar_base_con_migracion_1_y_una_pendiente(entorno)
    _escribir_migracion(entorno, "002_extra.sql", "ESTO NO ES SQL VALIDO;")

    with pytest.raises(ErrorBaseDatos):
        _migrar(entorno)

    zips = _zips(entorno)
    assert len(zips) == 1
    assert _PATRON_NOMBRE_PREVENTIVO.match(zips[0].name)
    assert "extra" not in _tablas(entorno["db"])


def test_el_backup_preventivo_usa_el_formato_existente_y_es_restaurable(entorno, tmp_path):
    _dejar_base_con_migracion_1_y_una_pendiente(entorno)
    ruta_backup = _migrar(entorno)

    # El validador del restore real acepta el ZIP: mismo formato que cualquier backup.
    extraido = modulo_restore.validar_y_extraer_backup(ruta_backup, tmp_path / "validacion")

    assert (extraido / "kiosco.db").is_file()
    assert (extraido / "imagenes_productos").is_dir()


def test_el_backup_preventivo_no_cuenta_como_ultimo_backup_automatico(entorno):
    _dejar_base_con_migracion_1_y_una_pendiente(entorno)
    _migrar(entorno)

    assert modulo_backup._fecha_ultimo_backup(entorno["backups"]) is None
