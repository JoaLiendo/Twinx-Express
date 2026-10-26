"""Migración 039 (V1.14-C): obligaciones causales e imputaciones FIFO reconstruidas desde la historia.

Upgrade desde una base V1.13 (001-038) con ventas a cuenta, cobros intercalados, clientes con y sin
deuda y ventas de contado: cada CARGO se vuelve su obligación (misma venta, fecha y monto), cada COBRO
queda imputado completo con FIFO y marcado `HEREDADA_FIFO`, y ningún saldo, venta, libro ni caja cambia.
Nunca toca `data/kiosco.db`.
"""

import sqlite3

import pytest

import db.conexion as modulo_conexion

NOMBRE_039 = "039_obligaciones_y_venta_mixta.sql"


@pytest.fixture
def previa(tmp_path, monkeypatch):
    ruta = tmp_path / "v113.db"
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
    con = sqlite3.connect(ruta)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(modulo_conexion._TABLA_MIGRACIONES)
    for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
        if migracion.name < NOMBRE_039:
            con.executescript(migracion.read_text(encoding="utf-8"))
            con.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (migracion.name,))
    con.execute("INSERT INTO usuarios (id, nombre_usuario, nombre_completo, password_hash, rol) VALUES (1, 'd', 'Dueño', 'h', 'OWNER')")
    con.execute("INSERT INTO clientes (id, nombre) VALUES (1, 'Ana'), (2, 'Beto'), (3, 'Caro'), (4, 'Sin deuda')")
    con.execute(
        "INSERT INTO sesiones_caja (id, estado, origen, fecha_apertura, usuario_apertura_id, fondo_centavos)"
        " VALUES (1, 'ABIERTA', 'NORMAL', '2026-01-01 08:00:00', 1, 0)"
    )
    ventas_id = iter(range(1, 100))
    caja_id = iter(range(1, 100))

    def vender(cliente_id, monto, tipo="CUENTA_CORRIENTE"):
        venta_id = next(ventas_id)
        con.execute(
            "INSERT INTO ventas (id, total_centavos, tipo_pago, usuario_id, sesion_caja_id, cliente_id) VALUES (?, ?, ?, 1, 1, ?)",
            (venta_id, monto, tipo, cliente_id),
        )
        if tipo == "CUENTA_CORRIENTE":
            con.execute(
                "INSERT INTO movimientos_cuenta (cliente_id, tipo, monto_centavos, venta_id, usuario_id) VALUES (?, 'CARGO', ?, ?, 1)",
                (cliente_id, monto, venta_id),
            )

    def cobrar(cliente_id, monto):
        movimiento_id = next(caja_id)
        con.execute(
            "INSERT INTO caja_movimientos (id, tipo, monto_centavos, usuario_id, sesion_caja_id, origen)"
            " VALUES (?, 'INGRESO', ?, 1, 1, 'COBRO_CUENTA')",
            (movimiento_id, monto),
        )
        con.execute(
            "INSERT INTO movimientos_cuenta (cliente_id, tipo, monto_centavos, caja_movimiento_id, usuario_id) VALUES (?, 'COBRO', ?, ?, 1)",
            (cliente_id, monto, movimiento_id),
        )

    # Ana: A=10.000, B=20.000, C=30.000 y un cobro de 25.000 (el ejemplo del enunciado).
    for monto in (1_000_000, 2_000_000, 3_000_000):
        vender(1, monto)
    cobrar(1, 2_500_000)
    # Beto: cargos y cobros intercalados; cada cobro solo puede pagar lo anterior a él.
    vender(2, 10_000)
    cobrar(2, 5_000)
    vender(2, 20_000)
    cobrar(2, 20_000)
    vender(2, 7_000)
    # Caro: pagó todo.
    vender(3, 4_000)
    cobrar(3, 4_000)
    # Ventas de contado, con y sin cliente.
    vender(4, 999, tipo="EFECTIVO")
    vender(None, 500, tipo="TARJETA")
    con.commit()
    yield type("Previa", (), {"ruta": ruta, "con": con})
    con.close()


def _migrar(previa):
    previa.con.close()
    modulo_conexion.inicializar_base_datos()
    previa.con = sqlite3.connect(previa.ruta)
    return previa.con


def _foto_historica(con):
    return {
        "ventas": con.execute("SELECT id, fecha, total_centavos, tipo_pago, cliente_id, estado FROM ventas ORDER BY id").fetchall(),
        "libro": con.execute("SELECT * FROM movimientos_cuenta ORDER BY id").fetchall(),
        "caja": con.execute("SELECT * FROM caja_movimientos ORDER BY id").fetchall(),
        "saldos": con.execute(
            "SELECT cliente_id, SUM(CASE tipo WHEN 'CARGO' THEN monto_centavos ELSE -monto_centavos END)"
            " FROM movimientos_cuenta GROUP BY cliente_id ORDER BY cliente_id"
        ).fetchall(),
    }


def test_historia_preservada_y_cada_cargo_es_su_obligacion(previa):
    antes = _foto_historica(previa.con)
    con = _migrar(previa)
    assert _foto_historica(con) == antes
    obligaciones = con.execute(
        "SELECT o.venta_id, o.monto_original_centavos, o.fecha_origen = m.fecha FROM obligaciones_cliente o"
        " JOIN movimientos_cuenta m ON m.id = o.movimiento_cargo_id ORDER BY o.id"
    ).fetchall()
    assert obligaciones == [(1, 1_000_000, 1), (2, 2_000_000, 1), (3, 3_000_000, 1), (4, 10_000, 1),
                            (5, 20_000, 1), (6, 7_000, 1), (7, 4_000, 1)]
    assert con.execute("SELECT COUNT(*) FROM ventas WHERE pago_contado_centavos <> 0").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM compradores_venta").fetchone()[0] == 0


def test_fifo_reconstruido_exacto_y_marcado_como_heredado(previa):
    con = _migrar(previa)
    imputaciones = con.execute(
        "SELECT i.movimiento_cobro_id, o.venta_id, i.monto_centavos, i.tipo FROM imputaciones_cobro i"
        " JOIN obligaciones_cliente o ON o.id = i.obligacion_id ORDER BY i.id"
    ).fetchall()
    cobro = {fila[0]: fila[1] for fila in con.execute("SELECT caja_movimiento_id, id FROM movimientos_cuenta WHERE tipo = 'COBRO'")}
    assert imputaciones == [
        (cobro[1], 1, 1_000_000, "HEREDADA_FIFO"),  # Ana: A completa
        (cobro[1], 2, 1_500_000, "HEREDADA_FIFO"),  #       B parcial (queda $5.000)
        (cobro[2], 4, 5_000, "HEREDADA_FIFO"),      # Beto: primer cobro solo pudo pagar lo anterior
        (cobro[3], 4, 5_000, "HEREDADA_FIFO"),
        (cobro[3], 5, 15_000, "HEREDADA_FIFO"),
        (cobro[4], 7, 4_000, "HEREDADA_FIFO"),      # Caro: saldada
    ]
    saldos = dict(con.execute("SELECT venta_id, saldo_pendiente_centavos FROM obligaciones_cliente").fetchall())
    assert saldos == {1: 0, 2: 500_000, 3: 3_000_000, 4: 0, 5: 5_000, 6: 7_000, 7: 0}


def test_saldo_igual_a_suma_de_obligaciones_e_integridad(previa):
    con = _migrar(previa)
    assert con.execute(
        "SELECT COUNT(*) FROM (SELECT cliente_id, SUM(CASE tipo WHEN 'CARGO' THEN monto_centavos ELSE -monto_centavos END) s"
        " FROM movimientos_cuenta GROUP BY cliente_id) l"
        " WHERE l.s <> (SELECT COALESCE(SUM(saldo_pendiente_centavos), 0) FROM obligaciones_cliente o WHERE o.cliente_id = l.cliente_id)"
    ).fetchone()[0] == 0
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert con.execute("SELECT name FROM sqlite_temp_master").fetchall() == []


def test_migraciones_idempotentes(previa):
    _migrar(previa)
    con = _migrar(previa)
    assert con.execute("SELECT COUNT(*) FROM schema_migraciones WHERE nombre_archivo = ?", (NOMBRE_039,)).fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM obligaciones_cliente").fetchone()[0] == 7


def test_un_cargo_historico_inconsistente_aborta_la_migracion_sin_cambios(previa):
    # Base corrupta (editada a mano): se quita el trigger de la 020 que lo impediría.
    previa.con.execute("DROP TRIGGER trg_ventas_cuenta_inmutable")
    previa.con.execute("UPDATE ventas SET total_centavos = 1 WHERE id = 1")
    previa.con.commit()
    with pytest.raises(Exception, match="sin venta a cuenta que lo respalde"):
        _migrar(previa)
    con = sqlite3.connect(previa.ruta)
    try:
        assert con.execute("SELECT COUNT(*) FROM schema_migraciones WHERE nombre_archivo = ?", (NOMBRE_039,)).fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'obligaciones_cliente'").fetchone()[0] == 0
    finally:
        con.close()
