"""V1.7-B: pantallas de anulación de compras -- solo OWNER, errores controlados, sin efectos dobles."""

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
    compra = servicio_compras.registrar_compra(proveedor.id, owner.id, [ItemCompra(producto.id, 5, 150)])
    return type(
        "E", (), {"ruta": base_datos_temporal, "owner": owner, "co": cookies, "cajera": cajera, "p": producto, "c": compra}
    )


def _stock(e):
    return consultar(e.ruta, "SELECT stock_actual FROM productos WHERE id = ?", (e.p.id,))[0][0]


def _estado(e):
    return consultar(e.ruta, "SELECT estado FROM compras WHERE id = ?", (e.c.id,))[0][0]


def _anular(e, cookies=None, **formulario):
    datos = {"motivo": "ERROR_CARGA", "observaciones": "", **formulario}
    return solicitud("POST", f"/compras/{e.c.id}/anular", cookies=cookies or e.co, formulario=datos)


def test_el_cajero_no_puede_ver_ni_anular(e):
    assert solicitud("GET", f"/compras/{e.c.id}/anular", cookies=e.cajera).status == 403
    assert _anular(e, cookies=e.cajera).status == 403
    assert solicitud("GET", f"/compras/{e.c.id}/anular").status == 303  # sin sesión
    assert _estado(e) == "ACTIVA" and _stock(e) == 15


def test_el_formulario_explica_el_efecto_sobre_stock_y_costo(e):
    html = solicitud("GET", f"/compras/{e.c.id}/anular", cookies=e.co).texto

    assert 'id="form-anular-compra"' in html and "revierte el stock" in html
    assert "solo si puede demostrarse" in html and "ERROR_CARGA" in html


def test_anular_desde_el_detalle_y_ver_el_resultado(e):
    detalle = solicitud("GET", f"/compras/{e.c.id}", cookies=e.co).texto
    assert 'id="anular-compra"' in detalle

    respuesta = _anular(e)

    assert respuesta.status == 303 and dict(respuesta.headers)[b"location"].startswith(f"/compras/{e.c.id}".encode())
    assert _estado(e) == "ANULADA" and _stock(e) == 10
    detalle = solicitud("GET", f"/compras/{e.c.id}", cookies=e.co).texto
    assert 'id="compra-anulada"' in detalle and 'id="anular-compra"' not in detalle
    lista = solicitud("GET", "/compras", cookies=e.co).texto
    assert f'data-compra="{e.c.id}" data-estado="ANULADA"' in lista


def test_segundo_post_es_error_controlado_y_no_descuenta_dos_veces(e):
    assert _anular(e).status == 303
    stock, auditorias = _stock(e), consultar(e.ruta, "SELECT COUNT(*) FROM auditoria WHERE accion = 'COMPRA_ANULADA'")[0][0]

    segunda = _anular(e)

    assert segunda.status == 303 and b"tipo=error" in dict(segunda.headers)[b"location"]
    assert _stock(e) == stock == 10
    assert consultar(e.ruta, "SELECT COUNT(*) FROM auditoria WHERE accion = 'COMPRA_ANULADA'")[0][0] == auditorias == 1


def test_formulario_de_una_compra_ya_anulada_redirige(e):
    _anular(e)

    respuesta = solicitud("GET", f"/compras/{e.c.id}/anular", cookies=e.co)

    assert respuesta.status == 303 and b"tipo=error" in dict(respuesta.headers)[b"location"]


def test_stock_insuficiente_es_error_controlado_sin_cambios(e):
    from tests.utilidades_clientes import escribir

    escribir(e.ruta, "UPDATE productos SET stock_actual = 2 WHERE id = ?", (e.p.id,))

    respuesta = _anular(e)

    assert respuesta.status == 303 and b"tipo=error" in dict(respuesta.headers)[b"location"]
    assert _estado(e) == "ACTIVA" and _stock(e) == 2


@pytest.mark.parametrize("formulario", [{"motivo": "INVENTADO"}, {"motivo": "OTRO"}])
def test_motivo_invalido_u_otro_sin_observaciones(e, formulario):
    respuesta = _anular(e, **formulario)

    assert respuesta.status == 303 and b"tipo=error" in dict(respuesta.headers)[b"location"]
    assert _estado(e) == "ACTIVA" and _stock(e) == 15


def test_compra_inexistente(e):
    for metodo in ("GET", "POST"):
        respuesta = solicitud(
            metodo, "/compras/9999/anular", cookies=e.co,
            formulario={"motivo": "ERROR_CARGA", "observaciones": ""} if metodo == "POST" else None,
        )
        assert respuesta.status == 303 and b"tipo=error" in dict(respuesta.headers)[b"location"]


def test_quien_anula_es_el_usuario_de_la_sesion(e):
    _anular(e, usuario_id=str(9999))  # un campo extra del formulario no se usa

    assert consultar(e.ruta, "SELECT anulada_por_usuario_id FROM compras WHERE id = ?", (e.c.id,))[0][0] == e.owner.id


def test_el_historial_de_precios_muestra_la_anulacion(e):
    _anular(e)

    html = solicitud("GET", f"/productos/{e.p.id}/precios", cookies=e.co)

    assert html.status == 200 and "Anulación de compra" in html.texto
