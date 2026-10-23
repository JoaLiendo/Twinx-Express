"""Migración 030 (V1.11-E): categorías jerárquicas (`categoria_padre_id`, sin ciclos) y etiquetas.

Upgrade desde una base V1.11-D (001-029) con categorías planas activas e inactivas, productos con y
sin categoría, ventas y ajustes: nada cambia y ninguna categoría queda "dentro" de otra.
Nunca toca `data/kiosco.db`.
"""

import sqlite3

import pytest

import db.conexion as modulo_conexion
from excepciones import ErrorBaseDatos

NOMBRE_030 = "030_categorias_jerarquicas_y_etiquetas.sql"


class BaseAnterior:
    def __init__(self, ruta, monkeypatch):
        self.ruta = ruta
        monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
        self.con = sqlite3.connect(ruta)
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.execute(modulo_conexion._TABLA_MIGRACIONES)
        for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
            if migracion.name < NOMBRE_030:
                self.con.executescript(migracion.read_text(encoding="utf-8"))
                self.con.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (migracion.name,))
        self.con.commit()

    def cargar_historia(self) -> None:
        con = self.con
        con.execute(
            "INSERT INTO usuarios (id, nombre_usuario, nombre_completo, password_hash, rol)"
            " VALUES (1, 'duenio', 'Dueño', 'h', 'OWNER')"
        )
        con.execute("INSERT INTO categorias (id, nombre) VALUES (1, 'Bebidas'), (2, 'Golosinas')")
        con.execute("INSERT INTO categorias (id, nombre, activa) VALUES (3, 'Vieja', 0)")
        con.execute(
            "INSERT INTO productos (id, codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos,"
            " stock_actual, categoria_id) VALUES (1, 'c1', 'Cola', 100, 200, 10, 1), (2, 'c2', 'Chicle', 10, 30, 0, 3),"
            " (3, 'c3', 'Suelto', 50, 90, 0, NULL)"
        )
        con.execute(
            "INSERT INTO ajustes_stock (producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante)"
            " VALUES (1, 1, 'ROTURA', -1, 11, 10)"
        )
        con.commit()

    def instantanea(self) -> dict:
        con = self.con
        return {
            "categorias": con.execute("SELECT id, nombre, activa, fecha_creacion FROM categorias ORDER BY id").fetchall(),
            "productos": con.execute("SELECT * FROM productos ORDER BY id").fetchall(),
            "ajustes": con.execute("SELECT * FROM ajustes_stock ORDER BY id").fetchall(),
        }

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


def test_upgrade_preserva_todo_y_deja_las_categorias_como_principales(migrada):
    con = migrada.con
    assert migrada.instantanea() == migrada.antes
    assert con.execute("SELECT COUNT(*) FROM categorias WHERE categoria_padre_id IS NOT NULL").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM etiquetas").fetchone()[0] == 0
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert NOMBRE_030 in [f[0] for f in con.execute("SELECT nombre_archivo FROM schema_migraciones")]


def test_upgrade_sobre_base_vacia(previa):
    previa.migrar()
    assert previa.con.execute("SELECT COUNT(*) FROM categorias").fetchone()[0] == 0


def test_no_se_puede_formar_un_ciclo(migrada):
    con = migrada.con
    con.execute("UPDATE categorias SET categoria_padre_id = 1 WHERE id = 2")  # Golosinas dentro de Bebidas
    con.execute("INSERT INTO categorias (id, nombre, categoria_padre_id) VALUES (4, 'Chicles', 2)")
    for categoria_id, padre_id in ((1, 1), (1, 2), (1, 4)):
        with pytest.raises(sqlite3.IntegrityError, match="sí misma"):
            con.execute("UPDATE categorias SET categoria_padre_id = ? WHERE id = ?", (padre_id, categoria_id))
    with pytest.raises(sqlite3.IntegrityError, match="sí misma"):
        con.execute("INSERT INTO categorias (id, nombre, categoria_padre_id) VALUES (9, 'Yo', 9)")


def test_no_se_borra_una_categoria_con_subcategorias(migrada):
    con = migrada.con
    con.execute("INSERT INTO categorias (id, nombre, categoria_padre_id) VALUES (4, 'Gaseosas', 1)")
    con.execute("UPDATE productos SET categoria_id = 4 WHERE id = 1")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM categorias WHERE id = 1")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO categorias (nombre, categoria_padre_id) VALUES ('Huérfana', 999)")


def test_etiquetas_nombre_unico_sin_mayusculas_y_cascada(migrada):
    con = migrada.con
    con.execute("INSERT INTO etiquetas (id, nombre) VALUES (1, 'Frío'), (2, 'Oferta')")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO etiquetas (nombre) VALUES ('OFERTA')")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO etiquetas (nombre) VALUES ('   ')")
    con.execute("INSERT INTO producto_etiquetas VALUES (1, 1), (1, 2), (3, 2)")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO producto_etiquetas VALUES (1, 1)")
    con.execute("DELETE FROM etiquetas WHERE id = 2")
    assert con.execute("SELECT * FROM producto_etiquetas").fetchall() == [(1, 1)]
    con.execute("DELETE FROM productos WHERE id = 3")  # sin historia: se borra, sin etiquetas que lo bloqueen
    con.execute("DELETE FROM productos WHERE id = 2")
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_una_base_con_columnas_inesperadas_no_se_migra(previa):
    previa.con.execute("ALTER TABLE categorias ADD COLUMN categoria_padre_id INTEGER")
    previa.con.commit()
    with pytest.raises(ErrorBaseDatos, match="columnas esperadas"):
        modulo_conexion.inicializar_base_datos()
    previa.con.close()
    previa.con = sqlite3.connect(previa.ruta)
    assert NOMBRE_030 not in [f[0] for f in previa.con.execute("SELECT nombre_archivo FROM schema_migraciones")]
    assert previa.con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'etiquetas'").fetchone()[0] == 0
