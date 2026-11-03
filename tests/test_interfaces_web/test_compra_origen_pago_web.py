"""RC4: el formulario de compra pregunta cómo se pagó (sin opción preseleccionada) y el de anulación de una
compra pagada con la caja pregunta si el efectivo vuelve."""

import pytest

from db.repositorios import caja as repositorio_caja
from domain.caja import MovimientoCaja
from domain.compra import ItemCompra
from services import servicio_caja, servicio_compras, servicio_proveedores, servicio_stock
from tests.utilidades_clientes import consultar, usuario_logueado

from ._asgi_cliente import solicitud


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies = usuario_logueado("OWNER", "duenio")
    repositorio_caja.registrar_movimiento(MovimientoCaja(tipo="APERTURA", monto_centavos=10000))
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 300, stock_actual=10)
    return type("E", (), {"ruta": base_datos_temporal, "owner": owner, "co": cookies, "prov": proveedor, "p": producto})


def _comprar(e, **extra):
    formulario = {
        "proveedor_id": str(e.prov.id), "producto_id": [str(e.p.id)], "cantidad": ["5"], "costo_unitario": ["1.50"],
        **extra,
    }
    return solicitud("POST", "/compras/nueva", cookies=e.co, formulario=formulario)


def _compras(e):
    return consultar(e.ruta, "SELECT id, origen_pago, caja_movimiento_id FROM compras ORDER BY id")


def test_el_formulario_pregunta_como_se_pago_sin_preseleccionar(e):
    html = solicitud("GET", "/compras/nueva", cookies=e.co).texto

    assert "¿Cómo se pagó esta compra?" in html
    assert "Con efectivo de la caja actual" in html and "Fuera de la caja" in html
    assert 'value="CAJA_ACTUAL" required' in html and "checked" not in html.split('id="origen-pago"')[1].split("</fieldset>")[0]


def test_sin_elegir_como_se_pago_no_se_registra(e):
    respuesta = _comprar(e)

    assert respuesta.status == 303 and _compras(e) == []


def test_con_efectivo_de_la_caja_genera_el_egreso_y_el_detalle_lo_muestra(e):
    antes = servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos

    assert _comprar(e, origen_pago="CAJA_ACTUAL").status == 303

    [(compra_id, origen, movimiento)] = _compras(e)
    assert origen == "CAJA_ACTUAL" and movimiento is not None
    assert servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos == antes - 750
    assert "Con efectivo de la caja" in solicitud("GET", f"/compras/{compra_id}", cookies=e.co).texto


def test_a_credito_el_origen_no_se_envia_ni_se_guarda(e):
    assert _comprar(e, condicion_pago="CREDITO", origen_pago="CAJA_ACTUAL").status == 303
    assert [fila[1:] for fila in _compras(e)] == [(None, None)]


def test_anular_una_compra_pagada_con_la_caja_pregunta_por_el_reintegro(e):
    compra = servicio_compras.registrar_compra(e.prov.id, e.owner.id, [ItemCompra(e.p.id, 5, 150)], origen_pago="CAJA_ACTUAL")
    html = solicitud("GET", f"/compras/{compra.id}/anular", cookies=e.co).texto
    assert 'id="reintegro-caja"' in html and "¿El efectivo vuelve a la caja?" in html

    sin_elegir = solicitud(
        "POST", f"/compras/{compra.id}/anular", cookies=e.co, formulario={"motivo": "ERROR_CARGA", "observaciones": ""}
    )
    assert sin_elegir.status == 303
    assert consultar(e.ruta, "SELECT estado FROM compras WHERE id = ?", (compra.id,))[0][0] == "ACTIVA"

    solicitud(
        "POST", f"/compras/{compra.id}/anular", cookies=e.co,
        formulario={"motivo": "ERROR_CARGA", "observaciones": "", "reintegro_a_caja": "SI"},
    )
    estado, reintegro = consultar(e.ruta, "SELECT estado, caja_movimiento_reintegro_id FROM compras WHERE id = ?", (compra.id,))[0]
    assert estado == "ANULADA" and reintegro is not None


def test_una_compra_fuera_de_caja_no_muestra_la_pregunta_de_reintegro(e):
    compra = servicio_compras.registrar_compra(e.prov.id, e.owner.id, [ItemCompra(e.p.id, 5, 150)], origen_pago="FUERA_DE_CAJA")
    assert 'id="reintegro-caja"' not in solicitud("GET", f"/compras/{compra.id}/anular", cookies=e.co).texto
