"""Cobros de cuenta corriente (migración 020): reglas, integración con la caja, auditoría
única, idempotencia y atomicidad (rollback en cada paso)."""

import pytest

from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import caja as repositorio_caja
from db.repositorios import clientes as repositorio_clientes
from domain.venta import ItemVenta
from excepciones import (
    CajaCerradaError,
    ClaveIdempotenciaReutilizadaError,
    ClienteNoEncontradoError,
    CobroInvalidoError,
    DatosInvalidosError,
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
    forzar_cliente_inactivo,
    violaciones_de_invariantes,
)

SALDO_INICIAL = 500


@pytest.fixture
def escenario(base_datos_temporal, caja_abierta):
    """Caja abierta (fondo 0) y un cliente con saldo de 500 por una venta a cuenta."""
    owner = crear_owner()
    cajera = crear_usuario("cajera", "CASHIER")
    cliente = servicio_clientes.crear_cliente("Ana", owner.id)
    producto = crear_producto(precio_centavos=SALDO_INICIAL, stock=10)
    servicio_ventas.registrar_venta(
        [ItemVenta(producto.id, 1)], "CUENTA_CORRIENTE", usuario_id=cajera.id, cliente_id=cliente.id
    )
    return type("Escenario", (), {"ruta": base_datos_temporal, "owner": owner, "cajera": cajera, "cliente": cliente})


def _cobrar(e, monto=200, usuario=None, **kwargs):
    return servicio_cuenta_corriente.registrar_cobro(e.cliente.id, monto, (usuario or e.cajera).id, **kwargs)


def _estado(e) -> dict:
    """Todo lo que un cobro puede tocar: si falla, esto no debe cambiar."""
    return {
        "caja_movimientos": contar(e.ruta, "caja_movimientos"),
        "ingresos_cobro": contar(e.ruta, "caja_movimientos", "origen = 'COBRO_CUENTA'"),
        "movimientos_cuenta": contar(e.ruta, "movimientos_cuenta"),
        "saldo": repositorio_clientes.obtener_saldo(e.cliente.id),
        "efectivo_estimado": servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos,
        "auditoria": contar(e.ruta, "auditoria"),
    }


# --- caso feliz -----------------------------------------------------------------------


def test_cobro_parcial_genera_ingreso_de_caja_y_cobro_de_cuenta(escenario):
    cobro = _cobrar(escenario, 200, descripcion="  entrega  ")

    assert (cobro.tipo, cobro.monto_centavos, cobro.cliente_id, cobro.descripcion) == (
        "COBRO", 200, escenario.cliente.id, "entrega"
    )
    ingreso = consultar(escenario.ruta, "SELECT * FROM caja_movimientos WHERE id = ?", (cobro.caja_movimiento_id,))[0]
    assert (ingreso["tipo"], ingreso["origen"], ingreso["monto_centavos"], ingreso["usuario_id"]) == (
        "INGRESO", "COBRO_CUENTA", 200, escenario.cajera.id
    )
    assert repositorio_clientes.obtener_saldo(escenario.cliente.id) == 300


def test_cobro_total_deja_saldo_cero_y_un_nuevo_cobro_se_rechaza(escenario):
    _cobrar(escenario, SALDO_INICIAL)

    assert repositorio_clientes.obtener_saldo(escenario.cliente.id) == 0
    with pytest.raises(CobroInvalidoError):
        _cobrar(escenario, 1)


def test_cobros_parciales_sucesivos_suman_hasta_el_saldo(escenario):
    _cobrar(escenario, 100)
    _cobrar(escenario, 150)
    _cobrar(escenario, 250)

    assert repositorio_clientes.obtener_saldo(escenario.cliente.id) == 0
    movimientos = servicio_clientes.listar_movimientos_cuenta(escenario.cliente.id)
    assert [(m.tipo, m.monto_centavos) for m in movimientos] == [
        ("CARGO", 500), ("COBRO", 100), ("COBRO", 150), ("COBRO", 250)
    ]


@pytest.mark.parametrize("rol", ["OWNER", "CASHIER"])
def test_owner_y_cashier_pueden_cobrar(escenario, rol):
    usuario = escenario.owner if rol == "OWNER" else escenario.cajera

    assert _cobrar(escenario, 100, usuario=usuario).usuario_id == usuario.id


def test_el_cobro_suma_al_efectivo_estimado_de_la_caja(escenario):
    antes = servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos

    _cobrar(escenario, 200)

    assert servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos == antes + 200


def test_cerrar_la_caja_tras_un_cobro_incluye_el_cobro_en_el_esperado(escenario):
    _cobrar(escenario, 200)

    cierre = servicio_caja.cerrar_caja(200)

    assert cierre.diferencia_centavos == 0  # fondo 0 + cobro 200 = 200 esperados


# --- auditoría única -----------------------------------------------------------------------


def test_el_cobro_se_audita_solo_como_cobro_cuenta_y_no_como_caja_ingreso(escenario):
    cobro = _cobrar(escenario, 200)

    cobros = consultar(escenario.ruta, "SELECT * FROM auditoria WHERE accion = 'COBRO_CUENTA'")
    assert [(f["entidad"], f["entidad_id"], f["usuario_id"]) for f in cobros] == [
        ("CLIENTE", escenario.cliente.id, escenario.cajera.id)
    ]
    assert contar(escenario.ruta, "auditoria", "accion = 'CAJA_INGRESO'") == 0
    assert cobro.caja_movimiento_id is not None


def test_un_ingreso_manual_sigue_auditandose_como_caja_ingreso(escenario):
    servicio_caja.registrar_ingreso(50, "aporte", usuario_id=escenario.cajera.id)

    assert contar(escenario.ruta, "auditoria", "accion = 'CAJA_INGRESO'") == 1
    assert contar(escenario.ruta, "caja_movimientos", "origen = 'COBRO_CUENTA'") == 0


def test_el_ingreso_manual_nunca_es_cobro_cuenta(escenario):
    ingreso = servicio_caja.registrar_ingreso(50, "aporte", usuario_id=escenario.cajera.id)

    assert ingreso.origen == "MANUAL"
    assert repositorio_caja.listar_movimientos()[-1].origen == "MANUAL"


# --- reglas comerciales ---------------------------------------------------------------------


@pytest.mark.parametrize("monto", [0, -1, -500])
def test_monto_cero_o_negativo_se_rechaza(escenario, monto):
    antes = _estado(escenario)

    with pytest.raises(CobroInvalidoError):
        _cobrar(escenario, monto)

    assert _estado(escenario) == antes


def test_monto_mayor_al_saldo_se_rechaza(escenario):
    antes = _estado(escenario)

    with pytest.raises(CobroInvalidoError, match="saldo"):
        _cobrar(escenario, SALDO_INICIAL + 1)

    assert _estado(escenario) == antes


@pytest.mark.parametrize("medio", ["TARJETA", "TRANSFERENCIA", "OTRO", "efectivo"])
def test_solo_se_cobra_en_efectivo(escenario, medio):
    antes = _estado(escenario)

    with pytest.raises(CobroInvalidoError, match="efectivo"):
        _cobrar(escenario, 100, medio_pago=medio)

    assert _estado(escenario) == antes


@pytest.mark.sin_caja_abierta
def test_cobro_sin_caja_abierta_se_rechaza(base_datos_temporal):
    owner = crear_owner()
    cliente = servicio_clientes.crear_cliente("Ana", owner.id)

    with pytest.raises(CajaCerradaError):
        servicio_cuenta_corriente.registrar_cobro(cliente.id, 100, owner.id)

    assert contar(base_datos_temporal, "caja_movimientos") == 0
    assert contar(base_datos_temporal, "movimientos_cuenta") == 0


def test_cobro_con_la_caja_cerrada_despues_de_la_venta_se_rechaza_sin_dejar_nada(escenario):
    servicio_caja.cerrar_caja(0)
    antes = contar(escenario.ruta, "movimientos_cuenta"), contar(escenario.ruta, "caja_movimientos")

    with pytest.raises(CajaCerradaError):
        _cobrar(escenario, 100)

    assert (contar(escenario.ruta, "movimientos_cuenta"), contar(escenario.ruta, "caja_movimientos")) == antes


def test_cobro_a_un_cliente_inexistente_se_rechaza(escenario):
    antes = _estado(escenario)

    with pytest.raises(ClienteNoEncontradoError):
        servicio_cuenta_corriente.registrar_cobro(999, 100, escenario.cajera.id)

    assert _estado(escenario) == antes


# --- cliente inactivo: con deuda puede pagarla; sin deuda no cobra por la regla de saldo --------------


def test_cliente_inactivo_con_deuda_puede_pagarla_e_igual_genera_ingreso_cobro_y_auditoria(escenario):
    forzar_cliente_inactivo(escenario.ruta, escenario.cliente.id)
    efectivo_antes = servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos

    cobro = _cobrar(escenario, 200)

    ingreso = consultar(escenario.ruta, "SELECT * FROM caja_movimientos WHERE id = ?", (cobro.caja_movimiento_id,))[0]
    assert (ingreso["tipo"], ingreso["origen"], ingreso["monto_centavos"]) == ("INGRESO", "COBRO_CUENTA", 200)
    assert (cobro.tipo, cobro.monto_centavos, cobro.cliente_id) == ("COBRO", 200, escenario.cliente.id)
    assert repositorio_clientes.obtener_saldo(escenario.cliente.id) == SALDO_INICIAL - 200
    assert servicio_clientes.obtener_cliente(escenario.cliente.id).activo is False  # el cobro no lo reactiva
    assert servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos == efectivo_antes + 200
    assert contar(escenario.ruta, "auditoria", "accion = 'COBRO_CUENTA'") == 1
    assert contar(escenario.ruta, "auditoria", "accion = 'CAJA_INGRESO'") == 0
    assert violaciones_de_invariantes(escenario.ruta) == []


def test_cliente_inactivo_con_deuda_puede_pagarla_en_cobros_parciales_y_total(escenario):
    forzar_cliente_inactivo(escenario.ruta, escenario.cliente.id)

    _cobrar(escenario, 100)
    _cobrar(escenario, SALDO_INICIAL - 100)

    assert repositorio_clientes.obtener_saldo(escenario.cliente.id) == 0
    assert servicio_clientes.obtener_cliente(escenario.cliente.id).activo is False
    assert violaciones_de_invariantes(escenario.ruta) == []


def test_cliente_inactivo_cuya_deuda_ya_se_pago_no_puede_cobrar_mas(escenario):
    forzar_cliente_inactivo(escenario.ruta, escenario.cliente.id)
    _cobrar(escenario, SALDO_INICIAL)
    antes = _estado(escenario)

    with pytest.raises(CobroInvalidoError, match="saldo"):
        _cobrar(escenario, 1)

    assert _estado(escenario) == antes


def test_cliente_inactivo_sin_deuda_se_rechaza_por_la_regla_de_saldo(escenario):
    sin_deuda = servicio_clientes.crear_cliente("Beto", escenario.owner.id)
    servicio_clientes.desactivar_cliente(sin_deuda.id, escenario.owner.id)  # baja normal: saldo 0
    antes = _estado(escenario)

    with pytest.raises(CobroInvalidoError, match="saldo"):
        servicio_cuenta_corriente.registrar_cobro(sin_deuda.id, 100, escenario.cajera.id)

    assert _estado(escenario) == antes


def test_cobro_de_cliente_inactivo_con_deuda_es_idempotente(escenario):
    forzar_cliente_inactivo(escenario.ruta, escenario.cliente.id)

    primero = _cobrar(escenario, 200, clave_idempotencia="c-inactivo")
    segundo = _cobrar(escenario, 200, clave_idempotencia="c-inactivo")

    assert segundo == primero
    assert contar(escenario.ruta, "caja_movimientos", "origen = 'COBRO_CUENTA'") == 1
    assert repositorio_clientes.obtener_saldo(escenario.cliente.id) == SALDO_INICIAL - 200


def test_cobro_de_cliente_inactivo_con_deuda_es_atomico_si_falla_la_auditoria(escenario, monkeypatch):
    forzar_cliente_inactivo(escenario.ruta, escenario.cliente.id)
    original = repositorio_auditoria.registrar_en_conexion

    def auditar_o_fallar(conexion, usuario_id, accion, *resto):
        if accion == "COBRO_CUENTA":
            raise RuntimeError("falla simulada durante la auditoría")
        return original(conexion, usuario_id, accion, *resto)

    antes = _estado(escenario)
    with monkeypatch.context() as parche:
        parche.setattr(repositorio_auditoria, "registrar_en_conexion", auditar_o_fallar)
        with pytest.raises(RuntimeError, match="durante la auditoría"):
            _cobrar(escenario, 200)

    assert _estado(escenario) == antes
    assert contar(escenario.ruta, "caja_movimientos", "origen = 'COBRO_CUENTA'") == 0
    assert _cobrar(escenario, 100).monto_centavos == 100  # y sigue pudiendo pagar


def test_cobro_exige_un_usuario_activo(escenario):
    inactivo = crear_usuario("baja", "CASHIER", activo=False)
    antes = _estado(escenario)

    with pytest.raises(PermisoDenegadoError):
        _cobrar(escenario, 100, usuario=inactivo)
    with pytest.raises(PermisoDenegadoError):
        servicio_cuenta_corriente.registrar_cobro(escenario.cliente.id, 100, 9999)

    assert _estado(escenario) == antes


def test_descripcion_del_cobro_acotada_a_250(escenario):
    _cobrar(escenario, 100, descripcion="a" * 250)
    antes = _estado(escenario)

    with pytest.raises(DatosInvalidosError):
        _cobrar(escenario, 100, descripcion="a" * 251)

    assert _estado(escenario) == antes


# --- idempotencia ------------------------------------------------------------------------------


def test_reintento_con_la_misma_clave_no_duplica_ingreso_ni_cobro(escenario):
    primero = _cobrar(escenario, 200, clave_idempotencia="c-1")
    segundo = _cobrar(escenario, 200, clave_idempotencia="c-1")

    assert segundo == primero
    assert contar(escenario.ruta, "caja_movimientos", "origen = 'COBRO_CUENTA'") == 1
    assert contar(escenario.ruta, "movimientos_cuenta", "tipo = 'COBRO'") == 1
    assert repositorio_clientes.obtener_saldo(escenario.cliente.id) == 300
    assert contar(escenario.ruta, "auditoria", "accion = 'COBRO_CUENTA'") == 1


def test_misma_clave_con_otro_contenido_se_rechaza(escenario):
    otro = servicio_clientes.crear_cliente("Beto", escenario.owner.id)
    _cobrar(escenario, 200, clave_idempotencia="c-1", descripcion="uno")
    antes = _estado(escenario)

    with pytest.raises(ClaveIdempotenciaReutilizadaError):
        _cobrar(escenario, 100, clave_idempotencia="c-1", descripcion="uno")  # otro monto
    with pytest.raises(ClaveIdempotenciaReutilizadaError):
        _cobrar(escenario, 200, clave_idempotencia="c-1", descripcion="dos")  # otra descripción
    with pytest.raises(ClaveIdempotenciaReutilizadaError):
        servicio_cuenta_corriente.registrar_cobro(
            otro.id, 200, escenario.cajera.id, descripcion="uno", clave_idempotencia="c-1"
        )  # otro cliente

    assert _estado(escenario) == antes


def test_reintento_de_un_cobro_ya_registrado_devuelve_el_original_con_la_caja_cerrada(escenario):
    original = _cobrar(escenario, 200, clave_idempotencia="c-1")
    servicio_caja.cerrar_caja(200)
    antes = _estado(escenario)

    reintento = _cobrar(escenario, 200, clave_idempotencia="c-1")

    assert reintento == original
    assert _estado(escenario) == antes


def test_un_cobro_rechazado_no_consume_la_clave(escenario):
    with pytest.raises(CobroInvalidoError):
        _cobrar(escenario, SALDO_INICIAL + 1, clave_idempotencia="c-1")

    cobro = _cobrar(escenario, 100, clave_idempotencia="c-1")

    assert cobro.monto_centavos == 100


def test_cobros_sin_clave_no_chocan_entre_si(escenario):
    _cobrar(escenario, 100)
    _cobrar(escenario, 100)

    assert contar(escenario.ruta, "movimientos_cuenta", "tipo = 'COBRO'") == 2


# --- atomicidad: rollback en cada paso ----------------------------------------------------------------


def _assert_no_quedo_nada(escenario, antes):
    assert _estado(escenario) == antes  # ingreso, cobro, saldo, arqueo y auditoría intactos
    assert contar(escenario.ruta, "caja_movimientos", "origen = 'COBRO_CUENTA'") == 0
    assert contar(escenario.ruta, "movimientos_cuenta", "tipo = 'COBRO'") == 0
    assert contar(escenario.ruta, "auditoria", "accion = 'COBRO_CUENTA'") == 0
    # Y la operación sigue funcionando: no quedó ningún lock ni estado a medias.
    assert _cobrar(escenario, 100).monto_centavos == 100


def test_rollback_si_falla_despues_del_ingreso(escenario, monkeypatch):
    original = repositorio_caja.registrar_movimiento_en_conexion

    def ingresar_y_fallar(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("falla simulada después del INGRESO")

    antes = _estado(escenario)
    with monkeypatch.context() as parche:
        parche.setattr(repositorio_caja, "registrar_movimiento_en_conexion", ingresar_y_fallar)
        with pytest.raises(RuntimeError, match="después del INGRESO"):
            _cobrar(escenario, 200, clave_idempotencia="c-1")

    _assert_no_quedo_nada(escenario, antes)


def test_rollback_si_el_esquema_rechaza_la_creacion_del_cobro(escenario, monkeypatch):
    original = repositorio_clientes.registrar_cobro_en_conexion

    def cobro_con_otro_monto(conexion, cliente_id, monto_centavos, *resto):
        return original(conexion, cliente_id, monto_centavos + 1, *resto)  # el trigger exige monto == ingreso

    antes = _estado(escenario)
    with monkeypatch.context() as parche:
        parche.setattr(repositorio_clientes, "registrar_cobro_en_conexion", cobro_con_otro_monto)
        with pytest.raises(ErrorBaseDatos, match="cobro debe respaldarse"):
            _cobrar(escenario, 200, clave_idempotencia="c-1")

    _assert_no_quedo_nada(escenario, antes)


def test_rollback_si_falla_al_intentar_crear_el_cobro(escenario, monkeypatch):
    def fallar(*_args, **_kwargs):
        raise RuntimeError("falla simulada al crear el COBRO")

    antes = _estado(escenario)
    with monkeypatch.context() as parche:
        parche.setattr(repositorio_clientes, "registrar_cobro_en_conexion", fallar)
        with pytest.raises(RuntimeError, match="al crear el COBRO"):
            _cobrar(escenario, 200)

    _assert_no_quedo_nada(escenario, antes)


def test_rollback_si_falla_despues_del_cobro(escenario, monkeypatch):
    original = repositorio_clientes.registrar_cobro_en_conexion

    def cobrar_y_fallar(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("falla simulada después del COBRO")

    antes = _estado(escenario)
    with monkeypatch.context() as parche:
        parche.setattr(repositorio_clientes, "registrar_cobro_en_conexion", cobrar_y_fallar)
        with pytest.raises(RuntimeError, match="después del COBRO"):
            _cobrar(escenario, 200, clave_idempotencia="c-1")

    _assert_no_quedo_nada(escenario, antes)


def test_rollback_si_falla_durante_la_auditoria(escenario, monkeypatch):
    original = repositorio_auditoria.registrar_en_conexion

    def auditar_o_fallar(conexion, usuario_id, accion, *resto):
        if accion == "COBRO_CUENTA":
            raise RuntimeError("falla simulada durante la auditoría")
        return original(conexion, usuario_id, accion, *resto)

    antes = _estado(escenario)
    with monkeypatch.context() as parche:
        parche.setattr(repositorio_auditoria, "registrar_en_conexion", auditar_o_fallar)
        with pytest.raises(RuntimeError, match="durante la auditoría"):
            _cobrar(escenario, 200, clave_idempotencia="c-1")

    _assert_no_quedo_nada(escenario, antes)
