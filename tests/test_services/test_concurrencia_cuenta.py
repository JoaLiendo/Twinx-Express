"""Concurrencia de la cuenta corriente (migración 020): dos o más hilos, cada uno con su propia
conexión, compiten por el mismo saldo, stock, clave de idempotencia o caja. `BEGIN IMMEDIATE`
los serializa: nunca debe haber un doble cobro, una venta sin stock ni un estado a medias."""

import threading

import pytest

from db.repositorios import clientes as repositorio_clientes
from domain.venta import ItemVenta
from excepciones import (
    CajaCerradaError,
    ClienteConSaldoError,
    ClienteInactivoError,
    CobroInvalidoError,
    StockInsuficienteError,
)
from services import servicio_caja, servicio_clientes, servicio_cuenta_corriente, servicio_stock, servicio_ventas
from tests.utilidades_clientes import (
    consultar,
    contar,
    crear_owner,
    crear_producto,
    crear_usuario,
    violaciones_de_invariantes,
)

CC = "CUENTA_CORRIENTE"


@pytest.fixture
def escenario(base_datos_temporal, caja_abierta):
    owner = crear_owner()
    cajera = crear_usuario("cajera", "CASHIER")
    cliente = servicio_clientes.crear_cliente("Ana", owner.id)
    producto = crear_producto(precio_centavos=500, stock=1)
    return type("Escenario", (), {"ruta": base_datos_temporal, "owner": owner, "cajera": cajera,
                                  "cliente": cliente, "producto": producto})


def _correr_en_paralelo(tareas):
    """Ejecuta cada callable en su propio hilo, todos liberados a la vez, y devuelve
    la lista de `("OK", resultado)` / `("ERROR", excepción)` en el mismo orden."""
    barrera = threading.Barrier(len(tareas))
    resultados = [None] * len(tareas)

    def envolver(indice, tarea):
        barrera.wait(timeout=10)
        try:
            resultados[indice] = ("OK", tarea())
        except Exception as error:  # noqa: BLE001 - se clasifica en cada test
            resultados[indice] = ("ERROR", error)

    hilos = [threading.Thread(target=envolver, args=(i, t)) for i, t in enumerate(tareas)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=30)
        assert not hilo.is_alive()
    return resultados


def _con_saldo(e, total=500):
    producto = crear_producto("7790000000099", precio_centavos=total, stock=10)
    servicio_ventas.registrar_venta(
        [ItemVenta(producto.id, 1)], CC, usuario_id=e.cajera.id, cliente_id=e.cliente.id
    )


def test_dos_cobros_concurrentes_que_no_caben_en_el_saldo_solo_uno_gana(escenario):
    _con_saldo(escenario, 500)

    resultados = _correr_en_paralelo(
        [lambda: servicio_cuenta_corriente.registrar_cobro(escenario.cliente.id, 300, escenario.cajera.id)] * 2
    )

    assert [r[0] for r in resultados].count("OK") == 1
    perdedor = next(r[1] for r in resultados if r[0] == "ERROR")
    assert isinstance(perdedor, CobroInvalidoError)
    assert repositorio_clientes.obtener_saldo(escenario.cliente.id) == 200
    assert contar(escenario.ruta, "caja_movimientos", "origen = 'COBRO_CUENTA'") == 1
    assert contar(escenario.ruta, "movimientos_cuenta", "tipo = 'COBRO'") == 1


def test_muchos_cobros_concurrentes_nunca_superan_el_saldo(escenario):
    _con_saldo(escenario, 500)

    resultados = _correr_en_paralelo(
        [lambda: servicio_cuenta_corriente.registrar_cobro(escenario.cliente.id, 100, escenario.cajera.id)] * 8
    )

    assert [r[0] for r in resultados].count("OK") == 5
    assert all(isinstance(r[1], CobroInvalidoError) for r in resultados if r[0] == "ERROR")
    assert repositorio_clientes.obtener_saldo(escenario.cliente.id) == 0
    assert contar(escenario.ruta, "movimientos_cuenta", "tipo = 'COBRO'") == 5
    assert contar(escenario.ruta, "caja_movimientos", "origen = 'COBRO_CUENTA'") == 5


def test_el_mismo_cobro_reenviado_en_paralelo_se_registra_una_sola_vez(escenario):
    _con_saldo(escenario, 500)

    resultados = _correr_en_paralelo(
        [lambda: servicio_cuenta_corriente.registrar_cobro(
            escenario.cliente.id, 200, escenario.cajera.id, clave_idempotencia="doble-clic")] * 6
    )

    assert all(r[0] == "OK" for r in resultados)
    assert len({r[1].id for r in resultados}) == 1
    assert repositorio_clientes.obtener_saldo(escenario.cliente.id) == 300
    assert contar(escenario.ruta, "caja_movimientos", "origen = 'COBRO_CUENTA'") == 1
    assert contar(escenario.ruta, "movimientos_cuenta", "tipo = 'COBRO'") == 1
    assert contar(escenario.ruta, "auditoria", "accion = 'COBRO_CUENTA'") == 1


def test_dos_ventas_a_cuenta_por_la_ultima_unidad_solo_una_gana(escenario):
    resultados = _correr_en_paralelo(
        [lambda: servicio_ventas.registrar_venta(
            [ItemVenta(escenario.producto.id, 1)], CC, usuario_id=escenario.cajera.id,
            cliente_id=escenario.cliente.id)] * 2
    )

    assert [r[0] for r in resultados].count("OK") == 1
    assert any(isinstance(r[1], StockInsuficienteError) for r in resultados if r[0] == "ERROR")
    assert servicio_stock.obtener_por_id(escenario.producto.id).stock_actual == 0
    assert contar(escenario.ruta, "ventas") == 1
    assert contar(escenario.ruta, "movimientos_cuenta") == 1
    assert repositorio_clientes.obtener_saldo(escenario.cliente.id) == 500


def test_la_misma_venta_a_cuenta_reenviada_en_paralelo_se_registra_una_sola_vez(escenario):
    resultados = _correr_en_paralelo(
        [lambda: servicio_ventas.registrar_venta(
            [ItemVenta(escenario.producto.id, 1)], CC, clave_idempotencia="doble-clic",
            usuario_id=escenario.cajera.id, cliente_id=escenario.cliente.id)] * 5
    )

    assert all(r[0] == "OK" for r in resultados)
    assert len({r[1].id for r in resultados}) == 1
    assert contar(escenario.ruta, "ventas") == 1
    assert contar(escenario.ruta, "movimientos_cuenta") == 1
    assert servicio_stock.obtener_por_id(escenario.producto.id).stock_actual == 0


def test_cobro_y_cierre_de_caja_concurrentes_quedan_consistentes(escenario):
    _con_saldo(escenario, 500)

    resultados = _correr_en_paralelo(
        [
            lambda: servicio_cuenta_corriente.registrar_cobro(escenario.cliente.id, 200, escenario.cajera.id),
            lambda: servicio_caja.cerrar_caja(0),
        ]
    )

    cobro, cierre = resultados
    assert cierre[0] == "OK"
    sesion = consultar(escenario.ruta, "SELECT * FROM sesiones_caja ORDER BY id DESC LIMIT 1")[0]
    ingresos = contar(escenario.ruta, "caja_movimientos", "origen = 'COBRO_CUENTA'")
    if cobro[0] == "OK":
        assert ingresos == 1
        assert sesion["diferencia_centavos"] == -200  # contado 0 contra 200 esperados: el cierre vio el cobro
    else:
        assert isinstance(cobro[1], CajaCerradaError)
        assert ingresos == 0
        assert sesion["diferencia_centavos"] == 0
    assert contar(escenario.ruta, "movimientos_cuenta", "tipo = 'COBRO'") == ingresos


def test_desactivar_y_vender_a_cuenta_concurrentes_nunca_dejan_un_inactivo_con_saldo(escenario):
    resultados = _correr_en_paralelo(
        [
            lambda: servicio_clientes.desactivar_cliente(escenario.cliente.id, escenario.owner.id),
            lambda: servicio_ventas.registrar_venta(
                [ItemVenta(escenario.producto.id, 1)], CC, usuario_id=escenario.cajera.id,
                cliente_id=escenario.cliente.id),
        ]
    )

    desactivar, vender = resultados
    cliente = servicio_clientes.obtener_cliente(escenario.cliente.id)
    saldo = repositorio_clientes.obtener_saldo(escenario.cliente.id)
    if desactivar[0] == "OK":
        assert cliente.activo is False and saldo == 0
        assert isinstance(vender[1], ClienteInactivoError)
    else:
        assert isinstance(desactivar[1], ClienteConSaldoError)
        assert cliente.activo is True and saldo == 500
        assert vender[0] == "OK"


def _reproducir_libro(ruta, cliente_id) -> list[int]:
    """Saldo acumulado tras cada movimiento del libro, en el orden real en que SQLite los confirmó."""
    saldos, saldo = [], 0
    for fila in consultar(ruta, "SELECT tipo, monto_centavos FROM movimientos_cuenta WHERE cliente_id = ? ORDER BY id",
                          (cliente_id,)):
        saldo += fila["monto_centavos"] if fila["tipo"] == "CARGO" else -fila["monto_centavos"]
        saldos.append(saldo)
    return saldos


RONDAS_VENTA_Y_COBRO = 8


@pytest.mark.parametrize("monto_cobro", [600, 200])
def test_venta_a_cuenta_y_cobro_concurrentes_del_mismo_cliente_se_serializan(escenario, monto_cobro):
    """Cliente con saldo 500; en paralelo, una venta a cuenta de 500 y un cobro. Un cobro de 600 solo cabe
    si la venta se confirmó antes; uno de 200 cabe en cualquier orden. El test no asume quién gana: lo
    deduce del libro. Se repite en varias rondas (cada una con un cliente nuevo) para que ambos órdenes
    tengan chances reales de ocurrir."""
    producto = crear_producto("7790000000077", precio_centavos=500, stock=2 * RONDAS_VENTA_Y_COBRO)
    for ronda in range(RONDAS_VENTA_Y_COBRO):
        cliente = servicio_clientes.crear_cliente(f"Cliente {ronda}", escenario.owner.id)
        servicio_ventas.registrar_venta(
            [ItemVenta(producto.id, 1)], CC, usuario_id=escenario.cajera.id, cliente_id=cliente.id
        )  # saldo inicial: 500

        venta, cobro = _correr_en_paralelo(
            [
                lambda: servicio_ventas.registrar_venta(
                    [ItemVenta(producto.id, 1)], CC, usuario_id=escenario.cajera.id, cliente_id=cliente.id),
                lambda: servicio_cuenta_corriente.registrar_cobro(cliente.id, monto_cobro, escenario.cajera.id),
            ]
        )

        assert venta[0] == "OK", (ronda, venta)  # hay stock y caja abierta: la venta nunca puede fallar
        tipos = [f["tipo"] for f in consultar(
            escenario.ruta, "SELECT tipo FROM movimientos_cuenta WHERE cliente_id = ? ORDER BY id", (cliente.id,))]
        if cobro[0] == "OK":
            assert tipos.count("COBRO") == 1
            if monto_cobro > 500:  # solo pudo entrar después del CARGO de la venta concurrente
                assert tipos == ["CARGO", "CARGO", "COBRO"], (ronda, tipos)
        else:
            assert isinstance(cobro[1], CobroInvalidoError), (ronda, cobro)
            assert monto_cobro > 500  # solo se rechaza si corrió antes que la venta y no cabía
            assert tipos == ["CARGO", "CARGO"], (ronda, tipos)

        saldos = _reproducir_libro(escenario.ruta, cliente.id)
        assert all(s >= 0 for s in saldos), (ronda, saldos)  # nunca saldo negativo, en ningún punto del libro
        assert saldos[-1] == repositorio_clientes.obtener_saldo(cliente.id)
        assert saldos[-1] == 1000 - (monto_cobro if cobro[0] == "OK" else 0)

    # Sin estado parcial en todo el conjunto de rondas.
    assert servicio_stock.obtener_por_id(producto.id).stock_actual == 0
    assert contar(escenario.ruta, "ventas", "tipo_pago = 'CUENTA_CORRIENTE'") == 2 * RONDAS_VENTA_Y_COBRO
    assert violaciones_de_invariantes(escenario.ruta) == []
