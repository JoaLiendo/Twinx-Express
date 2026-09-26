"""Migración 023 (índices de fecha para reportes): aditiva, sobre una base V1.6 con datos."""

import sqlite3

import pytest

import db.conexion as modulo_conexion

NOMBRE_023 = "023_indices_fechas_reportes.sql"


@pytest.fixture
def base_v16(tmp_path, monkeypatch):
    ruta = tmp_path / "v16.db"
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
    con = sqlite3.connect(ruta)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(modulo_conexion._TABLA_MIGRACIONES)
    for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
        if migracion.name < NOMBRE_023:
            con.executescript(migracion.read_text(encoding="utf-8"))
            con.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (migracion.name,))
    con.execute(
        "INSERT INTO usuarios (id, nombre_usuario, nombre_completo, password_hash, rol)"
        " VALUES (1, 'duenio', 'Dueño', 'h', 'OWNER')"
    )
    con.execute("INSERT INTO proveedores (id, nombre) VALUES (1, 'Prov')")
    con.execute(
        "INSERT INTO compras (id, proveedor_id, usuario_id, fecha, total_centavos)"
        " VALUES (1, 1, 1, '2026-01-10 10:00:00', 10)"
    )
    con.commit()
    con.close()
    return ruta


def _indices(ruta, tabla: str) -> dict[str, list[str]]:
    con = sqlite3.connect(ruta)
    try:
        return {
            fila[1]: [col[2] for col in con.execute(f"PRAGMA index_info({fila[1]})").fetchall()]
            for fila in con.execute(f"PRAGMA index_list({tabla})").fetchall()
        }
    finally:
        con.close()


def test_crea_los_indices_de_fecha_y_preserva_los_datos(base_v16):
    assert "idx_ventas_fecha" not in _indices(base_v16, "ventas")
    assert "idx_compras_fecha" not in _indices(base_v16, "compras")

    modulo_conexion.inicializar_base_datos()

    assert _indices(base_v16, "ventas")["idx_ventas_fecha"] == ["fecha"]
    assert _indices(base_v16, "compras")["idx_compras_fecha"] == ["fecha"]
    con = sqlite3.connect(base_v16)
    try:
        assert con.execute("SELECT id, fecha, total_centavos FROM compras").fetchall() == [
            (1, "2026-01-10 10:00:00", 10)
        ]
        assert con.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("SELECT 1 FROM schema_migraciones WHERE nombre_archivo = ?", (NOMBRE_023,)).fetchone()
    finally:
        con.close()


def test_segunda_aplicacion_no_repite_ni_duplica(base_v16):
    modulo_conexion.inicializar_base_datos()
    antes = (_indices(base_v16, "ventas"), _indices(base_v16, "compras"))

    modulo_conexion.inicializar_base_datos()

    assert (_indices(base_v16, "ventas"), _indices(base_v16, "compras")) == antes
    con = sqlite3.connect(base_v16)
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM schema_migraciones WHERE nombre_archivo = ?", (NOMBRE_023,)
        ).fetchone() == (1,)
    finally:
        con.close()
