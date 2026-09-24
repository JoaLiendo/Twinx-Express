"""Venta a cuenta corriente (migración 020): reglas comerciales, atomicidad, idempotencia,
no anulación y arqueo."""

import hashlib
import json

import pytest

from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import clientes as repositorio_clientes
from domain.venta import TIPOS_PAGO_VALIDOS, ItemVenta, calcular_hash_contenido
from excepciones import (
    CajaCerradaError,
    ClaveIdempotenciaReutilizadaError,
    ClienteInactivoError,
    ClienteNoEncontradoError,
    DatosInvalidosError,
    PermisoDenegadoError,
    StockInsuficienteError,
    VentaACuentaNoAnulableError,
)
from services import servicio_caja, servicio_clientes, servicio_stock, servicio_ventas
from tests.utilidades_clientes import (
    consultar,
    contar,
    crear_owner,
    crear_producto,
    crear_usuario,
    forzar_cliente_inactivo,
)

CC = "CUENTA_CORRIENTE"


@pytest.fixture
def escenario(base_datos_temporal, caja_abierta):
    owner = crear_owner()
    cajera = crear_usuario("cajera", "CASHIER")
    cliente = servicio_clientes.crear_cliente("Ana", owner.id)
    producto = crear_producto(precio_centavos=200, stock=10)
    return type("Escenario", (), {"ruta": base_datos_temporal, "owner": owner, "cajera": cajera,
                                  "cliente": cliente, "producto": producto})


def _vender(e, cantidad=1, tipo=CC, cliente=True, usuario=None, clave=None):
    return servicio_ventas.registrar_venta(
        [ItemVenta(e.producto.id, cantidad)],
        tipo,
        clave_idempotencia=clave,
        usuario_id=(usuario or e.cajera).id,
        cliente_id=e.cliente.id if cliente else None,
    )


def _estado(e) -> dict:
    """Todo lo que una venta a cuenta puede tocar: si falla, esto no debe cambiar."""
    return {
        "ventas": contar(e.ruta, "ventas"),
        "detalle": contar(e.ruta, "detalle_venta"),
        "movimientos_cuenta": contar(e.ruta, "movimientos_cuenta"),
        "caja_movimientos": contar(e.ruta, "caja_movimientos"),
        "auditoria": contar(e.ruta, "auditoria"),
        "stock": servicio_stock.obtener_por_id(e.producto.id).stock_actual,
        "saldo": repositorio_clientes.obtener_saldo(e.cliente.id),
    }


# --- caso feliz -------------------------------------------------------------------------


def test_venta_a_cuenta_genera_venta_detalle_cargo_y_descuenta_stock(escenario):
    venta = _vender(escenario, cantidad=3)

    assert (venta.tipo_pago, venta.total_centavos, venta.estado) == (CC, 600, "ACTIVA")
    fila = consultar(escenario.ruta, "SELECT cliente_id, usuario_id FROM ventas WHERE id = ?", (venta.id,))[0]
    assert (fila["cliente_id"], fila["usuario_id"]) == (escenario.cliente.id, escenario.cajera.id)
    cargos = repositorio_clientes.listar_movimientos_cuenta(escenario.cliente.id)
    assert [(m.tipo, m.monto_centavos, m.venta_id, m.caja_movimiento_id) for m in cargos] == [
        ("CARGO", 600, venta.id, None)
    ]
    assert repositorio_clientes.obtener_saldo(escenario.cliente.id) == 600
    assert servicio_stock.obtener_por_id(escenario.producto.id).stock_actual == 7
    assert contar(escenario.ruta, "detalle_venta") == 1


@pytest.mark.parametrize("rol", ["OWNER", "CASHIER"])
def test_owner_y_cashier_pueden_vender_a_cuenta(escenario, rol):
    usuario = escenario.owner if rol == "OWNER" else escenario.cajera

    venta = _vender(escenario, usuario=usuario)

    assert venta.tipo_pago == CC


def test_el_saldo_acumula_ventas_y_es_independiente_por_cliente(escenario):
    otro = servicio_clientes.crear_cliente("Beto", escenario.owner.id)
    _vender(escenario, 1)
    _vender(escenario, 2)
    servicio_ventas.registrar_venta(
        [ItemVenta(escenario.producto.id, 1)], CC, usuario_id=escenario.cajera.id, cliente_id=otro.id
    )

    assert repositorio_clientes.obtener_saldo(escenario.cliente.id) == 600
    assert repositorio_clientes.obtener_saldo(otro.id) == 200


def test_la_venta_a_cuenta_se_audita_una_sola_vez(escenario):
    venta = _vender(escenario)

    filas = consultar(escenario.ruta, "SELECT * FROM auditoria WHERE accion = 'VENTA_A_CUENTA'")
    assert [(f["entidad"], f["entidad_id"], f["usuario_id"]) for f in filas] == [
        ("VENTA", venta.id, escenario.cajera.id)
    ]


def test_la_venta_a_cuenta_no_mueve_el_efectivo_pero_si_el_total_vendido(escenario):
    _vender(escenario, 2)  # 400 a cuenta
    servicio_ventas.registrar_venta([ItemVenta(escenario.producto.id, 1)], "EFECTIVO")  # 200 en efectivo

    arqueo = servicio_caja.calcular_arqueo_de_sesion()

    assert arqueo.total_vendido_centavos == 600
    assert arqueo.total_efectivo_ventas_centavos == 200
    assert arqueo.efectivo_estimado_centavos == 200  # fondo 0 + 200 en efectivo; nada de la venta a cuenta


# --- reglas comerciales -------------------------------------------------------------------


def test_venta_a_cuenta_sin_cliente_se_rechaza_sin_tocar_nada(escenario):
    antes = _estado(escenario)

    with pytest.raises(DatosInvalidosError, match="requiere un cliente"):
        _vender(escenario, cliente=False)

    assert _estado(escenario) == antes


def test_cliente_inexistente_se_rechaza_sin_tocar_nada(escenario):
    antes = _estado(escenario)

    with pytest.raises(ClienteNoEncontradoError):
        servicio_ventas.registrar_venta(
            [ItemVenta(escenario.producto.id, 1)], CC, usuario_id=escenario.cajera.id, cliente_id=999
        )

    assert _estado(escenario) == antes


@pytest.mark.parametrize("tipo_pago", [CC, "EFECTIVO", "TARJETA"])
def test_cliente_inactivo_se_rechaza_en_cualquier_venta_nueva(escenario, tipo_pago):
    servicio_clientes.desactivar_cliente(escenario.cliente.id, escenario.owner.id)
    antes = _estado(escenario)

    with pytest.raises(ClienteInactivoError):
        _vender(escenario, tipo=tipo_pago)

    assert _estado(escenario) == antes


@pytest.mark.parametrize("tipo_pago", [CC, "EFECTIVO"])
def test_cliente_inactivo_con_deuda_sigue_sin_poder_recibir_ventas_nuevas(escenario, tipo_pago):
    _vender(escenario)  # deja saldo pendiente
    forzar_cliente_inactivo(escenario.ruta, escenario.cliente.id)  # inactivo con deuda: solo puede pagarla
    antes = _estado(escenario)

    with pytest.raises(ClienteInactivoError):
        _vender(escenario, tipo=tipo_pago)

    assert _estado(escenario) == antes


def test_cliente_activo_se_asocia_a_una_venta_normal_sin_generar_cargo(escenario):
    venta = _vender(escenario, tipo="EFECTIVO")

    assert consultar(escenario.ruta, "SELECT cliente_id FROM ventas WHERE id = ?", (venta.id,))[0][0] == (
        escenario.cliente.id
    )
    assert contar(escenario.ruta, "movimientos_cuenta") == 0
    assert repositorio_clientes.obtener_saldo(escenario.cliente.id) == 0


def test_una_venta_normal_sin_cliente_queda_con_cliente_null(escenario):
    venta = _vender(escenario, tipo="EFECTIVO", cliente=False)

    assert consultar(escenario.ruta, "SELECT cliente_id FROM ventas WHERE id = ?", (venta.id,))[0][0] is None


def test_venta_a_cuenta_sin_stock_no_deja_nada(escenario):
    antes = _estado(escenario)

    with pytest.raises(StockInsuficienteError):
        _vender(escenario, cantidad=11)

    assert _estado(escenario) == antes


@pytest.mark.sin_caja_abierta
def test_venta_a_cuenta_sin_caja_abierta_se_rechaza(base_datos_temporal):
    owner = crear_owner()
    cliente = servicio_clientes.crear_cliente("Ana", owner.id)
    producto = crear_producto(stock=10)

    with pytest.raises(CajaCerradaError):
        servicio_ventas.registrar_venta(
            [ItemVenta(producto.id, 1)], CC, usuario_id=owner.id, cliente_id=cliente.id
        )

    assert contar(base_datos_temporal, "ventas") == 0
    assert contar(base_datos_temporal, "movimientos_cuenta") == 0
    assert servicio_stock.obtener_por_id(producto.id).stock_actual == 10


def test_venta_a_cuenta_exige_un_usuario_activo(escenario):
    inactivo = crear_usuario("baja", "CASHIER", activo=False)
    antes = _estado(escenario)

    with pytest.raises(PermisoDenegadoError):
        servicio_ventas.registrar_venta(
            [ItemVenta(escenario.producto.id, 1)], CC, cliente_id=escenario.cliente.id
        )  # sin usuario
    with pytest.raises(PermisoDenegadoError):
        _vender(escenario, usuario=inactivo)

    assert _estado(escenario) == antes


def test_cuenta_corriente_nunca_es_el_pago_predeterminado_ni_una_opcion_del_pos():
    assert CC not in TIPOS_PAGO_VALIDOS
    assert sorted(TIPOS_PAGO_VALIDOS)[0] == "EFECTIVO"


def test_el_hash_de_una_venta_sin_cliente_no_cambia():
    items = [ItemVenta(1, 2), ItemVenta(2, 1)]
    canonico = json.dumps({"tipo_pago": "EFECTIVO", "items": [(1, 2), (2, 1)]}, sort_keys=True, separators=(",", ":"))

    assert calcular_hash_contenido(items, "EFECTIVO") == hashlib.sha256(canonico.encode()).hexdigest()
    assert calcular_hash_contenido(items, "EFECTIVO", 5) != calcular_hash_contenido(items, "EFECTIVO")


# --- anulación -------------------------------------------------------------------------------


def test_una_venta_a_cuenta_no_se_anula_y_no_cambia_nada(escenario):
    venta = _vender(escenario, 2)
    antes = _estado(escenario)

    with pytest.raises(VentaACuentaNoAnulableError):
        servicio_ventas.anular_venta(venta.id, "ERROR_CARGA", None, escenario.owner.id)

    assert _estado(escenario) == antes
    assert consultar(escenario.ruta, "SELECT estado FROM ventas WHERE id = ?", (venta.id,))[0][0] == "ACTIVA"


def test_una_venta_normal_con_cliente_si_se_anula(escenario):
    venta = _vender(escenario, tipo="EFECTIVO")

    anulada = servicio_ventas.anular_venta(venta.id, "ERROR_CARGA", None, escenario.owner.id)

    assert anulada.estado == "ANULADA"
    assert servicio_stock.obtener_por_id(escenario.producto.id).stock_actual == 10


# --- atomicidad ---------------------------------------------------------------------------------


def test_falla_al_crear_el_cargo_revierte_venta_detalle_y_stock(escenario, monkeypatch):
    def fallar(*_args, **_kwargs):
        raise RuntimeError("falla simulada al crear el CARGO")

    monkeypatch.setattr(repositorio_clientes, "registrar_cargo_en_conexion", fallar)
    antes = _estado(escenario)

    with pytest.raises(RuntimeError, match="CARGO"):
        _vender(escenario, cantidad=2)

    assert _estado(escenario) == antes


def test_falla_despues_del_cargo_revierte_todo(escenario, monkeypatch):
    original = repositorio_clientes.registrar_cargo_en_conexion

    def cargar_y_fallar(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("falla simulada después del CARGO")

    monkeypatch.setattr(repositorio_clientes, "registrar_cargo_en_conexion", cargar_y_fallar)
    antes = _estado(escenario)

    with pytest.raises(RuntimeError, match="después del CARGO"):
        _vender(escenario, cantidad=2)

    assert _estado(escenario) == antes


def test_falla_en_la_auditoria_revierte_todo(escenario, monkeypatch):
    def fallar(*_args, **_kwargs):
        raise RuntimeError("falla simulada en la auditoría")

    monkeypatch.setattr(repositorio_auditoria, "registrar_en_conexion", fallar)
    antes = _estado(escenario)

    with pytest.raises(RuntimeError, match="auditoría"):
        _vender(escenario, cantidad=2)

    assert _estado(escenario) == antes


def test_un_cargo_rechazado_por_el_esquema_revierte_la_venta(escenario, monkeypatch):
    original = repositorio_clientes.registrar_cargo_en_conexion

    def cargo_con_otro_monto(conexion, cliente_id, monto_centavos, venta_id, usuario_id, descripcion):
        return original(conexion, cliente_id, monto_centavos + 1, venta_id, usuario_id, descripcion)

    monkeypatch.setattr(repositorio_clientes, "registrar_cargo_en_conexion", cargo_con_otro_monto)
    antes = _estado(escenario)

    with pytest.raises(Exception, match="cargo debe respaldarse"):
        _vender(escenario, cantidad=2)

    assert _estado(escenario) == antes


# --- idempotencia ------------------------------------------------------------------------------------


def test_reintento_con_la_misma_clave_no_duplica_venta_cargo_ni_stock(escenario):
    primera = _vender(escenario, 2, clave="k-1")
    segunda = _vender(escenario, 2, clave="k-1")

    assert segunda.id == primera.id
    assert contar(escenario.ruta, "ventas") == 1
    assert contar(escenario.ruta, "movimientos_cuenta") == 1
    assert servicio_stock.obtener_por_id(escenario.producto.id).stock_actual == 8
    assert repositorio_clientes.obtener_saldo(escenario.cliente.id) == 400
    assert contar(escenario.ruta, "auditoria", "accion = 'VENTA_A_CUENTA'") == 1


def test_misma_clave_con_otro_contenido_se_rechaza(escenario):
    otro = servicio_clientes.crear_cliente("Beto", escenario.owner.id)
    _vender(escenario, 2, clave="k-1")
    antes = _estado(escenario)

    with pytest.raises(ClaveIdempotenciaReutilizadaError):
        _vender(escenario, 3, clave="k-1")  # otra cantidad
    with pytest.raises(ClaveIdempotenciaReutilizadaError):
        servicio_ventas.registrar_venta(
            [ItemVenta(escenario.producto.id, 2)], CC, clave_idempotencia="k-1",
            usuario_id=escenario.cajera.id, cliente_id=otro.id,
        )  # otro cliente
    with pytest.raises(ClaveIdempotenciaReutilizadaError):
        _vender(escenario, 2, tipo="EFECTIVO", clave="k-1")  # otro tipo de pago

    assert _estado(escenario) == antes


def test_reintento_de_una_venta_a_cuenta_ya_registrada_funciona_con_la_caja_cerrada(escenario):
    primera = _vender(escenario, clave="k-1")
    servicio_caja.cerrar_caja(0)

    segunda = _vender(escenario, clave="k-1")

    assert segunda.id == primera.id
    assert contar(escenario.ruta, "movimientos_cuenta") == 1


def test_una_venta_a_cuenta_rechazada_no_consume_la_clave(escenario):
    servicio_clientes.desactivar_cliente(escenario.cliente.id, escenario.owner.id)
    with pytest.raises(ClienteInactivoError):
        _vender(escenario, clave="k-1")
    servicio_clientes.reactivar_cliente(escenario.cliente.id, escenario.owner.id)

    venta = _vender(escenario, clave="k-1")

    assert venta.id is not None and contar(escenario.ruta, "movimientos_cuenta") == 1
