"""V1.14-D: cobros con imputación FIFO persistida y excepción manual del OWNER (migración 039).

El cobro crea el INGRESO de caja, el COBRO del libro y sus imputaciones en una sola transacción. FIFO
por defecto, exacto y persistido (una imputación vieja no cambia aunque después cambie todo lo demás);
la imputación manual es solo OWNER, con observación, auditada y marcada. Nunca hay sobrepago ni saldo a
favor, ni con dos cobros simultáneos ni con doble envío.
"""

import sqlite3
import threading

import pytest

from db.repositorios import clientes as repositorio_clientes
from db.repositorios import obligaciones as repositorio_obligaciones
from domain.cliente import Obligacion, imputar_fifo, validar_imputacion_manual
from domain.venta import ItemVenta
from excepciones import (
    ClaveIdempotenciaReutilizadaError,
    CobroInvalidoError,
    ErrorBaseDatos,
    PermisoDenegadoError,
)
from services import servicio_caja, servicio_clientes, servicio_cuenta_corriente, servicio_ventas
from tests.utilidades_clientes import (
    consultar,
    contar,
    crear_owner,
    crear_producto,
    crear_usuario,
    escribir,
    habilitar_credito_general,
    violaciones_de_invariantes,
)

CC = "CUENTA_CORRIENTE"
MIL = 100_000  # $1.000


@pytest.fixture
def e(base_datos_temporal, caja_abierta):
    owner = crear_owner()
    habilitar_credito_general(owner.id)
    cajera = crear_usuario("cajera", "CASHIER")
    cliente = servicio_clientes.crear_cliente("Ana", owner.id)
    producto = crear_producto(precio_centavos=MIL, stock=1000)
    escenario = type("E", (), {"ruta": base_datos_temporal, "owner": owner, "cajera": cajera,
                               "cliente": cliente, "producto": producto})
    # Obligaciones A = $10.000, B = $20.000, C = $30.000 (en ese orden).
    escenario.ventas = [
        servicio_ventas.registrar_venta([ItemVenta(producto.id, unidades)], CC, usuario_id=cajera.id, cliente_id=cliente.id)
        for unidades in (10, 20, 30)
    ]
    escenario.obligaciones = [repositorio_obligaciones.obtener_por_venta(v.id) for v in escenario.ventas]
    return escenario


def _cobrar(e, monto, usuario=None, **extra):
    return servicio_cuenta_corriente.registrar_cobro(e.cliente.id, monto, (usuario or e.cajera).id, **extra)


def _saldos(e) -> list[int]:
    return [repositorio_obligaciones.obtener_por_venta(v.id).saldo_pendiente_centavos for v in e.ventas]


def _imputaciones(e, cobro_id) -> list[tuple]:
    return [(f["venta_id"], f["monto_centavos"], f["tipo"]) for f in consultar(
        e.ruta,
        "SELECT o.venta_id, i.monto_centavos, i.tipo FROM imputaciones_cobro i JOIN obligaciones_cliente o"
        " ON o.id = i.obligacion_id WHERE i.movimiento_cobro_id = ? ORDER BY i.id",
        (cobro_id,),
    )]


# --- FIFO ---------------------------------------------------------------------------------------------------


def test_ejemplo_del_enunciado_25000_paga_a_y_15000_de_b(e):
    cobro = _cobrar(e, 25 * MIL)
    a, b, _ = e.ventas
    assert _imputaciones(e, cobro.id) == [(a.id, 10 * MIL, "FIFO"), (b.id, 15 * MIL, "FIFO")]
    assert _saldos(e) == [0, 5 * MIL, 30 * MIL]
    assert violaciones_de_invariantes(e.ruta) == []


def test_pago_exacto_de_una_obligacion_la_salda_y_no_toca_las_demas(e):
    _cobrar(e, 10 * MIL)
    assert _saldos(e) == [0, 20 * MIL, 30 * MIL]
    assert repositorio_obligaciones.obtener_por_venta(e.ventas[0].id).saldada


def test_pago_parcial_deja_saldo_exacto(e):
    _cobrar(e, 1)
    assert _saldos(e) == [10 * MIL - 1, 20 * MIL, 30 * MIL]


def test_pago_que_cubre_varias_y_luego_el_total(e):
    _cobrar(e, 35 * MIL)
    assert _saldos(e) == [0, 0, 25 * MIL]
    _cobrar(e, 25 * MIL)
    assert _saldos(e) == [0, 0, 0] and repositorio_clientes.obtener_saldo(e.cliente.id) == 0
    assert violaciones_de_invariantes(e.ruta) == []


def test_una_imputacion_historica_no_cambia_despues(e):
    primero = _cobrar(e, 15 * MIL)
    antes = _imputaciones(e, primero.id)
    _cobrar(e, 40 * MIL)
    servicio_ventas.registrar_venta([ItemVenta(e.producto.id, 1)], CC, usuario_id=e.cajera.id, cliente_id=e.cliente.id)
    assert _imputaciones(e, primero.id) == antes
    for sql in ("UPDATE imputaciones_cobro SET monto_centavos = 1", "DELETE FROM imputaciones_cobro"):
        with pytest.raises(sqlite3.IntegrityError):
            escribir(e.ruta, sql)


def test_una_venta_nueva_no_se_cobra_antes_que_las_viejas(e):
    nueva = servicio_ventas.registrar_venta([ItemVenta(e.producto.id, 1)], CC, usuario_id=e.cajera.id, cliente_id=e.cliente.id)
    cobro = _cobrar(e, 60 * MIL)
    assert nueva.id not in [venta_id for venta_id, _, _ in _imputaciones(e, cobro.id)]


# --- sobrepago, idempotencia, rollback ------------------------------------------------------------------------


def test_no_se_paga_mas_que_la_deuda_total(e):
    antes = (contar(e.ruta, "caja_movimientos"), contar(e.ruta, "imputaciones_cobro"))
    with pytest.raises(CobroInvalidoError, match="supera"):
        _cobrar(e, 60 * MIL + 1)
    assert (contar(e.ruta, "caja_movimientos"), contar(e.ruta, "imputaciones_cobro")) == antes


def test_doble_envio_es_un_solo_cobro_con_sus_imputaciones(e):
    primero = _cobrar(e, 25 * MIL, clave_idempotencia="c-1")
    segundo = _cobrar(e, 25 * MIL, clave_idempotencia="c-1")
    assert primero.id == segundo.id
    assert contar(e.ruta, "imputaciones_cobro") == 2 and _saldos(e) == [0, 5 * MIL, 30 * MIL]


def test_misma_clave_con_otro_reparto_es_uso_indebido(e):
    _cobrar(e, 10 * MIL, clave_idempotencia="c-1")
    with pytest.raises(ClaveIdempotenciaReutilizadaError):
        _cobrar(e, 10 * MIL, usuario=e.owner, clave_idempotencia="c-1",
                imputacion_manual={e.obligaciones[2].id: 10 * MIL}, observacion_imputacion="x")


def test_si_falla_la_imputacion_no_queda_ingreso_de_caja_ni_cobro(e, monkeypatch):
    efectivo = servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos
    antes = (contar(e.ruta, "caja_movimientos"), contar(e.ruta, "movimientos_cuenta"), _saldos(e))

    def fallar(*args, **kwargs):
        raise ErrorBaseDatos("falla simulada al imputar")

    monkeypatch.setattr(repositorio_obligaciones, "registrar_imputacion_en_conexion", fallar)
    with pytest.raises(ErrorBaseDatos):
        _cobrar(e, 25 * MIL)
    assert (contar(e.ruta, "caja_movimientos"), contar(e.ruta, "movimientos_cuenta"), _saldos(e)) == antes
    assert servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos == efectivo


def test_dos_cobros_simultaneos_por_casi_toda_la_deuda_solo_uno_pasa(e):
    barrera = threading.Barrier(2)
    resultados = []

    def cobrar():
        barrera.wait(timeout=10)
        try:
            resultados.append(_cobrar(e, 40 * MIL))
        except CobroInvalidoError as error:
            resultados.append(error)

    hilos = [threading.Thread(target=cobrar) for _ in range(2)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=30)
    assert sorted(type(r).__name__ for r in resultados) == ["CobroInvalidoError", "MovimientoCuenta"]
    assert repositorio_clientes.obtener_saldo(e.cliente.id) == 20 * MIL
    assert violaciones_de_invariantes(e.ruta) == []


# --- imputación manual (excepción OWNER) ----------------------------------------------------------------------


def test_owner_imputa_a_mano_con_observacion_y_queda_marcado_y_auditado(e):
    _, _, c = e.obligaciones
    cobro = _cobrar(e, 12 * MIL, usuario=e.owner, imputacion_manual={c.id: 12 * MIL},
                    observacion_imputacion="El cliente discute la venta A")
    assert _imputaciones(e, cobro.id) == [(e.ventas[2].id, 12 * MIL, "MANUAL")]
    assert _saldos(e) == [10 * MIL, 20 * MIL, 18 * MIL]
    observacion = consultar(e.ruta, "SELECT observacion, usuario_id FROM imputaciones_cobro WHERE tipo = 'MANUAL'")[0]
    assert (observacion["observacion"], observacion["usuario_id"]) == ("El cliente discute la venta A", e.owner.id)
    auditoria = consultar(e.ruta, "SELECT resumen FROM auditoria WHERE accion = 'COBRO_IMPUTACION_MANUAL'")
    assert len(auditoria) == 1 and "excepción" in auditoria[0]["resumen"]
    assert violaciones_de_invariantes(e.ruta) == []


def test_cashier_no_puede_imputar_a_mano(e):
    with pytest.raises(PermisoDenegadoError):
        _cobrar(e, 5 * MIL, imputacion_manual={e.obligaciones[2].id: 5 * MIL}, observacion_imputacion="quiero")
    assert contar(e.ruta, "imputaciones_cobro") == 0


@pytest.mark.parametrize(
    ("reparto", "observacion", "mensaje"),
    [
        (lambda o: {o[2].id: 5 * MIL}, "", "observación"),
        (lambda o: {o[0].id: 4 * MIL}, "x", "exactamente"),
        (lambda o: {o[0].id: 11 * MIL}, "x", "supera su saldo"),
        (lambda o: {o[0].id: 0, o[1].id: 5 * MIL}, "x", "mayor a cero"),
        (lambda o: {9999: 5 * MIL}, "x", "no está pendiente"),
        (lambda o: {}, "x", "al menos una"),
    ],
)
def test_reparto_manual_invalido_no_deja_nada(e, reparto, observacion, mensaje):
    with pytest.raises(CobroInvalidoError, match=mensaje):
        _cobrar(e, 5 * MIL, usuario=e.owner, imputacion_manual=reparto(e.obligaciones), observacion_imputacion=observacion)
    assert contar(e.ruta, "movimientos_cuenta", "tipo = 'COBRO'") == 0


def test_manual_no_puede_apuntar_a_una_obligacion_de_otro_cliente(e):
    otro = servicio_clientes.crear_cliente("Beto", e.owner.id)
    venta = servicio_ventas.registrar_venta([ItemVenta(e.producto.id, 1)], CC, usuario_id=e.cajera.id, cliente_id=otro.id)
    ajena = repositorio_obligaciones.obtener_por_venta(venta.id)
    with pytest.raises(CobroInvalidoError, match="no está pendiente"):
        _cobrar(e, MIL, usuario=e.owner, imputacion_manual={ajena.id: MIL}, observacion_imputacion="x")


def test_el_esquema_rechaza_imputaciones_fabricadas(e):
    cobro = _cobrar(e, 10 * MIL)
    _, b, c = e.obligaciones
    otro = servicio_clientes.crear_cliente("Beto", e.owner.id)
    venta = servicio_ventas.registrar_venta([ItemVenta(e.producto.id, 1)], CC, usuario_id=e.cajera.id, cliente_id=otro.id)
    ajena = repositorio_obligaciones.obtener_por_venta(venta.id)
    insertar = ("INSERT INTO imputaciones_cobro (movimiento_cobro_id, obligacion_id, monto_centavos, tipo, usuario_id)"
                " VALUES (?, ?, ?, 'FIFO', ?)")
    for obligacion_id, monto, mensaje in ((b.id, 1, "superan el monto del cobro"),
                                          (ajena.id, 1, "mismo cliente"),
                                          (c.id, 31 * MIL, "supera el saldo")):
        with pytest.raises(sqlite3.IntegrityError, match=mensaje):
            escribir(e.ruta, insertar, (cobro.id, obligacion_id, monto, e.owner.id))
    with pytest.raises(sqlite3.IntegrityError):  # manual sin observación
        escribir(e.ruta, "INSERT INTO imputaciones_cobro (movimiento_cobro_id, obligacion_id, monto_centavos, tipo, usuario_id)"
                         " VALUES (?, ?, 1, 'MANUAL', ?)", (cobro.id, c.id, e.owner.id))
    assert violaciones_de_invariantes(e.ruta) == []


# --- dominio puro -----------------------------------------------------------------------------------------------


def _ob(id_, saldo):
    return Obligacion(id=id_, cliente_id=1, venta_id=id_, monto_original_centavos=saldo, saldo_pendiente_centavos=saldo,
                      fecha_origen="2026-01-01")


def test_imputar_fifo_es_puro_y_exacto():
    pendientes = [_ob(1, 100), _ob(2, 200), _ob(3, 300)]
    assert imputar_fifo(pendientes, 250) == [(1, 100), (2, 150)]
    assert imputar_fifo(pendientes, 600) == [(1, 100), (2, 200), (3, 300)]
    for monto in (0, -1, 601):
        with pytest.raises(CobroInvalidoError):
            imputar_fifo(pendientes, monto)


def test_reparto_manual_se_devuelve_en_orden_de_antiguedad():
    pendientes = [_ob(1, 100), _ob(2, 200)]
    assert validar_imputacion_manual(pendientes, {2: 50, 1: 10}, 60) == [(1, 10), (2, 50)]
