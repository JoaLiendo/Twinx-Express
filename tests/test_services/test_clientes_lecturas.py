"""Lecturas de clientes para la interfaz web (021): listado con saldo, búsqueda, buscador del POS
y estado de cuenta."""

import pytest

from db.repositorios import clientes as repositorio_clientes
from domain.venta import ItemVenta
from excepciones import ClienteNoEncontradoError, DatosInvalidosError
from services import servicio_clientes, servicio_cuenta_corriente, servicio_ventas
from tests.utilidades_clientes import crear_owner, crear_producto, crear_usuario

pytestmark = pytest.mark.usefixtures("caja_abierta")


@pytest.fixture
def e(base_datos_temporal):
    owner = crear_owner()
    cajera = crear_usuario("cajera", "CASHIER")
    return type("Escenario", (), {"owner": owner, "cajera": cajera, "producto": crear_producto(precio_centavos=250)})


def _cliente(e, nombre, telefono=None):
    return servicio_clientes.crear_cliente(nombre, e.owner.id, telefono=telefono)


def _vender_a_cuenta(e, cliente, cantidad=1):
    return servicio_ventas.registrar_venta(
        [ItemVenta(e.producto.id, cantidad)], "CUENTA_CORRIENTE", usuario_id=e.cajera.id, cliente_id=cliente.id
    )


# --- listado con saldo -----------------------------------------------------------------------------


def test_por_defecto_lista_solo_activos_ordenados_por_nombre_sin_distinguir_mayusculas(e):
    _cliente(e, "beto")
    _cliente(e, "Ana")
    inactivo = _cliente(e, "Carlos")
    servicio_clientes.desactivar_cliente(inactivo.id, e.owner.id)

    assert [c.cliente.nombre for c in servicio_clientes.listar_clientes_con_saldo()] == ["Ana", "beto"]


def test_filtro_de_estado_activos_inactivos_y_todos(e):
    activo = _cliente(e, "Ana")
    inactivo = _cliente(e, "Beto")
    servicio_clientes.desactivar_cliente(inactivo.id, e.owner.id)

    ids = lambda estado: [c.cliente.id for c in servicio_clientes.listar_clientes_con_saldo(estado=estado)]  # noqa: E731

    assert ids("activos") == [activo.id]
    assert ids("inactivos") == [inactivo.id]
    assert ids("todos") == [activo.id, inactivo.id]


def test_estado_de_listado_invalido_se_rechaza(e):
    with pytest.raises(DatosInvalidosError):
        servicio_clientes.listar_clientes_con_saldo(estado="borrados")


def test_el_listado_informa_el_saldo_de_cada_cliente(e):
    con_deuda, sin_deuda = _cliente(e, "Ana"), _cliente(e, "Beto")
    _vender_a_cuenta(e, con_deuda, 3)
    servicio_cuenta_corriente.registrar_cobro(con_deuda.id, 250, e.cajera.id)

    saldos = {c.cliente.nombre: c.saldo_centavos for c in servicio_clientes.listar_clientes_con_saldo()}

    assert saldos == {"Ana": 500, "Beto": 0}
    assert saldos["Ana"] == repositorio_clientes.obtener_saldo(con_deuda.id)


def test_un_cliente_sin_movimientos_aparece_con_saldo_cero(e):
    _cliente(e, "Ana")

    assert [c.saldo_centavos for c in servicio_clientes.listar_clientes_con_saldo()] == [0]


# --- búsqueda -----------------------------------------------------------------------------------------


def test_busca_por_nombre_y_por_telefono_con_coincidencia_parcial(e):
    ana = _cliente(e, "Ana Gómez", "11-5555-1234")
    beto = _cliente(e, "Beto", "11-4444-9999")

    por_nombre = servicio_clientes.listar_clientes_con_saldo("gómez")
    por_telefono = servicio_clientes.listar_clientes_con_saldo("4444")

    assert [c.cliente.id for c in por_nombre] == [ana.id]
    assert [c.cliente.id for c in por_telefono] == [beto.id]
    assert servicio_clientes.listar_clientes_con_saldo("zzz") == []


def test_los_comodines_like_se_escapan_como_texto_literal(e):
    porcentaje = _cliente(e, "100% Leche")
    guion_bajo = _cliente(e, "Snack_Bar")
    _cliente(e, "Otro cliente")
    _cliente(e, "Snack Bar")

    assert [c.cliente.id for c in servicio_clientes.listar_clientes_con_saldo("%")] == [porcentaje.id]
    assert [c.cliente.id for c in servicio_clientes.listar_clientes_con_saldo("_")] == [guion_bajo.id]
    assert [c.cliente.id for c in servicio_clientes.listar_clientes_con_saldo("k_B")] == [guion_bajo.id]
    assert [c.cliente.id for c in servicio_clientes.listar_clientes_con_saldo("\\")] == []


def test_la_busqueda_respeta_el_filtro_de_estado(e):
    inactivo = _cliente(e, "Ana Vieja")
    servicio_clientes.desactivar_cliente(inactivo.id, e.owner.id)
    activo = _cliente(e, "Ana Nueva")

    assert [c.cliente.id for c in servicio_clientes.listar_clientes_con_saldo("ana")] == [activo.id]
    assert [c.cliente.id for c in servicio_clientes.listar_clientes_con_saldo("ana", "inactivos")] == [inactivo.id]


# --- buscador del POS ------------------------------------------------------------------------------------


def test_el_buscador_del_pos_solo_devuelve_activos_con_su_saldo(e):
    activo = _cliente(e, "Ana")
    inactivo = _cliente(e, "Ana Inactiva")
    servicio_clientes.desactivar_cliente(inactivo.id, e.owner.id)
    _vender_a_cuenta(e, activo)

    resultado = servicio_clientes.buscar_clientes_activos_para_venta("ana")

    assert [(c.cliente.id, c.saldo_centavos) for c in resultado] == [(activo.id, 250)]


def test_el_buscador_del_pos_devuelve_como_maximo_20(e):
    for numero in range(25):
        _cliente(e, f"Cliente {numero:02d}")

    assert len(servicio_clientes.buscar_clientes_activos_para_venta("")) == 20
    assert len(servicio_clientes.buscar_clientes_activos_para_venta("Cliente")) == 20
    assert len(servicio_clientes.buscar_clientes_activos_para_venta("Cliente", limite=500)) == 20  # el tope no se supera
    assert len(servicio_clientes.buscar_clientes_activos_para_venta("Cliente", limite=5)) == 5


# --- estado de cuenta -------------------------------------------------------------------------------------


def test_estado_de_cuenta_informa_totales_y_movimientos_del_mas_reciente_al_mas_antiguo(e):
    cliente = _cliente(e, "Ana")
    primera = _vender_a_cuenta(e, cliente, 2)  # +500
    servicio_cuenta_corriente.registrar_cobro(cliente.id, 200, e.cajera.id, descripcion="entrega")
    segunda = _vender_a_cuenta(e, cliente, 1)  # +250

    estado = servicio_clientes.obtener_estado_de_cuenta(cliente.id)

    assert estado.cliente.id == cliente.id
    assert (estado.resumen.total_cargos_centavos, estado.resumen.total_cobros_centavos) == (750, 200)
    assert estado.resumen.saldo_centavos == 550
    assert [(m.tipo, m.monto_centavos) for m in estado.movimientos] == [("CARGO", 250), ("COBRO", 200), ("CARGO", 500)]
    assert [m.venta_id for m in estado.movimientos] == [segunda.id, None, primera.id]
    assert estado.movimientos[1].descripcion == "entrega"


def test_el_saldo_del_estado_de_cuenta_nunca_diverge_del_repositorio(e):
    cliente = _cliente(e, "Ana")
    _vender_a_cuenta(e, cliente, 3)
    servicio_cuenta_corriente.registrar_cobro(cliente.id, 300, e.cajera.id)

    resumen = servicio_clientes.obtener_estado_de_cuenta(cliente.id).resumen

    assert resumen.saldo_centavos == repositorio_clientes.obtener_saldo(cliente.id)
    assert resumen.saldo_centavos == resumen.total_cargos_centavos - resumen.total_cobros_centavos


def test_estado_de_cuenta_de_un_cliente_sin_movimientos_es_cero(e):
    cliente = _cliente(e, "Ana")

    estado = servicio_clientes.obtener_estado_de_cuenta(cliente.id)

    assert (estado.resumen.saldo_centavos, estado.resumen.total_cargos_centavos, estado.resumen.total_cobros_centavos) == (0, 0, 0)
    assert estado.movimientos == []


def test_estado_de_cuenta_de_un_cliente_inexistente_falla(e):
    with pytest.raises(ClienteNoEncontradoError):
        servicio_clientes.obtener_estado_de_cuenta(999)
