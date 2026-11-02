"""Migración 046: la transparencia de la clase C depende del receptor.

Upgrade desde una base 001-045: solo cambia el trigger de líneas (mismo nombre), ningún comprobante ni línea
cambia y la migración queda registrada una sola vez. El comportamiento del trigger nuevo se prueba en
`test_fiscal_otros_impuestos.TestAlcanceFacturaC`. Nunca toca `data/kiosco.db`.
"""

import sqlite3

import pytest

import db.conexion as modulo_conexion

NOMBRE_046 = "046_transparencia_por_receptor.sql"
_TRIGGER = "trg_comprobante_fiscal_lineas_otros_impuestos"


@pytest.fixture
def base_045(tmp_path, monkeypatch):
    ruta = tmp_path / "045.db"
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
    con = sqlite3.connect(ruta)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(modulo_conexion._TABLA_MIGRACIONES)
    for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
        if migracion.name < NOMBRE_046:
            con.executescript(migracion.read_text(encoding="utf-8"))
            con.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (migracion.name,))
    con.commit()
    con.close()
    return ruta


def _sql_trigger(ruta) -> str:
    con = sqlite3.connect(ruta)
    try:
        return con.execute("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?", (_TRIGGER,)).fetchone()[0]
    finally:
        con.close()


def test_recrea_solo_el_trigger_y_es_idempotente(base_045):
    assert "condicion_iva_receptor_id" not in _sql_trigger(base_045)
    modulo_conexion.inicializar_base_datos()
    modulo_conexion.inicializar_base_datos()
    assert "clase <> 'C' OR condicion_iva_receptor_id = 5" in _sql_trigger(base_045)
    con = sqlite3.connect(base_045)
    try:
        assert con.execute("SELECT COUNT(*) FROM schema_migraciones WHERE nombre_archivo = ?", (NOMBRE_046,)).fetchone()[0] == 1
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        con.close()
