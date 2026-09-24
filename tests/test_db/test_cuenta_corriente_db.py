"""Garantías a nivel SQLite de la migración 020: `caja_movimientos.origen`, clientes,
`ventas.cliente_id`/`CUENTA_CORRIENTE` y el libro `movimientos_cuenta`.

Las escrituras se hacen con SQL directo a propósito: se comprueba que el ESQUEMA rechaza
lo que ningún servicio debería intentar, no que los servicios se porten bien.
"""

import sqlite3

import pytest

from db.repositorios import caja as repositorio_caja
from domain.caja import MovimientoCaja
from tests.utilidades_clientes import (
    conectar,
    consultar,
    contar,
    crear_usuario,
    escribir,
    forzar_cliente_inactivo,
)


@pytest.fixture
def bd(base_datos_temporal):
    """Base con una caja abierta, un usuario y un cliente activo (id 1)."""
    usuario = crear_usuario()
    repositorio_caja.registrar_movimiento(MovimientoCaja(tipo="APERTURA", monto_centavos=1000), usuario.id)
    escribir(base_datos_temporal, "INSERT INTO clientes (nombre) VALUES ('Ana')")
    return base_datos_temporal


def _sesion_abierta(ruta) -> int:
    return consultar(ruta, "SELECT id FROM sesiones_caja WHERE estado = 'ABIERTA'")[0]["id"]


def _ingreso(ruta, origen="MANUAL", tipo="INGRESO", monto=100) -> int:
    """Inserta un movimiento de caja directo en la sesión abierta y devuelve su id."""
    conexion = conectar(ruta)
    try:
        cursor = conexion.execute(
            "INSERT INTO caja_movimientos (tipo, monto_centavos, descripcion, sesion_caja_id, origen)"
            " VALUES (?, ?, 'x', ?, ?)",
            (tipo, monto, _sesion_abierta(ruta), origen),
        )
        conexion.commit()
        return cursor.lastrowid
    finally:
        conexion.close()


def _venta_a_cuenta(ruta, total=500, cliente_id=1) -> int:
    conexion = conectar(ruta)
    try:
        cursor = conexion.execute(
            "INSERT INTO ventas (total_centavos, tipo_pago, sesion_caja_id, cliente_id)"
            " VALUES (?, 'CUENTA_CORRIENTE', ?, ?)",
            (total, _sesion_abierta(ruta), cliente_id),
        )
        conexion.commit()
        return cursor.lastrowid
    finally:
        conexion.close()


def _cargo(ruta, venta_id, monto=500, cliente_id=1) -> None:
    escribir(
        ruta,
        "INSERT INTO movimientos_cuenta (cliente_id, tipo, monto_centavos, venta_id) VALUES (?, 'CARGO', ?, ?)",
        (cliente_id, monto, venta_id),
    )


def _cobro(ruta, ingreso_id, monto=100, cliente_id=1, clave=None) -> None:
    escribir(
        ruta,
        "INSERT INTO movimientos_cuenta (cliente_id, tipo, monto_centavos, caja_movimiento_id, clave_idempotencia)"
        " VALUES (?, 'COBRO', ?, ?, ?)",
        (cliente_id, monto, ingreso_id, clave),
    )


def _con_saldo(ruta, total=500) -> int:
    venta = _venta_a_cuenta(ruta, total)
    _cargo(ruta, venta, total)
    return venta


# --- caja_movimientos.origen: garantía final ------------------------------------


def test_origen_manual_a_cobro_cuenta_rechazado(bd):
    movimiento = _ingreso(bd, origen="MANUAL")

    with pytest.raises(sqlite3.IntegrityError, match="origen"):
        escribir(bd, "UPDATE caja_movimientos SET origen = 'COBRO_CUENTA' WHERE id = ?", (movimiento,))

    assert consultar(bd, "SELECT origen FROM caja_movimientos WHERE id = ?", (movimiento,))[0]["origen"] == "MANUAL"


def test_origen_cobro_cuenta_a_manual_rechazado(bd):
    movimiento = _ingreso(bd, origen="COBRO_CUENTA")

    with pytest.raises(sqlite3.IntegrityError, match="origen"):
        escribir(bd, "UPDATE caja_movimientos SET origen = 'MANUAL' WHERE id = ?", (movimiento,))

    assert consultar(bd, "SELECT origen FROM caja_movimientos WHERE id = ?", (movimiento,))[0]["origen"] == (
        "COBRO_CUENTA"
    )


def test_cobro_cuenta_ingreso_a_egreso_rechazado(bd):
    movimiento = _ingreso(bd, origen="COBRO_CUENTA")

    with pytest.raises(sqlite3.IntegrityError, match="tipo"):
        escribir(bd, "UPDATE caja_movimientos SET tipo = 'EGRESO' WHERE id = ?", (movimiento,))

    fila = consultar(bd, "SELECT tipo, origen FROM caja_movimientos WHERE id = ?", (movimiento,))[0]
    assert (fila["tipo"], fila["origen"]) == ("INGRESO", "COBRO_CUENTA")


@pytest.mark.parametrize("tipo_nuevo", ["EGRESO", "APERTURA", "CIERRE"])
def test_el_tipo_de_un_cobro_cuenta_no_cambia_a_ningun_otro(bd, tipo_nuevo):
    movimiento = _ingreso(bd, origen="COBRO_CUENTA")

    with pytest.raises(sqlite3.IntegrityError):
        escribir(bd, "UPDATE caja_movimientos SET tipo = ? WHERE id = ?", (tipo_nuevo, movimiento))


def test_origen_y_valores_conservados_tras_operaciones_validas(bd):
    manual = _ingreso(bd, origen="MANUAL", monto=100)
    cobro = _ingreso(bd, origen="COBRO_CUENTA", monto=200)

    # Operaciones válidas: cambiar la descripción, la clave de idempotencia y el usuario.
    escribir(bd, "UPDATE caja_movimientos SET descripcion = 'otra', clave_idempotencia = 'k1' WHERE id = ?", (cobro,))
    escribir(bd, "UPDATE caja_movimientos SET descripcion = 'otra', usuario_id = 1 WHERE id = ?", (manual,))
    # Reasignar exactamente el mismo valor a las columnas protegidas no es un cambio.
    escribir(
        bd,
        "UPDATE caja_movimientos SET origen = origen, tipo = tipo, monto_centavos = monto_centavos WHERE id = ?",
        (cobro,),
    )

    filas = {f["id"]: f for f in consultar(bd, "SELECT * FROM caja_movimientos WHERE id IN (?, ?)", (manual, cobro))}
    assert (filas[manual]["origen"], filas[manual]["tipo"], filas[manual]["monto_centavos"]) == ("MANUAL", "INGRESO", 100)
    assert (filas[cobro]["origen"], filas[cobro]["tipo"], filas[cobro]["monto_centavos"]) == (
        "COBRO_CUENTA", "INGRESO", 200
    )
    assert filas[cobro]["descripcion"] == "otra"


def test_actualizar_origen_y_tipo_a_la_vez_tambien_se_rechaza(bd):
    movimiento = _ingreso(bd, origen="COBRO_CUENTA")

    with pytest.raises(sqlite3.IntegrityError):
        escribir(bd, "UPDATE caja_movimientos SET origen = 'MANUAL', tipo = 'EGRESO' WHERE id = ?", (movimiento,))

    fila = consultar(bd, "SELECT tipo, origen FROM caja_movimientos WHERE id = ?", (movimiento,))[0]
    assert (fila["tipo"], fila["origen"]) == ("INGRESO", "COBRO_CUENTA")


@pytest.mark.parametrize("tipo", ["EGRESO", "APERTURA", "CIERRE"])
def test_cobro_cuenta_solo_puede_insertarse_como_ingreso(bd, tipo):
    with pytest.raises(sqlite3.IntegrityError, match="solo puede ser un INGRESO"):
        _ingreso(bd, origen="COBRO_CUENTA", tipo=tipo)


def test_monto_de_un_cobro_cuenta_es_inmutable_y_el_de_un_manual_no(bd):
    cobro = _ingreso(bd, origen="COBRO_CUENTA", monto=200)
    manual = _ingreso(bd, origen="MANUAL", monto=100)

    with pytest.raises(sqlite3.IntegrityError, match="monto"):
        escribir(bd, "UPDATE caja_movimientos SET monto_centavos = 999 WHERE id = ?", (cobro,))
    escribir(bd, "UPDATE caja_movimientos SET monto_centavos = 150 WHERE id = ?", (manual,))

    assert consultar(bd, "SELECT monto_centavos FROM caja_movimientos WHERE id = ?", (cobro,))[0][0] == 200
    assert consultar(bd, "SELECT monto_centavos FROM caja_movimientos WHERE id = ?", (manual,))[0][0] == 150


def test_origen_invalido_o_nulo_rechazado_por_check_y_not_null(bd):
    with pytest.raises(sqlite3.IntegrityError):
        _ingreso(bd, origen="OTRO_ORIGEN")
    with pytest.raises(sqlite3.IntegrityError):
        escribir(
            bd,
            "INSERT INTO caja_movimientos (tipo, monto_centavos, descripcion, sesion_caja_id, origen)"
            " VALUES ('INGRESO', 1, 'x', ?, NULL)",
            (_sesion_abierta(bd),),
        )


def test_origen_por_defecto_es_manual(bd):
    escribir(
        bd,
        "INSERT INTO caja_movimientos (tipo, monto_centavos, descripcion, sesion_caja_id)"
        " VALUES ('INGRESO', 1, 'x', ?)",
        (_sesion_abierta(bd),),
    )

    assert consultar(bd, "SELECT origen FROM caja_movimientos ORDER BY id DESC LIMIT 1")[0]["origen"] == "MANUAL"


# --- clientes -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("columna", "limite"),
    [("nombre", 120), ("telefono", 30), ("email", 150), ("direccion", 200), ("observaciones", 500)],
)
def test_limites_de_longitud_de_clientes(bd, columna, limite):
    # `nombre` es obligatorio; el resto de las columnas se prueba con un nombre válido.
    sql = "INSERT INTO clientes (nombre) VALUES (?)" if columna == "nombre" else (
        f"INSERT INTO clientes (nombre, {columna}) VALUES ('x', ?)"
    )

    escribir(bd, sql, ("a" * limite,))
    with pytest.raises(sqlite3.IntegrityError):
        escribir(bd, sql, ("a" * (limite + 1),))


def test_nombre_de_cliente_vacio_o_en_blanco_rechazado(bd):
    for nombre in ("", "   "):
        with pytest.raises(sqlite3.IntegrityError):
            escribir(bd, "INSERT INTO clientes (nombre) VALUES (?)", (nombre,))


def test_nombres_de_clientes_duplicados_permitidos(bd):
    escribir(bd, "INSERT INTO clientes (nombre) VALUES ('Ana')")

    assert contar(bd, "clientes", "nombre = 'Ana'") == 2


def test_email_sin_arroba_se_acepta(bd):
    escribir(bd, "INSERT INTO clientes (nombre, email) VALUES ('Beto', 'no-es-un-email')")

    assert contar(bd, "clientes", "email = 'no-es-un-email'") == 1


def test_cliente_nuevo_nace_activo(bd):
    assert consultar(bd, "SELECT activo FROM clientes WHERE id = 1")[0]["activo"] == 1


# --- ventas.cliente_id ----------------------------------------------------------------


def test_cliente_inactivo_rechazado_en_venta_normal_y_a_cuenta(bd):
    escribir(bd, "UPDATE clientes SET activo = 0 WHERE id = 1")
    sesion = _sesion_abierta(bd)

    for tipo in ("EFECTIVO", "CUENTA_CORRIENTE"):
        with pytest.raises(sqlite3.IntegrityError, match="cliente activo"):
            escribir(
                bd,
                "INSERT INTO ventas (total_centavos, tipo_pago, sesion_caja_id, cliente_id) VALUES (10, ?, ?, 1)",
                (tipo, sesion),
            )


def test_cliente_activo_se_asocia_a_venta_normal(bd):
    escribir(
        bd,
        "INSERT INTO ventas (total_centavos, tipo_pago, sesion_caja_id, cliente_id) VALUES (10, 'EFECTIVO', ?, 1)",
        (_sesion_abierta(bd),),
    )

    assert contar(bd, "ventas", "cliente_id = 1") == 1


def test_cliente_inexistente_rechazado_por_fk(bd):
    with pytest.raises(sqlite3.IntegrityError):
        escribir(
            bd,
            "INSERT INTO ventas (total_centavos, tipo_pago, sesion_caja_id, cliente_id) VALUES (10, 'EFECTIVO', ?, 99)",
            (_sesion_abierta(bd),),
        )


def test_cliente_de_una_venta_es_inmutable(bd):
    escribir(bd, "INSERT INTO clientes (nombre) VALUES ('Beto')")
    venta = _venta_a_cuenta(bd)

    with pytest.raises(sqlite3.IntegrityError, match="cliente de una venta"):
        escribir(bd, "UPDATE ventas SET cliente_id = 2 WHERE id = ?", (venta,))
    with pytest.raises(sqlite3.IntegrityError, match="cliente de una venta"):
        escribir(bd, "UPDATE ventas SET cliente_id = NULL WHERE id = ?", (venta,))


def test_tipo_de_pago_y_total_de_una_venta_a_cuenta_son_inmutables(bd):
    venta = _venta_a_cuenta(bd)

    with pytest.raises(sqlite3.IntegrityError):
        escribir(bd, "UPDATE ventas SET tipo_pago = 'EFECTIVO' WHERE id = ?", (venta,))
    with pytest.raises(sqlite3.IntegrityError):
        escribir(bd, "UPDATE ventas SET total_centavos = 1 WHERE id = ?", (venta,))


def test_una_venta_normal_no_puede_pasar_a_cuenta_corriente(bd):
    escribir(
        bd,
        "INSERT INTO ventas (total_centavos, tipo_pago, sesion_caja_id, cliente_id) VALUES (10, 'EFECTIVO', ?, 1)",
        (_sesion_abierta(bd),),
    )
    venta = consultar(bd, "SELECT MAX(id) AS id FROM ventas")[0]["id"]

    with pytest.raises(sqlite3.IntegrityError):
        escribir(bd, "UPDATE ventas SET tipo_pago = 'CUENTA_CORRIENTE' WHERE id = ?", (venta,))


def test_una_venta_a_cuenta_no_puede_anularse_pero_una_normal_si(bd):
    a_cuenta = _venta_a_cuenta(bd)
    escribir(
        bd,
        "INSERT INTO ventas (total_centavos, tipo_pago, sesion_caja_id, cliente_id) VALUES (10, 'EFECTIVO', ?, 1)",
        (_sesion_abierta(bd),),
    )
    normal = consultar(bd, "SELECT MAX(id) AS id FROM ventas")[0]["id"]

    with pytest.raises(sqlite3.IntegrityError, match="no puede anularse"):
        escribir(bd, "UPDATE ventas SET estado = 'ANULADA' WHERE id = ?", (a_cuenta,))
    escribir(bd, "UPDATE ventas SET estado = 'ANULADA' WHERE id = ?", (normal,))

    assert consultar(bd, "SELECT estado FROM ventas WHERE id = ?", (a_cuenta,))[0]["estado"] == "ACTIVA"
    assert consultar(bd, "SELECT estado FROM ventas WHERE id = ?", (normal,))[0]["estado"] == "ANULADA"


# --- movimientos_cuenta: CARGO -------------------------------------------------------


def test_cargo_valido_respaldado_por_una_venta_a_cuenta(bd):
    venta = _venta_a_cuenta(bd)

    _cargo(bd, venta)

    assert contar(bd, "movimientos_cuenta", "tipo = 'CARGO'") == 1


def test_cargo_con_monto_distinto_al_total_rechazado(bd):
    venta = _venta_a_cuenta(bd, total=500)

    with pytest.raises(sqlite3.IntegrityError, match="cargo debe respaldarse"):
        _cargo(bd, venta, monto=499)


def test_cargo_de_otro_cliente_rechazado(bd):
    escribir(bd, "INSERT INTO clientes (nombre) VALUES ('Beto')")
    venta = _venta_a_cuenta(bd, cliente_id=1)

    with pytest.raises(sqlite3.IntegrityError, match="cargo debe respaldarse"):
        _cargo(bd, venta, cliente_id=2)


def test_cargo_de_una_venta_que_no_es_a_cuenta_rechazado(bd):
    escribir(
        bd,
        "INSERT INTO ventas (total_centavos, tipo_pago, sesion_caja_id, cliente_id) VALUES (500, 'EFECTIVO', ?, 1)",
        (_sesion_abierta(bd),),
    )
    venta = consultar(bd, "SELECT MAX(id) AS id FROM ventas")[0]["id"]

    with pytest.raises(sqlite3.IntegrityError, match="cargo debe respaldarse"):
        _cargo(bd, venta)


def test_cargo_de_cliente_inactivo_rechazado(bd):
    venta = _venta_a_cuenta(bd)
    escribir(bd, "UPDATE clientes SET activo = 0 WHERE id = 1")

    with pytest.raises(sqlite3.IntegrityError, match="cliente activo"):
        _cargo(bd, venta)


def test_una_venta_tiene_como_maximo_un_cargo(bd):
    venta = _venta_a_cuenta(bd)
    _cargo(bd, venta)

    with pytest.raises(sqlite3.IntegrityError):
        _cargo(bd, venta)


def test_cargo_y_cobro_deben_referenciar_exactamente_lo_suyo(bd):
    venta = _venta_a_cuenta(bd)
    ingreso = _ingreso(bd, origen="COBRO_CUENTA")

    with pytest.raises(sqlite3.IntegrityError):  # CARGO con caja_movimiento_id
        escribir(
            bd,
            "INSERT INTO movimientos_cuenta (cliente_id, tipo, monto_centavos, venta_id, caja_movimiento_id)"
            " VALUES (1, 'CARGO', 500, ?, ?)",
            (venta, ingreso),
        )
    with pytest.raises(sqlite3.IntegrityError):  # COBRO sin caja_movimiento_id
        escribir(bd, "INSERT INTO movimientos_cuenta (cliente_id, tipo, monto_centavos) VALUES (1, 'COBRO', 5)")


def test_monto_de_un_movimiento_de_cuenta_debe_ser_positivo(bd):
    venta = _venta_a_cuenta(bd)

    with pytest.raises(sqlite3.IntegrityError):
        _cargo(bd, venta, monto=0)


# --- movimientos_cuenta: COBRO -------------------------------------------------------


def test_cobro_valido_verifica_origen_tipo_monto_y_sesion_del_ingreso(bd):
    _con_saldo(bd, 500)
    ingreso = _ingreso(bd, origen="COBRO_CUENTA", monto=200)

    _cobro(bd, ingreso, monto=200)

    assert contar(bd, "movimientos_cuenta", "tipo = 'COBRO'") == 1


def test_cobro_respaldado_por_un_ingreso_manual_rechazado(bd):
    _con_saldo(bd, 500)
    ingreso = _ingreso(bd, origen="MANUAL", monto=200)

    with pytest.raises(sqlite3.IntegrityError, match="cobro debe respaldarse"):
        _cobro(bd, ingreso, monto=200)


def test_cobro_con_monto_distinto_al_del_ingreso_rechazado(bd):
    _con_saldo(bd, 500)
    ingreso = _ingreso(bd, origen="COBRO_CUENTA", monto=200)

    with pytest.raises(sqlite3.IntegrityError, match="cobro debe respaldarse"):
        _cobro(bd, ingreso, monto=100)


def test_cobro_de_un_ingreso_de_una_sesion_cerrada_rechazado(bd):
    _con_saldo(bd, 500)
    ingreso = _ingreso(bd, origen="COBRO_CUENTA", monto=200)
    repositorio_caja.registrar_movimiento(MovimientoCaja(tipo="CIERRE", monto_centavos=0, diferencia_centavos=0))

    with pytest.raises(sqlite3.IntegrityError, match="cobro debe respaldarse"):
        _cobro(bd, ingreso, monto=200)


def test_cobro_mayor_al_saldo_rechazado_y_por_el_saldo_exacto_permitido(bd):
    _con_saldo(bd, 500)
    ingreso_de_mas = _ingreso(bd, origen="COBRO_CUENTA", monto=501)
    ingreso_exacto = _ingreso(bd, origen="COBRO_CUENTA", monto=500)

    with pytest.raises(sqlite3.IntegrityError, match="superar el saldo"):
        _cobro(bd, ingreso_de_mas, monto=501)
    _cobro(bd, ingreso_exacto, monto=500)

    saldo = consultar(
        bd, "SELECT SUM(CASE tipo WHEN 'CARGO' THEN monto_centavos ELSE -monto_centavos END) AS s FROM movimientos_cuenta"
    )[0]["s"]
    assert saldo == 0


def test_cobro_de_cliente_inactivo_con_saldo_permitido_por_el_trigger(bd):
    _con_saldo(bd, 500)
    ingreso = _ingreso(bd, origen="COBRO_CUENTA", monto=100)
    forzar_cliente_inactivo(bd, 1)  # estado que la aplicación no produce: se arma saltando el trigger de baja

    _cobro(bd, ingreso, monto=100)

    assert contar(bd, "movimientos_cuenta", "tipo = 'COBRO'") == 1
    assert consultar(bd, "SELECT activo FROM clientes WHERE id = 1")[0]["activo"] == 0


def test_cobro_de_cliente_inactivo_sin_saldo_rechazado_por_el_trigger_de_saldo(bd):
    escribir(bd, "UPDATE clientes SET activo = 0 WHERE id = 1")  # saldo 0: la baja normal está permitida
    ingreso = _ingreso(bd, origen="COBRO_CUENTA", monto=100)

    with pytest.raises(sqlite3.IntegrityError, match="superar el saldo"):
        _cobro(bd, ingreso, monto=100)

    assert contar(bd, "movimientos_cuenta") == 0


def test_cobro_de_cliente_inexistente_rechazado_por_el_trigger(bd):
    ingreso = _ingreso(bd, origen="COBRO_CUENTA", monto=100)

    with pytest.raises(sqlite3.IntegrityError, match="cliente existente"):
        _cobro(bd, ingreso, monto=100, cliente_id=999)

    assert contar(bd, "movimientos_cuenta") == 0


def test_un_ingreso_respalda_como_maximo_un_cobro(bd):
    _con_saldo(bd, 500)
    ingreso = _ingreso(bd, origen="COBRO_CUENTA", monto=100)
    _cobro(bd, ingreso, monto=100)

    with pytest.raises(sqlite3.IntegrityError):
        _cobro(bd, ingreso, monto=100)


def test_la_clave_de_idempotencia_de_un_cobro_es_unica(bd):
    _con_saldo(bd, 500)
    primero = _ingreso(bd, origen="COBRO_CUENTA", monto=100)
    segundo = _ingreso(bd, origen="COBRO_CUENTA", monto=100)
    _cobro(bd, primero, monto=100, clave="k")

    with pytest.raises(sqlite3.IntegrityError):
        _cobro(bd, segundo, monto=100, clave="k")


# --- libro append-only y FK RESTRICT ----------------------------------------------------


def test_el_libro_no_admite_updates_ni_deletes(bd):
    _con_saldo(bd, 500)

    with pytest.raises(sqlite3.IntegrityError, match="no pueden modificarse"):
        escribir(bd, "UPDATE movimientos_cuenta SET monto_centavos = 1")
    with pytest.raises(sqlite3.IntegrityError, match="no pueden borrarse"):
        escribir(bd, "DELETE FROM movimientos_cuenta")

    assert contar(bd, "movimientos_cuenta") == 1


def test_no_se_borra_el_ingreso_de_caja_que_respalda_un_cobro(bd):
    _con_saldo(bd, 500)
    ingreso = _ingreso(bd, origen="COBRO_CUENTA", monto=100)
    _cobro(bd, ingreso, monto=100)

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        escribir(bd, "DELETE FROM caja_movimientos WHERE id = ?", (ingreso,))

    assert contar(bd, "caja_movimientos", f"id = {ingreso}") == 1


def test_fk_de_caja_movimiento_id_es_restrict_y_no_cascade(bd):
    referencias = consultar(bd, "SELECT * FROM pragma_foreign_key_list('movimientos_cuenta')")

    por_columna = {f["from"]: f["on_delete"] for f in referencias}
    assert por_columna["caja_movimiento_id"] == "RESTRICT"
    assert set(por_columna.values()) == {"RESTRICT"}


def test_no_se_borra_la_venta_ni_el_cliente_con_movimientos(bd):
    venta = _con_saldo(bd, 500)

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        escribir(bd, "DELETE FROM ventas WHERE id = ?", (venta,))
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        escribir(bd, "DELETE FROM clientes WHERE id = 1")


# --- desactivación -------------------------------------------------------------------------


def test_no_se_desactiva_un_cliente_con_saldo_pero_si_con_saldo_cero(bd):
    _con_saldo(bd, 500)
    with pytest.raises(sqlite3.IntegrityError, match="saldo pendiente"):
        escribir(bd, "UPDATE clientes SET activo = 0 WHERE id = 1")

    ingreso = _ingreso(bd, origen="COBRO_CUENTA", monto=500)
    _cobro(bd, ingreso, monto=500)
    escribir(bd, "UPDATE clientes SET activo = 0 WHERE id = 1")

    assert consultar(bd, "SELECT activo FROM clientes WHERE id = 1")[0]["activo"] == 0
