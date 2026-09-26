"""Migración 024 (anulación de compras y trazabilidad de costo): sobre una base V1.6 realista, con la
reconstrucción de `historial_precios` verificada fila por fila."""

import sqlite3

import pytest

import db.conexion as modulo_conexion
from excepciones import ErrorBaseDatos

NOMBRE_024 = "024_anulacion_compras.sql"


@pytest.fixture
def base_v16(tmp_path, monkeypatch):
    ruta = tmp_path / "v16.db"
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
    con = sqlite3.connect(ruta)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(modulo_conexion._TABLA_MIGRACIONES)
    for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
        if migracion.name < NOMBRE_024:
            con.executescript(migracion.read_text(encoding="utf-8"))
            con.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (migracion.name,))
    con.execute(
        "INSERT INTO usuarios (id, nombre_usuario, nombre_completo, password_hash, rol)"
        " VALUES (1, 'duenio', 'Dueño', 'h', 'OWNER')"
    )
    con.execute("INSERT INTO proveedores (id, nombre) VALUES (1, 'Prov')")
    for producto_id in (1, 2):
        con.execute(
            "INSERT INTO productos (id, codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos,"
            " stock_actual) VALUES (?, ?, ?, 100, 200, 5)",
            (producto_id, f"c{producto_id}", f"P{producto_id}"),
        )
    con.execute(
        "INSERT INTO lotes_precios (id, usuario_id, tipo, direccion, valor, redondeo, alcance, cantidad_productos)"
        " VALUES (1, 1, 'PORCENTAJE', 'AUMENTAR', 10, 'NINGUNO', 'TODOS', 2)"
    )
    # Ids con hueco (el 3 se "perdió"): la secuencia AUTOINCREMENT debe seguir de 7, no de 4.
    for evento_id, producto_id, campo, anterior, nuevo, origen, lote in (
        (1, 1, "COSTO", 100, 120, "COMPRA", None),
        (2, 1, "VENTA", 200, 220, "MASIVA", 1),
        (4, 2, "COSTO", 100, 90, "EDICION", None),
        (7, 2, "VENTA", 200, 250, "IMPORTACION", None),
    ):
        con.execute(
            "INSERT INTO historial_precios (id, producto_id, usuario_id, fecha, campo, precio_anterior_centavos,"
            " precio_nuevo_centavos, origen, lote_id) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)",
            (evento_id, producto_id, f"2026-03-0{evento_id % 9 + 1} 10:00:00", campo, anterior, nuevo, origen, lote),
        )
    con.execute("UPDATE sqlite_sequence SET seq = 9 WHERE name = 'historial_precios'")
    con.execute(
        "INSERT INTO compras (id, proveedor_id, usuario_id, fecha, total_centavos)"
        " VALUES (1, 1, 1, '2026-03-01 09:00:00', 120)"
    )
    con.execute(
        "INSERT INTO detalle_compra (compra_id, producto_id, cantidad, costo_unitario_centavos, subtotal_centavos)"
        " VALUES (1, 1, 1, 120, 120)"
    )
    con.commit()
    con.close()
    return ruta


def _leer(ruta, sql, args=()):
    con = sqlite3.connect(ruta)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


_HISTORIAL = "SELECT * FROM historial_precios ORDER BY id"


def test_preserva_el_historial_y_las_compras_y_las_deja_activas_sin_trazabilidad(base_v16):
    historial_antes = _leer(base_v16, _HISTORIAL)
    compras_antes = _leer(base_v16, "SELECT id, proveedor_id, usuario_id, fecha, total_centavos FROM compras")

    modulo_conexion.inicializar_base_datos()

    assert _leer(base_v16, _HISTORIAL) == historial_antes
    assert _leer(base_v16, "SELECT id, proveedor_id, usuario_id, fecha, total_centavos FROM compras") == compras_antes
    assert _leer(
        base_v16, "SELECT estado, motivo_anulacion, observaciones_anulacion, anulada_por_usuario_id, fecha_anulacion,"
        " costo_trazable FROM compras"
    ) == [("ACTIVA", None, None, None, None, 0)]
    assert _leer(base_v16, "SELECT historial_precio_id FROM detalle_compra") == [(None,)]


def test_integridad_indices_y_registro(base_v16):
    modulo_conexion.inicializar_base_datos()

    assert _leer(base_v16, "PRAGMA integrity_check") == [("ok",)]
    assert _leer(base_v16, "PRAGMA foreign_key_check") == []
    indices = {f[0] for f in _leer(base_v16, "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='historial_precios'")}
    assert {"idx_historial_precios_producto_id", "idx_historial_precios_lote_id"} <= indices
    assert _leer(base_v16, "SELECT 1 FROM schema_migraciones WHERE nombre_archivo = ?", (NOMBRE_024,))
    assert _leer(base_v16, "SELECT name FROM sqlite_master WHERE name LIKE '%historial_precios_nueva%'") == []
    assert _leer(base_v16, "SELECT name FROM sqlite_temp_master") == []  # sin restos temporales


def test_la_secuencia_no_retrocede(base_v16):
    modulo_conexion.inicializar_base_datos()
    con = sqlite3.connect(base_v16)
    try:
        con.execute("PRAGMA foreign_keys = ON")
        nuevo = con.execute(
            "INSERT INTO historial_precios (producto_id, usuario_id, campo, precio_anterior_centavos,"
            " precio_nuevo_centavos, origen) VALUES (1, 1, 'COSTO', 120, 130, 'COMPRA')"
        ).lastrowid
    finally:
        con.close()
    assert nuevo == 10  # la secuencia previa era 9


def test_origen_anulacion_compra_es_valido_y_uno_inventado_no(base_v16):
    modulo_conexion.inicializar_base_datos()
    con = sqlite3.connect(base_v16)
    con.execute("PRAGMA foreign_keys = ON")
    try:
        con.execute(
            "INSERT INTO historial_precios (producto_id, campo, precio_anterior_centavos, precio_nuevo_centavos, origen)"
            " VALUES (1, 'COSTO', 120, 100, 'ANULACION_COMPRA')"
        )
        for origen in ("INVENTADO", "compra"):
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO historial_precios (producto_id, campo, precio_anterior_centavos,"
                    " precio_nuevo_centavos, origen) VALUES (1, 'COSTO', 1, 2, ?)",
                    (origen,),
                )
        with pytest.raises(sqlite3.IntegrityError):  # se conserva el CHECK de "precio distinto"
            con.execute(
                "INSERT INTO historial_precios (producto_id, campo, precio_anterior_centavos, precio_nuevo_centavos,"
                " origen) VALUES (1, 'COSTO', 5, 5, 'COMPRA')"
            )
    finally:
        con.close()


def test_restricciones_de_compras_y_detalle(base_v16):
    modulo_conexion.inicializar_base_datos()
    con = sqlite3.connect(base_v16)
    con.execute("PRAGMA foreign_keys = ON")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("UPDATE compras SET estado = 'BORRADA'")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("UPDATE compras SET motivo_anulacion = 'CUALQUIERA'")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("UPDATE compras SET costo_trazable = 2")
        with pytest.raises(sqlite3.IntegrityError):  # FK hacia un evento que no existe
            con.execute("UPDATE detalle_compra SET historial_precio_id = 999")
        con.execute("UPDATE detalle_compra SET historial_precio_id = 1")  # evento real
        con.execute("UPDATE compras SET estado = 'ANULADA', motivo_anulacion = 'OTRO'")
    finally:
        con.close()


def test_segunda_aplicacion_no_repite(base_v16):
    modulo_conexion.inicializar_base_datos()
    despues = _leer(base_v16, _HISTORIAL)

    modulo_conexion.inicializar_base_datos()

    assert _leer(base_v16, _HISTORIAL) == despues
    assert _leer(base_v16, "SELECT COUNT(*) FROM schema_migraciones WHERE nombre_archivo = ?", (NOMBRE_024,)) == [(1,)]


def test_es_atomica_si_una_verificacion_falla(base_v16):
    """Un objeto ajeno que referencia `historial_precios` aborta la migración sin dejar cambios parciales."""
    con = sqlite3.connect(base_v16)
    con.execute("CREATE VIEW vista_ajena AS SELECT id FROM historial_precios")
    con.commit()
    con.close()
    historial_antes = _leer(base_v16, _HISTORIAL)

    with pytest.raises(ErrorBaseDatos):
        modulo_conexion.inicializar_base_datos()

    assert _leer(base_v16, _HISTORIAL) == historial_antes
    assert not _leer(base_v16, "SELECT 1 FROM schema_migraciones WHERE nombre_archivo = ?", (NOMBRE_024,))
    columnas = [f[1] for f in _leer(base_v16, "PRAGMA table_info(compras)")]
    assert "estado" not in columnas
    assert _leer(base_v16, "SELECT COUNT(*) FROM sqlite_master WHERE name = 'historial_precios_nueva'") == [(0,)]
