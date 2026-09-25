"""Migración 021 (relación producto-proveedor): esquema, restricciones y backfill desde las
compras históricas.

Cada test arma una base con el esquema previo (migraciones 001-020) y compras históricas, aplica
la 021 con el runner real (`inicializar_base_datos`) e inspecciona el resultado. Nunca toca
`data/kiosco.db`.
"""

import sqlite3

import pytest

import db.conexion as modulo_conexion

NOMBRE_021 = "021_producto_proveedor.sql"


def _base_anterior(ruta, monkeypatch) -> sqlite3.Connection:
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
    con = sqlite3.connect(ruta)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(modulo_conexion._TABLA_MIGRACIONES)
    for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
        if migracion.name < NOMBRE_021:
            con.executescript(migracion.read_text(encoding="utf-8"))
            con.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (migracion.name,))
    con.commit()
    con.execute(
        "INSERT INTO usuarios (id, nombre_usuario, nombre_completo, password_hash, rol)"
        " VALUES (1, 'duenio', 'Dueño', 'h', 'OWNER')"
    )
    for producto_id in (1, 2, 3, 4):
        con.execute(
            "INSERT INTO productos (id, codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos,"
            " stock_actual) VALUES (?, ?, ?, 10, 100, 5)",
            (producto_id, f"c{producto_id}", f"P{producto_id}"),
        )
    for proveedor_id, activo in ((1, 1), (2, 1), (3, 0)):
        con.execute(
            "INSERT INTO proveedores (id, nombre, activo) VALUES (?, ?, ?)",
            (proveedor_id, f"Prov{proveedor_id}", activo),
        )
    con.commit()
    return con


def _compra(con, compra_id, proveedor_id, fecha, lineas) -> None:
    con.execute(
        "INSERT INTO compras (id, proveedor_id, usuario_id, fecha, total_centavos) VALUES (?, ?, 1, ?, 0)",
        (compra_id, proveedor_id, fecha),
    )
    for producto_id, costo in lineas:
        con.execute(
            "INSERT INTO detalle_compra (compra_id, producto_id, cantidad, costo_unitario_centavos,"
            " subtotal_centavos) VALUES (?, ?, 1, ?, ?)",
            (compra_id, producto_id, costo, costo),
        )


def _vinculos(ruta) -> list[tuple]:
    con = sqlite3.connect(ruta)
    try:
        return con.execute(
            "SELECT producto_id, proveedor_id, es_principal, codigo_proveedor FROM producto_proveedor"
            " ORDER BY producto_id, proveedor_id"
        ).fetchall()
    finally:
        con.close()


@pytest.fixture
def historia(tmp_path, monkeypatch):
    """Compras históricas con ids y fechas deliberadamente desordenados entre sí."""
    ruta = tmp_path / "v13.db"
    con = _base_anterior(ruta, monkeypatch)
    # Producto 1: compras 1 (Prov1), 2 (Prov2), 3 (Prov1): la de mayor id es de Prov1, aunque la
    # compra 2 tiene la fecha más reciente.
    _compra(con, 1, 1, "2026-01-10 10:00:00", [(1, 100)])
    _compra(con, 2, 2, "2026-03-01 10:00:00", [(1, 120), (2, 50)])
    _compra(con, 3, 1, "2026-02-01 10:00:00", [(1, 110)])
    # Producto 3: un solo proveedor, inactivo. Producto 4: sin compras.
    _compra(con, 4, 3, "2026-02-15 10:00:00", [(3, 30)])
    con.commit()
    con.close()
    modulo_conexion.inicializar_base_datos()
    return ruta


def test_crea_la_tabla_con_sus_restricciones(historia):
    con = sqlite3.connect(historia)
    try:
        columnas = [f[1] for f in con.execute("PRAGMA table_info(producto_proveedor)").fetchall()]
        indices = {f[1] for f in con.execute("PRAGMA index_list(producto_proveedor)").fetchall()}
        fks = {(f[2], f[6]) for f in con.execute("PRAGMA foreign_key_list(producto_proveedor)").fetchall()}
        registrada = con.execute(
            "SELECT 1 FROM schema_migraciones WHERE nombre_archivo = ?", (NOMBRE_021,)
        ).fetchone()
    finally:
        con.close()
    assert columnas == ["id", "producto_id", "proveedor_id", "codigo_proveedor", "es_principal", "fecha_creacion"]
    assert "idx_producto_proveedor_principal" in indices
    assert fks == {("productos", "RESTRICT"), ("proveedores", "RESTRICT")}
    assert registrada is not None


def test_backfill_un_vinculo_por_par_producto_proveedor(historia):
    pares = [(p, pr) for p, pr, _, _ in _vinculos(historia)]

    assert pares == [(1, 1), (1, 2), (2, 2), (3, 3)]


def test_el_principal_es_el_proveedor_de_la_compra_de_mayor_id_no_de_la_mas_reciente(historia):
    principales = {p: pr for p, pr, es_principal, _ in _vinculos(historia) if es_principal}

    # Producto 1: compra 3 (Prov1) tiene el mayor id aunque la compra 2 (Prov2) tiene mayor fecha.
    assert principales == {1: 1, 2: 2, 3: 3}


def test_un_proveedor_inactivo_conserva_su_vinculo_y_un_producto_sin_compras_no_recibe_ninguno(historia):
    vinculos = _vinculos(historia)

    assert (3, 3, 1, None) in vinculos
    assert not [v for v in vinculos if v[0] == 4]


def test_el_backfill_no_modifica_compras_ni_stock_ni_costos(historia):
    con = sqlite3.connect(historia)
    try:
        assert con.execute("SELECT COUNT(*), SUM(total_centavos) FROM compras").fetchone() == (4, 0)
        assert con.execute("SELECT COUNT(*) FROM detalle_compra").fetchone()[0] == 5
        assert con.execute("SELECT SUM(stock_actual), SUM(precio_costo_centavos) FROM productos").fetchone() == (20, 40)
    finally:
        con.close()


def test_correr_el_script_dos_veces_no_duplica_ni_cambia_nada(historia):
    antes = _vinculos(historia)
    script = (modulo_conexion.DIRECTORIO_MIGRACIONES / NOMBRE_021).read_text(encoding="utf-8")
    con = sqlite3.connect(historia)
    con.execute("PRAGMA foreign_keys = ON")
    try:
        con.executescript("BEGIN IMMEDIATE;\n" + script)
    finally:
        con.close()

    assert _vinculos(historia) == antes


def test_el_backfill_es_determinista_entre_bases_iguales(tmp_path, monkeypatch):
    resultados = []
    for nombre in ("a.db", "b.db"):
        ruta = tmp_path / nombre
        con = _base_anterior(ruta, monkeypatch)
        _compra(con, 1, 2, "2026-01-01 00:00:00", [(1, 10)])
        _compra(con, 2, 1, "2026-01-01 00:00:00", [(1, 10)])  # misma fecha: desempata el id
        con.commit()
        con.close()
        modulo_conexion.inicializar_base_datos()
        resultados.append(_vinculos(ruta))

    assert resultados[0] == resultados[1] == [(1, 1, 1, None), (1, 2, 0, None)]


def test_una_base_sin_compras_migra_sin_vinculos(tmp_path, monkeypatch):
    ruta = tmp_path / "vacia.db"
    _base_anterior(ruta, monkeypatch).close()

    modulo_conexion.inicializar_base_datos()

    assert _vinculos(ruta) == []


# --- restricciones del esquema (acceso directo a la base) --------------------------------------------


def _con_fk(ruta) -> sqlite3.Connection:
    con = sqlite3.connect(ruta)
    con.execute("PRAGMA foreign_keys = ON")
    return con


def test_unique_producto_proveedor(historia):
    con = _con_fk(historia)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO producto_proveedor (producto_id, proveedor_id) VALUES (1, 1)")
    finally:
        con.close()


def test_a_lo_sumo_un_principal_por_producto(historia):
    con = _con_fk(historia)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("UPDATE producto_proveedor SET es_principal = 1 WHERE producto_id = 1 AND proveedor_id = 2")
        # Dos NO principales del mismo producto sí conviven.
        con.execute("INSERT INTO producto_proveedor (producto_id, proveedor_id, es_principal) VALUES (4, 1, 0)")
        con.execute("INSERT INTO producto_proveedor (producto_id, proveedor_id, es_principal) VALUES (4, 2, 0)")
    finally:
        con.close()


def test_fk_restrict_impide_borrar_producto_o_proveedor_vinculado(historia):
    con = _con_fk(historia)
    try:
        con.execute("INSERT INTO producto_proveedor (producto_id, proveedor_id) VALUES (4, 1)")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("DELETE FROM productos WHERE id = 4")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("DELETE FROM proveedores WHERE id = 3")
    finally:
        con.close()


def test_check_de_codigo_proveedor_y_es_principal(historia):
    con = _con_fk(historia)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO producto_proveedor (producto_id, proveedor_id, codigo_proveedor) VALUES (4, 1, '  ')")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO producto_proveedor (producto_id, proveedor_id, es_principal) VALUES (4, 1, 2)")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO producto_proveedor (producto_id, proveedor_id) VALUES (99, 1)")
    finally:
        con.close()
