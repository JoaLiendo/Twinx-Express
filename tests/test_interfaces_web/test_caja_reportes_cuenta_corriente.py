"""Caja, dashboard y reportes con cuenta corriente (021): el ingreso de un cobro se identifica como tal;
una venta a cuenta es una venta más en los reportes (con su medio de pago); un cobro NUNCA es una venta;
y no se agregó ningún indicador nuevo de deudores o cobranzas."""

import pytest

from domain.venta import ItemVenta
from services import servicio_caja, servicio_clientes, servicio_cuenta_corriente, servicio_reportes, servicio_stock
from services import servicio_ventas
from tests.utilidades_clientes import usuario_logueado

from ._asgi_cliente import solicitud

pytestmark = pytest.mark.usefixtures("caja_abierta")

CC = "CUENTA_CORRIENTE"
ETIQUETA_COBRO = "Cobro cta. cte."


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies_owner = usuario_logueado("OWNER", "duenio")
    cajera, cookies_cajera = usuario_logueado("CASHIER", "cajera")
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=50)
    cliente = servicio_clientes.crear_cliente("Ana", owner.id)
    return type(
        "Escenario",
        (),
        {"owner": owner, "cajera": cajera, "co": cookies_owner, "cc": cookies_cajera,
         "producto": producto, "cliente": cliente},
    )


def _a_cuenta(e, cantidad=1):
    return servicio_ventas.registrar_venta(
        [ItemVenta(e.producto.id, cantidad)], CC, usuario_id=e.cajera.id, cliente_id=e.cliente.id
    )


def _reporte():
    return servicio_reportes.generar_reporte_ventas("2000-01-01", "2099-12-31")


# --- caja -----------------------------------------------------------------------------------------------


def test_el_panel_de_caja_identifica_el_ingreso_de_un_cobro_y_no_un_ingreso_manual(e):
    _a_cuenta(e, 3)
    servicio_caja.registrar_ingreso(500, "aporte manual", usuario_id=e.cajera.id)
    servicio_cuenta_corriente.registrar_cobro(e.cliente.id, 200, e.cajera.id)

    texto = solicitud("GET", "/caja", cookies=e.cc).texto

    assert texto.count(ETIQUETA_COBRO) == 1
    assert "Cobro de cuenta corriente: Ana" in texto
    fila_manual = texto[texto.index("aporte manual") - 600 : texto.index("aporte manual")]
    assert ETIQUETA_COBRO not in fila_manual


def test_el_panel_de_caja_sin_cobros_no_muestra_la_etiqueta(e):
    servicio_caja.registrar_ingreso(500, "aporte", usuario_id=e.cajera.id)

    assert ETIQUETA_COBRO not in solicitud("GET", "/caja", cookies=e.cc).texto


def test_el_dashboard_identifica_el_cobro(e):
    _a_cuenta(e, 3)
    servicio_cuenta_corriente.registrar_cobro(e.cliente.id, 200, e.cajera.id)

    texto = solicitud("GET", "/", cookies=e.cc).texto

    assert texto.count(ETIQUETA_COBRO) == 1


def test_el_dashboard_sin_cobros_no_muestra_la_etiqueta(e):
    servicio_caja.registrar_ingreso(500, "aporte", usuario_id=e.cajera.id)

    assert ETIQUETA_COBRO not in solicitud("GET", "/", cookies=e.cc).texto


def test_un_cobro_suma_al_efectivo_de_la_caja_pero_no_es_una_venta_del_arqueo(e):
    _a_cuenta(e, 3)  # 600 centavos a cuenta
    antes = servicio_caja.calcular_arqueo_de_sesion()

    servicio_cuenta_corriente.registrar_cobro(e.cliente.id, 250, e.cajera.id)
    despues = servicio_caja.calcular_arqueo_de_sesion()

    assert despues.cantidad_ventas == antes.cantidad_ventas == 1
    assert despues.total_vendido_centavos == antes.total_vendido_centavos == 600
    assert despues.efectivo_estimado_centavos == antes.efectivo_estimado_centavos + 250


# --- reportes: la venta a cuenta cuenta como venta -------------------------------------------------------------


def test_una_venta_a_cuenta_cuenta_en_todas_las_metricas_de_ventas(e):
    servicio_ventas.registrar_venta([ItemVenta(e.producto.id, 1)], "EFECTIVO", usuario_id=e.cajera.id)  # 200
    _a_cuenta(e, 2)  # 400

    reporte = _reporte()

    assert reporte.cantidad_ventas == 2
    assert reporte.total_facturado_centavos == 600
    assert reporte.ticket_promedio_centavos == 300
    medios = {m.tipo_pago: (m.cantidad_ventas, m.total_centavos) for m in reporte.ventas_por_medio_pago}
    assert medios == {"EFECTIVO": (1, 200), CC: (1, 400)}
    assert sum(d.total_centavos for d in reporte.evolucion_por_dia) == 600
    assert reporte.productos_mas_vendidos[0].unidades_vendidas == 3
    assert reporte.productos_mas_vendidos[0].total_vendido_centavos == 600
    assert reporte.ventas_por_usuario[0].cantidad_ventas == 2 and reporte.ventas_por_usuario[0].total_vendido_centavos == 600
    assert reporte.rentabilidad.costo_total_centavos == 300  # 3 unidades a costo 100
    assert reporte.rentabilidad.margen_bruto_centavos == 300


def test_un_cobro_no_es_una_venta_ni_altera_ninguna_metrica_de_ventas(e):
    _a_cuenta(e, 3)
    antes = _reporte()

    servicio_cuenta_corriente.registrar_cobro(e.cliente.id, 300, e.cajera.id)
    servicio_cuenta_corriente.registrar_cobro(e.cliente.id, 100, e.cajera.id)
    despues = _reporte()

    assert despues == antes  # ningún campo del reporte cambia: ni cantidad, ni totales, ni medios, ni margen
    assert despues.cantidad_ventas == 1 and despues.total_facturado_centavos == 600


def test_una_venta_a_cuenta_anulada_no_existe_pero_las_normales_siguen_excluyendo_anuladas(e):
    normal = servicio_ventas.registrar_venta([ItemVenta(e.producto.id, 1)], "EFECTIVO", usuario_id=e.cajera.id)
    servicio_ventas.anular_venta(normal.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=e.owner.id)
    _a_cuenta(e)

    reporte = _reporte()

    assert reporte.cantidad_ventas == 1 and reporte.resumen_anulaciones.cantidad == 1


def test_el_reporte_web_muestra_cuenta_corriente_como_medio_de_pago_con_su_badge(e):
    _a_cuenta(e, 2)

    respuesta = solicitud("GET", "/reportes?fecha_desde=2000-01-01&fecha_hasta=2099-12-31", cookies=e.co)

    assert respuesta.status == 200
    assert CC in respuesta.texto and "bg-primary-subtle" in respuesta.texto


def test_los_reportes_no_agregan_indicadores_de_deudores_ni_de_cobranzas(e):
    _a_cuenta(e, 2)
    servicio_cuenta_corriente.registrar_cobro(e.cliente.id, 100, e.cajera.id)

    texto = solicitud("GET", "/reportes?fecha_desde=2000-01-01&fecha_hasta=2099-12-31", cookies=e.co).texto.lower()

    for palabra in ("deudor", "deuda total", "cobranza", "total de cobros", "saldo pendiente"):
        assert palabra not in texto, palabra
    assert not hasattr(_reporte(), "cobranzas") and not hasattr(_reporte(), "deudores")


def test_los_reportes_siguen_siendo_solo_para_el_owner(e):
    assert solicitud("GET", "/reportes", cookies=e.cc).status == 403
