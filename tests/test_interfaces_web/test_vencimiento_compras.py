"""V1.10-C: pantallas de alta y edición del vencimiento de compras -- solo OWNER, errores controlados."""

from datetime import date, timedelta
from urllib.parse import unquote_plus

import pytest

from domain.compra import ItemCompra
from services import servicio_compras, servicio_proveedores, servicio_stock
from tests.utilidades_clientes import consultar, usuario_logueado

from ._asgi_cliente import solicitud


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies = usuario_logueado("OWNER", "duenio")
    _, cajera = usuario_logueado("CASHIER", "cajera")
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 300, stock_actual=10)
    hoy = date.fromisoformat(consultar(base_datos_temporal, "SELECT date('now', 'localtime')")[0][0])
    return type(
        "E", (), {"ruta": base_datos_temporal, "owner": owner, "co": cookies, "cajera": cajera, "prov": proveedor,
                  "p": producto, "hoy": hoy}
    )


def _en(e, dias):
    return (e.hoy + timedelta(days=dias)).isoformat()


def _comprar(e, condicion="CREDITO", vencimiento=None):
    return servicio_compras.registrar_compra(
        e.prov.id, e.owner.id, [ItemCompra(e.p.id, 5, 150)], condicion_pago=condicion, fecha_vencimiento=vencimiento
    )


def _alta(e, cookies=None, **extra):
    formulario = {
        "proveedor_id": str(e.prov.id), "producto_id": str(e.p.id), "cantidad": "2", "costo_unitario": "1.50",
        "clave_idempotencia": "clave-web-1", "condicion_pago": "CREDITO", **extra,
    }
    return solicitud("POST", "/compras/nueva", cookies=cookies or e.co, formulario=formulario)


def _vencimiento(e, compra_id):
    return consultar(e.ruta, "SELECT fecha_vencimiento FROM compras WHERE id = ?", (compra_id,))[0][0]


def _mensaje(respuesta) -> str:
    return unquote_plus(dict(respuesta.headers)[b"location"].decode())


def _editar(e, compra, fecha, cookies=None):
    return solicitud(
        "POST", f"/compras/{compra.id}/vencimiento", cookies=e.co if cookies is None else cookies,
        formulario={"fecha_vencimiento": fecha},
    )


# --- Alta ---------------------------------------------------------------------------------------------


def test_el_formulario_de_alta_tiene_el_campo_de_vencimiento(e):
    html = solicitud("GET", "/compras/nueva", cookies=e.co).texto

    assert 'name="fecha_vencimiento"' in html and 'type="date"' in html and "Fecha de vencimiento" in html


def test_alta_a_credito_guarda_el_vencimiento_y_el_detalle_lo_muestra(e):
    fecha = _en(e, 20)

    respuesta = _alta(e, fecha_vencimiento=fecha)

    assert respuesta.status == 303
    assert consultar(e.ruta, "SELECT fecha_vencimiento FROM compras")[0][0] == fecha
    detalle = solicitud("GET", "/compras/1", cookies=e.co).texto
    assert 'id="compra-vencimiento"' in detalle and fecha in detalle


def test_alta_a_credito_con_campo_vacio_queda_sin_vencimiento(e):
    assert _alta(e, fecha_vencimiento="").status == 303

    assert _vencimiento(e, 1) is None
    detalle = solicitud("GET", "/compras/1", cookies=e.co).texto
    assert "Sin vencimiento" in detalle


def test_alta_al_contado_sin_campo_no_persiste_fecha_ni_muestra_vencimiento(e):
    assert _alta(e, condicion_pago="CONTADO", fecha_vencimiento="").status == 303

    assert _vencimiento(e, 1) is None
    detalle = solicitud("GET", "/compras/1", cookies=e.co).texto
    assert 'id="compra-vencimiento"' not in detalle and "Sin vencimiento" not in detalle
    assert 'id="editar-vencimiento"' not in detalle


def test_alta_al_contado_con_fecha_es_error_controlado_sin_efectos(e):
    respuesta = _alta(e, condicion_pago="CONTADO", fecha_vencimiento=_en(e, 5))

    assert respuesta.status == 303 and "tipo=error" in _mensaje(respuesta) and "contado" in _mensaje(respuesta)
    assert consultar(e.ruta, "SELECT COUNT(*) FROM compras")[0][0] == 0


@pytest.mark.parametrize("fecha", ["2000-01-01", "2099-02-30", "no-es-fecha"])
def test_alta_con_fecha_invalida_es_error_controlado_sin_efectos(e, fecha):
    respuesta = _alta(e, fecha_vencimiento=fecha)

    assert respuesta.status == 303 and "tipo=error" in _mensaje(respuesta)
    assert consultar(e.ruta, "SELECT COUNT(*) FROM compras")[0][0] == 0


# --- Detalle y edición ---------------------------------------------------------------------------------


def test_el_detalle_de_credito_activa_ofrece_editar_el_vencimiento(e):
    compra = _comprar(e, vencimiento=_en(e, 9))

    detalle = solicitud("GET", f"/compras/{compra.id}", cookies=e.co).texto

    assert f'href="/compras/{compra.id}/vencimiento"' in detalle and "Editar vencimiento" in detalle


def test_el_formulario_de_edicion_muestra_el_valor_actual_y_permite_quitarlo(e):
    fecha = _en(e, 9)
    compra = _comprar(e, vencimiento=fecha)

    html = solicitud("GET", f"/compras/{compra.id}/vencimiento", cookies=e.co).texto

    assert 'id="form-vencimiento"' in html and f'value="{fecha}"' in html
    assert "vacío" in html  # explica cómo quitarlo


def test_editar_cambia_el_vencimiento_y_se_ve_en_el_detalle(e):
    compra = _comprar(e)
    fecha = _en(e, 45)

    respuesta = _editar(e, compra, fecha)

    assert respuesta.status == 303 and _mensaje(respuesta).startswith(f"/compras/{compra.id}?tipo=success")
    assert _vencimiento(e, compra.id) == fecha
    assert fecha in solicitud("GET", f"/compras/{compra.id}", cookies=e.co).texto
    assert consultar(e.ruta, "SELECT COUNT(*) FROM auditoria WHERE accion = 'VENCIMIENTO_COMPRA_MODIFICADO'")[0][0] == 1


def test_quitar_el_vencimiento_con_campo_vacio(e):
    compra = _comprar(e, vencimiento=_en(e, 9))

    respuesta = _editar(e, compra, "")

    assert respuesta.status == 303 and "tipo=success" in _mensaje(respuesta)
    assert _vencimiento(e, compra.id) is None


def test_editar_con_el_mismo_valor_no_audita_y_lo_dice(e):
    fecha = _en(e, 9)
    compra = _comprar(e, vencimiento=fecha)

    respuesta = _editar(e, compra, fecha)

    assert respuesta.status == 303 and "sin cambios" in _mensaje(respuesta).lower()
    assert consultar(e.ruta, "SELECT COUNT(*) FROM auditoria WHERE accion = 'VENCIMIENTO_COMPRA_MODIFICADO'")[0][0] == 0


@pytest.mark.parametrize("fecha", ["2000-01-01", "2099-02-30", "no-es-fecha"])
def test_editar_con_fecha_invalida_es_error_controlado(e, fecha):
    compra = _comprar(e, vencimiento=_en(e, 9))

    respuesta = _editar(e, compra, fecha)

    assert respuesta.status == 303 and "tipo=error" in _mensaje(respuesta)
    assert _vencimiento(e, compra.id) == _en(e, 9)


def test_una_compra_al_contado_no_se_edita(e):
    compra = _comprar(e, "CONTADO")

    formulario = solicitud("GET", f"/compras/{compra.id}/vencimiento", cookies=e.co)
    envio = _editar(e, compra, _en(e, 3))

    assert formulario.status == 303 and "tipo=error" in _mensaje(formulario)
    assert envio.status == 303 and "tipo=error" in _mensaje(envio)
    assert _vencimiento(e, compra.id) is None


def test_una_compra_anulada_no_se_edita_y_el_detalle_no_ofrece_editar(e):
    fecha = _en(e, 9)
    compra = _comprar(e, vencimiento=fecha)
    servicio_compras.anular_compra(compra.id, "ERROR_CARGA", None, e.owner.id)

    formulario = solicitud("GET", f"/compras/{compra.id}/vencimiento", cookies=e.co)
    envio = _editar(e, compra, _en(e, 30))

    assert formulario.status == 303 and "tipo=error" in _mensaje(formulario)
    assert envio.status == 303 and "tipo=error" in _mensaje(envio)
    assert _vencimiento(e, compra.id) == fecha
    assert 'id="editar-vencimiento"' not in solicitud("GET", f"/compras/{compra.id}", cookies=e.co).texto


def test_compra_inexistente_es_error_controlado(e):
    assert solicitud("GET", "/compras/999/vencimiento", cookies=e.co).status == 303
    respuesta = solicitud("POST", "/compras/999/vencimiento", cookies=e.co, formulario={"fecha_vencimiento": _en(e, 3)})
    assert respuesta.status == 303 and "tipo=error" in _mensaje(respuesta)


def test_sin_el_campo_en_el_post_es_error_controlado_no_500(e):
    compra = _comprar(e, vencimiento=_en(e, 9))

    respuesta = solicitud("POST", f"/compras/{compra.id}/vencimiento", cookies=e.co, formulario={})

    assert respuesta.status == 303 and "tipo=error" in _mensaje(respuesta)
    assert _vencimiento(e, compra.id) == _en(e, 9)


# --- Permisos -------------------------------------------------------------------------------------------


def test_el_cajero_no_puede_ver_ni_editar_el_vencimiento(e):
    fecha = _en(e, 9)
    compra = _comprar(e, vencimiento=fecha)

    assert solicitud("GET", f"/compras/{compra.id}/vencimiento", cookies=e.cajera).status == 403
    assert _editar(e, compra, _en(e, 30), cookies=e.cajera).status == 403
    assert solicitud("GET", f"/compras/{compra.id}/vencimiento").status == 303  # sin sesión
    assert _editar(e, compra, _en(e, 30), cookies={}).status == 303
    assert _vencimiento(e, compra.id) == fecha
    assert consultar(e.ruta, "SELECT COUNT(*) FROM auditoria WHERE accion = 'VENCIMIENTO_COMPRA_MODIFICADO'")[0][0] == 0
