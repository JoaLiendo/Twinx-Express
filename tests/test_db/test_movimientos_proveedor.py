"""Integridad directa de `movimientos_proveedor` y de `caja_movimientos.origen = 'PAGO_PROVEEDOR'`
(migración 025, V1.9-A). Solo la base de datos: todavía no existe ningún servicio que genere estos
movimientos (eso es V1.9-B/V1.9-C) -- estos tests insertan directamente por SQL para probar que las
garantías estructurales (CHECKs, índices únicos parciales y triggers) sostienen el libro por sí
solas, sin depender de ninguna capa superior.
"""

import sqlite3

import pytest

import db.conexion as modulo_conexion


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", tmp_path / "kiosco.db")
    modulo_conexion.inicializar_base_datos()
    con = sqlite3.connect(tmp_path / "kiosco.db")
    con.execute("PRAGMA foreign_keys = ON")

    con.execute(
        "INSERT INTO usuarios (id, nombre_usuario, nombre_completo, password_hash, rol)"
        " VALUES (1, 'duenio', 'Dueño', 'h', 'OWNER')"
    )
    con.execute("INSERT INTO proveedores (id, nombre) VALUES (1, 'Prov A'), (2, 'Prov B')")
    con.execute(
        "INSERT INTO productos (id, codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos,"
        " stock_actual) VALUES (1, 'c1', 'P1', 100, 200, 10)"
    )
    con.execute(
        "INSERT INTO sesiones_caja (id, estado, origen, fecha_apertura, usuario_apertura_id, fondo_centavos)"
        " VALUES (1, 'ABIERTA', 'NORMAL', '2026-04-01 08:00:00', 1, 1000)"
    )
    # Compra a crédito (proveedor 1) y una a contado (proveedor 1), para probar los rechazos.
    con.execute(
        "INSERT INTO compras (id, proveedor_id, usuario_id, total_centavos, condicion_pago)"
        " VALUES (1, 1, 1, 1000, 'CREDITO'), (2, 1, 1, 500, 'CONTADO')"
    )
    con.execute(
        "INSERT INTO detalle_compra (compra_id, producto_id, cantidad, costo_unitario_centavos, subtotal_centavos)"
        " VALUES (1, 1, 10, 100, 1000), (2, 1, 5, 100, 500)"
    )
    con.commit()
    yield con
    con.close()


def _cargo_valido(con, **overrides):
    valores = {"proveedor_id": 1, "tipo": "CARGO_COMPRA", "monto_centavos": 1000, "compra_id": 1, **overrides}
    con.execute(
        f"INSERT INTO movimientos_proveedor ({', '.join(valores)}) VALUES ({', '.join('?' * len(valores))})",
        tuple(valores.values()),
    )


def _egreso_pago_proveedor(con, monto=1000) -> int:
    return con.execute(
        "INSERT INTO caja_movimientos (tipo, monto_centavos, sesion_caja_id, origen)"
        " VALUES ('EGRESO', ?, 1, 'PAGO_PROVEEDOR')",
        (monto,),
    ).lastrowid


# --- CARGO_COMPRA ------------------------------------------------------------------------


def test_cargo_valido_se_acepta(base):
    _cargo_valido(base)
    assert base.execute("SELECT COUNT(*) FROM movimientos_proveedor WHERE tipo = 'CARGO_COMPRA'").fetchone()[0] == 1


def test_cargo_con_proveedor_incorrecto_rechazado(base):
    with pytest.raises(sqlite3.IntegrityError):
        _cargo_valido(base, proveedor_id=2)  # la compra 1 es del proveedor 1


def test_cargo_con_monto_incorrecto_rechazado(base):
    with pytest.raises(sqlite3.IntegrityError):
        _cargo_valido(base, monto_centavos=999)


def test_cargo_sobre_compra_contado_rechazado(base):
    with pytest.raises(sqlite3.IntegrityError):
        _cargo_valido(base, compra_id=2, monto_centavos=500)  # M2/M6: compra 2 es CONTADO


def test_cargo_sobre_compra_anulada_rechazado(base):
    base.execute("UPDATE compras SET estado = 'ANULADA' WHERE id = 1")
    with pytest.raises(sqlite3.IntegrityError):
        _cargo_valido(base)


def test_cargo_duplicado_rechazado(base):
    _cargo_valido(base)
    with pytest.raises(sqlite3.IntegrityError):  # M4: un segundo cargo para la misma compra
        _cargo_valido(base)
    assert base.execute("SELECT COUNT(*) FROM movimientos_proveedor WHERE compra_id = 1").fetchone()[0] == 1


# --- REVERSA_COMPRA ------------------------------------------------------------------------


def _reversa(con, **overrides):
    valores = {"proveedor_id": 1, "tipo": "REVERSA_COMPRA", "monto_centavos": 1000, "compra_id": 1, **overrides}
    con.execute(
        f"INSERT INTO movimientos_proveedor ({', '.join(valores)}) VALUES ({', '.join('?' * len(valores))})",
        tuple(valores.values()),
    )


def test_reversa_valida_se_acepta(base):
    _cargo_valido(base)
    _reversa(base)
    assert base.execute("SELECT COUNT(*) FROM movimientos_proveedor WHERE tipo = 'REVERSA_COMPRA'").fetchone()[0] == 1


def test_reversa_sin_cargo_previo_rechazada(base):
    with pytest.raises(sqlite3.IntegrityError):
        _reversa(base)


def test_reversa_duplicada_rechazada(base):
    _cargo_valido(base)
    _reversa(base)
    with pytest.raises(sqlite3.IntegrityError):
        _reversa(base)
    assert base.execute("SELECT COUNT(*) FROM movimientos_proveedor WHERE tipo = 'REVERSA_COMPRA'").fetchone()[0] == 1


def test_reversa_con_monto_distinto_del_cargo_rechazada(base):
    _cargo_valido(base)
    with pytest.raises(sqlite3.IntegrityError):
        _reversa(base, monto_centavos=1)


# --- PAGO ------------------------------------------------------------------------------------


def test_pago_efectivo_respaldado_correctamente_se_acepta(base):
    egreso_id = _egreso_pago_proveedor(base)
    base.execute(
        "INSERT INTO movimientos_proveedor (proveedor_id, tipo, monto_centavos, medio_pago, caja_movimiento_id)"
        " VALUES (1, 'PAGO', 1000, 'EFECTIVO', ?)",
        (egreso_id,),
    )
    assert base.execute("SELECT COUNT(*) FROM movimientos_proveedor WHERE tipo = 'PAGO'").fetchone()[0] == 1


def test_pago_efectivo_con_caja_incorrecta_rechazado(base):
    egreso_id = _egreso_pago_proveedor(base, monto=1000)
    with pytest.raises(sqlite3.IntegrityError):  # monto no coincide con el egreso real
        base.execute(
            "INSERT INTO movimientos_proveedor (proveedor_id, tipo, monto_centavos, medio_pago, caja_movimiento_id)"
            " VALUES (1, 'PAGO', 1, 'EFECTIVO', ?)",
            (egreso_id,),
        )


def test_pago_efectivo_contra_egreso_manual_rechazado(base):
    egreso_manual = base.execute(
        "INSERT INTO caja_movimientos (tipo, monto_centavos, sesion_caja_id, origen)"
        " VALUES ('EGRESO', 1000, 1, 'MANUAL')"
    ).lastrowid
    with pytest.raises(sqlite3.IntegrityError):
        base.execute(
            "INSERT INTO movimientos_proveedor (proveedor_id, tipo, monto_centavos, medio_pago, caja_movimiento_id)"
            " VALUES (1, 'PAGO', 1000, 'EFECTIVO', ?)",
            (egreso_manual,),
        )


def test_transferencia_sin_caja_aceptada(base):
    base.execute(
        "INSERT INTO movimientos_proveedor (proveedor_id, tipo, monto_centavos, medio_pago)"
        " VALUES (1, 'PAGO', 1000, 'TRANSFERENCIA')"
    )
    assert base.execute(
        "SELECT COUNT(*) FROM movimientos_proveedor WHERE medio_pago = 'TRANSFERENCIA'"
    ).fetchone()[0] == 1


def test_transferencia_con_caja_rechazada(base):
    egreso_id = _egreso_pago_proveedor(base)
    with pytest.raises(sqlite3.IntegrityError):
        base.execute(
            "INSERT INTO movimientos_proveedor (proveedor_id, tipo, monto_centavos, medio_pago, caja_movimiento_id)"
            " VALUES (1, 'PAGO', 1000, 'TRANSFERENCIA', ?)",
            (egreso_id,),
        )


def test_pago_con_compra_id_rechazado(base):
    """Un PAGO nunca se imputa a una compra puntual (ver auditoría V1.9)."""
    with pytest.raises(sqlite3.IntegrityError):
        base.execute(
            "INSERT INTO movimientos_proveedor (proveedor_id, tipo, monto_centavos, medio_pago, compra_id)"
            " VALUES (1, 'PAGO', 1000, 'TRANSFERENCIA', 1)"
        )


def test_monto_cero_o_negativo_rechazado_para_cualquier_tipo(base):
    with pytest.raises(sqlite3.IntegrityError):
        _cargo_valido(base, monto_centavos=0)
    with pytest.raises(sqlite3.IntegrityError):
        base.execute(
            "INSERT INTO movimientos_proveedor (proveedor_id, tipo, monto_centavos, medio_pago)"
            " VALUES (1, 'PAGO', -100, 'TRANSFERENCIA')"
        )


# --- Inmutabilidad, borrado e idempotencia ----------------------------------------------------


def test_update_rechazado(base):
    """M5: intentar 'quitar' la inmutabilidad del libro editando una fila ya insertada."""
    _cargo_valido(base)
    with pytest.raises(sqlite3.IntegrityError):
        base.execute("UPDATE movimientos_proveedor SET monto_centavos = 1 WHERE compra_id = 1")
    fila = base.execute("SELECT monto_centavos FROM movimientos_proveedor WHERE compra_id = 1").fetchone()
    assert fila[0] == 1000  # sin cambios: la mutación no dejó rastro


def test_delete_rechazado(base):
    _cargo_valido(base)
    with pytest.raises(sqlite3.IntegrityError):
        base.execute("DELETE FROM movimientos_proveedor WHERE compra_id = 1")
    assert base.execute("SELECT COUNT(*) FROM movimientos_proveedor").fetchone()[0] == 1


def test_clave_idempotencia_duplicada_rechazada(base):
    _cargo_valido(base, clave_idempotencia="clave-1")
    with pytest.raises(sqlite3.IntegrityError):
        _reversa(base, clave_idempotencia="clave-1")  # cualquier otra fila con la misma clave choca
    base.execute(
        "INSERT INTO movimientos_proveedor (proveedor_id, tipo, monto_centavos, medio_pago)"
        " VALUES (1, 'PAGO', 1, 'TRANSFERENCIA')"
    )  # sin clave: no choca contra nada
    base.execute(
        "INSERT INTO movimientos_proveedor (proveedor_id, tipo, monto_centavos, medio_pago)"
        " VALUES (1, 'PAGO', 2, 'TRANSFERENCIA')"
    )  # dos filas sin clave tampoco chocan entre sí


# --- Deuda retroactiva sobre compras V1.8 (M6) ------------------------------------------------


def test_no_se_puede_generar_deuda_retroactiva_sobre_una_compra_historica_contado(base):
    """M6: cualquier intento de cargo contra una compra CONTADO (el estado de toda compra V1.8
    tras migrar) debe fallar -- mismo mecanismo que M2, aplicado al caso real de compatibilidad."""
    with pytest.raises(sqlite3.IntegrityError):
        _cargo_valido(base, compra_id=2, monto_centavos=500)
    assert base.execute("SELECT COUNT(*) FROM movimientos_proveedor").fetchone()[0] == 0


# --- Saldo derivado del libro (sin columna desnormalizada) ------------------------------------


def test_saldo_se_deriva_del_libro_cargos_menos_pagos_menos_reversas(base):
    _cargo_valido(base)  # +1000
    egreso_id = _egreso_pago_proveedor(base, monto=400)
    base.execute(
        "INSERT INTO movimientos_proveedor (proveedor_id, tipo, monto_centavos, medio_pago, caja_movimiento_id)"
        " VALUES (1, 'PAGO', 400, 'EFECTIVO', ?)",
        (egreso_id,),
    )  # -400

    saldo = base.execute(
        "SELECT COALESCE(SUM(CASE tipo"
        " WHEN 'CARGO_COMPRA' THEN monto_centavos"
        " WHEN 'PAGO' THEN -monto_centavos"
        " WHEN 'REVERSA_COMPRA' THEN -monto_centavos END), 0)"
        " FROM movimientos_proveedor WHERE proveedor_id = 1"
    ).fetchone()[0]
    assert saldo == 600

    columnas_proveedores = [f[1] for f in base.execute("PRAGMA table_info(proveedores)")]
    assert "saldo" not in columnas_proveedores  # sin columna desnormalizada (ver auditoría V1.9)
