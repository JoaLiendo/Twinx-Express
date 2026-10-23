"""V1.11-E: dashboard operativo. Cuatro accesos como máximo; el CASHIER nunca ve costos, valorización
ni pérdidas; el OWNER ve los indicadores que ya existen en Reportes."""

import re

import pytest

from services import servicio_stock
from tests.utilidades_clientes import usuario_logueado

from ._asgi_cliente import solicitud


@pytest.fixture
def e(base_datos_temporal):
    owner, co = usuario_logueado("OWNER", "duenio")
    _, cajera = usuario_logueado("CASHIER", "cajera")
    producto = servicio_stock.registrar_producto("1", "Alfajor", 12345, 30000, stock_actual=10, usuario_id=owner.id)
    servicio_stock.ajustar_stock(producto.id, -2, "ROTURA", owner.id)
    return type("E", (), {"co": co, "cajera": cajera})


def _accesos(html: str) -> list[str]:
    return re.findall(r'data-acceso="(\w+)"', html)


def test_owner_cuatro_accesos_e_indicadores(e):
    html = solicitud("GET", "/", cookies=e.co).texto
    assert _accesos(html) == ["venta", "consulta", "mercaderia", "actividad"]
    assert 'href="/compras/nueva"' in html
    assert 'data-indicador="valorizacion"' in html and "$987,60" in html  # 8 u. x $123,45
    assert 'data-indicador="perdidas"' in html and "$246,90" in html  # 2 u. rotas a costo histórico


def test_cajero_sin_costos_ni_ingreso_de_mercaderia(e):
    html = solicitud("GET", "/", cookies=e.cajera).texto
    assert _accesos(html) == ["venta", "consulta", "caja", "actividad"]
    assert "data-indicador" not in html
    assert "123,45" not in html and "987,60" not in html and "246,90" not in html
    assert 'href="/compras/nueva"' not in html


def test_consultar_producto_busca_en_el_listado(e):
    html = solicitud("GET", "/", cookies=e.cajera).texto
    assert 'action="/productos"' in html and 'name="q"' in html
    assert ">Alfajor</p>" in solicitud("GET", "/productos?q=alfa", cookies=e.cajera).texto
