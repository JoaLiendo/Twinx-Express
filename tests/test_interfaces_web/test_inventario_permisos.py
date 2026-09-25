"""Matriz de permisos del inventario (auditoría V1.4): TODAS las rutas de inventario, descubiertas desde el
esquema OpenAPI de la app real, con lo que puede hacer cada rol. Si aparece una ruta nueva, este test falla
hasta que se la clasifique: así ninguna ruta puede exponer datos del inventario (stock esperado, diferencia)
al CASHIER sin que alguien lo decida explícitamente."""

import re

import pytest

from interfaces.web.app import app
from services import servicio_inventario
from tests.utilidades_clientes import crear_producto, usuario_logueado

from ._asgi_cliente import solicitud

STOCK_SECRETO = 6113

# (método, ruta) -> el CASHIER puede usarla. El resto exige OWNER.
MATRIZ = {
    ("GET", "/inventario"): True,  # redirige a la pantalla de conteo
    ("GET", "/inventario/nuevo"): False,
    ("POST", "/inventario/nuevo"): False,
    ("GET", "/inventario/conteo"): True,
    ("POST", "/inventario/conteo/{producto_id}"): True,
    ("POST", "/api/inventario/conteo/{producto_id}"): True,
    ("GET", "/inventario/{inventario_id}"): False,
    ("POST", "/inventario/{inventario_id}/confirmar"): False,
    ("POST", "/inventario/{inventario_id}/cancelar"): False,
}


def _rutas_de_inventario() -> set[tuple[str, str]]:
    rutas = set()
    for ruta, metodos in app.openapi()["paths"].items():
        if ruta.startswith(("/inventario", "/api/inventario")):
            rutas |= {(metodo.upper(), ruta) for metodo in metodos}
    return rutas


def test_la_matriz_cubre_exactamente_las_rutas_de_inventario_de_la_app():
    assert _rutas_de_inventario() == set(MATRIZ)


@pytest.fixture
def e(base_datos_temporal, caja_abierta):
    owner, cookies_owner = usuario_logueado("OWNER", "duenio")
    _, cookies_cajera = usuario_logueado("CASHIER", "cajera")
    producto = crear_producto("7790000000001", stock=STOCK_SECRETO)
    inventario = servicio_inventario.crear_inventario(owner.id, [producto.id])
    servicio_inventario.registrar_conteo(inventario.id, producto.id, STOCK_SECRETO - 1, owner.id)
    return type("E", (), {"co": cookies_owner, "cc": cookies_cajera, "producto": producto, "inventario": inventario})


def _url(ruta: str, e) -> str:
    return ruta.replace("{producto_id}", str(e.producto.id)).replace("{inventario_id}", str(e.inventario.id))


@pytest.mark.parametrize(("metodo", "ruta"), sorted(MATRIZ))
def test_el_cashier_solo_accede_a_lo_permitido_y_nunca_ve_esperado_ni_diferencia(e, metodo, ruta):
    kwargs = {"json_body": {"cantidad": 3}} if ruta.startswith("/api/") else {"formulario": {"cantidad": "3"}}

    respuesta = solicitud(metodo, _url(ruta, e), cookies=e.cc, **kwargs)

    if MATRIZ[(metodo, ruta)]:
        assert respuesta.status in (200, 303), (metodo, ruta, respuesta.status)
    else:
        assert respuesta.status == 403, (metodo, ruta, respuesta.status)
    assert str(STOCK_SECRETO) not in respuesta.texto and str(STOCK_SECRETO - 1) not in re.sub(
        r"Contado: \d+|value=\"\d+\"", "", respuesta.texto
    )


def test_el_cashier_sigue_sin_ver_el_esperado_siguiendo_las_redirecciones_permitidas(e):
    conteo = solicitud("GET", "/inventario/conteo", cookies=e.cc)

    assert conteo.status == 200 and str(STOCK_SECRETO) not in conteo.texto
    assert "Esperado" not in conteo.texto and "Diferencia" not in conteo.texto and "version" not in conteo.texto.lower()


@pytest.mark.parametrize(("metodo", "ruta"), sorted(MATRIZ))
def test_el_owner_accede_a_todas_las_rutas(e, metodo, ruta):
    kwargs = {"json_body": {"cantidad": 3}} if ruta.startswith("/api/") else {"formulario": {"cantidad": "3"}}

    respuesta = solicitud(metodo, _url(ruta, e), cookies=e.co, **kwargs)

    assert respuesta.status != 403, (metodo, ruta)
