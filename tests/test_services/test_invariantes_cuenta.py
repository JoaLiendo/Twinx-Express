"""Invariantes globales de la cuenta corriente (migración 020), comprobadas sobre la base entera
tras escenarios variados: ventas, cobros, reintentos, rechazos, fallas simuladas y varias sesiones de caja."""

import pytest

from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import caja as repositorio_caja
from db.repositorios import clientes as repositorio_clientes
from domain.caja import MovimientoCaja
from domain.venta import ItemVenta
from excepciones import ClienteConSaldoError, CobroInvalidoError, StockInsuficienteError
from services import servicio_caja, servicio_clientes, servicio_cuenta_corriente, servicio_ventas
from tests.utilidades_clientes import (
    conectar,
    consultar,
    crear_owner,
    crear_producto,
    crear_usuario,
    escribir,
    saldos_esperados_del_libro,
    violaciones_de_invariantes,
)

CC = "CUENTA_CORRIENTE"


@pytest.fixture
def e(base_datos_temporal, caja_abierta):
    owner, cajera = crear_owner(), crear_usuario("cajera", "CASHIER")
    return type(
        "Escenario",
        (),
        {
            "ruta": base_datos_temporal,
            "owner": owner,
            "cajera": cajera,
            "ana": servicio_clientes.crear_cliente("Ana", owner.id),
            "beto": servicio_clientes.crear_cliente("Beto", owner.id),
            "producto": crear_producto(precio_centavos=250, stock=50),
        },
    )


def _vender_a_cuenta(e, cliente, cantidad=1, clave=None):
    return servicio_ventas.registrar_venta(
        [ItemVenta(e.producto.id, cantidad)],
        CC,
        clave_idempotencia=clave,
        usuario_id=e.cajera.id,
        cliente_id=cliente.id,
    )


def _assert_saldos_coinciden_con_el_libro(e):
    """saldo (repositorio) == SUM(CARGO) - SUM(COBRO) (recalculado aparte), para cada cliente."""
    esperados = saldos_esperados_del_libro(e.ruta)
    for cliente in (e.ana, e.beto):
        assert repositorio_clientes.obtener_saldo(cliente.id) == esperados.get(cliente.id, 0)
        assert servicio_clientes.obtener_saldo(cliente.id) >= 0


def test_una_base_recien_migrada_sin_operaciones_es_consistente(base_datos_temporal):
    assert violaciones_de_invariantes(base_datos_temporal) == []


def test_invariantes_tras_un_escenario_completo(e):
    # Ventas a cuenta de dos clientes, con un reintento idempotente en el medio.
    _vender_a_cuenta(e, e.ana, 2, clave="v-1")
    _vender_a_cuenta(e, e.ana, 2, clave="v-1")
    _vender_a_cuenta(e, e.beto, 3)
    servicio_ventas.registrar_venta(
        [ItemVenta(e.producto.id, 1)], "EFECTIVO", usuario_id=e.cajera.id, cliente_id=e.ana.id
    )  # venta normal con cliente: sin CARGO
    # Cobros parciales, un reintento idempotente y un cobro total.
    servicio_cuenta_corriente.registrar_cobro(e.ana.id, 200, e.cajera.id, clave_idempotencia="c-1")
    servicio_cuenta_corriente.registrar_cobro(e.ana.id, 200, e.cajera.id, clave_idempotencia="c-1")
    servicio_cuenta_corriente.registrar_cobro(e.beto.id, 750, e.owner.id)
    # Operaciones rechazadas que no deben dejar rastro.
    with pytest.raises(CobroInvalidoError):
        servicio_cuenta_corriente.registrar_cobro(e.ana.id, 10_000, e.cajera.id)
    with pytest.raises(StockInsuficienteError):
        _vender_a_cuenta(e, e.ana, 1000)
    with pytest.raises(ClienteConSaldoError):
        servicio_clientes.desactivar_cliente(e.ana.id, e.owner.id)
    # Ingreso y egreso manuales conviven con los cobros.
    servicio_caja.registrar_ingreso(40, "aporte", usuario_id=e.cajera.id)
    servicio_caja.registrar_egreso(10, "retiro", usuario_id=e.cajera.id)
    # Cierre, nueva sesión y más actividad.
    servicio_caja.cerrar_caja(0)
    servicio_caja.abrir_caja(0)
    _vender_a_cuenta(e, e.beto, 1)
    servicio_cuenta_corriente.registrar_cobro(e.beto.id, 250, e.cajera.id)
    servicio_cuenta_corriente.registrar_cobro(e.ana.id, 200, e.cajera.id)

    assert violaciones_de_invariantes(e.ruta) == []
    _assert_saldos_coinciden_con_el_libro(e)
    assert repositorio_clientes.obtener_saldo(e.ana.id) == 2 * 250 - 200 - 200  # una sola venta de 2 (reintento)
    assert repositorio_clientes.obtener_saldo(e.beto.id) == 3 * 250 - 750 + 250 - 250
    assert consultar(e.ruta, "SELECT COUNT(*) AS n FROM movimientos_cuenta WHERE tipo = 'CARGO'")[0]["n"] == 3
    assert consultar(e.ruta, "SELECT COUNT(*) AS n FROM movimientos_cuenta WHERE tipo = 'COBRO'")[0]["n"] == 4


def test_invariantes_tras_operaciones_que_fallan_a_mitad(e, monkeypatch):
    _vender_a_cuenta(e, e.ana, 2)
    original = repositorio_auditoria.registrar_en_conexion

    def fallar_cobro_y_venta_a_cuenta(conexion, usuario_id, accion, *resto):
        if accion in ("COBRO_CUENTA", "VENTA_A_CUENTA"):
            raise RuntimeError("falla simulada")
        return original(conexion, usuario_id, accion, *resto)

    with monkeypatch.context() as parche:
        parche.setattr(repositorio_auditoria, "registrar_en_conexion", fallar_cobro_y_venta_a_cuenta)
        with pytest.raises(RuntimeError):
            servicio_cuenta_corriente.registrar_cobro(e.ana.id, 100, e.cajera.id)
        with pytest.raises(RuntimeError):
            _vender_a_cuenta(e, e.ana, 1)

    assert violaciones_de_invariantes(e.ruta) == []
    _assert_saldos_coinciden_con_el_libro(e)
    assert repositorio_clientes.obtener_saldo(e.ana.id) == 500


# --- el verificador detecta violaciones reales (no es vacuo) --------------------------------------------


def test_el_verificador_detecta_un_ingreso_cobro_cuenta_huerfano(e):
    repositorio_caja.registrar_movimiento(
        MovimientoCaja(tipo="INGRESO", monto_centavos=50, descripcion="huérfano", origen="COBRO_CUENTA")
    )

    violaciones = violaciones_de_invariantes(e.ruta)

    assert len(violaciones) == 1 and "INGRESO COBRO_CUENTA sin exactamente un COBRO" in violaciones[0]


def test_el_verificador_detecta_una_venta_a_cuenta_sin_cargo(e):
    sesion = consultar(e.ruta, "SELECT id FROM sesiones_caja WHERE estado = 'ABIERTA'")[0]["id"]
    escribir(
        e.ruta,
        "INSERT INTO ventas (total_centavos, tipo_pago, sesion_caja_id, cliente_id)"
        " VALUES (100, 'CUENTA_CORRIENTE', ?, ?)",
        (sesion, e.ana.id),
    )

    assert any("venta a cuenta sin exactamente un CARGO" in v for v in violaciones_de_invariantes(e.ruta))


def test_el_verificador_detecta_un_cobro_con_monto_distinto_al_de_su_ingreso(e):
    _vender_a_cuenta(e, e.ana, 2)
    servicio_cuenta_corriente.registrar_cobro(e.ana.id, 100, e.cajera.id)
    conexion = conectar(e.ruta)
    try:  # se falsea el monto del ingreso saltando el trigger que lo protege, solo para probar el verificador
        conexion.execute("DROP TRIGGER trg_caja_movimientos_cobro_monto_inmutable")
        conexion.execute("UPDATE caja_movimientos SET monto_centavos = 999 WHERE origen = 'COBRO_CUENTA'")
        conexion.commit()
    finally:
        conexion.close()

    assert any("monto del COBRO distinto" in v for v in violaciones_de_invariantes(e.ruta))


def test_el_verificador_detecta_un_saldo_negativo_en_el_libro(e):
    sesion = consultar(e.ruta, "SELECT id FROM sesiones_caja WHERE estado = 'ABIERTA'")[0]["id"]
    conexion = conectar(e.ruta)
    try:  # cobro sin saldo, saltando el trigger de saldo (solo para probar el verificador)
        conexion.execute("DROP TRIGGER trg_movimientos_cuenta_cobro_valido")
        ingreso = conexion.execute(
            "INSERT INTO caja_movimientos (tipo, monto_centavos, descripcion, sesion_caja_id, origen)"
            " VALUES ('INGRESO', 70, 'x', ?, 'COBRO_CUENTA')",
            (sesion,),
        ).lastrowid
        conexion.execute(
            "INSERT INTO movimientos_cuenta (cliente_id, tipo, monto_centavos, caja_movimiento_id)"
            " VALUES (?, 'COBRO', 70, ?)",
            (e.ana.id, ingreso),
        )
        conexion.commit()
    finally:
        conexion.close()

    assert any("saldo negativo" in v for v in violaciones_de_invariantes(e.ruta))
