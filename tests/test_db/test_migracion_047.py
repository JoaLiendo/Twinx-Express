"""Migración 047: origen del pago de compras al contado. Upgrade desde 001-046 con compras históricas: ninguna
fila cambia (no se infiere ningún origen) y la migración queda registrada una sola vez. Nunca toca
`data/kiosco.db`."""

import sqlite3

import pytest

import db.conexion as modulo_conexion

NOMBRE_047 = "047_origen_pago_compras.sql"


@pytest.fixture
def base_046(tmp_path, monkeypatch):
    ruta = tmp_path / "046.db"
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
    con = sqlite3.connect(ruta)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(modulo_conexion._TABLA_MIGRACIONES)
    for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
        if migracion.name < NOMBRE_047:
            con.executescript(migracion.read_text(encoding="utf-8"))
            con.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (migracion.name,))
    con.execute("INSERT INTO usuarios (nombre_usuario, nombre_completo, password_hash, rol) VALUES ('a', 'A', 'h', 'OWNER')")
    con.execute("INSERT INTO proveedores (nombre) VALUES ('Distribuidora')")
    con.execute("INSERT INTO compras (proveedor_id, usuario_id, total_centavos) VALUES (1, 1, 500)")
    con.execute("INSERT INTO compras (proveedor_id, usuario_id, total_centavos, condicion_pago) VALUES (1, 1, 700, 'CREDITO')")
    con.commit()
    con.close()
    return ruta


def test_las_compras_historicas_quedan_sin_origen_y_es_idempotente(base_046):
    modulo_conexion.inicializar_base_datos()
    modulo_conexion.inicializar_base_datos()

    con = sqlite3.connect(base_046)
    try:
        filas = con.execute(
            "SELECT total_centavos, condicion_pago, origen_pago, caja_movimiento_id, caja_movimiento_reintegro_id"
            " FROM compras ORDER BY id"
        ).fetchall()
        assert filas == [(500, "CONTADO", None, None, None), (700, "CREDITO", None, None, None)]
        assert con.execute("SELECT COUNT(*) FROM schema_migraciones WHERE nombre_archivo = ?", (NOMBRE_047,)).fetchone()[0] == 1
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        con.close()
