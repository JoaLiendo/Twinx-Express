"""Tests de `services/servicio_restore.py`: validación defensiva de un
ZIP de backup (estructura, path traversal, integridad de SQLite) y
restauración segura con rollback ante cualquier fallo durante el
reemplazo de la instalación actual."""

import sqlite3
import zipfile
from pathlib import Path

import pytest

import services.servicio_restore as modulo_restore
from excepciones import ErrorRestore


def _crear_zip_valido(ruta_zip, fila_sentinela="SENTINEL_RESTORE", incluir_imagen=True):
    ruta_db_temporal = ruta_zip.parent / "temp_kiosco.db"
    conexion = sqlite3.connect(ruta_db_temporal)
    conexion.execute("CREATE TABLE productos (id INTEGER PRIMARY KEY, nombre TEXT)")
    conexion.execute("INSERT INTO productos (nombre) VALUES (?)", (fila_sentinela,))
    conexion.commit()
    conexion.close()

    with zipfile.ZipFile(ruta_zip, "w") as zf:
        zf.write(ruta_db_temporal, "kiosco.db")
        if incluir_imagen:
            zf.writestr("imagenes_productos/1_abc.jpg", b"CONTENIDO-IMAGEN")
        else:
            zf.writestr("imagenes_productos/", "")

    ruta_db_temporal.unlink()
    return ruta_zip


@pytest.fixture
def instalacion_existente(tmp_path, monkeypatch):
    directorio_data = tmp_path / "data"
    directorio_data.mkdir()
    (directorio_data / "kiosco.db").write_bytes(b"DB-VIEJA-ANTES-DEL-RESTORE")
    (directorio_data / "imagenes_productos").mkdir()
    (directorio_data / "imagenes_productos" / "vieja.jpg").write_bytes(b"IMAGEN-VIEJA")

    monkeypatch.setattr(modulo_restore, "DIRECTORIO_DATA", directorio_data)
    return directorio_data


def test_zip_valido_restaura_correctamente(instalacion_existente, tmp_path):
    ruta_zip = _crear_zip_valido(tmp_path / "backup.zip")

    modulo_restore.restaurar_desde_backup(ruta_zip)

    conexion = sqlite3.connect(instalacion_existente / "kiosco.db")
    try:
        fila = conexion.execute("SELECT nombre FROM productos").fetchone()
        assert fila[0] == "SENTINEL_RESTORE"
    finally:
        conexion.close()
    assert (instalacion_existente / "imagenes_productos" / "1_abc.jpg").read_bytes() == b"CONTENIDO-IMAGEN"


def test_zip_corrupto_es_rechazado(instalacion_existente, tmp_path):
    ruta_zip = tmp_path / "backup_corrupto.zip"
    ruta_zip.write_bytes(b"esto no es un zip valido")

    with pytest.raises(ErrorRestore):
        modulo_restore.restaurar_desde_backup(ruta_zip)

    assert (instalacion_existente / "kiosco.db").read_bytes() == b"DB-VIEJA-ANTES-DEL-RESTORE"


def test_path_traversal_es_rechazado(instalacion_existente, tmp_path):
    ruta_zip = tmp_path / "backup_malicioso.zip"
    with zipfile.ZipFile(ruta_zip, "w") as zf:
        zf.writestr("kiosco.db", b"contenido")
        zf.writestr("imagenes_productos/../../evil.txt", b"malicioso")

    with pytest.raises(ErrorRestore):
        modulo_restore.restaurar_desde_backup(ruta_zip)

    assert (instalacion_existente / "kiosco.db").read_bytes() == b"DB-VIEJA-ANTES-DEL-RESTORE"


def test_estructura_incorrecta_es_rechazada(instalacion_existente, tmp_path):
    ruta_zip = tmp_path / "backup_sin_db.zip"
    with zipfile.ZipFile(ruta_zip, "w") as zf:
        zf.writestr("imagenes_productos/1.jpg", b"x")
        zf.writestr("archivo_inesperado.txt", b"no deberia estar aca")

    with pytest.raises(ErrorRestore):
        modulo_restore.restaurar_desde_backup(ruta_zip)

    assert (instalacion_existente / "kiosco.db").read_bytes() == b"DB-VIEJA-ANTES-DEL-RESTORE"


def test_db_corrupta_es_rechazada(instalacion_existente, tmp_path):
    ruta_zip = tmp_path / "backup_db_corrupta.zip"
    with zipfile.ZipFile(ruta_zip, "w") as zf:
        zf.writestr("kiosco.db", b"esto no es una base de datos sqlite valida")
        zf.writestr("imagenes_productos/", "")

    with pytest.raises(ErrorRestore):
        modulo_restore.restaurar_desde_backup(ruta_zip)

    assert (instalacion_existente / "kiosco.db").read_bytes() == b"DB-VIEJA-ANTES-DEL-RESTORE"


def test_falta_carpeta_de_imagenes_es_rechazada(instalacion_existente, tmp_path):
    ruta_zip = tmp_path / "backup_sin_imagenes.zip"
    with zipfile.ZipFile(ruta_zip, "w") as zf:
        zf.writestr("kiosco.db", b"x")

    with pytest.raises(ErrorRestore):
        modulo_restore.restaurar_desde_backup(ruta_zip)

    assert (instalacion_existente / "kiosco.db").read_bytes() == b"DB-VIEJA-ANTES-DEL-RESTORE"
    assert (instalacion_existente / "imagenes_productos" / "vieja.jpg").exists()


def test_rollback_funciona_ante_fallo_durante_el_reemplazo(instalacion_existente, tmp_path, monkeypatch):
    ruta_zip = _crear_zip_valido(tmp_path / "backup.zip")

    original_rename = Path.rename
    intentos = {"n": 0}

    def rename_falla_en_el_segundo_intento(self, target):
        intentos["n"] += 1
        # 1er rename: mover la data vieja al rollback (debe funcionar).
        # 2do rename: mover la data nueva a su lugar final (la rompemos).
        if intentos["n"] == 2:
            raise OSError("fallo simulado moviendo la nueva data a su lugar")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", rename_falla_en_el_segundo_intento)

    with pytest.raises(OSError):
        modulo_restore.restaurar_desde_backup(ruta_zip)

    assert (instalacion_existente / "kiosco.db").read_bytes() == b"DB-VIEJA-ANTES-DEL-RESTORE"
    assert (instalacion_existente / "imagenes_productos" / "vieja.jpg").read_bytes() == b"IMAGEN-VIEJA"
