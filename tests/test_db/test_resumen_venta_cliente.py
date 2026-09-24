"""`ResumenVenta` con cliente (021): `cliente_id`/`cliente_nombre` en `listar_resumen` y
`obtener_resumen_por_id`, sin duplicar filas ni alterar la cantidad de líneas."""

import pytest

from db.repositorios import ventas as repositorio_ventas
from domain.venta import ItemVenta, ResumenVenta
from services import servicio_clientes, servicio_ventas
from tests.utilidades_clientes import crear_owner, crear_producto

pytestmark = pytest.mark.usefixtures("caja_abierta")

CC = "CUENTA_CORRIENTE"


@pytest.fixture
def e(base_datos_temporal):
    owner = crear_owner()
    return type(
        "Escenario",
        (),
        {
            "owner": owner,
            "cliente": servicio_clientes.crear_cliente("Ana Gómez", owner.id),
            "p1": crear_producto("7790000000001", 100, 50),
            "p2": crear_producto("7790000000002", 250, 50),
        },
    )


def test_los_campos_nuevos_van_al_final_con_default_y_no_rompen_construcciones_previas():
    resumen = ResumenVenta(1, "2026-01-01 10:00:00", 100, "EFECTIVO", None, 1)  # forma posicional histórica

    assert (resumen.cliente_id, resumen.cliente_nombre) == (None, None)
    assert resumen.estado == "ACTIVA"


def test_venta_a_cuenta_informa_cliente_en_listado_y_detalle(e):
    venta = servicio_ventas.registrar_venta(
        [ItemVenta(e.p1.id, 1)], CC, usuario_id=e.owner.id, cliente_id=e.cliente.id
    )

    del_listado = repositorio_ventas.listar_resumen()[0]
    del_detalle = repositorio_ventas.obtener_resumen_por_id(venta.id)

    for resumen in (del_listado, del_detalle):
        assert (resumen.cliente_id, resumen.cliente_nombre) == (e.cliente.id, "Ana Gómez")
        assert resumen.tipo_pago == CC


def test_venta_normal_con_cliente_tambien_lo_informa(e):
    venta = servicio_ventas.registrar_venta(
        [ItemVenta(e.p1.id, 1)], "EFECTIVO", usuario_id=e.owner.id, cliente_id=e.cliente.id
    )

    resumen = repositorio_ventas.obtener_resumen_por_id(venta.id)

    assert (resumen.cliente_id, resumen.cliente_nombre) == (e.cliente.id, "Ana Gómez")


def test_venta_historica_o_sin_cliente_queda_con_none(e):
    venta = servicio_ventas.registrar_venta([ItemVenta(e.p1.id, 1)], "EFECTIVO")

    assert (repositorio_ventas.listar_resumen()[0].cliente_id, repositorio_ventas.listar_resumen()[0].cliente_nombre) == (
        None,
        None,
    )
    resumen = repositorio_ventas.obtener_resumen_por_id(venta.id)
    assert (resumen.cliente_id, resumen.cliente_nombre) == (None, None)


def test_venta_anulada_conserva_el_cliente_y_los_datos_de_anulacion(e):
    venta = servicio_ventas.registrar_venta(
        [ItemVenta(e.p1.id, 1)], "EFECTIVO", usuario_id=e.owner.id, cliente_id=e.cliente.id
    )
    servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=e.owner.id)

    resumen = repositorio_ventas.obtener_resumen_por_id(venta.id)

    assert resumen.estado == "ANULADA"
    assert resumen.motivo_anulacion == "ERROR_CARGA"
    assert (resumen.cliente_id, resumen.cliente_nombre) == (e.cliente.id, "Ana Gómez")


def test_el_join_con_clientes_no_duplica_filas_ni_altera_la_cantidad_de_lineas(e):
    servicio_ventas.registrar_venta(
        [ItemVenta(e.p1.id, 2), ItemVenta(e.p2.id, 1)], CC, usuario_id=e.owner.id, cliente_id=e.cliente.id
    )
    servicio_ventas.registrar_venta([ItemVenta(e.p1.id, 1)], CC, usuario_id=e.owner.id, cliente_id=e.cliente.id)
    servicio_ventas.registrar_venta([ItemVenta(e.p2.id, 1)], "EFECTIVO")

    resumenes = repositorio_ventas.listar_resumen()

    assert len(resumenes) == 3  # una fila por venta
    assert [r.cantidad_lineas for r in resumenes] == [1, 1, 2]  # más reciente primero; líneas intactas
    assert [r.total_centavos for r in resumenes] == [250, 100, 450]


def test_el_filtro_por_medio_de_pago_ya_encuentra_las_ventas_a_cuenta(e):
    servicio_ventas.registrar_venta([ItemVenta(e.p1.id, 1)], CC, usuario_id=e.owner.id, cliente_id=e.cliente.id)
    servicio_ventas.registrar_venta([ItemVenta(e.p1.id, 1)], "EFECTIVO")

    solo_a_cuenta = repositorio_ventas.listar_resumen(tipo_pago=CC)

    assert [r.tipo_pago for r in solo_a_cuenta] == [CC]
    assert solo_a_cuenta[0].cliente_nombre == "Ana Gómez"
