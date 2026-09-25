"""`productos.version_stock` (migración 022), verificado contra SQLite real: semántica de `RETURNING`, el
caso ABA, idempotencia del runner y que la migración solo agrega columnas a las tablas existentes."""

import sqlite3

import pytest

import db.conexion as modulo_conexion

NOMBRE_021 = "021_producto_proveedor.sql"
NOMBRE_022 = "022_inventario_fisico.sql"


@pytest.fixture
def bd(base_datos_temporal):
    con = sqlite3.connect(base_datos_temporal)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(
        "INSERT INTO productos (id, codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos,"
        " stock_actual) VALUES (1, 'c1', 'P1', 10, 100, 10)"
    )
    con.commit()
    yield con
    con.close()


def _version(con) -> int:
    return con.execute("SELECT version_stock FROM productos WHERE id = 1").fetchone()[0]


def test_returning_devuelve_la_version_previa_al_trigger_por_eso_no_se_usa_en_el_codigo(bd):
    """Semántica real de SQLite: `RETURNING` refleja el UPDATE pero no lo que hacen los triggers AFTER.
    Por eso `version_stock` se lee siempre con un SELECT explícito dentro de la transacción."""
    fila = bd.execute("UPDATE productos SET stock_actual = 8 WHERE id = 1 RETURNING stock_actual, version_stock").fetchone()

    assert tuple(fila) == (8, 0)  # la versión devuelta es la previa al trigger
    assert _version(bd) == 1  # la lectura posterior ya la ve incrementada


def test_el_aba_10_8_10_deja_una_version_mayor(bd):
    bd.execute("UPDATE productos SET stock_actual = 8 WHERE id = 1")
    bd.execute("UPDATE productos SET stock_actual = 10 WHERE id = 1")

    assert bd.execute("SELECT stock_actual FROM productos WHERE id = 1").fetchone()[0] == 10
    assert _version(bd) == 2


def test_el_runner_es_idempotente_y_no_reaplica_migraciones(base_datos_temporal):
    def instantanea():
        con = sqlite3.connect(base_datos_temporal)
        try:
            return (
                con.execute("SELECT nombre_archivo, fecha_aplicada FROM schema_migraciones ORDER BY 1").fetchall(),
                con.execute("SELECT type, name, sql FROM sqlite_master ORDER BY 1, 2").fetchall(),
            )
        finally:
            con.close()

    antes = instantanea()
    modulo_conexion.inicializar_base_datos()
    modulo_conexion.inicializar_base_datos()

    assert instantanea() == antes
    assert {f[0] for f in antes[0]} >= {NOMBRE_021, NOMBRE_022}


def test_la_022_solo_agrega_columnas_a_las_tablas_existentes(tmp_path, monkeypatch):
    """Ninguna tabla previa se reconstruye: su definición original sigue siendo un prefijo de la nueva
    (solo `ADD COLUMN`), y el resto de las tablas previas queda idéntico."""
    ruta = tmp_path / "v13.db"
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
    con = sqlite3.connect(ruta)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(modulo_conexion._TABLA_MIGRACIONES)
    for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
        if migracion.name < NOMBRE_022:
            con.executescript(migracion.read_text(encoding="utf-8"))
            con.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (migracion.name,))
    con.commit()
    antes = dict(con.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'").fetchall())
    con.close()

    modulo_conexion.inicializar_base_datos()

    con = sqlite3.connect(ruta)
    despues = dict(con.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'").fetchall())
    con.close()
    modificadas = {tabla for tabla in antes if despues[tabla] != antes[tabla]}
    assert modificadas == {"productos", "ajustes_stock"}
    for tabla in modificadas:
        cuerpo_previo = antes[tabla].rstrip().rstrip(")").rstrip()
        assert despues[tabla].startswith(cuerpo_previo), tabla
    assert set(despues) - set(antes) == {"inventarios", "inventario_lineas"}
