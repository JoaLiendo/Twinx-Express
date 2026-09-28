"""Migración 026 (fecha de vencimiento de las compras, V1.10-A): `compras.fecha_vencimiento TEXT NULL`
con un CHECK que la liga a la condición de pago y a la fecha de la compra.

Cada test arma una base con el esquema previo a la 026 (migraciones 001-025, V1.9.0 completa) y datos
históricos representativos (compras contado y a crédito con su libro de proveedor, pagos en efectivo y por
transferencia, una reversa, caja y cuenta corriente de clientes), aplica la 026 con el runner real
(`inicializar_base_datos`) e inspecciona el resultado. Nunca toca `data/kiosco.db`.
"""

import shutil
import sqlite3

import pytest

import db.conexion as modulo_conexion
from excepciones import ErrorBaseDatos

NOMBRE_026 = "026_vencimiento_compras.sql"
FECHA_COMPRA = "2026-04-01 10:00:00"

COLUMNAS_COMPRAS_PREVIAS = (
    "id, proveedor_id, usuario_id, fecha, observaciones, total_centavos, clave_idempotencia, estado,"
    " motivo_anulacion, observaciones_anulacion, anulada_por_usuario_id, fecha_anulacion, costo_trazable,"
    " condicion_pago"
)
SALDO_SQL = (
    "SELECT proveedor_id, SUM(CASE tipo WHEN 'CARGO_COMPRA' THEN monto_centavos ELSE -monto_centavos END)"
    " FROM movimientos_proveedor GROUP BY proveedor_id ORDER BY proveedor_id"
)


class BaseAnterior:
    """Base con el esquema de V1.9.0 (migraciones 001-025), con historia representativa."""

    def __init__(self, ruta, monkeypatch):
        self.ruta = ruta
        self.monkeypatch = monkeypatch
        monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
        self.con = sqlite3.connect(ruta)
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.execute(modulo_conexion._TABLA_MIGRACIONES)
        for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
            if migracion.name < NOMBRE_026:
                self.con.executescript(migracion.read_text(encoding="utf-8"))
                self.con.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (migracion.name,))
        self.con.commit()

    def cargar_historia(self) -> None:
        con = self.con
        con.execute(
            "INSERT INTO usuarios (id, nombre_usuario, nombre_completo, password_hash, rol)"
            " VALUES (1, 'duenio', 'Dueño', 'h', 'OWNER')"
        )
        con.execute("INSERT INTO proveedores (id, nombre) VALUES (1, 'Prov A'), (2, 'Prov B')")
        con.execute("INSERT INTO clientes (id, nombre) VALUES (1, 'Ana')")
        con.execute(
            "INSERT INTO productos (id, codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos,"
            " stock_actual) VALUES (1, 'c1', 'P1', 100, 200, 10)"
        )
        # Sesión de caja abierta: los triggers de V1.9 exigen una sesión ABIERTA para los egresos de pago.
        con.execute(
            "INSERT INTO sesiones_caja (id, estado, origen, fecha_apertura, usuario_apertura_id, fondo_centavos)"
            " VALUES (1, 'ABIERTA', 'NORMAL', '2026-04-01 08:00:00', 1, 1000)"
        )
        con.execute(
            "INSERT INTO caja_movimientos (id, fecha, tipo, monto_centavos, usuario_id, sesion_caja_id)"
            " VALUES (1, '2026-04-01 08:00:00', 'APERTURA', 1000, 1, 1)"
        )
        # Cuenta corriente de clientes: una venta a cuenta con su cargo.
        con.execute(
            "INSERT INTO ventas (id, total_centavos, tipo_pago, usuario_id, sesion_caja_id, cliente_id)"
            " VALUES (1, 500, 'CUENTA_CORRIENTE', 1, 1, 1)"
        )
        con.execute(
            "INSERT INTO detalle_venta (venta_id, producto_id, cantidad, precio_unitario_centavos, subtotal_centavos,"
            " costo_unitario_centavos) VALUES (1, 1, 1, 500, 500, 100)"
        )
        con.execute(
            "INSERT INTO movimientos_cuenta (id, cliente_id, tipo, monto_centavos, venta_id)"
            " VALUES (1, 1, 'CARGO', 500, 1)"
        )
        # Compras: 1 contado; 2 y 3 a crédito del proveedor 1 (la 3 se anula); 4 a crédito del proveedor 2.
        con.execute(
            "INSERT INTO compras (id, proveedor_id, usuario_id, fecha, total_centavos, condicion_pago)"
            " VALUES (1, 1, 1, ?, 1000, 'CONTADO'), (2, 1, 1, ?, 2000, 'CREDITO'),"
            " (3, 1, 1, ?, 500, 'CREDITO'), (4, 2, 1, ?, 700, 'CREDITO')",
            (FECHA_COMPRA,) * 4,
        )
        con.execute(
            "INSERT INTO movimientos_proveedor (id, proveedor_id, tipo, monto_centavos, compra_id)"
            " VALUES (1, 1, 'CARGO_COMPRA', 2000, 2), (2, 1, 'CARGO_COMPRA', 500, 3)"
        )
        con.execute(
            "UPDATE compras SET estado = 'ANULADA', motivo_anulacion = 'ERROR_CARGA', anulada_por_usuario_id = 1,"
            " fecha_anulacion = '2026-04-01 11:00:00' WHERE id = 3"
        )
        con.execute(
            "INSERT INTO movimientos_proveedor (id, proveedor_id, tipo, monto_centavos, compra_id)"
            " VALUES (3, 1, 'REVERSA_COMPRA', 500, 3)"
        )
        con.execute(
            "INSERT INTO caja_movimientos (id, fecha, tipo, monto_centavos, descripcion, usuario_id, sesion_caja_id,"
            " origen) VALUES (2, '2026-04-01 12:00:00', 'EGRESO', 300, 'Pago a proveedor: Prov A', 1, 1,"
            " 'PAGO_PROVEEDOR')"
        )
        con.execute(
            "INSERT INTO movimientos_proveedor (id, proveedor_id, tipo, monto_centavos, caja_movimiento_id, medio_pago)"
            " VALUES (4, 1, 'PAGO', 300, 2, 'EFECTIVO')"
        )
        con.execute(
            "INSERT INTO movimientos_proveedor (id, proveedor_id, tipo, monto_centavos, medio_pago)"
            " VALUES (5, 1, 'PAGO', 200, 'TRANSFERENCIA')"
        )
        con.execute(
            "INSERT INTO movimientos_proveedor (id, proveedor_id, tipo, monto_centavos, compra_id)"
            " VALUES (6, 2, 'CARGO_COMPRA', 700, 4)"
        )
        con.commit()

    def instantanea(self) -> dict:
        con = self.con
        return {
            "compras": con.execute(f"SELECT {COLUMNAS_COMPRAS_PREVIAS} FROM compras ORDER BY id").fetchall(),
            "movimientos_proveedor": con.execute("SELECT * FROM movimientos_proveedor ORDER BY id").fetchall(),
            "saldos": con.execute(SALDO_SQL).fetchall(),
            "caja": con.execute("SELECT * FROM caja_movimientos ORDER BY id").fetchall(),
            "sesiones": con.execute("SELECT * FROM sesiones_caja ORDER BY id").fetchall(),
            "cuenta": con.execute("SELECT * FROM movimientos_cuenta ORDER BY id").fetchall(),
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

    def migrar_con_migracion_alterada(self, tmp_path, reemplazo: str) -> None:
        """Aplica la 026 con una mutación inyectada justo antes de su verificación (paso 3): prueba que
        la verificación de la propia migración detecta el daño."""
        directorio = tmp_path / "migraciones_alteradas"
        shutil.copytree(modulo_conexion.DIRECTORIO_MIGRACIONES, directorio)
        ruta = directorio / NOMBRE_026
        original = ruta.read_text(encoding="utf-8")
        marcador = "-- 3. Verificación"
        assert marcador in original
        ruta.write_text(original.replace(marcador, reemplazo + "\n" + marcador, 1), encoding="utf-8")
        self.monkeypatch.setattr(modulo_conexion, "DIRECTORIO_MIGRACIONES", directorio)
        self.migrar()


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


# --- Upgrade V1.9 -> V1.10 --------------------------------------------------------------


def test_upgrade_aplica_la_026_y_pasa_de_25_a_26_migraciones(previa):
    previa.cargar_historia()
    antes = previa.instantanea()["migraciones"]
    assert len(antes) == 25 and NOMBRE_026 not in antes

    previa.migrar()

    despues = previa.instantanea()["migraciones"]
    todas = sorted(p.name for p in modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql"))
    assert despues == todas and len(despues) >= 26
    assert despues[:25] == antes and despues[25] == NOMBRE_026


def test_todas_las_compras_existentes_quedan_sin_vencimiento_incluso_las_de_credito(previa):
    previa.cargar_historia()
    previa.migrar()

    filas = previa.con.execute("SELECT id, condicion_pago, fecha_vencimiento FROM compras ORDER BY id").fetchall()
    assert [(f[0], f[1]) for f in filas] == [(1, "CONTADO"), (2, "CREDITO"), (3, "CREDITO"), (4, "CREDITO")]
    assert all(f[2] is None for f in filas)


def test_compras_conservan_ids_y_contenido_fila_por_fila(previa):
    previa.cargar_historia()
    antes = previa.instantanea()
    previa.migrar()

    assert previa.instantanea()["compras"] == antes["compras"]
    assert [f[0] for f in previa.con.execute("SELECT id FROM compras ORDER BY id")] == [1, 2, 3, 4]


def test_libro_de_proveedor_y_saldos_quedan_identicos(previa):
    previa.cargar_historia()
    antes = previa.instantanea()
    assert antes["saldos"] == [(1, 1500), (2, 700)]

    previa.migrar()

    despues = previa.instantanea()
    assert despues["movimientos_proveedor"] == antes["movimientos_proveedor"]
    assert despues["saldos"] == antes["saldos"] == [(1, 1500), (2, 700)]


def test_caja_y_cuenta_corriente_de_clientes_quedan_intactas(previa):
    previa.cargar_historia()
    antes = previa.instantanea()
    previa.migrar()

    despues = previa.instantanea()
    assert despues["caja"] == antes["caja"] and len(despues["caja"]) == 2
    assert despues["sesiones"] == antes["sesiones"]
    assert despues["cuenta"] == antes["cuenta"] and len(despues["cuenta"]) == 1


def test_sqlite_sequence_se_conserva(previa):
    previa.cargar_historia()
    antes = previa.instantanea()["secuencias"]
    previa.migrar()

    assert previa.instantanea()["secuencias"] == antes


def test_integridad_y_claves_foraneas_limpias_tras_migrar(migrada):
    assert migrada.con.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert migrada.con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_base_vacia_tambien_migra(previa):
    previa.migrar()
    assert previa.con.execute("SELECT COUNT(*) FROM compras").fetchone()[0] == 0
    assert previa.con.execute("SELECT COUNT(*) FROM movimientos_proveedor").fetchone()[0] == 0


def test_la_migracion_queda_registrada_una_sola_vez(previa):
    previa.cargar_historia()
    previa.migrar()
    modulo_conexion.inicializar_base_datos()

    filas = previa.con.execute("SELECT COUNT(*) FROM schema_migraciones WHERE nombre_archivo = ?", (NOMBRE_026,))
    assert filas.fetchone()[0] == 1


# --- Forma de la columna: sin reconstruir, sin índice, sin objetos huérfanos -------------


def test_la_columna_es_text_null_sin_default_y_va_al_final(migrada):
    columnas = migrada.con.execute("PRAGMA table_info(compras)").fetchall()
    ultima = columnas[-1]
    assert ultima[1] == "fecha_vencimiento"
    assert (ultima[2], ultima[3], ultima[4], ultima[5]) == ("TEXT", 0, None, 0)
    assert [c[1] for c in columnas][:-1] == [c.strip() for c in COLUMNAS_COMPRAS_PREVIAS.split(",")]


def test_no_se_agrega_ningun_indice_sobre_fecha_vencimiento(migrada):
    indices = migrada.con.execute("SELECT name, sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")
    assert [n for n, sql in indices if "fecha_vencimiento" in sql] == []
    assert migrada.con.execute("PRAGMA index_list(compras)").fetchall() is not None
    for indice in migrada.con.execute("PRAGMA index_list(compras)").fetchall():
        columnas = [c[2] for c in migrada.con.execute(f"PRAGMA index_info('{indice[1]}')")]
        assert "fecha_vencimiento" not in columnas


def test_no_queda_ninguna_tabla_ni_objeto_temporal_huerfano(migrada):
    nombres = {f[0] for f in migrada.con.execute("SELECT name FROM sqlite_master")}
    assert not {n for n in nombres if n.startswith("_") or n.endswith("_nueva")}
    assert migrada.con.execute("SELECT name FROM sqlite_temp_master").fetchall() == []


def test_el_resto_del_esquema_no_cambia(previa):
    previa.cargar_historia()
    consulta = "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name <> 'compras' ORDER BY name"
    antes = previa.con.execute(consulta).fetchall()
    previa.migrar()

    assert previa.con.execute(consulta).fetchall() == antes


# --- CHECK: valores válidos ----------------------------------------------------------------


def _insertar_compra(con, condicion, vencimiento, fecha=FECHA_COMPRA):
    con.execute(
        "INSERT INTO compras (proveedor_id, usuario_id, fecha, total_centavos, condicion_pago, fecha_vencimiento)"
        " VALUES (1, 1, ?, 100, ?, ?)",
        (fecha, condicion, vencimiento),
    )


@pytest.mark.parametrize(
    "condicion, vencimiento, fecha",
    [
        ("CONTADO", None, FECHA_COMPRA),
        ("CREDITO", None, FECHA_COMPRA),
        ("CREDITO", "2026-04-01", FECHA_COMPRA),  # el mismo día de la compra
        ("CREDITO", "2026-04-01", "2026-04-01 23:59:59"),  # mismo día aunque la compra sea a último momento
        ("CREDITO", "2026-04-02", FECHA_COMPRA),
        ("CREDITO", "2026-05-01", FECHA_COMPRA),
        ("CREDITO", "2028-02-29", FECHA_COMPRA),  # año bisiesto real
        ("CREDITO", "2030-01-01", FECHA_COMPRA),  # muy superior a 365 días: no hay tope superior
        ("CREDITO", "2099-12-31", FECHA_COMPRA),
    ],
)
def test_check_acepta_valores_validos(migrada, condicion, vencimiento, fecha):
    _insertar_compra(migrada.con, condicion, vencimiento, fecha)

    guardada = migrada.con.execute("SELECT fecha_vencimiento FROM compras ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert guardada == vencimiento


def test_un_vencimiento_superior_a_365_dias_es_valido_y_no_se_altera(migrada):
    _insertar_compra(migrada.con, "CREDITO", "2031-04-01")  # cinco años después

    assert migrada.con.execute("SELECT fecha_vencimiento FROM compras ORDER BY id DESC LIMIT 1").fetchone()[0] == (
        "2031-04-01"
    )


# --- CHECK: valores inválidos --------------------------------------------------------------


def test_check_rechaza_vencimiento_en_compra_contado(migrada):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        _insertar_compra(migrada.con, "CONTADO", "2026-05-01")


@pytest.mark.parametrize(
    "vencimiento",
    [
        "2026/05/01", "01-05-2026", "2026-5-1", "26-05-01", "2026-05-01 ", " 2026-05-01", "2026-05-01T00:00:00",
        "2026-05-01 10:00:00", "20260501", "", "abc", "2026-05", "2026-05-011", "٢٠٢٦-٠٥-٠١",
    ],
)
def test_check_rechaza_formatos_incorrectos(migrada, vencimiento):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        _insertar_compra(migrada.con, "CREDITO", vencimiento)


@pytest.mark.parametrize(
    "vencimiento",
    [
        "2099-02-30", "2099-02-29", "2099-02-31", "2099-04-31", "2099-06-31",
        "2099-13-01", "2099-00-10", "2099-12-00", "2099-12-32", "2099-01-99", "0000-00-00",
    ],
)
def test_check_rechaza_fechas_inexistentes(migrada, vencimiento):
    """Incluye las que `date()` no normaliza sino que devuelve NULL (mes 13, día 32...): un CHECK que da NULL
    no se viola, así que estos casos solo se rechazan gracias a comparar con `IS`."""
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        _insertar_compra(migrada.con, "CREDITO", vencimiento)


@pytest.mark.parametrize("vencimiento", ["2026-03-31", "2026-01-01", "2025-12-31"])
def test_check_rechaza_vencimiento_anterior_a_la_compra(migrada, vencimiento):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        _insertar_compra(migrada.con, "CREDITO", vencimiento)


@pytest.mark.parametrize("vencimiento", [20260501, b"2026-05-01", 2026.0501])
def test_check_rechaza_valores_que_no_son_texto(migrada, vencimiento):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        _insertar_compra(migrada.con, "CREDITO", vencimiento)


def test_check_tambien_rige_en_update(migrada):
    con = migrada.con
    con.execute("UPDATE compras SET fecha_vencimiento = '2026-06-30' WHERE id = 2")
    assert con.execute("SELECT fecha_vencimiento FROM compras WHERE id = 2").fetchone()[0] == "2026-06-30"
    con.execute("UPDATE compras SET fecha_vencimiento = NULL WHERE id = 2")

    for invalido in ("2099-02-30", "2099-13-01", "2026-03-31", "2026-6-30"):
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            con.execute("UPDATE compras SET fecha_vencimiento = ? WHERE id = 2", (invalido,))
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        con.execute("UPDATE compras SET fecha_vencimiento = '2026-06-30' WHERE id = 1")  # id 1 es CONTADO


def test_una_compra_con_vencimiento_no_puede_pasar_a_contado(migrada):
    con = migrada.con
    con.execute("UPDATE compras SET fecha_vencimiento = '2026-06-30' WHERE id = 2")

    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        con.execute("UPDATE compras SET condicion_pago = 'CONTADO' WHERE id = 2")


def test_el_vencimiento_no_toca_total_ni_libro_de_proveedor(migrada):
    con = migrada.con
    antes = migrada.instantanea()
    con.execute("UPDATE compras SET fecha_vencimiento = '2026-07-01' WHERE id IN (2, 4)")

    despues = migrada.instantanea()
    assert despues["movimientos_proveedor"] == antes["movimientos_proveedor"]
    assert despues["saldos"] == antes["saldos"]
    assert [f[5] for f in despues["compras"]] == [f[5] for f in antes["compras"]]


# --- Atomicidad y verificación propia de la migración -------------------------------------


def test_guardia_previa_aborta_si_la_columna_ya_existe_y_no_deja_cambios(previa):
    previa.cargar_historia()
    previa.con.execute("ALTER TABLE compras ADD COLUMN fecha_vencimiento TEXT NULL")
    previa.con.commit()
    antes = previa.instantanea()

    with pytest.raises(ErrorBaseDatos, match="verificación fallida"):
        previa.migrar()

    previa.reabrir()
    assert previa.instantanea() == antes
    assert NOMBRE_026 not in antes["migraciones"]


def test_guardia_previa_aborta_si_hay_violacion_de_clave_foranea_previa(previa):
    previa.cargar_historia()
    previa.con.execute("PRAGMA foreign_keys = OFF")
    previa.con.execute(
        "INSERT INTO detalle_compra (compra_id, producto_id, cantidad, costo_unitario_centavos, subtotal_centavos)"
        " VALUES (1, 999, 1, 1, 1)"
    )
    previa.con.commit()
    antes = previa.instantanea()

    with pytest.raises(ErrorBaseDatos, match="foreign_key_check previo"):
        previa.migrar()

    previa.reabrir()
    assert previa.instantanea() == antes
    assert "fecha_vencimiento" not in [f[1] for f in previa.con.execute("PRAGMA table_info(compras)")]


def test_verificacion_detecta_una_compra_con_vencimiento_inventado(previa, tmp_path):
    """Si alguna versión de la migración infiriera plazos (ej. +30 días), la propia migración aborta."""
    previa.cargar_historia()
    antes = previa.instantanea()

    with pytest.raises(ErrorBaseDatos, match="cero inferencias"):
        previa.migrar_con_migracion_alterada(
            tmp_path, "UPDATE compras SET fecha_vencimiento = date(fecha, '+30 days') WHERE condicion_pago = 'CREDITO';"
        )

    previa.reabrir()
    assert previa.instantanea() == antes
    assert "fecha_vencimiento" not in [f[1] for f in previa.con.execute("PRAGMA table_info(compras)")]


def test_verificacion_detecta_un_saldo_de_proveedor_alterado(previa, tmp_path):
    previa.cargar_historia()
    antes = previa.instantanea()

    with pytest.raises(ErrorBaseDatos, match="verificación fallida"):
        previa.migrar_con_migracion_alterada(
            tmp_path,
            "INSERT INTO movimientos_proveedor (proveedor_id, tipo, monto_centavos, medio_pago)"
            " VALUES (1, 'PAGO', 1, 'TRANSFERENCIA');",
        )

    previa.reabrir()
    assert previa.instantanea() == antes


def test_verificacion_detecta_una_caja_alterada(previa, tmp_path):
    previa.cargar_historia()
    antes = previa.instantanea()

    with pytest.raises(ErrorBaseDatos, match="caja_movimientos cambió"):
        previa.migrar_con_migracion_alterada(
            tmp_path, "UPDATE caja_movimientos SET descripcion = 'alterada' WHERE id = 1;"
        )

    previa.reabrir()
    assert previa.instantanea() == antes


def test_verificacion_detecta_un_cambio_de_esquema_fuera_de_compras(previa, tmp_path):
    previa.cargar_historia()
    antes = previa.instantanea()

    with pytest.raises(ErrorBaseDatos, match="el esquema fuera de compras cambió"):
        previa.migrar_con_migracion_alterada(tmp_path, "CREATE INDEX idx_extra_clientes ON clientes(nombre);")

    previa.reabrir()
    assert previa.instantanea() == antes
