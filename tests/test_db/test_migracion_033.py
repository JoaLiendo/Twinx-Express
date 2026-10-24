"""Migración 033 (V1.13-A): clasificación de proveedores por categorías y datos fiscales.

Upgrade desde una base V1.12 (001-032) con proveedores activos e inactivos, compras contado y crédito,
pagos y categorías: cada proveedor queda idéntico, sin ningún dato fiscal inventado ni clasificación
asignada; el libro de cuenta a pagar y las compras no cambian. Nunca toca `data/kiosco.db`.
"""

import sqlite3

import pytest

import db.conexion as modulo_conexion

NOMBRE_033 = "033_proveedores_clasificacion_y_datos_fiscales.sql"
_TABLAS_HISTORICAS = ("proveedores", "compras", "detalle_compra", "movimientos_proveedor", "categorias", "productos")


class BaseAnterior:
    def __init__(self, ruta, monkeypatch):
        self.ruta = ruta
        monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
        self.con = sqlite3.connect(ruta)
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.execute(modulo_conexion._TABLA_MIGRACIONES)
        for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
            if migracion.name < NOMBRE_033:
                self.con.executescript(migracion.read_text(encoding="utf-8"))
                self.con.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (migracion.name,))
        self.con.commit()

    def cargar_historia(self) -> None:
        con = self.con
        con.execute(
            "INSERT INTO usuarios (id, nombre_usuario, nombre_completo, password_hash, rol)"
            " VALUES (1, 'duenio', 'Dueño', 'h', 'OWNER')"
        )
        con.execute("INSERT INTO categorias (id, nombre) VALUES (1, 'Bebidas'), (2, 'Limpieza')")
        con.execute("INSERT INTO categorias (id, nombre, categoria_padre_id) VALUES (3, 'Gaseosas', 1)")
        con.execute(
            "INSERT INTO productos (id, codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos,"
            " stock_actual, categoria_id) VALUES (1, 'c1', 'Cola', 100, 200, 0, 3)"
        )
        con.execute(
            "INSERT INTO proveedores (id, nombre, contacto_nombre, telefono, notas, activo)"
            " VALUES (1, 'Norte', 'Ana', '4444', 'martes', 1), (2, 'Viejo', NULL, NULL, NULL, 0)"
        )
        con.execute(
            "INSERT INTO compras (id, proveedor_id, usuario_id, total_centavos, condicion_pago)"
            " VALUES (1, 1, 1, 1000, 'CREDITO'), (2, 2, 1, 500, 'CONTADO')"
        )
        con.execute(
            "INSERT INTO detalle_compra (compra_id, producto_id, cantidad, costo_unitario_centavos, subtotal_centavos)"
            " VALUES (1, 1, 10, 100, 1000), (2, 1, 5, 100, 500)"
        )
        con.execute(
            "INSERT INTO movimientos_proveedor (proveedor_id, tipo, monto_centavos, compra_id, usuario_id)"
            " VALUES (1, 'CARGO_COMPRA', 1000, 1, 1)"
        )
        con.execute(
            "INSERT INTO movimientos_proveedor (proveedor_id, tipo, monto_centavos, medio_pago, usuario_id)"
            " VALUES (1, 'PAGO', 400, 'TRANSFERENCIA', 1)"
        )
        con.commit()

    def instantanea(self) -> dict:
        columnas_previas = "id, nombre, contacto_nombre, telefono, email, direccion, notas, activo, fecha_creacion"
        resultado = {t: self.con.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in _TABLAS_HISTORICAS[1:]}
        resultado["proveedores"] = self.con.execute(f"SELECT {columnas_previas} FROM proveedores ORDER BY id").fetchall()
        return resultado

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
    previa.saldo_antes = previa.con.execute(
        "SELECT SUM(CASE tipo WHEN 'CARGO_COMPRA' THEN monto_centavos ELSE -monto_centavos END)"
        " FROM movimientos_proveedor WHERE proveedor_id = 1"
    ).fetchone()[0]
    previa.migrar()
    return previa


def test_preserva_proveedores_compras_y_saldos_sin_inventar_datos(migrada):
    con = migrada.con
    assert migrada.instantanea() == migrada.antes
    assert con.execute(
        "SELECT razon_social, cuit, condicion_fiscal, ingresos_brutos, convenio_multilateral FROM proveedores"
    ).fetchall() == [(None, None, None, None, 0), (None, None, None, None, 0)]
    assert con.execute("SELECT COUNT(*) FROM proveedor_categorias").fetchone()[0] == 0
    saldo = con.execute(
        "SELECT SUM(CASE tipo WHEN 'CARGO_COMPRA' THEN monto_centavos ELSE -monto_centavos END)"
        " FROM movimientos_proveedor WHERE proveedor_id = 1"
    ).fetchone()[0]
    assert saldo == migrada.saldo_antes == 600
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert NOMBRE_033 in [f[0] for f in con.execute("SELECT nombre_archivo FROM schema_migraciones")]


def test_reiniciar_la_aplicacion_no_vuelve_a_aplicar_nada(migrada):
    antes = migrada.con.execute("SELECT * FROM proveedores ORDER BY id").fetchall()
    migrada.migrar()
    migrada.migrar()
    assert migrada.con.execute("SELECT * FROM proveedores ORDER BY id").fetchall() == antes
    assert migrada.con.execute(
        "SELECT COUNT(*) FROM schema_migraciones WHERE nombre_archivo = ?", (NOMBRE_033,)
    ).fetchone()[0] == 1


def test_una_categoria_que_clasifica_proveedores_no_se_borra_fisicamente(migrada):
    con = migrada.con
    con.execute("INSERT INTO proveedor_categorias VALUES (1, 2)")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        con.execute("DELETE FROM categorias WHERE id = 2")


def test_cuit_unico_solo_cuando_esta_informado(migrada):
    con = migrada.con
    con.execute("UPDATE proveedores SET cuit = '30712345671' WHERE id = 2")
    with pytest.raises(sqlite3.IntegrityError, match="proveedores.cuit"):
        con.execute("UPDATE proveedores SET cuit = '30712345671' WHERE id = 1")
    con.execute("INSERT INTO proveedores (nombre) VALUES ('Sin CUIT 1'), ('Sin CUIT 2')")


def test_falla_si_proveedores_no_tiene_la_forma_esperada(previa):
    previa.con.execute("ALTER TABLE proveedores ADD COLUMN cuit TEXT NULL")
    previa.con.commit()
    with pytest.raises(Exception, match="Migración 033"):
        modulo_conexion.inicializar_base_datos()
    aplicadas = [f[0] for f in previa.con.execute("SELECT nombre_archivo FROM schema_migraciones")]
    assert NOMBRE_033 not in aplicadas
    assert not previa.con.execute("SELECT 1 FROM sqlite_master WHERE name = 'proveedor_categorias'").fetchall()
