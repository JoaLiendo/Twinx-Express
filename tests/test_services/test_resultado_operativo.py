"""Resultado operativo estimado (bloque A3): margen bruto − gastos vigentes por período, anulaciones
informadas aparte, costo desconocido avisado y nunca inventado, sin doble conteo de la mercadería."""

import pytest

from domain.analitica import LineaVentaDia
from domain.compra import ItemCompra
from domain.resultado import AnulacionesDia, agrupar_resultado
from domain.venta import ItemVenta
from excepciones import DatosInvalidosError
from services import servicio_analitica, servicio_compras, servicio_gastos, servicio_proveedores, servicio_stock, servicio_ventas
from tests.utilidades_clientes import usuario_logueado

from tests.test_interfaces_web._asgi_cliente import solicitud


def _linea(fecha, unidades, facturado, costo_ref=None):
    con_costo = costo_ref is not None
    return LineaVentaDia(
        fecha=fecha, producto_id=1, producto_nombre="P", categoria_id=None, categoria_nombre=None,
        unidades=unidades, facturado_centavos=facturado,
        unidades_con_costo_ref=unidades if con_costo else 0, costo_ref_centavos=(unidades * costo_ref) if con_costo else 0,
        facturado_con_costo_ref_centavos=facturado if con_costo else 0,
        unidades_costo_real=0, costo_real_centavos=0, unidades_comparables=0,
        costo_ref_comparable_centavos=0, costo_real_comparable_centavos=0,
    )


class TestDominio:
    def test_agrupa_por_mes_y_calcula_margen_menos_gastos(self):
        lineas = [_linea("2026-10-05", 10, 10_000, 600), _linea("2026-11-02", 5, 6_000, 600)]
        anulaciones = [AnulacionesDia("2026-11-03", 1, 2_000)]
        gastos = [("2026-10-01", 1_500), ("2026-11-10", 900), ("2026-12-01", 100)]
        total, periodos = agrupar_resultado(lineas, anulaciones, gastos, "mes")

        assert [p.clave for p in periodos] == ["2026-10", "2026-11", "2026-12"]
        octubre, noviembre, diciembre = periodos
        assert octubre.margen_bruto_centavos == 4_000 and octubre.resultado_operativo_centavos == 2_500
        assert noviembre.margen_bruto_centavos == 3_000 and noviembre.gastos_centavos == 900
        assert noviembre.resultado_operativo_centavos == 2_100 and noviembre.anulaciones_cantidad == 1
        # Un período solo con gastos: resultado negativo igual al gasto, sin ventas.
        assert diciembre.facturado_centavos == 0 and diciembre.resultado_operativo_centavos == -100
        assert total.facturado_centavos == 16_000 and total.gastos_centavos == 2_500
        assert total.resultado_operativo_centavos == 7_000 - 2_500 and total.anulaciones_centavos == 2_000
        assert total.resultado_operativo_porcentual == pytest.approx(4_500 / 16_000 * 100)

    def test_sin_costo_conocido_no_hay_resultado_y_se_avisa(self):
        total, periodos = agrupar_resultado([_linea("2026-11-02", 5, 6_000)], [], [("2026-11-02", 100)], "dia")
        assert total.margen_bruto_centavos is None and total.resultado_operativo_centavos is None
        assert total.incompleto and total.unidades_sin_costo == 5
        assert periodos[0].resultado_operativo_porcentual is None

    def test_granularidad_desconocida(self):
        with pytest.raises(ValueError):
            agrupar_resultado([], [], [], "anio")


@pytest.fixture
def escenario(caja_abierta):
    owner, cookies = usuario_logueado("OWNER", "duenio")
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 250, stock_actual=0)
    proveedor = servicio_proveedores.crear_proveedor("Prov")
    servicio_compras.registrar_compra(proveedor.id, owner.id, [ItemCompra(producto.id, 20, 100)], origen_pago="FUERA_DE_CAJA")
    servicio_ventas.registrar_venta([ItemVenta(producto.id, 4)], "EFECTIVO", usuario_id=owner.id)  # 1000 facturado, 400 costo
    anulada = servicio_ventas.registrar_venta([ItemVenta(producto.id, 2)], "EFECTIVO", usuario_id=owner.id)
    servicio_ventas.anular_venta(anulada.id, "ERROR_CARGA", None, owner.id)
    from datetime import date

    hoy = date.today().isoformat()
    servicio_gastos.registrar_gasto(usuario_id=owner.id, fecha=hoy, categoria="ALQUILER", descripcion="Alq", importe_centavos=350, medio_pago="EFECTIVO")
    anulado = servicio_gastos.registrar_gasto(usuario_id=owner.id, fecha=hoy, categoria="AGUA", descripcion="Dup", importe_centavos=9_999, medio_pago="EFECTIVO")
    servicio_gastos.anular_gasto(anulado.id, usuario_id=owner.id, motivo="Duplicado")
    return owner, cookies, hoy


class TestServicioYWeb:
    def test_el_servicio_cruza_ventas_anulaciones_y_gastos_sin_contar_la_compra(self, escenario):
        _, _, hoy = escenario
        analisis = servicio_analitica.analizar_resultado(hoy, hoy, "dia")
        total = analisis.total
        assert total.facturado_centavos == 1_000 and total.costo_conocido_centavos == 400
        assert total.margen_bruto_centavos == 600
        assert total.anulaciones_cantidad == 1 and total.anulaciones_centavos == 500
        assert total.gastos_centavos == 350  # el gasto anulado no cuenta; la compra de mercadería tampoco
        assert total.resultado_operativo_centavos == 250
        assert [(t.categoria, t.importe_centavos) for t in analisis.gastos_por_categoria] == [("ALQUILER", 350)]
        assert len(analisis.periodos) == 1 and analisis.periodos[0].clave == hoy
        with pytest.raises(DatosInvalidosError):
            servicio_analitica.analizar_resultado(hoy, hoy, "anio")

    def test_la_pantalla_y_el_csv_muestran_la_estimacion_y_sus_limites(self, escenario):
        _, cookies, hoy = escenario
        html = solicitud("GET", f"/analitica/resultado?fecha_desde={hoy}&fecha_hasta={hoy}&granularidad=dia", cookies=cookies).texto
        assert "Resultado operativo estimado" in html and "no la ganancia neta" in html
        assert 'id="resultado-operativo"' in html and "$2,50" in html
        assert "1 venta(s) anulada(s) por $5,00" in html
        assert "Alquiler" in html and 'href="/analitica/resultado"' in html
        csv = solicitud("GET", f"/analitica/resultado/exportar?fecha_desde={hoy}&fecha_hasta={hoy}&granularidad=dia", cookies=cookies)
        assert csv.status == 200 and "resultado_operativo_estimado" in csv.texto and "2.50" in csv.texto
        assert "Total del período" in csv.texto

    def test_el_cajero_no_accede(self, escenario):
        _, cajero = usuario_logueado("CASHIER", "cajera")
        assert solicitud("GET", "/analitica/resultado", cookies=cajero).status == 403
        assert solicitud("GET", "/analitica/resultado/exportar", cookies=cajero).status == 403

    def test_sin_datos_la_pantalla_no_falla(self, caja_abierta):
        _, cookies = usuario_logueado("OWNER", "duenio")
        html = solicitud("GET", "/analitica/resultado", cookies=cookies).texto
        assert "Sin ventas ni gastos en el período" in html
