"""Migración 025 (infraestructura de cuentas a pagar a proveedores, V1.9-A): reconstrucción de
`caja_movimientos` (origen `PAGO_PROVEEDOR`) y `movimientos_cuenta` (hijo RESTRICT que había que
sacar de en medio), `compras.condicion_pago` y la tabla nueva `movimientos_proveedor`.

Cada test arma una base con el esquema previo a la 025 (migraciones 001-024) y datos históricos
realistas (caja MANUAL, un COBRO_CUENTA, compras ACTIVA y ANULADA, proveedores), aplica la 025 con
el runner real (`inicializar_base_datos`) e inspecciona el resultado. Nunca toca `data/kiosco.db`.
"""

import sqlite3

import pytest

import db.conexion as modulo_conexion
from excepciones import ErrorBaseDatos

NOMBRE_025 = "025_cuentas_a_pagar_proveedores.sql"
PREFIJO_TRANSACCION = "BEGIN IMMEDIATE;\n"

COLUMNAS_CAJA_PREVIAS = (
    "id, fecha, tipo, monto_centavos, descripcion, usuario_id, diferencia_centavos, clave_idempotencia,"
    " sesion_caja_id, origen"
)
COLUMNAS_CUENTA_PREVIAS = (
    "id, fecha, cliente_id, tipo, monto_centavos, descripcion, venta_id, caja_movimiento_id, usuario_id,"
    " clave_idempotencia, contenido_hash"
)
COLUMNAS_COMPRAS_PREVIAS = (
    "id, proveedor_id, usuario_id, fecha, observaciones, total_centavos, estado, motivo_anulacion,"
    " observaciones_anulacion, anulada_por_usuario_id, fecha_anulacion, costo_trazable"
)
OBJETOS_CAJA_ORIGINALES = (
    "idx_caja_movimientos_clave_idempotencia",
    "idx_caja_movimientos_sesion",
    "idx_caja_movimientos_una_apertura_por_sesion",
    "trg_caja_movimientos_sesion_operable",
    "trg_caja_movimientos_sesion_inmutable",
    "trg_caja_movimientos_origen_inmutable",
    "trg_caja_movimientos_cobro_tipo_inmutable",
    "trg_caja_movimientos_cobro_solo_ingreso",
    "trg_caja_movimientos_cobro_monto_inmutable",
)
OBJETOS_CUENTA_ORIGINALES = (
    "idx_movimientos_cuenta_cliente",
    "idx_movimientos_cuenta_clave_idempotencia",
    "idx_movimientos_cuenta_un_cargo_por_venta",
    "idx_movimientos_cuenta_un_cobro_por_ingreso",
    "trg_movimientos_cuenta_cargo_valido",
    "trg_movimientos_cuenta_cobro_valido",
    "trg_movimientos_cuenta_inmutable",
    "trg_movimientos_cuenta_no_borrable",
)


class BaseAnterior:
    """Base con el esquema previo a la 025 (V1.8 completa: migraciones 001-024), con datos
    históricos representativos: caja MANUAL, un COBRO_CUENTA, compras ACTIVA y ANULADA."""

    def __init__(self, ruta, monkeypatch):
        self.ruta = ruta
        monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
        self.con = sqlite3.connect(ruta)
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.execute(modulo_conexion._TABLA_MIGRACIONES)
        for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
            if migracion.name < NOMBRE_025:
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

        # Sesión de caja histórica, ya cerrada.
        con.execute(
            "INSERT INTO sesiones_caja (id, estado, origen, fecha_apertura, usuario_apertura_id, fondo_centavos)"
            " VALUES (1, 'ABIERTA', 'NORMAL', '2026-03-01 08:00:00', 1, 1000)"
        )
        con.execute(
            "INSERT INTO caja_movimientos (id, fecha, tipo, monto_centavos, descripcion, usuario_id, sesion_caja_id)"
            " VALUES (1, '2026-03-01 08:00:00', 'APERTURA', 1000, NULL, 1, 1),"
            " (2, '2026-03-01 09:00:00', 'INGRESO', 300, 'aporte', 1, 1),"
            " (3, '2026-03-01 09:15:00', 'EGRESO', 50, 'retiro', 1, 1)"
        )
        # Venta a cuenta + su cargo, y un cobro respaldado por un COBRO_CUENTA (migración 020 ya aplicada).
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
        con.execute(
            "INSERT INTO caja_movimientos (id, fecha, tipo, monto_centavos, descripcion, usuario_id, sesion_caja_id,"
            " origen) VALUES (4, '2026-03-01 09:30:00', 'INGRESO', 200, 'Cobro cuenta', 1, 1, 'COBRO_CUENTA')"
        )
        con.execute(
            "INSERT INTO movimientos_cuenta (id, cliente_id, tipo, monto_centavos, caja_movimiento_id)"
            " VALUES (2, 1, 'COBRO', 200, 4)"
        )
        con.execute(
            "INSERT INTO caja_movimientos (id, fecha, tipo, monto_centavos, usuario_id, sesion_caja_id)"
            " VALUES (5, '2026-03-01 18:00:00', 'CIERRE', 1450, 1, 1)"
        )
        con.execute(
            "UPDATE sesiones_caja SET estado = 'CERRADA', fecha_cierre = '2026-03-01 18:00:00',"
            " usuario_cierre_id = 1, contado_centavos = 1450, diferencia_centavos = 0 WHERE id = 1"
        )

        # Compras históricas: una ACTIVA, una ANULADA -- ninguna con noción de crédito (no existe todavía).
        con.execute(
            "INSERT INTO compras (id, proveedor_id, usuario_id, fecha, total_centavos, estado)"
            " VALUES (1, 1, 1, '2026-03-01 10:00:00', 1200, 'ACTIVA'),"
            " (2, 2, 1, '2026-03-01 11:00:00', 800, 'ANULADA')"
        )
        con.execute(
            "UPDATE compras SET motivo_anulacion = 'OTRO', observaciones_anulacion = 'obs',"
            " anulada_por_usuario_id = 1, fecha_anulacion = '2026-03-01 12:00:00' WHERE id = 2"
        )
        con.execute(
            "INSERT INTO detalle_compra (compra_id, producto_id, cantidad, costo_unitario_centavos, subtotal_centavos)"
            " VALUES (1, 1, 12, 100, 1200), (2, 1, 8, 100, 800)"
        )
        con.commit()

    def instantanea(self) -> dict:
        con = self.con
        return {
            "caja": con.execute(f"SELECT {COLUMNAS_CAJA_PREVIAS} FROM caja_movimientos ORDER BY id").fetchall(),
            "cuenta": con.execute(f"SELECT {COLUMNAS_CUENTA_PREVIAS} FROM movimientos_cuenta ORDER BY id").fetchall(),
            "compras": con.execute(f"SELECT {COLUMNAS_COMPRAS_PREVIAS} FROM compras ORDER BY id").fetchall(),
            "secuencias": sorted(con.execute("SELECT name, seq FROM sqlite_sequence").fetchall()),
            "migraciones": [f[0] for f in con.execute("SELECT nombre_archivo FROM schema_migraciones ORDER BY 1")],
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


def _objeto(con, nombre):
    return con.execute("SELECT type, tbl_name, sql FROM sqlite_master WHERE name = ?", (nombre,)).fetchone()


def _estado_completo(previa) -> dict:
    return previa.instantanea()


# --- Reconstrucción de caja_movimientos y movimientos_cuenta ---------------------------


def test_caja_movimientos_se_conserva_fila_por_fila(previa):
    previa.cargar_historia()
    antes = previa.instantanea()
    previa.migrar()

    assert previa.instantanea()["caja"] == antes["caja"]
    origenes = {f[0] for f in previa.con.execute("SELECT origen FROM caja_movimientos")}
    assert origenes == {"MANUAL", "COBRO_CUENTA"}


def test_movimientos_cuenta_se_conserva_fila_por_fila(previa):
    previa.cargar_historia()
    antes = previa.instantanea()
    previa.migrar()

    assert previa.instantanea()["cuenta"] == antes["cuenta"]
    tipos = {f[0] for f in previa.con.execute("SELECT tipo FROM movimientos_cuenta")}
    assert tipos == {"CARGO", "COBRO"}


def test_sqlite_sequence_preservada_para_ambas_tablas(previa):
    previa.cargar_historia()
    antes = dict(previa.instantanea()["secuencias"])
    assert antes["caja_movimientos"] == 5
    assert antes["movimientos_cuenta"] == 2
    previa.migrar()

    despues = dict(previa.instantanea()["secuencias"])
    assert despues["caja_movimientos"] == 5
    assert despues["movimientos_cuenta"] == 2
    assert "caja_movimientos_nueva" not in despues


def test_la_siguiente_fila_usa_el_id_posterior_a_la_secuencia_preservada(previa):
    previa.cargar_historia()
    previa.migrar()
    con = previa.con
    con.execute(
        "INSERT INTO sesiones_caja (estado, origen, fecha_apertura, usuario_apertura_id, fondo_centavos)"
        " VALUES ('ABIERTA', 'NORMAL', '2026-03-02 08:00:00', 1, 0)"
    )
    sesion = con.execute("SELECT MAX(id) FROM sesiones_caja").fetchone()[0]
    cursor = con.execute(
        "INSERT INTO caja_movimientos (tipo, monto_centavos, sesion_caja_id) VALUES ('INGRESO', 1, ?)", (sesion,)
    )
    assert cursor.lastrowid == 6


def test_indices_y_triggers_originales_de_caja_movimientos_quedan_identicos(previa):
    previa.cargar_historia()
    antes = {nombre: _objeto(previa.con, nombre) for nombre in OBJETOS_CAJA_ORIGINALES}
    assert all(antes.values())
    previa.migrar()

    for nombre in OBJETOS_CAJA_ORIGINALES:
        assert _objeto(previa.con, nombre) == antes[nombre], nombre


def test_indices_y_triggers_originales_de_movimientos_cuenta_quedan_identicos(previa):
    previa.cargar_historia()
    antes = {nombre: _objeto(previa.con, nombre) for nombre in OBJETOS_CUENTA_ORIGINALES}
    assert all(antes.values())
    previa.migrar()

    for nombre in OBJETOS_CUENTA_ORIGINALES:
        assert _objeto(previa.con, nombre) == antes[nombre], nombre


def test_no_queda_ninguna_tabla_ni_objeto_temporal_huerfano(previa):
    previa.cargar_historia()
    previa.migrar()

    nombres = {f[0] for f in previa.con.execute("SELECT name FROM sqlite_master")}
    assert "caja_movimientos_nueva" not in nombres
    assert {"caja_movimientos", "movimientos_cuenta", "movimientos_proveedor", "compras"} <= nombres
    assert previa.con.execute("SELECT name FROM sqlite_temp_master").fetchall() == []


def test_integridad_y_claves_foraneas_limpias_tras_migrar(previa):
    previa.cargar_historia()
    previa.migrar()

    assert previa.con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert previa.con.execute("PRAGMA integrity_check").fetchall() == [("ok",)]


def test_movimientos_cuenta_conserva_restrict_hacia_caja_movimientos(previa):
    previa.cargar_historia()
    previa.migrar()

    referencia = previa.con.execute("PRAGMA foreign_key_list(movimientos_cuenta)").fetchall()
    coincidencias = [(f[2], f[3], f[6]) for f in referencia if f[2] == "caja_movimientos"]
    assert coincidencias == [("caja_movimientos", "caja_movimiento_id", "RESTRICT")]


def test_movimientos_cuenta_sigue_siendo_inmutable_y_no_borrable(previa):
    previa.cargar_historia()
    previa.migrar()
    con = previa.con

    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE movimientos_cuenta SET monto_centavos = 999 WHERE id = 1")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM movimientos_cuenta WHERE id = 1")


# --- compras.condicion_pago -------------------------------------------------------------


def test_compras_historicas_quedan_contado_sin_deuda_retroactiva(previa):
    previa.cargar_historia()
    previa.migrar()
    con = previa.con

    condiciones = {f[0] for f in con.execute("SELECT condicion_pago FROM compras")}
    assert condiciones == {"CONTADO"}
    assert con.execute("SELECT COUNT(*) FROM movimientos_proveedor").fetchone()[0] == 0


def test_condicion_pago_es_not_null_con_default_contado_y_check(previa):
    previa.cargar_historia()
    previa.migrar()
    con = previa.con

    columna = [f for f in con.execute("PRAGMA table_info(compras)") if f[1] == "condicion_pago"][0]
    assert columna[2] == "TEXT" and columna[3] == 1 and columna[4] == "'CONTADO'"
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE compras SET condicion_pago = 'FIADO' WHERE id = 1")
    con.execute("UPDATE compras SET condicion_pago = 'CREDITO' WHERE id = 1")


# --- caja_movimientos.origen = PAGO_PROVEEDOR --------------------------------------------


def test_origen_pago_proveedor_aceptado_solo_como_egreso(previa):
    previa.cargar_historia()
    previa.migrar()
    con = previa.con
    con.execute(
        "INSERT INTO sesiones_caja (estado, origen, fecha_apertura, usuario_apertura_id, fondo_centavos)"
        " VALUES ('ABIERTA', 'NORMAL', '2026-03-02 08:00:00', 1, 0)"
    )
    sesion = con.execute("SELECT MAX(id) FROM sesiones_caja").fetchone()[0]

    with pytest.raises(sqlite3.IntegrityError):  # M1: PAGO_PROVEEDOR como INGRESO
        con.execute(
            "INSERT INTO caja_movimientos (tipo, monto_centavos, sesion_caja_id, origen)"
            " VALUES ('INGRESO', 100, ?, 'PAGO_PROVEEDOR')",
            (sesion,),
        )
    assert con.execute("SELECT COUNT(*) FROM caja_movimientos WHERE origen = 'PAGO_PROVEEDOR'").fetchone()[0] == 0

    con.execute(
        "INSERT INTO caja_movimientos (tipo, monto_centavos, sesion_caja_id, origen)"
        " VALUES ('EGRESO', 100, ?, 'PAGO_PROVEEDOR')",
        (sesion,),
    )
    assert con.execute("SELECT COUNT(*) FROM caja_movimientos WHERE origen = 'PAGO_PROVEEDOR'").fetchone()[0] == 1


def test_origen_invalido_rechazado(previa):
    previa.cargar_historia()
    previa.migrar()
    con = previa.con
    con.execute(
        "INSERT INTO sesiones_caja (estado, origen, fecha_apertura, usuario_apertura_id, fondo_centavos)"
        " VALUES ('ABIERTA', 'NORMAL', '2026-03-02 08:00:00', 1, 0)"
    )
    sesion = con.execute("SELECT MAX(id) FROM sesiones_caja").fetchone()[0]

    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO caja_movimientos (tipo, monto_centavos, sesion_caja_id, origen)"
            " VALUES ('EGRESO', 100, ?, 'INVENTADO')",
            (sesion,),
        )


def test_pago_proveedor_tipo_y_monto_inmutables(previa):
    previa.cargar_historia()
    previa.migrar()
    con = previa.con
    con.execute(
        "INSERT INTO sesiones_caja (estado, origen, fecha_apertura, usuario_apertura_id, fondo_centavos)"
        " VALUES ('ABIERTA', 'NORMAL', '2026-03-02 08:00:00', 1, 0)"
    )
    sesion = con.execute("SELECT MAX(id) FROM sesiones_caja").fetchone()[0]
    cursor = con.execute(
        "INSERT INTO caja_movimientos (tipo, monto_centavos, sesion_caja_id, origen)"
        " VALUES ('EGRESO', 100, ?, 'PAGO_PROVEEDOR')",
        (sesion,),
    )
    id_mov = cursor.lastrowid

    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE caja_movimientos SET tipo = 'INGRESO' WHERE id = ?", (id_mov,))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE caja_movimientos SET monto_centavos = 1 WHERE id = ?", (id_mov,))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE caja_movimientos SET origen = 'MANUAL' WHERE id = ?", (id_mov,))


# --- Atomicidad y guardias previas --------------------------------------------------------


def test_falla_tardia_revierte_toda_la_migracion(previa):
    previa.cargar_historia()
    # Un índice con el mismo nombre que crea la 025 al final: rompe DESPUÉS de reconstruir todo.
    previa.con.execute("CREATE INDEX idx_movimientos_proveedor_proveedor ON productos(nombre)")
    previa.con.commit()
    antes = _estado_completo(previa)

    with pytest.raises(ErrorBaseDatos):
        previa.migrar()

    previa.con = sqlite3.connect(previa.ruta)
    assert _estado_completo(previa) == antes
    assert NOMBRE_025 not in antes["migraciones"]
    nombres = {f[0] for f in previa.con.execute("SELECT name FROM sqlite_master")}
    assert "caja_movimientos_nueva" not in nombres
    assert "movimientos_proveedor" not in nombres
    assert "condicion_pago" not in [f[1] for f in previa.con.execute("PRAGMA table_info(compras)")]


def test_guardia_previa_aborta_si_ya_existe_una_tabla_de_la_migracion(previa):
    previa.cargar_historia()
    previa.con.execute("CREATE TABLE movimientos_proveedor (id INTEGER PRIMARY KEY)")
    previa.con.commit()
    antes = _estado_completo(previa)

    with pytest.raises(ErrorBaseDatos, match="ya existe alguna tabla"):
        previa.migrar()

    previa.con = sqlite3.connect(previa.ruta)
    assert _estado_completo(previa) == antes


def test_guardia_previa_aborta_si_hay_violacion_de_clave_foranea_previa(previa):
    previa.cargar_historia()
    previa.con.execute("PRAGMA foreign_keys = OFF")
    # `detalle_compra` no tiene ningún trigger de validez propio (a diferencia de `movimientos_cuenta`),
    # así que esto deja una FK realmente colgante sin que nada la rechace antes del guardia previo.
    previa.con.execute(
        "INSERT INTO detalle_compra (compra_id, producto_id, cantidad, costo_unitario_centavos, subtotal_centavos)"
        " VALUES (1, 999, 1, 1, 1)"
    )
    previa.con.commit()
    antes = _estado_completo(previa)

    with pytest.raises(ErrorBaseDatos, match="foreign_key_check previo"):
        previa.migrar()

    previa.con = sqlite3.connect(previa.ruta)
    assert _estado_completo(previa) == antes


def test_guardia_previa_aborta_si_otro_objeto_referencia_caja_movimientos(previa):
    previa.cargar_historia()
    previa.con.execute("CREATE VIEW vista_caja_x AS SELECT id FROM caja_movimientos")
    previa.con.commit()
    antes = _estado_completo(previa)

    with pytest.raises(ErrorBaseDatos, match="vista inesperada que referencia"):
        previa.migrar()

    previa.con = sqlite3.connect(previa.ruta)
    assert _estado_completo(previa) == antes


def test_la_migracion_queda_registrada_una_sola_vez(previa):
    previa.cargar_historia()
    previa.migrar()
    modulo_conexion.inicializar_base_datos()

    filas = previa.con.execute("SELECT COUNT(*) FROM schema_migraciones WHERE nombre_archivo = ?", (NOMBRE_025,))
    assert filas.fetchone()[0] == 1


def test_verificacion_final_de_triggers_de_la_025(previa):
    previa.cargar_historia()
    previa.migrar()

    esperados = {
        "trg_caja_movimientos_pago_proveedor_solo_egreso",
        "trg_caja_movimientos_pago_proveedor_tipo_inmutable",
        "trg_caja_movimientos_pago_proveedor_monto_inmutable",
        "trg_movimientos_proveedor_cargo_valido",
        "trg_movimientos_proveedor_reversa_valida",
        "trg_movimientos_proveedor_pago_efectivo_valido",
        "trg_movimientos_proveedor_inmutable",
        "trg_movimientos_proveedor_no_borrable",
    }
    presentes = {f[0] for f in previa.con.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    assert esperados <= presentes
