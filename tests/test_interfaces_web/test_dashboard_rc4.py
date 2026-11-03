"""RC4: el dashboard no informa mal. El período de pérdidas dice lo que calcula (30 días), un producto
agotado con mínimo nunca deja "Todo en orden", y el OWNER ve la alerta persistente de backup fallido."""

import pytest

from db.repositorios import auditoria as repositorio_auditoria
from domain.auditoria import ACCION_BACKUP_ERROR
from services import servicio_reportes, servicio_stock
from tests.utilidades_clientes import usuario_logueado

from ._asgi_cliente import solicitud


@pytest.fixture
def e(base_datos_temporal):
    owner, co = usuario_logueado("OWNER", "duenio")
    _, cajera = usuario_logueado("CASHIER", "cajera")
    return type("E", (), {"owner": owner, "co": co, "cajera": cajera})


def test_la_tarjeta_de_perdidas_dice_el_periodo_que_realmente_calcula(e):
    html = solicitud("GET", "/", cookies=e.co).texto
    assert servicio_reportes.DIAS_RANGO_POR_DEFECTO_OPERATIVO == 30
    assert "Pérdidas (últimos 30 días)" in html and "7 días" not in html


def test_un_agotado_con_minimo_nunca_muestra_todo_en_orden(e):
    servicio_stock.registrar_producto("1", "Gaseosa", 100, 200, stock_actual=0, stock_minimo=6)
    servicio_stock.registrar_producto("2", "Alfajor", 100, 200, stock_actual=2, stock_minimo=5)

    for cookies in (e.co, e.cajera):
        html = solicitud("GET", "/", cookies=cookies).texto
        assert "Todo en orden" not in html
        assert 'id="alerta-stock-critico"' in html and "1 agotado y 1 con stock bajo" in html
        assert "1 agotado · 1 stock bajo" in html


def test_sin_minimo_configurado_un_producto_en_cero_no_es_alerta(e):
    for i in range(3):  # p. ej. el catálogo inicial todavía sin cargar
        servicio_stock.registrar_producto(f"C{i}", f"Catálogo {i}", 0, 0)

    html = solicitud("GET", "/", cookies=e.co).texto
    assert "Todo en orden" in html and 'id="alerta-stock-critico"' not in html


def test_el_listado_marca_el_agotado_distinto_del_sin_stock(e):
    servicio_stock.registrar_producto("1", "Gaseosa", 100, 200, stock_actual=0, stock_minimo=6)
    html = solicitud("GET", "/productos?estado_stock=critico", cookies=e.co).texto
    assert "Gaseosa" in html and "Agotado" in html


def test_el_owner_ve_la_alerta_de_backup_fallido_y_el_cajero_no(e):
    assert 'id="alerta-backup"' not in solicitud("GET", "/", cookies=e.co).texto
    repositorio_auditoria.registrar(None, ACCION_BACKUP_ERROR, "BACKUP", None, "Disco lleno")

    html = solicitud("GET", "/", cookies=e.co).texto
    assert 'id="alerta-backup"' in html and "Disco lleno" in html and 'href="/backup"' in html
    assert 'id="alerta-backup"' not in solicitud("GET", "/", cookies=e.cajera).texto
