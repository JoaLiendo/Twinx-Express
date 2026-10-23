"""Migración 032 (V1.12-A): partidas de stock y libro de movimientos.

Upgrade desde una base V1.11 (001-031) con productos activos/inactivos con y sin stock, ventas, compras y
ajustes: cada producto con stock recibe UNA partida HEREDADA por exactamente su stock, sin vencimiento,
costo ni fecha inventados; nada más cambia. Además se prueban las garantías de la base sobre el libro.
Nunca toca `data/kiosco.db`.
"""

import sqlite3

import pytest

import db.conexion as modulo_conexion
from excepciones import ErrorBaseDatos

NOMBRE_032 = "032_partidas_stock.sql"


class BaseAnterior:
    def __init__(self, ruta, monkeypatch):
        self.ruta = ruta
        monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
        self.con = sqlite3.connect(ruta)
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.execute(modulo_conexion._TABLA_MIGRACIONES)
        for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
            if migracion.name < NOMBRE_032:
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
            " stock_actual, activo) VALUES (1, 'c1', 'Yogur', 100, 200, 30, 1), (2, 'c2', 'Sin stock', 10, 20, 0, 1),"
            " (3, 'c3', 'Inactivo con stock', 50, 90, 4, 0), (4, 'c4', 'Inactivo sin stock', 50, 90, 0, 0)"
        )
        con.execute(
            "INSERT INTO ajustes_stock (producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante,"
            " costo_unitario_centavos) VALUES (1, 1, 'ROTURA', -2, 32, 30, 100)"
        )
        con.commit()

    def instantanea(self) -> dict:
        con = self.con
        return {t: con.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in ("productos", "ajustes_stock")}

    def migrar(self) -> None:
        self.con.commit()
        modulo_conexion.inicializar_base_datos()
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
    previa.antes = previa.instantanea()
    previa.migrar()
    return previa


def test_heredada_por_exactamente_el_stock_sin_datos_inventados(migrada):
    con = migrada.con
    assert migrada.instantanea() == migrada.antes
    assert con.execute(
        "SELECT producto_id, origen, cantidad_original, cantidad_disponible, fecha_vencimiento, costo_unitario_centavos,"
        " fecha_ingreso, detalle_compra_id, ajuste_id, detalle_venta_id FROM partidas_stock ORDER BY producto_id"
    ).fetchall() == [
        (1, "HEREDADA", 30, 30, None, None, None, None, None, None),
        (3, "HEREDADA", 4, 4, None, None, None, None, None, None),
    ]
    assert con.execute("SELECT COUNT(*) FROM movimientos_partida").fetchone()[0] == 0
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert NOMBRE_032 in [f[0] for f in con.execute("SELECT nombre_archivo FROM schema_migraciones")]


def test_base_vacia(previa):
    previa.migrar()
    assert previa.con.execute("SELECT COUNT(*) FROM partidas_stock").fetchone()[0] == 0


def test_stock_negativo_previo_aborta_sin_cambios(previa):
    previa.cargar_historia()
    previa.con.execute("PRAGMA ignore_check_constraints = ON")
    previa.con.execute("UPDATE productos SET stock_actual = -1 WHERE id = 2")
    previa.con.commit()
    with pytest.raises(ErrorBaseDatos, match="stock negativo"):
        modulo_conexion.inicializar_base_datos()
    assert previa.con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'partidas_stock'").fetchone()[0] == 0


class TestGarantiasDelLibro:
    """La causa de los movimientos es el ajuste ROTURA (id 1) de la historia cargada."""

    def test_el_movimiento_aplica_el_disponible(self, migrada):
        con = migrada.con

        con.execute("INSERT INTO movimientos_partida (partida_id, delta, ajuste_id, criterio) VALUES (1, -5, 1, 'FEFO')")
        assert con.execute("SELECT cantidad_disponible FROM partidas_stock WHERE id = 1").fetchone()[0] == 25

    def test_no_se_consume_de_mas_ni_se_devuelve_por_encima_de_lo_ingresado(self, migrada):
        con = migrada.con

        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO movimientos_partida (partida_id, delta, ajuste_id, criterio) VALUES (1, -31, 1, 'FEFO')")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO movimientos_partida (partida_id, delta, ajuste_id, criterio) VALUES (1, 1, 1, 'REVERSION')")

    def test_el_disponible_no_se_escribe_directo(self, migrada):
        with pytest.raises(sqlite3.IntegrityError, match="solo cambia mediante un movimiento"):
            migrada.con.execute("UPDATE partidas_stock SET cantidad_disponible = 29 WHERE id = 1")

    def test_origen_movimientos_y_partidas_inmutables(self, migrada):
        con = migrada.con

        con.execute("INSERT INTO movimientos_partida (partida_id, delta, ajuste_id, criterio) VALUES (1, -1, 1, 'FEFO')")
        for sql in (
            "UPDATE partidas_stock SET fecha_vencimiento = '2030-01-01' WHERE id = 1",
            "UPDATE partidas_stock SET costo_unitario_centavos = 1 WHERE id = 1",
            "UPDATE movimientos_partida SET delta = -2",
            "DELETE FROM movimientos_partida",
            "DELETE FROM partidas_stock WHERE id = 2",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(sql)

    @pytest.mark.parametrize(
        "columnas, valores",
        [
            ("origen", "'COMPRA'"),  # compra sin su línea
            ("origen, ajuste_id", "'STOCK_INICIAL', NULL"),  # alta sin su ajuste
            ("origen, fecha_vencimiento", "'HEREDADA', '2030-01-01'"),  # heredada con dato inventado
            ("origen, fecha_ingreso", "'HEREDADA', '2026-01-01'"),
            ("origen, ajuste_id, fecha_vencimiento", "'RECUENTO', 1, '2030-02-30'"),  # fecha imposible
        ],
    )
    def test_constraints_de_origen(self, migrada, columnas, valores):
        con = migrada.con
        extra = "" if "fecha_ingreso" in columnas or "'HEREDADA'" in valores else ", fecha_ingreso"
        extra_v = "" if extra == "" else ", datetime('now')"
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                f"INSERT INTO partidas_stock (producto_id, {columnas}, cantidad_original, cantidad_disponible{extra})"
                f" VALUES (2, {valores}, 5, 5{extra_v})"
            )

    def test_una_sola_causa_por_movimiento(self, migrada):
        con = migrada.con

        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO movimientos_partida (partida_id, delta, detalle_venta_id, ajuste_id, criterio)"
                " VALUES (1, -1, 1, 1, 'FEFO')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO movimientos_partida (partida_id, delta, criterio) VALUES (1, -1, 'FEFO')")

    def test_una_partida_nace_completa_y_una_sola_heredada(self, migrada):
        con = migrada.con
        with pytest.raises(sqlite3.IntegrityError, match="nace con todo"):
            con.execute(
                "INSERT INTO partidas_stock (producto_id, origen, ajuste_id, cantidad_original, cantidad_disponible,"
                " fecha_ingreso) VALUES (1, 'AJUSTE', 1, 5, 3, datetime('now'))"
            )
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO partidas_stock (producto_id, origen, cantidad_original, cantidad_disponible)"
                " VALUES (1, 'HEREDADA', 1, 1)"
            )
