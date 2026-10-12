"""Migración 027 (nuevos motivos manuales y snapshot de costo, V1.11-A): amplía el CHECK de
`ajustes_stock.motivo` con `FALLA_FABRICA` y agrega `ajustes_stock.costo_unitario_centavos`
(nullable). Reconstruye `ajustes_stock`, que tiene un hijo `ON DELETE RESTRICT`
(`inventario_lineas.ajuste_id`) -- el caso de estrés real es un inventario CONFIRMADO con un
RECUENTO ya asignado a su línea.

Cada test arma una base con el esquema previo a la 027 (migraciones 001-026, V1.10.0 completa) y
datos históricos representativos, aplica la 027 con el runner real (`inicializar_base_datos`) e
inspecciona el resultado. Nunca toca `data/kiosco.db`.
"""

import sqlite3

import pytest

import db.conexion as modulo_conexion
from excepciones import ErrorBaseDatos

NOMBRE_027 = "027_motivos_ajuste_stock.sql"

COLUMNAS_AJUSTES_PREVIAS = (
    "id, producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones,"
    " fecha, clave_idempotencia, inventario_id"
)


class BaseAnterior:
    """Base con el esquema de V1.10.0 (migraciones 001-026), con historia representativa."""

    def __init__(self, ruta, monkeypatch):
        self.ruta = ruta
        self.monkeypatch = monkeypatch
        monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
        self.con = sqlite3.connect(ruta)
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.execute(modulo_conexion._TABLA_MIGRACIONES)
        for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
            if migracion.name < NOMBRE_027:
                self.con.executescript(migracion.read_text(encoding="utf-8"))
                self.con.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (migracion.name,))
        self.con.commit()

    def cargar_historia(self) -> None:
        """Un ajuste manual legacy (MERMA, sin inventario) y un inventario físico confirmado con
        una línea en diferencia: genera un RECUENTO con `inventario_lineas.ajuste_id` asignado --
        el caso real de la referencia ON DELETE RESTRICT que la migración debe atravesar."""
        con = self.con
        con.execute(
            "INSERT INTO usuarios (id, nombre_usuario, nombre_completo, password_hash, rol)"
            " VALUES (1, 'duenio', 'Dueño', 'h', 'OWNER')"
        )
        con.execute(
            "INSERT INTO productos (id, codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos,"
            " stock_actual) VALUES (1, 'c1', 'Alfajor', 100, 200, 10), (2, 'c2', 'Gaseosa', 150, 300, 8)"
        )
        con.execute(
            "INSERT INTO ajustes_stock (id, producto_id, usuario_id, motivo, delta, stock_anterior,"
            " stock_resultante) VALUES (1, 1, 1, 'MERMA', -2, 12, 10)"
        )
        con.execute("INSERT INTO inventarios (id, estado, usuario_id) VALUES (1, 'ABIERTO', 1)")
        con.execute("INSERT INTO inventario_lineas (id, inventario_id, producto_id) VALUES (1, 1, 2)")
        con.execute(
            "UPDATE inventario_lineas SET stock_esperado = 5, version_esperada = 0, cantidad_contada = 8,"
            " costo_unitario_centavos = 150, usuario_conteo_id = 1, fecha_conteo = datetime('now') WHERE id = 1"
        )
        con.execute(
            "INSERT INTO ajustes_stock (id, producto_id, usuario_id, motivo, delta, stock_anterior,"
            " stock_resultante, observaciones, inventario_id) VALUES (2, 2, 1, 'RECUENTO', 3, 5, 8,"
            " 'Inventario #1', 1)"
        )
        con.execute("UPDATE inventario_lineas SET ajuste_id = 2 WHERE id = 1")
        con.execute(
            "UPDATE inventarios SET estado = 'CONFIRMADO', fecha_cierre = datetime('now'),"
            " usuario_cierre_id = 1 WHERE id = 1"
        )
        con.commit()

    def instantanea(self) -> dict:
        con = self.con
        return {
            "ajustes": con.execute(
                f"SELECT {COLUMNAS_AJUSTES_PREVIAS} FROM ajustes_stock ORDER BY id"
            ).fetchall(),
            "lineas_ajuste": con.execute(
                "SELECT id, ajuste_id FROM inventario_lineas WHERE ajuste_id IS NOT NULL ORDER BY id"
            ).fetchall(),
            "productos": con.execute("SELECT id, stock_actual FROM productos ORDER BY id").fetchall(),
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


def test_upgrade_aplica_la_027_y_pasa_de_26_a_27_migraciones(previa):
    previa.cargar_historia()
    antes = previa.instantanea()["migraciones"]
    assert len(antes) == 26 and NOMBRE_027 not in antes

    previa.migrar()

    despues = previa.instantanea()["migraciones"]
    todas = sorted(p.name for p in modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql"))
    assert despues == todas and len(despues) >= 27
    assert despues[:26] == antes and despues[26] == NOMBRE_027


def test_ajustes_stock_conserva_datos_fila_por_fila(previa):
    previa.cargar_historia()
    antes = previa.instantanea()
    previa.migrar()

    despues = previa.instantanea()
    assert despues["ajustes"] == antes["ajustes"]
    assert despues["productos"] == antes["productos"]


def test_inventario_lineas_ajuste_id_se_restaura(migrada):
    assert migrada.instantanea()["lineas_ajuste"] == [(1, 2)]


def test_ajustes_historicos_quedan_con_costo_unitario_centavos_null(migrada):
    filas = migrada.con.execute("SELECT id, costo_unitario_centavos FROM ajustes_stock ORDER BY id").fetchall()
    assert filas == [(1, None), (2, None)]


def test_falla_fabrica_ahora_es_un_motivo_valido(migrada):
    migrada.con.execute(
        "INSERT INTO ajustes_stock (producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante,"
        " costo_unitario_centavos) VALUES (1, 1, 'FALLA_FABRICA', -1, 10, 9, 100)"
    )
    fila = migrada.con.execute(
        "SELECT motivo, costo_unitario_centavos FROM ajustes_stock ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert fila == ("FALLA_FABRICA", 100)


def test_motivo_invalido_sigue_siendo_rechazado(migrada):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        migrada.con.execute(
            "INSERT INTO ajustes_stock (producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante)"
            " VALUES (1, 1, 'DESCUENTO_PROMOCIONAL', -1, 10, 9)"
        )


def test_el_guardia_de_inventario_lineas_cerrado_sigue_activo(migrada):
    with pytest.raises(sqlite3.IntegrityError, match="inventario cerrado"):
        migrada.con.execute("UPDATE inventario_lineas SET cantidad_contada = 99 WHERE id = 1")


def test_el_trigger_de_recuento_valido_sigue_activo(migrada):
    """`trg_ajustes_stock_inventario_valido` (migración 022) debe seguir rechazando un RECUENTO
    que no corresponda a una línea contada de un inventario ABIERTO."""
    with pytest.raises(sqlite3.IntegrityError, match="línea contada"):
        migrada.con.execute(
            "INSERT INTO ajustes_stock (producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante,"
            " inventario_id) VALUES (2, 1, 'RECUENTO', 1, 8, 9, 1)"
        )


def test_integridad_y_claves_foraneas_limpias_tras_migrar(migrada):
    assert migrada.con.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert migrada.con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_sqlite_sequence_no_retrocede(previa):
    previa.cargar_historia()
    antes = dict(previa.instantanea()["secuencias"])
    previa.migrar()

    despues = dict(previa.instantanea()["secuencias"])
    assert despues["ajustes_stock"] >= antes["ajustes_stock"]


def test_no_queda_ninguna_tabla_ni_objeto_temporal_huerfano(migrada):
    nombres = {f[0] for f in migrada.con.execute("SELECT name FROM sqlite_master")}
    assert not {n for n in nombres if n.startswith("_") or n.endswith("_nueva")}
    assert migrada.con.execute("SELECT name FROM sqlite_temp_master").fetchall() == []


def test_base_vacia_tambien_migra(previa):
    previa.migrar()
    assert previa.con.execute("SELECT COUNT(*) FROM ajustes_stock").fetchone()[0] == 0


def test_la_migracion_queda_registrada_una_sola_vez(previa):
    previa.cargar_historia()
    previa.migrar()
    modulo_conexion.inicializar_base_datos()

    filas = previa.con.execute("SELECT COUNT(*) FROM schema_migraciones WHERE nombre_archivo = ?", (NOMBRE_027,))
    assert filas.fetchone()[0] == 1


def test_guardia_previa_aborta_si_la_columna_ya_existe_y_no_deja_cambios(previa):
    previa.cargar_historia()
    previa.con.execute(
        "ALTER TABLE ajustes_stock ADD COLUMN costo_unitario_centavos INTEGER NULL"
    )
    previa.con.commit()
    antes = previa.instantanea()

    with pytest.raises(ErrorBaseDatos, match="verificación fallida"):
        previa.migrar()

    previa.reabrir()
    assert previa.instantanea() == antes
    assert NOMBRE_027 not in antes["migraciones"]
