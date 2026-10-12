"""Migración 029 (stock inicial trazable, V1.11-C): agrega el motivo interno `STOCK_INICIAL`,
admite `usuario_id` NULL solo para él, un único STOCK_INICIAL por producto y un trigger que lo
valida; además `trg_ajustes_stock_reversa_valida` rechaza revertirlo.

Caso de estrés: una base V1.11-B con una reversa histórica (autorreferencia `reversa_de_id`, ON
DELETE RESTRICT) y un RECUENTO asignado a `inventario_lineas.ajuste_id` de un inventario CONFIRMADO.
Un primer borrador de esta migración fallaba con "FOREIGN KEY constraint failed" en ese escenario
(el DROP borraba el original antes que su reversa). Nunca toca `data/kiosco.db`.
"""

import sqlite3

import pytest

import db.conexion as modulo_conexion
from excepciones import ErrorBaseDatos

NOMBRE_029 = "029_stock_inicial.sql"

COLUMNAS_AJUSTES = (
    "id, producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones,"
    " fecha, clave_idempotencia, inventario_id, costo_unitario_centavos, reversa_de_id"
)


class BaseAnterior:
    """Base con el esquema previo a la 029 (migraciones 001-028), con historia representativa."""

    def __init__(self, ruta, monkeypatch):
        self.ruta = ruta
        monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
        self.con = sqlite3.connect(ruta)
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.execute(modulo_conexion._TABLA_MIGRACIONES)
        for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
            if migracion.name < NOMBRE_029:
                self.con.executescript(migracion.read_text(encoding="utf-8"))
                self.con.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (migracion.name,))
        self.con.commit()

    def cargar_historia(self) -> None:
        con = self.con
        con.execute(
            "INSERT INTO usuarios (id, nombre_usuario, nombre_completo, password_hash, rol)"
            " VALUES (1, 'duenio', 'Dueño', 'h', 'OWNER')"
        )
        con.execute(
            "INSERT INTO productos (id, codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos,"
            " stock_actual) VALUES (1, 'c1', 'Alfajor', 100, 200, 10), (2, 'c2', 'Gaseosa', 150, 300, 8)"
        )
        # Ajuste manual y su reversa (V1.11-B): la autorreferencia que el DROP debe atravesar.
        con.execute(
            "INSERT INTO ajustes_stock (id, producto_id, usuario_id, motivo, delta, stock_anterior,"
            " stock_resultante, costo_unitario_centavos) VALUES (1, 1, 1, 'ROTURA', -2, 10, 8, 100)"
        )
        con.execute(
            "INSERT INTO ajustes_stock (id, producto_id, usuario_id, motivo, delta, stock_anterior,"
            " stock_resultante, observaciones, costo_unitario_centavos, reversa_de_id)"
            " VALUES (2, 1, 1, 'REVERSA', 2, 8, 10, 'corrección', 100, 1)"
        )
        # RECUENTO de un inventario confirmado: el hijo ON DELETE RESTRICT de inventario_lineas.
        con.execute("INSERT INTO inventarios (id, estado, usuario_id) VALUES (1, 'ABIERTO', 1)")
        con.execute("INSERT INTO inventario_lineas (id, inventario_id, producto_id) VALUES (1, 1, 2)")
        con.execute(
            "UPDATE inventario_lineas SET stock_esperado = 5, version_esperada = 0, cantidad_contada = 8,"
            " costo_unitario_centavos = 150, usuario_conteo_id = 1, fecha_conteo = datetime('now') WHERE id = 1"
        )
        con.execute(
            "INSERT INTO ajustes_stock (id, producto_id, usuario_id, motivo, delta, stock_anterior,"
            " stock_resultante, observaciones, inventario_id) VALUES (3, 2, 1, 'RECUENTO', 3, 5, 8, 'Inventario #1', 1)"
        )
        con.execute("UPDATE inventario_lineas SET ajuste_id = 3 WHERE id = 1")
        con.execute(
            "UPDATE inventarios SET estado = 'CONFIRMADO', fecha_cierre = datetime('now'),"
            " usuario_cierre_id = 1 WHERE id = 1"
        )
        con.commit()

    def instantanea(self) -> dict:
        con = self.con
        return {
            "ajustes": con.execute(f"SELECT {COLUMNAS_AJUSTES} FROM ajustes_stock ORDER BY id").fetchall(),
            "lineas_ajuste": con.execute(
                "SELECT id, ajuste_id FROM inventario_lineas WHERE ajuste_id IS NOT NULL ORDER BY id"
            ).fetchall(),
            "productos": con.execute("SELECT id, stock_actual, version_stock FROM productos ORDER BY id").fetchall(),
            "secuencias": sorted(con.execute("SELECT name, seq FROM sqlite_sequence").fetchall()),
            "migraciones": [f[0] for f in con.execute("SELECT nombre_archivo FROM schema_migraciones ORDER BY 1")],
        }

    def migrar(self) -> None:
        self.con.commit()
        modulo_conexion.inicializar_base_datos()
        self.reabrir()

    def reabrir(self) -> None:
        self.con.close()
        self.con = sqlite3.connect(self.ruta)
        self.con.execute("PRAGMA foreign_keys = ON")


@pytest.fixture
def previa(tmp_path, monkeypatch):
    base = BaseAnterior(tmp_path / "previa.db", monkeypatch)
    yield base
    base.con.close()


@pytest.fixture
def migrada(previa):
    previa.cargar_historia()
    previa.migrar()
    return previa


def _producto_nuevo(con, producto_id=10, stock=7):
    con.execute(
        "INSERT INTO productos (id, codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos, stock_actual)"
        " VALUES (?, ?, 'Nuevo', 50, 90, ?)",
        (producto_id, f"n{producto_id}", stock),
    )


def _stock_inicial(con, producto_id, usuario_id=1, delta=7, anterior=0, resultante=7):
    con.execute(
        "INSERT INTO ajustes_stock (producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante,"
        " costo_unitario_centavos) VALUES (?, ?, 'STOCK_INICIAL', ?, ?, ?, 50)",
        (producto_id, usuario_id, delta, anterior, resultante),
    )


def test_upgrade_aplica_la_029_y_pasa_de_28_a_29_migraciones(previa):
    previa.cargar_historia()
    antes = previa.instantanea()["migraciones"]
    assert len(antes) == 28 and NOMBRE_029 not in antes

    previa.migrar()

    despues = previa.instantanea()["migraciones"]
    assert despues[:28] == antes and despues[28] == NOMBRE_029


def test_historia_completa_se_conserva_fila_por_fila_incluida_la_reversa(previa):
    previa.cargar_historia()
    antes = previa.instantanea()
    previa.migrar()

    despues = previa.instantanea()
    assert despues["ajustes"] == antes["ajustes"]
    assert despues["productos"] == antes["productos"]
    assert despues["lineas_ajuste"] == antes["lineas_ajuste"] == [(1, 3)]
    assert dict(despues["secuencias"])["ajustes_stock"] >= dict(antes["secuencias"])["ajustes_stock"]


def test_no_se_infiere_ningun_stock_inicial_historico(migrada):
    assert migrada.con.execute("SELECT COUNT(*) FROM ajustes_stock WHERE motivo = 'STOCK_INICIAL'").fetchone()[0] == 0
    assert migrada.con.execute("SELECT COUNT(*) FROM ajustes_stock WHERE usuario_id IS NULL").fetchone()[0] == 0


def test_integridad_y_claves_foraneas_limpias_tras_migrar(migrada):
    assert migrada.con.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert migrada.con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_no_queda_ningun_objeto_temporal_huerfano(migrada):
    nombres = {f[0] for f in migrada.con.execute("SELECT name FROM sqlite_master")}
    assert not {n for n in nombres if n.startswith("_") or n.endswith("_nueva")}
    assert migrada.con.execute("SELECT name FROM sqlite_temp_master").fetchall() == []


def test_base_vacia_tambien_migra(previa):
    previa.migrar()
    assert previa.con.execute("SELECT COUNT(*) FROM ajustes_stock").fetchone()[0] == 0


def test_guardia_previa_aborta_sin_dejar_cambios(previa):
    previa.cargar_historia()
    previa.con.execute("CREATE INDEX idx_inesperado ON ajustes_stock(fecha)")
    previa.con.commit()
    antes = previa.instantanea()

    with pytest.raises(ErrorBaseDatos, match="verificación fallida"):
        previa.migrar()

    previa.reabrir()
    assert previa.instantanea() == antes and NOMBRE_029 not in antes["migraciones"]


# --- reglas del STOCK_INICIAL, ya migrada ----------------------------------------------------


def test_stock_inicial_valido_con_y_sin_usuario(migrada):
    con = migrada.con
    _producto_nuevo(con, 10)
    _producto_nuevo(con, 11)
    _stock_inicial(con, 10, usuario_id=1)
    _stock_inicial(con, 11, usuario_id=None)

    assert con.execute(
        "SELECT producto_id, usuario_id FROM ajustes_stock WHERE motivo = 'STOCK_INICIAL' ORDER BY producto_id"
    ).fetchall() == [(10, 1), (11, None)]


def test_un_solo_stock_inicial_por_producto(migrada):
    con = migrada.con
    _producto_nuevo(con, 10)
    _stock_inicial(con, 10)

    with pytest.raises(sqlite3.IntegrityError):
        _stock_inicial(con, 10)


def test_stock_inicial_rechazado_para_un_producto_con_historia(migrada):
    with pytest.raises(sqlite3.IntegrityError, match="stock inicial no es válido"):
        _stock_inicial(migrada.con, 1, delta=10, resultante=10)


@pytest.mark.parametrize(
    "delta, anterior, resultante",
    [(7, 0, 8), (7, 1, 8), (-7, 7, 0)],  # no coincide con el stock actual / no parte de 0 / resta
)
def test_stock_inicial_inconsistente_es_rechazado(migrada, delta, anterior, resultante):
    _producto_nuevo(migrada.con, 10)
    with pytest.raises(sqlite3.IntegrityError):
        _stock_inicial(migrada.con, 10, delta=delta, anterior=anterior, resultante=resultante)


def test_otro_motivo_sin_usuario_sigue_rechazado(migrada):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        migrada.con.execute(
            "INSERT INTO ajustes_stock (producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante)"
            " VALUES (1, NULL, 'ROTURA', -1, 10, 9)"
        )


def test_stock_inicial_no_se_puede_revertir(migrada):
    con = migrada.con
    _producto_nuevo(con, 10)
    _stock_inicial(con, 10)
    stock_inicial_id = con.execute("SELECT id FROM ajustes_stock WHERE motivo = 'STOCK_INICIAL'").fetchone()[0]

    with pytest.raises(sqlite3.IntegrityError, match="reversa no es válida"):
        con.execute(
            "INSERT INTO ajustes_stock (producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante,"
            " observaciones, reversa_de_id) VALUES (10, 1, 'REVERSA', -7, 7, 0, 'x', ?)",
            (stock_inicial_id,),
        )


def test_reglas_de_reversa_de_028_siguen_vigentes(migrada):
    con = migrada.con
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):  # el 1 ya tiene reversa
        con.execute(
            "INSERT INTO ajustes_stock (producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante,"
            " observaciones, reversa_de_id) VALUES (1, 1, 'REVERSA', 2, 10, 12, 'x', 1)"
        )
    with pytest.raises(sqlite3.IntegrityError, match="no se puede modificar"):
        con.execute("UPDATE ajustes_stock SET reversa_de_id = NULL WHERE id = 2")


def test_el_guardia_de_inventario_lineas_cerrado_sigue_activo(migrada):
    with pytest.raises(sqlite3.IntegrityError, match="inventario cerrado"):
        migrada.con.execute("UPDATE inventario_lineas SET cantidad_contada = 99 WHERE id = 1")
