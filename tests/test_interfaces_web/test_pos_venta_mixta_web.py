"""V1.14-C por la API del POS: venta mixta, crédito disponible y comprador de empresa. El backend manda:
un POST armado a mano no puede saltear el límite, inventar un autorizado ni mandar montos no enteros."""

import pytest

from services import servicio_clientes
from tests.utilidades_clientes import contar, crear_producto, habilitar_credito_general, usuario_logueado

from ._asgi_cliente import solicitud

pytestmark = pytest.mark.usefixtures("caja_abierta")

CC = "CUENTA_CORRIENTE"


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies_owner = usuario_logueado("OWNER", "duenio")
    _, cookies_cajera = usuario_logueado("CASHIER", "cajera")
    return type("E", (), {
        "ruta": base_datos_temporal, "owner": owner, "co": cookies_owner, "cc": cookies_cajera,
        "cliente": servicio_clientes.crear_cliente("Ana", owner.id),
        "producto": crear_producto(precio_centavos=5_000_000, stock=10),
    })


def _vender(e, clave="k1", cliente_id=None, **extra):
    cuerpo = {"items": [{"producto_id": e.producto.id, "cantidad": 1}], "tipo_pago": CC,
              "clave_idempotencia": clave, "cliente_id": cliente_id or e.cliente.id, **extra}
    return solicitud("POST", "/api/ventas", cookies=e.cc, json_body=cuerpo)


def test_venta_mixta_por_api_devuelve_contado_y_credito(e):
    habilitar_credito_general(e.owner.id)
    respuesta = _vender(e, pago_contado_centavos=2_000_000)
    assert respuesta.status == 200
    assert (respuesta.json()["pago_contado_centavos"], respuesta.json()["credito_centavos"]) == (2_000_000, 3_000_000)


def test_sin_politica_la_api_rechaza_con_mensaje_claro_y_sin_escribir(e):
    respuesta = _vender(e)
    assert respuesta.status in (409, 422) and "crédito" in respuesta.json()["error"].lower()
    assert contar(e.ruta, "ventas") == 0


def test_post_manual_no_saltea_el_limite(e):
    habilitar_credito_general(e.owner.id, limite_centavos=1_000_000)
    respuesta = _vender(e, pago_contado_centavos=1)
    assert respuesta.status in (409, 422) and "disponible" in respuesta.json()["error"]
    assert contar(e.ruta, "ventas") == 0


@pytest.mark.parametrize("contado", ["100", 10.5, True])
def test_contado_no_entero_se_rechaza_en_la_frontera(e, contado):
    habilitar_credito_general(e.owner.id)
    assert _vender(e, pago_contado_centavos=contado).status == 422
    assert contar(e.ruta, "ventas") == 0


def test_empresa_por_api_exige_comprador_y_acepta_excepcional(e):
    habilitar_credito_general(e.owner.id)
    empresa = servicio_clientes.crear_cliente(
        "Norte", e.owner.id, tipo_cliente="EMPRESA", condicion_fiscal="EXENTO", razon_social="Norte SA",
        cuit="30-71234567-1", telefono="1", direccion="d", contacto="c",
    )
    sin_comprador = _vender(e, cliente_id=empresa.id)
    assert sin_comprador.status == 422 and "empresa" in sin_comprador.json()["error"]
    inventado = _vender(e, clave="k2", cliente_id=empresa.id, comprador_autorizado_id=999)
    assert inventado.status == 422
    excepcional = _vender(e, clave="k3", cliente_id=empresa.id, comprador_excepcional_nombre="Pedro",
                          comprador_excepcional_metodo="LLAMADA", comprador_excepcional_detalle="Llamó Marta")
    assert excepcional.status == 200
    assert contar(e.ruta, "compradores_venta") == 1 and contar(e.ruta, "compradores_autorizados") == 0
