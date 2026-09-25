"""Migración 020 (clientes y cuenta corriente): reconstrucción de `ventas` (estrategia A),
atomicidad, secuencias, índices/triggers y `caja_movimientos.origen`.

Cada test arma una base con el esquema previo a la 020 (migraciones 001-019) y datos
históricos, aplica la 020 con el runner real (`inicializar_base_datos`) e inspecciona el
resultado. Nunca toca `data/kiosco.db`.
"""

import sqlite3

import pytest

import db.conexion as modulo_conexion
from excepciones import ErrorBaseDatos

NOMBRE_020 = "020_clientes_cuenta_corriente.sql"
# Igual que el runner (`db.conexion.inicializar_base_datos`): el script corre dentro de un BEGIN IMMEDIATE.
PREFIJO_TRANSACCION = "BEGIN IMMEDIATE;\n"

COLUMNAS_VENTAS_PREVIAS = (
    "id, fecha, total_centavos, tipo_pago, clave_idempotencia, contenido_hash, usuario_id, estado, "
    "motivo_anulacion, observaciones_anulacion, anulada_por_usuario_id, fecha_anulacion, sesion_caja_id"
)
COLUMNAS_DETALLE = (
    "id, venta_id, producto_id, cantidad, precio_unitario_centavos, subtotal_centavos, costo_unitario_centavos"
)
COLUMNAS_MOVIMIENTOS_PREVIAS = (
    "id, fecha, tipo, monto_centavos, descripcion, usuario_id, diferencia_centavos, clave_idempotencia, sesion_caja_id"
)
OBJETOS_VENTAS_ORIGINALES = (
    "idx_ventas_clave_idempotencia",
    "idx_ventas_sesion",
    "trg_ventas_sesion_operable",
    "trg_ventas_sesion_inmutable",
)


class BaseAnterior:
    """Base con el esquema previo a la 020, con datos históricos con ids no consecutivos."""

    def __init__(self, ruta, monkeypatch):
        self.ruta = ruta
        monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
        self.con = sqlite3.connect(ruta)
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.execute(modulo_conexion._TABLA_MIGRACIONES)
        for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
            if migracion.name < NOMBRE_020:
                self.con.executescript(migracion.read_text(encoding="utf-8"))
                self.con.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (migracion.name,))
        self.con.commit()

    def cargar_historia(self, con_ventas: bool = True) -> None:
        con = self.con
        con.execute(
            "INSERT INTO usuarios (id, nombre_usuario, nombre_completo, password_hash, rol)"
            " VALUES (1, 'cajera', 'Cajera', 'h', 'CASHIER')"
        )
        con.execute(
            "INSERT INTO productos (id, codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos,"
            " stock_actual) VALUES (1, '1', 'P1', 10, 100, 50), (2, '2', 'P2', 20, 250, 50)"
        )
        con.execute(
            "INSERT INTO sesiones_caja (id, estado, origen, fecha_apertura, usuario_apertura_id, fondo_centavos)"
            " VALUES (1, 'ABIERTA', 'NORMAL', '2026-03-01 08:00:00', 1, 1000)"
        )
        con.execute(
            "INSERT INTO caja_movimientos (id, fecha, tipo, monto_centavos, descripcion, usuario_id, sesion_caja_id)"
            " VALUES (1, '2026-03-01 08:00:00', 'APERTURA', 1000, NULL, 1, 1),"
            " (2, '2026-03-01 09:30:00', 'INGRESO', 300, 'aporte', 1, 1),"
            " (3, '2026-03-01 09:40:00', 'EGRESO', 50, 'retiro', 1, 1)"
        )
        if con_ventas:
            for venta_id, fecha, total, tipo, estado, clave in (
                (1, "2026-03-01 09:00:00", 100, "EFECTIVO", "ACTIVA", "clave-1"),
                (2, "2026-03-01 09:10:00", 500, "TARJETA", "ACTIVA", None),
                (5, "2026-03-01 09:20:00", 250, "TRANSFERENCIA", "ANULADA", "clave-5"),
                (9, "2026-03-01 09:50:00", 100, "OTRO", "ACTIVA", None),
            ):
                con.execute(
                    "INSERT INTO ventas (id, fecha, total_centavos, tipo_pago, clave_idempotencia, contenido_hash,"
                    " usuario_id, estado, motivo_anulacion, observaciones_anulacion, anulada_por_usuario_id,"
                    " fecha_anulacion, sesion_caja_id) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, 1)",
                    (
                        venta_id, fecha, total, tipo, clave, "hash" if clave else None, estado,
                        "OTRO" if estado == "ANULADA" else None,
                        "obs" if estado == "ANULADA" else None,
                        1 if estado == "ANULADA" else None,
                        "2026-03-01 09:25:00" if estado == "ANULADA" else None,
                    ),
                )
            for detalle_id, venta_id, producto_id, cantidad, precio, costo in (
                (1, 1, 1, 1, 100, 10),
                (2, 2, 2, 2, 250, 20),
                (4, 5, 2, 1, 250, None),
                (7, 9, 1, 1, 100, 10),
                (8, 9, 1, 0 + 3, 0, 10),
            ):
                con.execute(
                    "INSERT INTO detalle_venta (id, venta_id, producto_id, cantidad, precio_unitario_centavos,"
                    " subtotal_centavos, costo_unitario_centavos) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (detalle_id, venta_id, producto_id, cantidad, precio, precio * cantidad, costo),
                )
            # Ids "quemados": las secuencias quedan por encima del máximo id existente.
            con.execute(
                "INSERT INTO ventas (id, total_centavos, tipo_pago, sesion_caja_id) VALUES (20, 1, 'OTRO', 1)"
            )
            con.execute(
                "INSERT INTO detalle_venta (id, venta_id, producto_id, cantidad, precio_unitario_centavos,"
                " subtotal_centavos) VALUES (30, 20, 1, 1, 1, 1)"
            )
            con.execute("DELETE FROM ventas WHERE id = 20")  # el CASCADE borra el detalle 30
        con.execute(
            "UPDATE sesiones_caja SET estado = 'CERRADA', fecha_cierre = '2026-03-01 18:00:00',"
            " usuario_cierre_id = 1, contado_centavos = 1000, diferencia_centavos = 0 WHERE id = 1"
        )
        con.commit()

    def instantanea(self) -> dict:
        con = self.con
        return {
            "ventas": con.execute(f"SELECT {COLUMNAS_VENTAS_PREVIAS} FROM ventas ORDER BY id").fetchall(),
            "detalle": con.execute(f"SELECT {COLUMNAS_DETALLE} FROM detalle_venta ORDER BY id").fetchall(),
            "movimientos": con.execute(
                f"SELECT {COLUMNAS_MOVIMIENTOS_PREVIAS} FROM caja_movimientos ORDER BY id"
            ).fetchall(),
            "secuencias": sorted(con.execute("SELECT name, seq FROM sqlite_sequence").fetchall()),
            "esquema": sorted(con.execute("SELECT type, name, sql FROM sqlite_master").fetchall()),
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
    fila = con.execute("SELECT type, tbl_name, sql FROM sqlite_master WHERE name = ?", (nombre,)).fetchone()
    return fila


# --- Reconstrucción de ventas -------------------------------------------------


def test_ventas_se_conservan_fila_por_fila_y_sin_cliente(previa):
    previa.cargar_historia()
    antes = previa.instantanea()
    previa.migrar()

    despues = previa.instantanea()
    assert despues["ventas"] == antes["ventas"]
    assert [f[0] for f in despues["ventas"]] == [1, 2, 5, 9]  # ids preservados, con huecos
    assert previa.con.execute("SELECT COUNT(*) FROM ventas WHERE cliente_id IS NOT NULL").fetchone()[0] == 0
    columnas = [f[1] for f in previa.con.execute("PRAGMA table_info(ventas)")]
    assert columnas == COLUMNAS_VENTAS_PREVIAS.split(", ") + ["cliente_id"]


def test_detalle_venta_se_restaura_fila_por_fila(previa):
    previa.cargar_historia()
    antes = previa.instantanea()
    previa.migrar()

    assert previa.instantanea()["detalle"] == antes["detalle"]
    referencia = previa.con.execute("PRAGMA foreign_key_list(detalle_venta)").fetchall()
    assert [(f[2], f[3], f[6]) for f in referencia if f[2] == "ventas"] == [("ventas", "venta_id", "CASCADE")]


def test_sqlite_sequence_se_preserva_por_encima_del_maximo_id(previa):
    previa.cargar_historia()
    antes = dict(previa.instantanea()["secuencias"])
    assert antes["ventas"] == 20 and antes["detalle_venta"] == 30  # por encima del máximo id existente
    previa.migrar()

    despues = dict(previa.instantanea()["secuencias"])
    assert despues["ventas"] == 20
    assert despues["detalle_venta"] == 30
    assert despues["caja_movimientos"] == antes["caja_movimientos"]
    assert "ventas_nueva" not in despues


def test_la_siguiente_venta_usa_el_id_posterior_a_la_secuencia_preservada(previa):
    previa.cargar_historia()
    previa.migrar()
    previa.con.execute(
        "UPDATE sesiones_caja SET estado = estado WHERE 0"  # no-op: la sesión 1 está cerrada
    )
    previa.con.execute(
        "INSERT INTO sesiones_caja (estado, origen, fecha_apertura, usuario_apertura_id, fondo_centavos)"
        " VALUES ('ABIERTA', 'NORMAL', '2026-03-02 08:00:00', 1, 0)"
    )
    sesion = previa.con.execute("SELECT MAX(id) FROM sesiones_caja").fetchone()[0]
    cursor = previa.con.execute(
        "INSERT INTO ventas (total_centavos, tipo_pago, sesion_caja_id) VALUES (10, 'EFECTIVO', ?)", (sesion,)
    )
    assert cursor.lastrowid == 21


def test_base_sin_ventas_ni_secuencias_queda_sin_filas_de_secuencia_espurias(previa):
    previa.cargar_historia(con_ventas=False)
    antes = dict(previa.instantanea()["secuencias"])
    assert "ventas" not in antes and "detalle_venta" not in antes
    previa.migrar()

    despues = dict(previa.instantanea()["secuencias"])
    assert "ventas" not in despues and "detalle_venta" not in despues
    assert "ventas_nueva" not in despues


def test_indices_y_triggers_originales_de_ventas_quedan_identicos(previa):
    previa.cargar_historia()
    antes = {nombre: _objeto(previa.con, nombre) for nombre in OBJETOS_VENTAS_ORIGINALES}
    assert all(antes.values())
    previa.migrar()

    for nombre in OBJETOS_VENTAS_ORIGINALES:
        assert _objeto(previa.con, nombre) == antes[nombre], nombre


def test_ventas_no_deja_tabla_temporal_ni_objetos_huerfanos(previa):
    previa.cargar_historia()
    previa.migrar()

    nombres = {f[0] for f in previa.con.execute("SELECT name FROM sqlite_master")}
    assert "ventas_nueva" not in nombres
    assert {"clientes", "movimientos_cuenta", "ventas"} <= nombres


def test_la_migracion_no_deja_objetos_temp_en_la_conexion_que_la_ejecuto(previa):
    """Los objetos TEMP son por conexión: solo se pueden inspeccionar desde la MISMA conexión que
    corrió el script (una conexión nueva los vería siempre vacíos, aunque hubieran quedado)."""
    previa.cargar_historia()
    previa.con.commit()
    script = (modulo_conexion.DIRECTORIO_MIGRACIONES / NOMBRE_020).read_text(encoding="utf-8")

    # Sanidad del método: en la misma conexión, a mitad de la migración, los objetos TEMP existen.
    previa.con.executescript(PREFIJO_TRANSACCION + script.split("-- 2. Tablas nuevas")[0])
    durante = {f[0] for f in previa.con.execute("SELECT name FROM sqlite_temp_master")}
    previa.con.rollback()
    assert {"_fallas", "_antes", "_ventas_copia", "_detalle_copia", "_abortar_por_falla"} <= durante

    # La migración completa, como la corre el runner, no debe dejar ninguno.
    previa.con.executescript(PREFIJO_TRANSACCION + script)
    previa.con.commit()
    assert previa.con.execute("SELECT name FROM sqlite_temp_master").fetchall() == []


def test_integridad_y_claves_foraneas_limpias_tras_migrar(previa):
    previa.cargar_historia()
    previa.migrar()

    assert previa.con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert previa.con.execute("PRAGMA integrity_check").fetchall() == [("ok",)]


def test_la_migracion_queda_registrada_una_sola_vez(previa):
    previa.cargar_historia()
    previa.migrar()
    modulo_conexion.inicializar_base_datos()

    filas = previa.con.execute("SELECT COUNT(*) FROM schema_migraciones WHERE nombre_archivo = ?", (NOMBRE_020,))
    assert filas.fetchone()[0] == 1


def test_ventas_acepta_cuenta_corriente_pero_exige_cliente(previa):
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
            "INSERT INTO ventas (total_centavos, tipo_pago, sesion_caja_id) VALUES (10, 'CUENTA_CORRIENTE', ?)",
            (sesion,),
        )
    con.execute("INSERT INTO clientes (nombre) VALUES ('Ana')")
    con.execute(
        "INSERT INTO ventas (total_centavos, tipo_pago, sesion_caja_id, cliente_id)"
        " VALUES (10, 'CUENTA_CORRIENTE', ?, 1)",
        (sesion,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO ventas (total_centavos, tipo_pago, sesion_caja_id) VALUES (10, 'FIADO', ?)", (sesion,))


# --- caja_movimientos.origen --------------------------------------------------


def test_movimientos_historicos_quedan_manual_sin_perder_datos(previa):
    previa.cargar_historia()
    antes = previa.instantanea()
    previa.migrar()

    assert previa.instantanea()["movimientos"] == antes["movimientos"]
    origenes = {f[0] for f in previa.con.execute("SELECT origen FROM caja_movimientos")}
    assert origenes == {"MANUAL"}
    assert previa.con.execute("SELECT COUNT(*) FROM caja_movimientos").fetchone()[0] == 3


def test_la_columna_origen_es_not_null_con_default_manual(previa):
    previa.cargar_historia()
    previa.migrar()

    columna = [f for f in previa.con.execute("PRAGMA table_info(caja_movimientos)") if f[1] == "origen"][0]
    assert columna[2] == "TEXT" and columna[3] == 1 and columna[4] == "'MANUAL'"


# --- Atomicidad y rollback ----------------------------------------------------


def _estado_completo(previa) -> dict:
    return previa.instantanea()


def test_falla_tardia_revierte_toda_la_migracion_incluida_la_reconstruccion(previa):
    previa.cargar_historia()
    # Un índice con el mismo nombre que crea la 020 al final: rompe DESPUÉS de reconstruir `ventas`.
    previa.con.execute("CREATE INDEX idx_movimientos_cuenta_cliente ON productos(nombre)")
    previa.con.commit()
    antes = _estado_completo(previa)

    with pytest.raises(ErrorBaseDatos):
        previa.migrar()

    previa.con = sqlite3.connect(previa.ruta)
    assert _estado_completo(previa) == antes
    assert NOMBRE_020 not in antes["migraciones"]
    assert "ventas_nueva" not in {f[0] for f in previa.con.execute("SELECT name FROM sqlite_master")}


def test_guardia_previa_aborta_si_ya_existe_una_tabla_de_la_migracion(previa):
    previa.cargar_historia()
    previa.con.execute("CREATE TABLE clientes (id INTEGER PRIMARY KEY)")
    previa.con.commit()
    antes = _estado_completo(previa)

    with pytest.raises(ErrorBaseDatos, match="ya existe alguna tabla"):
        previa.migrar()

    previa.con = sqlite3.connect(previa.ruta)
    assert _estado_completo(previa) == antes


def test_guardia_previa_aborta_si_hay_una_violacion_de_clave_foranea(previa):
    previa.cargar_historia()
    previa.con.execute("PRAGMA foreign_keys = OFF")
    previa.con.execute(
        "INSERT INTO detalle_venta (venta_id, producto_id, cantidad, precio_unitario_centavos, subtotal_centavos)"
        " VALUES (999, 1, 1, 1, 1)"
    )
    previa.con.commit()
    antes = _estado_completo(previa)

    with pytest.raises(ErrorBaseDatos, match="foreign_key_check previo"):
        previa.migrar()

    previa.con = sqlite3.connect(previa.ruta)
    assert _estado_completo(previa) == antes


def test_guardia_previa_aborta_si_otro_objeto_referencia_ventas(previa):
    previa.cargar_historia()
    previa.con.execute("CREATE VIEW vista_ventas_x AS SELECT id FROM ventas")
    previa.con.commit()
    antes = _estado_completo(previa)

    with pytest.raises(ErrorBaseDatos, match="objeto inesperado que referencia ventas"):
        previa.migrar()

    previa.con = sqlite3.connect(previa.ruta)
    assert _estado_completo(previa) == antes


def test_guardia_previa_aborta_si_falta_un_trigger_esperado_de_ventas(previa):
    previa.cargar_historia()
    previa.con.execute("DROP TRIGGER trg_ventas_sesion_inmutable")
    previa.con.commit()
    antes = _estado_completo(previa)

    with pytest.raises(ErrorBaseDatos, match="falta un índice o trigger esperado"):
        previa.migrar()

    previa.con = sqlite3.connect(previa.ruta)
    assert _estado_completo(previa) == antes


def test_migracion_desde_base_completamente_vacia_aplica_todo(tmp_path, monkeypatch):
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", tmp_path / "vacia.db")
    modulo_conexion.inicializar_base_datos()

    con = sqlite3.connect(tmp_path / "vacia.db")
    try:
        registradas = [f[0] for f in con.execute("SELECT nombre_archivo FROM schema_migraciones ORDER BY 1")]
        assert NOMBRE_020 in registradas  # las migraciones posteriores (021+) también se aplican
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        # 7 triggers de la 019 + 13 nuevos de la 020 (los 2 de ventas de la 019 se recrean, no se suman).
        # (sin contar los de migraciones posteriores: trg_productos_* y trg_inventario*, de la 022)
        triggers_019_020 = con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name NOT LIKE 'trg_productos_%'"
            " AND name NOT LIKE 'trg_inventario%' AND name NOT LIKE 'trg_ajustes_stock_inventario%'"
        ).fetchall()
        assert len(triggers_019_020) == 20
    finally:
        con.close()


def test_verificacion_final_de_triggers_de_la_020(previa):
    previa.cargar_historia()
    previa.migrar()

    esperados = {
        "trg_ventas_sesion_operable", "trg_ventas_sesion_inmutable", "trg_ventas_cliente_activo",
        "trg_ventas_cliente_inmutable", "trg_ventas_cuenta_inmutable", "trg_ventas_cuenta_no_anulable",
        "trg_caja_movimientos_origen_inmutable", "trg_caja_movimientos_cobro_tipo_inmutable",
        "trg_caja_movimientos_cobro_solo_ingreso", "trg_caja_movimientos_cobro_monto_inmutable",
        "trg_clientes_no_desactivar_con_saldo", "trg_movimientos_cuenta_cargo_valido",
        "trg_movimientos_cuenta_cobro_valido", "trg_movimientos_cuenta_inmutable",
        "trg_movimientos_cuenta_no_borrable",
    }
    presentes = {f[0] for f in previa.con.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    assert esperados <= presentes


# --- Constraints de la `ventas` reconstruida a mano ---------------------------------------


def _fks_de_ventas(con) -> set[tuple]:
    """(tabla padre, columna hija, columna padre, on_update, on_delete) de cada FK de `ventas`."""
    return {(f[2], f[3], f[4], f[5], f[6]) for f in con.execute("PRAGMA foreign_key_list(ventas)")}


def _abrir_sesion(con) -> int:
    cursor = con.execute(
        "INSERT INTO sesiones_caja (estado, origen, fecha_apertura, usuario_apertura_id, fondo_centavos)"
        " VALUES ('ABIERTA', 'NORMAL', '2026-03-02 08:00:00', 1, 0)"
    )
    return cursor.lastrowid


def test_table_info_de_ventas_conserva_las_columnas_previas_y_solo_agrega_cliente_id(previa):
    previa.cargar_historia()
    antes = previa.con.execute("PRAGMA table_info(ventas)").fetchall()
    previa.migrar()

    despues = previa.con.execute("PRAGMA table_info(ventas)").fetchall()
    # (nombre, tipo, notnull, default, pk): idénticos, en el mismo orden; `cid` es solo la posición.
    assert [f[1:] for f in despues[: len(antes)]] == [f[1:] for f in antes]
    assert [f[1:] for f in despues[len(antes):]] == [("cliente_id", "INTEGER", 0, None, 0)]


def test_foreign_key_list_de_ventas_conserva_las_previas_y_agrega_solo_cliente_id(previa):
    previa.cargar_historia()
    antes = _fks_de_ventas(previa.con)
    assert antes == {
        ("usuarios", "usuario_id", "id", "NO ACTION", "RESTRICT"),
        ("usuarios", "anulada_por_usuario_id", "id", "NO ACTION", "RESTRICT"),
        ("sesiones_caja", "sesion_caja_id", "id", "NO ACTION", "RESTRICT"),
    }
    previa.migrar()

    despues = _fks_de_ventas(previa.con)
    assert antes <= despues
    assert despues - antes == {("clientes", "cliente_id", "id", "NO ACTION", "RESTRICT")}


def _insertar_venta(con, sesion, **columnas):
    valores = {"total_centavos": 10, "tipo_pago": "EFECTIVO", "sesion_caja_id": sesion, **columnas}
    con.execute(
        f"INSERT INTO ventas ({', '.join(valores)}) VALUES ({', '.join('?' * len(valores))})", tuple(valores.values())
    )


@pytest.fixture
def migrada_con_sesion(previa):
    previa.cargar_historia()
    previa.migrar()
    return previa.con, _abrir_sesion(previa.con)


def test_ventas_reconstruida_rechaza_total_negativo(migrada_con_sesion):
    con, sesion = migrada_con_sesion

    with pytest.raises(sqlite3.IntegrityError):
        _insertar_venta(con, sesion, total_centavos=-1)
    _insertar_venta(con, sesion, total_centavos=0)  # el borde válido sigue aceptado


def test_ventas_reconstruida_rechaza_estado_invalido(migrada_con_sesion):
    con, sesion = migrada_con_sesion

    with pytest.raises(sqlite3.IntegrityError):
        _insertar_venta(con, sesion, estado="PENDIENTE")
    _insertar_venta(con, sesion, estado="ANULADA")


def test_ventas_reconstruida_rechaza_usuario_inexistente(migrada_con_sesion):
    con, sesion = migrada_con_sesion

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        _insertar_venta(con, sesion, usuario_id=999)
    _insertar_venta(con, sesion, usuario_id=1)


def test_ventas_reconstruida_rechaza_anulada_por_inexistente(migrada_con_sesion):
    con, sesion = migrada_con_sesion

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        _insertar_venta(con, sesion, anulada_por_usuario_id=999)
    _insertar_venta(con, sesion, anulada_por_usuario_id=1)


def test_ventas_reconstruida_rechaza_clave_de_idempotencia_duplicada(migrada_con_sesion):
    con, sesion = migrada_con_sesion
    _insertar_venta(con, sesion, clave_idempotencia="nueva")

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        _insertar_venta(con, sesion, clave_idempotencia="nueva")
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        _insertar_venta(con, sesion, clave_idempotencia="clave-1")  # una clave histórica conservada
    _insertar_venta(con, sesion)  # varias ventas sin clave siguen sin chocar
    _insertar_venta(con, sesion)


def test_ventas_reconstruida_mantiene_restrict_de_usuario_a_ventas(migrada_con_sesion):
    con, _sesion = migrada_con_sesion  # el usuario 1 tiene ventas históricas

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        con.execute("DELETE FROM usuarios WHERE id = 1")

    assert con.execute("SELECT COUNT(*) FROM usuarios WHERE id = 1").fetchone()[0] == 1
