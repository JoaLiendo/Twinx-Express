"""POS con venta a cuenta (021): opción explícita, nunca predeterminada, con cliente activo obligatorio;
las otras 4 formas de pago intactas; y que el JS no inserte datos del cliente como HTML."""

import re
from pathlib import Path

import pytest

from domain.venta import TIPOS_PAGO_VALIDOS
from services import servicio_clientes, servicio_stock
from tests.utilidades_clientes import consultar, contar, forzar_cliente_inactivo, usuario_logueado

from ._asgi_cliente import solicitud

pytestmark = pytest.mark.usefixtures("caja_abierta")

CC = "CUENTA_CORRIENTE"
DIRECTORIO_JS = Path(__file__).resolve().parents[2] / "interfaces" / "web" / "static" / "js"


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies_owner = usuario_logueado("OWNER", "duenio")
    cajera, cookies_cajera = usuario_logueado("CASHIER", "cajera")
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=50)
    cliente = servicio_clientes.crear_cliente("Ana", owner.id)
    return type(
        "Escenario",
        (),
        {"ruta": base_datos_temporal, "owner": owner, "cajera": cajera, "co": cookies_owner, "cc": cookies_cajera,
         "producto": producto, "cliente": cliente},
    )


def _html(e) -> str:
    respuesta = solicitud("GET", "/ventas", cookies=e.cc)
    assert respuesta.status == 200
    return respuesta.texto


def _radios_de_pago(html: str) -> list[tuple[str, bool]]:
    """`[(value, checked)]` de cada radio `name="tipo_pago"`, en el orden en que aparecen."""
    radios = re.findall(r'<input type="radio" name="tipo_pago" value="([A-Z_]+)"[^>]*>', html)
    marcados = re.findall(r'<input type="radio" name="tipo_pago" value="([A-Z_]+)"([^>]*)>', html)
    assert len(radios) == len(marcados)
    return [(valor, "checked" in resto) for valor, resto in marcados]


def _vender(e, tipo_pago, clave="k1", cliente_id=None, cookies=None):
    cuerpo = {"items": [{"producto_id": e.producto.id, "cantidad": 2}], "tipo_pago": tipo_pago, "clave_idempotencia": clave}
    if cliente_id is not None:
        cuerpo["cliente_id"] = cliente_id
    return solicitud("POST", "/api/ventas", cookies=cookies or e.cc, json_body=cuerpo)


# --- la página -----------------------------------------------------------------------------------------


def test_efectivo_sigue_marcado_por_defecto_y_es_el_unico_marcado(e):
    radios = _radios_de_pago(_html(e))

    marcados = [valor for valor, marcado in radios if marcado]
    assert marcados == ["EFECTIVO"]
    assert radios[0] == ("EFECTIVO", True)  # y es el primero, como siempre


def test_cuenta_corriente_esta_presente_pero_nunca_seleccionada_ni_primera(e):
    radios = _radios_de_pago(_html(e))

    assert (CC, False) in radios
    assert radios[0][0] != CC


def test_los_cuatro_medios_de_siempre_siguen_estando_en_el_mismo_orden(e):
    radios = _radios_de_pago(_html(e))

    assert [valor for valor, _ in radios if valor != CC] == sorted(TIPOS_PAGO_VALIDOS)
    assert CC not in TIPOS_PAGO_VALIDOS  # la opción a cuenta no se coló en la lista original


def test_el_selector_de_cliente_existe_y_arranca_oculto(e):
    html = _html(e)

    bloque = re.search(r'<div id="bloque-cliente" class="([^"]*)"', html)
    assert bloque and "hidden" in bloque.group(1).split()
    for gancho in ("data-cliente-busqueda", "data-cliente-resultados", "data-cliente-seleccionado", "data-cliente-quitar"):
        assert gancho in html


def test_los_scripts_se_sirven_en_el_orden_que_necesitan(e):
    html = _html(e)

    assert html.index("/static/js/dinero_cobro.js") < html.index("/static/js/cliente_selector.js") < html.index("/static/js/pos.js")
    assert solicitud("GET", "/static/js/cliente_selector.js").status == 200


def test_la_pagina_no_incluye_nombres_de_clientes(e):
    """Los clientes se piden por la API cuando el cajero elige CUENTA_CORRIENTE: no viajan en el HTML."""
    servicio_clientes.crear_cliente("Cliente Reservado Xyz", e.owner.id)

    assert "Cliente Reservado Xyz" not in _html(e)


# --- venta a cuenta por la API --------------------------------------------------------------------------------


def test_venta_a_cuenta_con_cliente_activo(e):
    respuesta = _vender(e, CC, cliente_id=e.cliente.id)

    assert respuesta.status == 200
    venta = respuesta.json()
    assert (venta["tipo_pago"], venta["total_centavos"]) == (CC, 400)
    fila = consultar(e.ruta, "SELECT cliente_id, usuario_id FROM ventas WHERE id = ?", (venta["id"],))[0]
    assert (fila["cliente_id"], fila["usuario_id"]) == (e.cliente.id, e.cajera.id)
    cargo = consultar(e.ruta, "SELECT * FROM movimientos_cuenta")[0]
    assert (cargo["tipo"], cargo["monto_centavos"], cargo["venta_id"]) == ("CARGO", 400, venta["id"])
    assert servicio_stock.obtener_por_id(e.producto.id).stock_actual == 48


def test_owner_tambien_puede_vender_a_cuenta(e):
    assert _vender(e, CC, cliente_id=e.cliente.id, cookies=e.co).status == 200


def test_venta_a_cuenta_sin_cliente_se_rechaza_con_un_mensaje_claro(e):
    respuesta = _vender(e, CC)

    assert respuesta.status == 422 and "cliente" in respuesta.json()["error"].lower()
    assert contar(e.ruta, "ventas") == 0 and contar(e.ruta, "movimientos_cuenta") == 0
    assert servicio_stock.obtener_por_id(e.producto.id).stock_actual == 50


def test_venta_a_cuenta_a_un_cliente_inactivo_se_rechaza(e):
    servicio_clientes.desactivar_cliente(e.cliente.id, e.owner.id)

    respuesta = _vender(e, CC, cliente_id=e.cliente.id)

    assert respuesta.status == 422 and "inactivo" in respuesta.json()["error"].lower()
    assert contar(e.ruta, "ventas") == 0


def test_un_cliente_inactivo_con_deuda_tampoco_recibe_ventas_nuevas(e):
    _vender(e, CC, clave="k0", cliente_id=e.cliente.id)
    forzar_cliente_inactivo(e.ruta, e.cliente.id)

    respuesta = _vender(e, CC, clave="k1", cliente_id=e.cliente.id)

    assert respuesta.status == 422
    assert contar(e.ruta, "ventas") == 1


def test_venta_a_cuenta_a_un_cliente_inexistente_se_rechaza(e):
    respuesta = _vender(e, CC, cliente_id=999)

    assert respuesta.status == 422
    assert contar(e.ruta, "ventas") == 0


def test_venta_a_cuenta_sin_sesion_se_rechaza(e):
    respuesta = solicitud(
        "POST", "/api/ventas",
        json_body={"items": [{"producto_id": e.producto.id, "cantidad": 1}], "tipo_pago": CC,
                   "clave_idempotencia": "k", "cliente_id": e.cliente.id},
    )

    assert respuesta.status == 401
    assert contar(e.ruta, "ventas") == 0


def test_el_reenvio_de_una_venta_a_cuenta_con_la_misma_clave_no_duplica_nada(e):
    primera = _vender(e, CC, clave="unica", cliente_id=e.cliente.id).json()
    segunda = _vender(e, CC, clave="unica", cliente_id=e.cliente.id).json()

    assert primera["id"] == segunda["id"]
    assert contar(e.ruta, "ventas") == 1 and contar(e.ruta, "movimientos_cuenta") == 1
    assert servicio_stock.obtener_por_id(e.producto.id).stock_actual == 48


def test_la_misma_clave_con_otro_cliente_se_rechaza(e):
    otro = servicio_clientes.crear_cliente("Beto", e.owner.id)
    _vender(e, CC, clave="unica", cliente_id=e.cliente.id)

    respuesta = _vender(e, CC, clave="unica", cliente_id=otro.id)

    assert respuesta.status == 422
    assert contar(e.ruta, "ventas") == 1


# --- regresión de las otras cuatro formas de pago ---------------------------------------------------------------


@pytest.mark.parametrize("tipo_pago", sorted(TIPOS_PAGO_VALIDOS))
def test_los_medios_de_siempre_se_venden_igual_sin_cliente_y_sin_cargo(e, tipo_pago):
    respuesta = _vender(e, tipo_pago)

    assert respuesta.status == 200 and respuesta.json()["tipo_pago"] == tipo_pago
    assert consultar(e.ruta, "SELECT cliente_id FROM ventas")[0]["cliente_id"] is None
    assert contar(e.ruta, "movimientos_cuenta") == 0


def test_una_venta_normal_puede_asociarse_a_un_cliente_activo_sin_generar_cargo(e):
    respuesta = _vender(e, "EFECTIVO", cliente_id=e.cliente.id)

    assert respuesta.status == 200
    assert consultar(e.ruta, "SELECT cliente_id FROM ventas")[0]["cliente_id"] == e.cliente.id
    assert contar(e.ruta, "movimientos_cuenta") == 0


def test_un_tipo_de_pago_desconocido_sigue_rechazandose(e):
    respuesta = _vender(e, "FIADO", cliente_id=e.cliente.id)

    assert respuesta.status == 422
    assert contar(e.ruta, "ventas") == 0


# --- seguridad: ningún dato del cliente se inserta como HTML ----------------------------------------------------


_APIS_QUE_PARSEAN_MARCADO = ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "srcdoc")


def test_cliente_selector_js_no_usa_ninguna_api_que_interprete_html():
    codigo = (DIRECTORIO_JS / "cliente_selector.js").read_text(encoding="utf-8")

    for api in _APIS_QUE_PARSEAN_MARCADO:
        assert api not in codigo, f"cliente_selector.js usa {api}"
    assert codigo.count("textContent") >= 6  # nombre, teléfono, saldos y mensajes se asignan como texto


def test_pos_js_no_mezcla_datos_del_cliente_con_html():
    lineas = (DIRECTORIO_JS / "pos.js").read_text(encoding="utf-8").splitlines()

    for numero, linea in enumerate(lineas, start=1):
        if "cliente" in linea.lower() and not linea.strip().startswith(("//", "*")):
            assert "innerHTML" not in linea, f"pos.js:{numero} mezcla datos del cliente con innerHTML"
