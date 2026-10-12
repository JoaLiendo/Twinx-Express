"""Migración 028 (reversas auditables de ajustes de stock, V1.11-B): amplía el CHECK de
`ajustes_stock.motivo` con `REVERSA` y agrega `ajustes_stock.reversa_de_id` (nullable,
autorreferencia), con un índice único parcial (a lo sumo una reversa por original) y dos triggers
que validan la consistencia de la reversa e impiden modificar `reversa_de_id`.

Mismo caso de estrés que la migración 027 (ver ese archivo de test): un inventario CONFIRMADO con
un RECUENTO ya asignado a `inventario_lineas.ajuste_id` (hijo ON DELETE RESTRICT de
`ajustes_stock`, la tabla que se reconstruye). Nunca toca `data/kiosco.db`.
"""

import sqlite3

import pytest

import db.conexion as modulo_conexion
from excepciones import ErrorBaseDatos

NOMBRE_028 = "028_reversas_ajuste_stock.sql"

COLUMNAS_AJUSTES_PREVIAS = (
    "id, producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones,"
    " fecha, clave_idempotencia, inventario_id, costo_unitario_centavos"
)


class BaseAnterior:
    """Base con el esquema previo a la 028 (migraciones 001-027), con historia representativa."""

    def __init__(self, ruta, monkeypatch):
        self.ruta = ruta
        self.monkeypatch = monkeypatch
        monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
        self.con = sqlite3.connect(ruta)
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.execute(modulo_conexion._TABLA_MIGRACIONES)
        for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
            if migracion.name < NOMBRE_028:
                self.con.executescript(migracion.read_text(encoding="utf-8"))
                self.con.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (migracion.name,))
        self.con.commit()

    def cargar_historia(self) -> None:
        """Un ajuste manual (ROTURA, costo congelado) y un inventario físico confirmado con una
        línea en diferencia: genera un RECUENTO con `inventario_lineas.ajuste_id` asignado -- el
        caso real de la referencia ON DELETE RESTRICT que la migración debe atravesar."""
        con = self.con
        con.execute(
            "INSERT INTO usuarios (id, nombre_usuario, nombre_completo, password_hash, rol)"
            " VALUES (1, 'duenio', 'Dueño', 'h', 'OWNER')"
        )
        con.execute(
            "INSERT INTO productos (id, codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos,"
            " stock_actual) VALUES (1, 'c1', 'Alfajor', 100, 200, 8), (2, 'c2', 'Gaseosa', 150, 300, 8)"
        )
        con.execute(
            "INSERT INTO ajustes_stock (id, producto_id, usuario_id, motivo, delta, stock_anterior,"
            " stock_resultante, costo_unitario_centavos) VALUES (1, 1, 1, 'ROTURA', -2, 10, 8, 100)"
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


def _insertar_ajuste(con, **campos):
    base = dict(
        producto_id=1, usuario_id=1, motivo="ROTURA", delta=-1, stock_anterior=8, stock_resultante=7,
        observaciones=None, inventario_id=None, costo_unitario_centavos=None, reversa_de_id=None,
    )
    base.update(campos)
    columnas = ", ".join(base.keys())
    marcadores = ", ".join("?" for _ in base)
    cur = con.execute(
        f"INSERT INTO ajustes_stock ({columnas}) VALUES ({marcadores})", tuple(base.values())
    )
    return cur.lastrowid


def test_upgrade_aplica_la_028_y_pasa_de_27_a_28_migraciones(previa):
    previa.cargar_historia()
    antes = previa.instantanea()["migraciones"]
    assert len(antes) == 27 and NOMBRE_028 not in antes

    previa.migrar()

    despues = previa.instantanea()["migraciones"]
    todas = sorted(p.name for p in modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql"))
    assert despues == todas and len(despues) >= 28
    assert despues[:27] == antes and despues[27] == NOMBRE_028


def test_ajustes_stock_conserva_datos_fila_por_fila(previa):
    previa.cargar_historia()
    antes = previa.instantanea()
    previa.migrar()

    despues = previa.instantanea()
    assert despues["ajustes"] == antes["ajustes"]
    assert despues["productos"] == antes["productos"]


def test_inventario_lineas_ajuste_id_se_restaura(migrada):
    assert migrada.instantanea()["lineas_ajuste"] == [(1, 2)]


def test_ajustes_historicos_quedan_con_reversa_de_id_null(migrada):
    filas = migrada.con.execute("SELECT id, reversa_de_id FROM ajustes_stock ORDER BY id").fetchall()
    assert filas == [(1, None), (2, None)]


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

    filas = previa.con.execute("SELECT COUNT(*) FROM schema_migraciones WHERE nombre_archivo = ?", (NOMBRE_028,))
    assert filas.fetchone()[0] == 1


def test_guardia_previa_aborta_si_la_columna_ya_existe_y_no_deja_cambios(previa):
    previa.cargar_historia()
    previa.con.execute("ALTER TABLE ajustes_stock ADD COLUMN reversa_de_id INTEGER NULL")
    previa.con.commit()
    antes = previa.instantanea()

    with pytest.raises(ErrorBaseDatos, match="verificación fallida"):
        previa.migrar()

    previa.reabrir()
    assert previa.instantanea() == antes
    assert NOMBRE_028 not in antes["migraciones"]


# --- CHECK / triggers de la reversa, ya migrada ------------------------------------------


def test_reversa_valida_es_aceptada(migrada):
    con = migrada.con
    reversa_id = _insertar_ajuste(
        con, motivo="REVERSA", delta=2, stock_anterior=8, stock_resultante=10,
        observaciones="corrección", reversa_de_id=1,
    )
    assert con.execute("SELECT motivo FROM ajustes_stock WHERE id = ?", (reversa_id,)).fetchone()[0] == "REVERSA"


def test_segunda_reversa_del_mismo_original_es_rechazada(migrada):
    con = migrada.con
    _insertar_ajuste(con, motivo="REVERSA", delta=2, stock_anterior=8, stock_resultante=10,
                      observaciones="primera", reversa_de_id=1)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        _insertar_ajuste(con, motivo="REVERSA", delta=2, stock_anterior=10, stock_resultante=12,
                          observaciones="segunda", reversa_de_id=1)


def test_reversa_de_una_reversa_es_rechazada(migrada):
    con = migrada.con
    reversa_id = _insertar_ajuste(con, motivo="REVERSA", delta=2, stock_anterior=8, stock_resultante=10,
                                   observaciones="primera", reversa_de_id=1)
    with pytest.raises(sqlite3.IntegrityError, match="no es válida"):
        _insertar_ajuste(con, motivo="REVERSA", delta=-2, stock_anterior=10, stock_resultante=8,
                          observaciones="reversa de reversa", reversa_de_id=reversa_id)


def test_reversa_de_un_recuento_es_rechazada(migrada):
    con = migrada.con
    with pytest.raises(sqlite3.IntegrityError, match="no es válida"):
        _insertar_ajuste(con, producto_id=2, motivo="REVERSA", delta=-3, stock_anterior=8, stock_resultante=5,
                          observaciones="no debería poder", reversa_de_id=2)


def test_reversa_con_delta_distinto_de_menos_el_original_es_rechazada(migrada):
    con = migrada.con
    with pytest.raises(sqlite3.IntegrityError, match="no es válida"):
        _insertar_ajuste(con, motivo="REVERSA", delta=5, stock_anterior=8, stock_resultante=13,
                          observaciones="delta incorrecto", reversa_de_id=1)


def test_motivo_reversa_sin_reversa_de_id_es_rechazado(migrada):
    con = migrada.con
    with pytest.raises(sqlite3.IntegrityError, match="no es válida"):
        _insertar_ajuste(con, motivo="REVERSA", delta=1, stock_anterior=8, stock_resultante=9,
                          observaciones="sin original")


def test_reversa_de_id_con_motivo_distinto_es_rechazado(migrada):
    con = migrada.con
    with pytest.raises(sqlite3.IntegrityError, match="no es válida"):
        _insertar_ajuste(con, motivo="ROTURA", delta=-1, stock_anterior=8, stock_resultante=7, reversa_de_id=1)


def test_reversa_de_id_es_inmutable(migrada):
    con = migrada.con
    reversa_id = _insertar_ajuste(con, motivo="REVERSA", delta=2, stock_anterior=8, stock_resultante=10,
                                   observaciones="corrección", reversa_de_id=1)
    with pytest.raises(sqlite3.IntegrityError, match="no se puede modificar"):
        con.execute("UPDATE ajustes_stock SET reversa_de_id = NULL WHERE id = ?", (reversa_id,))


def test_falla_fabrica_y_demas_motivos_de_027_siguen_funcionando(migrada):
    con = migrada.con
    con.execute(
        "INSERT INTO ajustes_stock (producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante)"
        " VALUES (1, 1, 'FALLA_FABRICA', -1, 8, 7)"
    )
    assert con.execute("SELECT motivo FROM ajustes_stock ORDER BY id DESC LIMIT 1").fetchone()[0] == "FALLA_FABRICA"


def test_el_guardia_de_inventario_lineas_cerrado_sigue_activo(migrada):
    with pytest.raises(sqlite3.IntegrityError, match="inventario cerrado"):
        migrada.con.execute("UPDATE inventario_lineas SET cantidad_contada = 99 WHERE id = 1")
