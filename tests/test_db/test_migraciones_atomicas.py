"""Atomicidad de las migraciones: cada una se aplica completa (con su
registro en `schema_migraciones`) o no deja ningún cambio."""

import sqlite3

import pytest

import db.conexion as modulo
from excepciones import ErrorBaseDatos

_OK_A = "CREATE TABLE a (id INTEGER PRIMARY KEY);"
_OK_B = "CREATE TABLE b (id INTEGER PRIMARY KEY);"
# Primero cambios válidos (una tabla y un ALTER) y después una sentencia inválida.
_FALLA_C = """
CREATE TABLE tabla_prueba (id INTEGER PRIMARY KEY);
ALTER TABLE a ADD COLUMN extra TEXT;
CREATE TABLE tabla_invalida (id INTEGER PRIMARY KEY, );
"""
_OK_C = "CREATE TABLE tabla_prueba (id INTEGER PRIMARY KEY);\nALTER TABLE a ADD COLUMN extra TEXT;"


@pytest.fixture
def migraciones(tmp_path, monkeypatch):
    """Directorio de migraciones propio y DB temporal (aún inexistente)."""
    directorio = tmp_path / "migraciones"
    directorio.mkdir()
    monkeypatch.setattr(modulo, "RUTA_BASE_DATOS", tmp_path / "kiosco.db")
    monkeypatch.setattr(modulo, "DIRECTORIO_MIGRACIONES", directorio)
    return directorio


def _escribir(directorio, nombre: str, sql: str) -> None:
    (directorio / nombre).write_text(sql, encoding="utf-8")


def _estado() -> tuple[set[str], set[str], list[str]]:
    """(tablas de usuario, columnas de `a`, migraciones registradas), leído desde una conexión nueva."""
    conexion = sqlite3.connect(modulo.RUTA_BASE_DATOS)
    try:
        tablas = {
            f[0]
            for f in conexion.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' AND name <> 'schema_migraciones'"
            )
        }
        columnas_a = {f[1] for f in conexion.execute("PRAGMA table_info(a)")}
        registradas = [f[0] for f in conexion.execute("SELECT nombre_archivo FROM schema_migraciones ORDER BY 1")]
    finally:
        conexion.close()
    return tablas, columnas_a, registradas


def test_migracion_valida_aplica_todo_y_queda_registrada(migraciones):
    _escribir(migraciones, "001_a.sql", _OK_A)
    _escribir(migraciones, "002_c.sql", _OK_C)

    modulo.inicializar_base_datos()

    tablas, columnas_a, registradas = _estado()
    assert tablas == {"a", "tabla_prueba"}
    assert "extra" in columnas_a
    assert registradas == ["001_a.sql", "002_c.sql"]


def test_migracion_fallida_no_deja_ningun_cambio_ni_registro(migraciones):
    _escribir(migraciones, "001_a.sql", _OK_A)
    _escribir(migraciones, "002_falla.sql", _FALLA_C)

    with pytest.raises(ErrorBaseDatos):
        modulo.inicializar_base_datos()

    tablas, columnas_a, registradas = _estado()
    assert "tabla_prueba" not in tablas  # la primera sentencia de la migración fallida se revirtió
    assert columnas_a == {"id"}  # el ALTER intermedio también
    assert registradas == ["001_a.sql"]


def test_una_migracion_fallida_conserva_las_anteriores_y_queda_pendiente(migraciones):
    _escribir(migraciones, "001_a.sql", _OK_A)
    _escribir(migraciones, "002_b.sql", _OK_B)
    _escribir(migraciones, "003_falla.sql", _FALLA_C)

    with pytest.raises(ErrorBaseDatos):
        modulo.inicializar_base_datos()

    tablas, _columnas_a, registradas = _estado()
    assert tablas == {"a", "b"}
    assert registradas == ["001_a.sql", "002_b.sql"]
    assert modulo.hay_migraciones_pendientes_en_base_existente() is True


def test_una_migracion_fallida_se_puede_reintentar_una_sola_vez_registrada(migraciones):
    _escribir(migraciones, "001_a.sql", _OK_A)
    _escribir(migraciones, "002_c.sql", _FALLA_C)
    with pytest.raises(ErrorBaseDatos):
        modulo.inicializar_base_datos()

    _escribir(migraciones, "002_c.sql", _OK_C)  # se corrige la migración
    modulo.inicializar_base_datos()
    modulo.inicializar_base_datos()  # un arranque más: no debe reaplicar nada

    tablas, columnas_a, registradas = _estado()
    assert tablas == {"a", "tabla_prueba"}
    assert "extra" in columnas_a
    assert registradas == ["001_a.sql", "002_c.sql"]  # cada una registrada una sola vez


def test_las_migraciones_reales_se_aplican_completas_desde_una_base_vacia(tmp_path, monkeypatch):
    monkeypatch.setattr(modulo, "RUTA_BASE_DATOS", tmp_path / "kiosco.db")
    esperadas = sorted(ruta.name for ruta in modulo.DIRECTORIO_MIGRACIONES.glob("*.sql"))
    assert len(esperadas) >= 13

    modulo.inicializar_base_datos()

    conexion = sqlite3.connect(modulo.RUTA_BASE_DATOS)
    try:
        registradas = [f[0] for f in conexion.execute("SELECT nombre_archivo FROM schema_migraciones ORDER BY 1")]
        integridad = conexion.execute("PRAGMA integrity_check").fetchone()[0]
        claves_foraneas = conexion.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        conexion.close()
    assert registradas == esperadas
    assert integridad == "ok"
    assert claves_foraneas == []
    assert modulo.hay_migraciones_pendientes_en_base_existente() is False
