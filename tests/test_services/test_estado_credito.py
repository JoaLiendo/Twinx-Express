"""V1.14-E: estado financiero objetivo, cierres, strikes, veto y autorizaciones (migración 040).

Fechas controladas: las obligaciones se fabrican con su fecha de origen en el pasado (por el mismo camino
que una venta real: venta a cuenta + CARGO, cuyo trigger crea la obligación) y `servicio_credito.hoy` se
fija en cada paso. Se prueban los bordes 29/30/44/45/59/60/89/90, el paso de los días, pagos parciales y
totales, reaperturas, un segundo y un tercer episodio, y que ni el OWNER ni un POST/SQL manual rehabiliten
un crédito vetado.
"""

import sqlite3
import threading
from datetime import date, timedelta

import pytest

from domain.venta import ItemVenta
from excepciones import (
    CreditoNoDisponibleError,
    DatosInvalidosError,
    PermisoDenegadoError,
    ReaperturaCreditoInvalidaError,
)
from services import servicio_clientes, servicio_credito, servicio_cuenta_corriente, servicio_ventas
from tests.utilidades_clientes import (
    conectar,
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
D0 = date.today()


@pytest.fixture
def e(base_datos_temporal, caja_abierta, monkeypatch):
    owner = crear_owner()
    habilitar_credito_general(owner.id)
    escenario = type("E", (), {
        "ruta": base_datos_temporal, "owner": owner, "cajera": crear_usuario("cajera", "CASHIER"),
        "cliente": servicio_clientes.crear_cliente("Ana", owner.id),
        "producto": crear_producto(precio_centavos=1_000, stock=1_000),
    })

    def fijar_hoy(dia: date) -> None:
        monkeypatch.setattr(servicio_credito, "hoy", lambda: dia)

    escenario.fijar_hoy = fijar_hoy
    fijar_hoy(D0)
    return escenario


def _deuda(e, dias_atras: int, monto: int = 10_000, cliente=None) -> int:
    """Una venta a cuenta cuya obligación nació hace `dias_atras` días (respecto de D0). Devuelve su venta."""
    cliente = cliente or e.cliente
    sesion = consultar(e.ruta, "SELECT id FROM sesiones_caja WHERE estado = 'ABIERTA'")[0]["id"]
    fecha = f"{(D0 - timedelta(days=dias_atras)).isoformat()} 10:00:00"
    venta_id = consultar(e.ruta, "SELECT COALESCE(MAX(id), 0) + 1 AS n FROM ventas")[0]["n"]
    conexion = conectar(e.ruta)
    try:  # venta y CARGO juntos, como una venta real: si el libro rechaza el cargo no queda nada
        conexion.execute("INSERT INTO ventas (id, fecha, total_centavos, tipo_pago, usuario_id, sesion_caja_id, cliente_id)"
                         " VALUES (?, ?, ?, 'CUENTA_CORRIENTE', ?, ?, ?)", (venta_id, fecha, monto, e.cajera.id, sesion, cliente.id))
        conexion.execute("INSERT INTO movimientos_cuenta (fecha, cliente_id, tipo, monto_centavos, venta_id, usuario_id)"
                         " VALUES (?, ?, 'CARGO', ?, ?, ?)", (fecha, cliente.id, monto, venta_id, e.cajera.id))
        conexion.commit()
    finally:
        conexion.close()
    return venta_id


def _situacion(e, dias=0, cliente=None):
    e.fijar_hoy(D0 + timedelta(days=dias))
    return servicio_credito.situacion_crediticia((cliente or e.cliente).id)


def _vender_a_cuenta(e, usuario=None):
    return servicio_ventas.registrar_venta(
        [ItemVenta(e.producto.id, 1)], CC, usuario_id=(usuario or e.cajera).id, cliente_id=e.cliente.id
    )


def _cobrar_todo(e):
    saldo = servicio_credito.situacion_crediticia(e.cliente.id).saldo_centavos
    servicio_cuenta_corriente.registrar_cobro(e.cliente.id, saldo, e.cajera.id)


def _episodios(e):
    return consultar(e.ruta, "SELECT * FROM episodios_credito WHERE cliente_id = ? ORDER BY id", (e.cliente.id,))


# --- estado por antigüedad ---------------------------------------------------------------------------------


def test_sin_deuda_es_sin_deuda(e):
    assert _situacion(e).estado == "SIN_DEUDA"


@pytest.mark.parametrize(
    ("dias", "estado"),
    [(0, "NORMAL"), (29, "NORMAL"), (30, "REGULAR"), (44, "REGULAR"), (45, "DEMORADO"), (59, "DEMORADO"),
     (60, "CRITICA"), (89, "CRITICA"), (90, "CRITICA")],
)
def test_bordes_de_estado(e, dias, estado):
    _deuda(e, dias_atras=0)
    assert _situacion(e, dias=dias).estado == estado


def test_el_estado_lo_define_la_obligacion_pendiente_mas_antigua(e):
    _deuda(e, dias_atras=50, monto=1_000)
    _deuda(e, dias_atras=5, monto=50_000)
    assert _situacion(e).estado == "DEMORADO"
    servicio_cuenta_corriente.registrar_cobro(e.cliente.id, 1_000, e.cajera.id)  # salda la vieja (FIFO)
    assert _situacion(e).estado == "NORMAL"


def test_regular_puede_seguir_comprando_dentro_del_limite(e):
    _deuda(e, dias_atras=35)
    assert _vender_a_cuenta(e).estado == "ACTIVA"


# --- DEMORADO y autorización temporal ------------------------------------------------------------------------


def test_demorado_no_compra_a_cuenta_sin_autorizacion_pero_si_al_contado(e):
    _deuda(e, dias_atras=45)
    with pytest.raises(CreditoNoDisponibleError, match="autorización temporal"):
        _vender_a_cuenta(e)
    contado = servicio_ventas.registrar_venta([ItemVenta(e.producto.id, 1)], "EFECTIVO", usuario_id=e.cajera.id,
                                              cliente_id=e.cliente.id)
    assert contado.estado == "ACTIVA"


def test_owner_autoriza_y_el_cashier_opera_dentro_de_la_vigencia_y_no_despues(e):
    _deuda(e, dias_atras=45)
    servicio_credito.autorizar_demorado(e.cliente.id, "Paga el viernes", D0 + timedelta(days=5), e.owner.id)
    assert _vender_a_cuenta(e).estado == "ACTIVA"
    e.fijar_hoy(D0 + timedelta(days=5))  # último día vigente (inclusive)
    assert _vender_a_cuenta(e).estado == "ACTIVA"
    e.fijar_hoy(D0 + timedelta(days=6))
    with pytest.raises(CreditoNoDisponibleError, match="autorización temporal"):
        _vender_a_cuenta(e)
    auditoria = consultar(e.ruta, "SELECT resumen FROM auditoria WHERE accion = 'CREDITO_AUTORIZACION_DEMORADO'")
    assert len(auditoria) == 1 and "Paga el viernes" in auditoria[0]["resumen"]


def test_la_autorizacion_no_salva_un_cliente_que_ya_entro_en_critica(e):
    _deuda(e, dias_atras=55)
    servicio_credito.autorizar_demorado(e.cliente.id, "Promete pagar", D0 + timedelta(days=10), e.owner.id)
    e.fijar_hoy(D0 + timedelta(days=5))
    with pytest.raises(CreditoNoDisponibleError, match="cerrado|crítica"):
        _vender_a_cuenta(e)


@pytest.mark.parametrize(
    ("compromiso", "dias", "mensaje"),
    [("", 5, "compromiso"), ("x", 0, "posterior a hoy"), ("x", -1, "posterior a hoy"), ("x", 31, "como máximo")],
)
def test_autorizacion_invalida(e, compromiso, dias, mensaje):
    _deuda(e, dias_atras=45)
    with pytest.raises(DatosInvalidosError, match=mensaje):
        servicio_credito.autorizar_demorado(e.cliente.id, compromiso, D0 + timedelta(days=dias), e.owner.id)


def test_solo_se_autoriza_a_un_demorado(e):
    _deuda(e, dias_atras=10)
    with pytest.raises(DatosInvalidosError, match="DEMORADO"):
        servicio_credito.autorizar_demorado(e.cliente.id, "x", D0 + timedelta(days=5), e.owner.id)


def test_cashier_no_fabrica_una_autorizacion_ni_por_servicio_ni_por_sql(e):
    _deuda(e, dias_atras=45)
    with pytest.raises(PermisoDenegadoError):
        servicio_credito.autorizar_demorado(e.cliente.id, "x", D0 + timedelta(days=5), e.cajera.id)
    with pytest.raises(sqlite3.IntegrityError, match="OWNER"):
        escribir(e.ruta, "INSERT INTO autorizaciones_credito (cliente_id, compromiso, fecha_limite, usuario_id)"
                         " VALUES (?, 'x', ?, ?)", (e.cliente.id, (D0 + timedelta(days=5)).isoformat(), e.cajera.id))
    assert contar(e.ruta, "autorizaciones_credito") == 0


# --- CRÍTICA, cierre, reapertura -------------------------------------------------------------------------------


def test_entrar_en_critica_abre_un_solo_episodio_aunque_se_evalue_muchas_veces(e):
    _deuda(e, dias_atras=60)
    for _ in range(3):
        situacion = _situacion(e)
    assert situacion.credito_cerrado
    episodios = _episodios(e)
    assert len(episodios) == 1 and episodios[0]["fecha_inicio"] == D0.isoformat()
    assert contar(e.ruta, "auditoria", "accion = 'CREDITO_CIERRE'") == 1


def test_detectado_tarde_el_episodio_conserva_su_fecha_de_inicio_teorica(e):
    _deuda(e, dias_atras=75)
    _situacion(e)
    assert _episodios(e)[0]["fecha_inicio"] == (D0 - timedelta(days=15)).isoformat()


def test_en_critica_se_consulta_y_se_cobra_pero_no_se_aumenta_deuda(e):
    _deuda(e, dias_atras=60)
    with pytest.raises(CreditoNoDisponibleError):
        _vender_a_cuenta(e)
    servicio_cuenta_corriente.registrar_cobro(e.cliente.id, 1_000, e.cajera.id)
    assert servicio_credito.situacion_crediticia(e.cliente.id).saldo_centavos == 9_000


def test_pagar_todo_no_reabre_el_credito(e):
    _deuda(e, dias_atras=60)
    _situacion(e)
    _cobrar_todo(e)
    situacion = _situacion(e)
    assert (situacion.estado, situacion.credito_cerrado) == ("SIN_DEUDA", True)
    with pytest.raises(CreditoNoDisponibleError, match="cerrado"):
        _vender_a_cuenta(e)


def test_reapertura_explicita_del_owner_y_luego_vende(e):
    _deuda(e, dias_atras=60)
    _situacion(e)
    _cobrar_todo(e)
    servicio_credito.reabrir_credito(e.cliente.id, "Pagó todo, se le da otra oportunidad", e.owner.id)
    assert not _situacion(e).credito_cerrado
    assert _vender_a_cuenta(e).estado == "ACTIVA"
    episodio = _episodios(e)[0]
    assert (episodio["reabierto_por_usuario_id"], episodio["fecha_strike"]) == (e.owner.id, None)


def test_no_se_reabre_mientras_siga_en_critica_ni_sin_observacion_ni_siendo_cashier(e):
    _deuda(e, dias_atras=60)
    _situacion(e)
    with pytest.raises(ReaperturaCreditoInvalidaError, match="crítica"):
        servicio_credito.reabrir_credito(e.cliente.id, "x", e.owner.id)
    _cobrar_todo(e)
    with pytest.raises(DatosInvalidosError, match="observación"):
        servicio_credito.reabrir_credito(e.cliente.id, "  ", e.owner.id)
    with pytest.raises(PermisoDenegadoError):
        servicio_credito.reabrir_credito(e.cliente.id, "x", e.cajera.id)
    with pytest.raises(sqlite3.IntegrityError, match="OWNER"):
        escribir(e.ruta, "UPDATE episodios_credito SET fecha_reapertura = 'x', reabierto_por_usuario_id = ?,"
                         " observacion_reapertura = 'x'", (e.cajera.id,))


def test_sin_cierre_no_hay_nada_que_reabrir(e):
    with pytest.raises(ReaperturaCreditoInvalidaError, match="no tiene el crédito cerrado"):
        servicio_credito.reabrir_credito(e.cliente.id, "x", e.owner.id)


def test_dos_reaperturas_simultaneas_solo_una_registra(e):
    _deuda(e, dias_atras=60)
    _situacion(e)
    _cobrar_todo(e)
    barrera = threading.Barrier(2)
    resultados = []

    def reabrir():
        barrera.wait(timeout=10)
        try:
            resultados.append(servicio_credito.reabrir_credito(e.cliente.id, "ok", e.owner.id))
        except ReaperturaCreditoInvalidaError as error:
            resultados.append(error)

    hilos = [threading.Thread(target=reabrir) for _ in range(2)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=30)
    assert sum(isinstance(r, ReaperturaCreditoInvalidaError) for r in resultados) == 1
    assert contar(e.ruta, "auditoria", "accion = 'CREDITO_REAPERTURA'") == 1


# --- strikes --------------------------------------------------------------------------------------------------


def test_dia_89_sin_strike_dia_90_un_strike_y_no_uno_por_dia(e):
    _deuda(e, dias_atras=0)
    assert _situacion(e, dias=89).strikes == 0
    assert _situacion(e, dias=90).strikes == 1
    for dias in (91, 100, 150):
        assert _situacion(e, dias=dias).strikes == 1
    episodio = _episodios(e)[0]
    assert episodio["fecha_strike"] == (D0 + timedelta(days=90)).isoformat()
    assert contar(e.ruta, "auditoria", "accion = 'CREDITO_STRIKE'") == 1


def test_salir_de_critica_antes_del_dia_90_evita_el_strike(e):
    _deuda(e, dias_atras=0, monto=1_000)
    _deuda(e, dias_atras=-40, monto=1_000)  # una obligación 40 días más nueva
    _situacion(e, dias=62)  # entra en crítica por la vieja
    servicio_cuenta_corriente.registrar_cobro(e.cliente.id, 1_000, e.cajera.id)  # FIFO: salda la vieja
    situacion = _situacion(e, dias=90)  # la otra tiene 50 días: DEMORADO, no CRÍTICA
    assert (situacion.estado, situacion.strikes, situacion.credito_cerrado) == ("DEMORADO", 0, True)


def test_un_strike_detectado_tarde_igual_cuenta_y_se_registra_antes_de_cobrar(e):
    _deuda(e, dias_atras=0)
    e.fijar_hoy(D0 + timedelta(days=120))
    servicio_cuenta_corriente.registrar_cobro(e.cliente.id, 10_000, e.cajera.id)
    episodio = _episodios(e)[0]
    assert episodio["fecha_strike"] == (D0 + timedelta(days=90)).isoformat()


def test_dos_evaluaciones_simultaneas_no_duplican_cierre_ni_strike(e):
    _deuda(e, dias_atras=95)
    barrera = threading.Barrier(3)

    def evaluar():
        barrera.wait(timeout=10)
        servicio_credito.situacion_crediticia(e.cliente.id)

    hilos = [threading.Thread(target=evaluar) for _ in range(3)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=30)
    episodios = _episodios(e)
    assert len(episodios) == 1 and episodios[0]["fecha_strike"] is not None
    assert contar(e.ruta, "auditoria", "accion = 'CREDITO_STRIKE'") == 1


def _crisis_con_strike(e, inicio_dias: int) -> None:
    """Deuda nueva con origen en `inicio_dias`, que llega a 90 días (strike), se paga y se reabre."""
    _deuda(e, dias_atras=-inicio_dias)
    _situacion(e, dias=inicio_dias + 90)
    _cobrar_todo(e)
    situacion = servicio_credito.situacion_crediticia(e.cliente.id)
    if not situacion.vetado:
        servicio_credito.reabrir_credito(e.cliente.id, "nueva oportunidad", e.owner.id)


def test_segundo_episodio_meses_despues_suma_su_propio_strike(e):
    _crisis_con_strike(e, 0)
    _crisis_con_strike(e, 200)
    episodios = _episodios(e)
    assert [ep["fecha_strike"] is not None for ep in episodios] == [True, True]
    assert servicio_credito.situacion_crediticia(e.cliente.id).strikes == 2


def test_tres_strikes_veto_permanente_aunque_pague_todo_y_el_owner_quiera_rehabilitar(e):
    for inicio in (0, 200, 400):
        _crisis_con_strike(e, inicio)
    situacion = servicio_credito.situacion_crediticia(e.cliente.id)
    assert (situacion.strikes, situacion.vetado, situacion.saldo_centavos) == (3, True, 0)
    assert contar(e.ruta, "auditoria", "accion = 'CREDITO_VETO'") == 1
    with pytest.raises(ReaperturaCreditoInvalidaError, match="vetado"):
        servicio_credito.reabrir_credito(e.cliente.id, "lo perdono", e.owner.id)
    actual = servicio_credito.obtener_politica_individual(e.cliente.id)
    servicio_credito.configurar_politica("CLIENTE", "LIMITE", 10**9, e.owner.id, version_esperada=actual.version,
                                         cliente_id=e.cliente.id)
    with pytest.raises(CreditoNoDisponibleError, match="vetado"):
        _vender_a_cuenta(e, usuario=e.owner)
    # Al contado sigue comprando.
    assert servicio_ventas.registrar_venta([ItemVenta(e.producto.id, 1)], "EFECTIVO", usuario_id=e.cajera.id,
                                           cliente_id=e.cliente.id).estado == "ACTIVA"


def test_el_esquema_impide_rehabilitar_un_vetado_o_fabricar_historia(e):
    for inicio in (0, 200, 400):
        _crisis_con_strike(e, inicio)
    abierto = _episodios(e)[-1]
    with pytest.raises(sqlite3.IntegrityError, match="vetado"):
        escribir(e.ruta, "UPDATE episodios_credito SET fecha_reapertura = 'x', reabierto_por_usuario_id = ?,"
                         " observacion_reapertura = 'x' WHERE id = ?", (e.owner.id, abierto["id"]))
    with pytest.raises(sqlite3.IntegrityError):
        escribir(e.ruta, "UPDATE episodios_credito SET fecha_strike = NULL, strike_registrado_en = NULL WHERE id = ?",
                 (abierto["id"],))
    with pytest.raises(sqlite3.IntegrityError):
        escribir(e.ruta, "DELETE FROM episodios_credito")
    with pytest.raises(sqlite3.IntegrityError, match="vetado"):
        _deuda(e, dias_atras=-500)
    assert violaciones_de_invariantes(e.ruta) == []


def test_un_cliente_sin_cierre_puede_tener_un_cargo_pero_no_uno_cerrado_por_sql(e):
    _deuda(e, dias_atras=60)
    _situacion(e)
    with pytest.raises(sqlite3.IntegrityError, match="cerrado"):
        _deuda(e, dias_atras=0)
