"""Pantallas de analítica (V1.17): solo OWNER, datos reales en cada sección, filtros y exportaciones."""

from datetime import date, timedelta

import pytest

from domain.compra import ItemCompra
from domain.venta import ItemVenta
from services import servicio_caja, servicio_clientes, servicio_compras, servicio_proveedores, servicio_stock, servicio_ventas
from tests.utilidades_clientes import habilitar_credito_general, usuario_logueado

from ._asgi_cliente import solicitud

pytestmark = pytest.mark.sin_caja_abierta

HOY = date.today()
DESDE = (HOY - timedelta(days=29)).isoformat()
HASTA = HOY.isoformat()
PANTALLAS = ("/analitica", "/analitica/ventas", "/analitica/margen", "/analitica/precios", "/analitica/deuda", "/analitica/proveedores")


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies = usuario_logueado("OWNER", "duenio")
    _, cookies_cajero = usuario_logueado("CASHIER", "cajero")
    servicio_caja.abrir_caja(0, None, owner.id)
    gaseosa = servicio_stock.registrar_producto("1", "Gaseosa Cola", 100, 200, stock_minimo=0)
    proveedor = servicio_proveedores.crear_proveedor("Alfa SA")
    servicio_compras.registrar_compra(proveedor.id, owner.id, [ItemCompra(gaseosa.id, 10, 100)], origen_pago="FUERA_DE_CAJA")
    servicio_compras.registrar_compra(proveedor.id, owner.id, [ItemCompra(gaseosa.id, 10, 130)], condicion_pago="CREDITO")
    servicio_ventas.registrar_venta([ItemVenta(gaseosa.id, 4)], "EFECTIVO", usuario_id=owner.id)
    habilitar_credito_general(owner.id)
    cliente = servicio_clientes.crear_cliente("Ana Pérez", owner.id)
    servicio_ventas.registrar_venta([ItemVenta(gaseosa.id, 1)], "CUENTA_CORRIENTE", usuario_id=owner.id, cliente_id=cliente.id)
    return type("E", (), {"cookies": cookies, "cookies_cajero": cookies_cajero, "gaseosa": gaseosa, "proveedor": proveedor})


class TestPermisos:
    @pytest.mark.parametrize("ruta", PANTALLAS + ("/analitica/margen/exportar", "/analitica/precios/exportar"))
    def test_cashier_no_entra(self, e, ruta):
        respuesta = solicitud("GET", ruta, cookies=e.cookies_cajero)
        assert respuesta.status == 403

    def test_cashier_no_ve_analitica_en_el_menu(self, e):
        respuesta = solicitud("GET", "/", cookies=e.cookies_cajero)
        assert 'href="/analitica"' not in respuesta.texto

    def test_owner_ve_analitica_en_el_menu(self, e):
        respuesta = solicitud("GET", "/", cookies=e.cookies)
        assert 'href="/analitica"' in respuesta.texto


class TestDashboard:
    def test_owner_ve_los_ultimos_30_dias_en_el_dashboard(self, e):
        respuesta = solicitud("GET", "/", cookies=e.cookies)

        assert respuesta.status == 200
        assert 'id="dashboard-analitica"' in respuesta.texto
        # 5 u. a 200 con costo de referencia 130: margen (1000 − 650) / 1000.
        assert 'data-indicador="margen-30"' in respuesta.texto and "35,0 %" in respuesta.texto
        assert 'data-indicador="decisiones"' in respuesta.texto

    def test_cashier_no_recibe_margen_ni_deuda_en_el_dashboard(self, e):
        respuesta = solicitud("GET", "/", cookies=e.cookies_cajero)

        assert respuesta.status == 200
        assert 'id="dashboard-analitica"' not in respuesta.texto
        assert "Margen bruto" not in respuesta.texto and "Deuda de clientes" not in respuesta.texto


class TestResumen:
    def test_muestra_kpis_y_decisiones(self, e):
        respuesta = solicitud("GET", "/analitica", cookies=e.cookies)

        assert respuesta.status == 200
        assert 'id="kpi-ventas"' in respuesta.texto and "10,00" in respuesta.texto
        assert 'data-decision="costo-sin-precio"' in respuesta.texto
        assert "Gaseosa Cola" in respuesta.texto
        assert 'data-grafico="grafico-ventas-dia"' in respuesta.texto


class TestVentas:
    def test_periodos_comparaciones_y_top(self, e):
        respuesta = solicitud("GET", f"/analitica/ventas?fecha_desde={DESDE}&fecha_hasta={HASTA}&granularidad=mes", cookies=e.cookies)

        assert respuesta.status == 200
        assert f'data-periodo="{HASTA[:7]}"' in respuesta.texto
        assert 'id="top-facturacion"' in respuesta.texto and "5 u." in respuesta.texto
        assert "Historial insuficiente" in respuesta.texto

    def test_granularidad_invalida_es_error_claro(self, e):
        respuesta = solicitud("GET", "/analitica/ventas?granularidad=trimestre", cookies=e.cookies)
        assert respuesta.status == 303 and "tipo=error" in respuesta.header("location")

    def test_fechas_extremas_son_error_claro_no_500(self, e):
        respuesta = solicitud("GET", "/analitica/ventas?fecha_desde=0001-01-01&fecha_hasta=0001-01-05", cookies=e.cookies)
        assert respuesta.status == 303 and "tipo=error" in respuesta.header("location")


class TestMargen:
    def test_distingue_costo_de_referencia_y_real(self, e):
        respuesta = solicitud("GET", f"/analitica/margen?fecha_desde={DESDE}&fecha_hasta={HASTA}", cookies=e.cookies)

        assert respuesta.status == 200
        # 5 unidades: referencia 130 (costo vigente al vender), real 100 (partida consumida por FEFO).
        assert 'id="margen-referencia"' in respuesta.texto and "3,50" in respuesta.texto
        assert 'id="margen-real"' in respuesta.texto and "5,00" in respuesta.texto
        assert "-1,50" in respuesta.texto  # real − referencia sobre 5 u. comparables
        assert 'data-grupo="' + str(e.gaseosa.id) + '"' in respuesta.texto

    def test_exporta_csv(self, e):
        respuesta = solicitud("GET", f"/analitica/margen/exportar?fecha_desde={DESDE}&fecha_hasta={HASTA}", cookies=e.cookies)

        assert respuesta.status == 200
        assert respuesta.header("content-type").startswith("text/csv")
        assert "grupo,unidades,facturado" in respuesta.texto and "Gaseosa Cola" in respuesta.texto


class TestPrecios:
    def test_evolucion_y_costo_sin_precio(self, e):
        respuesta = solicitud("GET", f"/analitica/precios?fecha_desde={DESDE}&fecha_hasta={HASTA}&umbral=10", cookies=e.cookies)

        assert respuesta.status == 200
        assert 'id="costos-sin-precio"' in respuesta.texto
        assert f'data-producto="{e.gaseosa.id}" data-campo="COSTO"' in respuesta.texto
        assert "+30,0 %" in respuesta.texto

    def test_umbral_invalido(self, e):
        respuesta = solicitud("GET", "/analitica/precios?umbral=abc", cookies=e.cookies)
        assert respuesta.status == 303 and "tipo=error" in respuesta.header("location")

    def test_exporta_csv(self, e):
        respuesta = solicitud("GET", "/analitica/precios/exportar", cookies=e.cookies)
        assert respuesta.status == 200 and "producto_id,producto" in respuesta.texto


class TestDeuda:
    def test_clientes_y_proveedores(self, e):
        respuesta = solicitud("GET", "/analitica/deuda", cookies=e.cookies)

        assert respuesta.status == 200
        assert "Ana Pérez" in respuesta.texto and 'data-estado="NORMAL"' in respuesta.texto
        assert 'id="tabla-deuda-proveedores"' in respuesta.texto and "Alfa SA" in respuesta.texto
        assert "13,00" in respuesta.texto


class TestProveedores:
    def test_tabla_con_compras_y_costos(self, e):
        respuesta = solicitud("GET", f"/analitica/proveedores?fecha_desde={DESDE}&fecha_hasta={HASTA}", cookies=e.cookies)

        assert respuesta.status == 200
        assert f'data-proveedor="{e.proveedor.id}" data-seguimiento="si"' in respuesta.texto  # dependencia alta
        assert 'id="costos-por-proveedor"' in respuesta.texto and "+30,0 %" in respuesta.texto


class TestReposicionYPerdidas:
    def test_reposicion_explica_el_calculo(self, e):
        servicio_stock.actualizar_producto(e.gaseosa.id, "1", "Gaseosa Cola", 130, 200, 30)

        respuesta = solicitud("GET", "/reposicion", cookies=e.cookies)

        assert respuesta.status == 200
        assert "data-explicacion" in respuesta.texto and "Por mínimo: 30" in respuesta.texto
        assert "5 u. vendidas en 30 días" in respuesta.texto

    def test_perdidas_filtra_por_categoria(self, e):
        respuesta = solicitud("GET", "/reportes/perdidas?categoria_id=abc", cookies=e.cookies)
        assert respuesta.status == 303 and "tipo=error" in respuesta.header("location")
        respuesta = solicitud("GET", "/reportes/perdidas?categoria_id=999", cookies=e.cookies)
        assert respuesta.status == 303 and "tipo=error" in respuesta.header("location")
        respuesta = solicitud("GET", "/reportes/perdidas", cookies=e.cookies)
        assert respuesta.status == 200 and 'name="categoria_id"' in respuesta.texto
