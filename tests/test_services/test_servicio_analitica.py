"""Analítica V1.17 sobre datos reales: ventas por período, margen con costo de referencia y costo real de
partidas, evolución de precios, deuda por antigüedad, proveedores y reposición explicada."""

from datetime import date, timedelta

import pytest

from db.conexion import obtener_conexion
from domain.compra import ItemCompra
from domain.venta import ItemVenta
from excepciones import DatosInvalidosError
from services import (
    servicio_analitica,
    servicio_caja,
    servicio_categorias,
    servicio_clientes,
    servicio_compras,
    servicio_evaluaciones_proveedor,
    servicio_proveedores,
    servicio_reclamos_proveedor,
    servicio_stock,
    servicio_ventas,
)
from tests.utilidades_clientes import crear_owner, habilitar_credito_general, heredar_existencia_en_conexion

pytestmark = pytest.mark.sin_caja_abierta

HOY = date.today()
DESDE = (HOY - timedelta(days=29)).isoformat()
HASTA = HOY.isoformat()


@pytest.fixture
def e(base_datos_temporal):
    owner = crear_owner()
    servicio_caja.abrir_caja(0, None, owner.id)
    bebidas = servicio_categorias.crear_categoria("Bebidas")
    gaseosas = servicio_categorias.crear_categoria("Gaseosas", bebidas.id)
    gaseosa = servicio_stock.registrar_producto("1", "Gaseosa", 100, 200, categoria_id=gaseosas.id, stock_minimo=0)
    alfajor = servicio_stock.registrar_producto("2", "Alfajor", 50, 90, stock_actual=0)
    heredado = servicio_stock.registrar_producto("3", "Heredado", 30, 60)
    with obtener_conexion(inmediata=True) as conexion:
        heredar_existencia_en_conexion(conexion, heredado.id, 10)
        conexion.execute("UPDATE productos SET stock_actual = 10 WHERE id = ?", (heredado.id,))
    proveedor_a = servicio_proveedores.crear_proveedor("Alfa SA")
    proveedor_b = servicio_proveedores.crear_proveedor("Beta SRL")
    # Dos compras a Alfa: la segunda sube el costo de la gaseosa de 100 a 120 (historial COSTO).
    servicio_compras.registrar_compra(
        proveedor_a.id, owner.id, [ItemCompra(gaseosa.id, 10, 100)], origen_pago="FUERA_DE_CAJA"
    )
    servicio_compras.registrar_compra(proveedor_a.id, owner.id, [ItemCompra(gaseosa.id, 10, 120)], condicion_pago="CREDITO")
    # El alfajor cambia de precio de venta (90 → 110) sin tocar el costo.
    servicio_stock.actualizar_producto(alfajor.id, "2", "Alfajor", 50, 110, 0, usuario_id=owner.id)
    # Venta: 5 gaseosas (FEFO consume la partida a 100; la referencia es el costo vigente 120) y 2 heredados
    # (partida sin costo: costo real desconocido).
    venta = servicio_ventas.registrar_venta(
        [ItemVenta(gaseosa.id, 5), ItemVenta(heredado.id, 2)], "EFECTIVO", usuario_id=owner.id
    )
    return type("E", (), {
        "ruta": base_datos_temporal, "owner": owner, "bebidas": bebidas, "gaseosas": gaseosas,
        "gaseosa": gaseosa, "alfajor": alfajor, "heredado": heredado,
        "a": proveedor_a, "b": proveedor_b, "venta": venta,
    })


class TestVentas:
    def test_periodos_totales_y_comparaciones(self, e):
        analisis = servicio_analitica.analizar_ventas(DESDE, HASTA, "dia")

        assert (analisis.cantidad_ventas, analisis.unidades, analisis.total_centavos) == (1, 7, 1120)
        assert analisis.ticket_promedio_centavos == 1120
        [periodo] = analisis.periodos
        assert (periodo.clave, periodo.total_centavos) == (HASTA, 1120)
        assert analisis.comparacion_anterior.total_anterior_centavos == 0
        assert analisis.comparacion_anterior.variacion_total is None
        assert analisis.comparacion_anio_anterior.fecha_hasta == (HOY.replace(year=HOY.year - 1) if HOY.month != 2 or HOY.day != 29 else HOY.replace(year=HOY.year - 1, day=28)).isoformat()
        assert [g.etiqueta for g in analisis.top_facturacion] == ["Gaseosa", "Heredado"]
        assert not analisis.estacionalidad.suficiente

    def test_filtro_por_categoria_incluye_subcategorias(self, e):
        analisis = servicio_analitica.analizar_ventas(DESDE, HASTA, "mes", categoria_id=e.bebidas.id)

        assert (analisis.unidades, analisis.total_centavos) == (5, 1000)
        assert analisis.periodos[0].clave == HASTA[:7]

    def test_opciones_invalidas(self, e):
        with pytest.raises(DatosInvalidosError):
            servicio_analitica.analizar_ventas(DESDE, HASTA, "trimestre")
        with pytest.raises(DatosInvalidosError):
            servicio_analitica.analizar_ventas(DESDE, HASTA, categoria_id=999)
        with pytest.raises(DatosInvalidosError):
            servicio_analitica.analizar_ventas("2026-13-01", HASTA)


class TestMargen:
    def test_costo_de_referencia_costo_real_y_desconocido(self, e):
        analisis = servicio_analitica.analizar_margen(DESDE, HASTA, "producto")
        total = analisis.total

        assert total.facturado_centavos == 1120
        # Referencia: gaseosa 5 × 120 + heredado 2 × 30 (el costo vigente al vender siempre se conoce).
        assert total.costo_ref_centavos == 600 + 60
        assert total.margen_ref_centavos == 1120 - 660
        # Real: solo las 5 gaseosas salieron de una partida con costo (100); los 2 heredados no.
        assert (total.unidades_costo_real, total.costo_real_centavos) == (5, 500)
        assert total.unidades_sin_costo_real == 2
        # Sobre las mismas 5 unidades: real 500 vs referencia 600.
        assert total.diferencia_real_vs_ref_centavos == -100
        gaseosa, heredado = analisis.grupos
        assert (gaseosa.etiqueta, gaseosa.margen_ref_centavos) == ("Gaseosa", 400)
        assert heredado.diferencia_real_vs_ref_centavos is None

    def test_por_categoria_y_por_periodo(self, e):
        por_categoria = servicio_analitica.analizar_margen(DESDE, HASTA, "categoria").grupos
        assert [(g.etiqueta, g.unidades) for g in por_categoria] == [("Gaseosas", 5), ("Sin categoría", 2)]
        por_mes = servicio_analitica.analizar_margen(DESDE, HASTA, "mes").grupos
        assert [(g.clave, g.facturado_centavos) for g in por_mes] == [(HASTA[:7], 1120)]
        with pytest.raises(DatosInvalidosError):
            servicio_analitica.analizar_margen(DESDE, HASTA, "vendedor")


class TestPrecios:
    def test_evolucion_desde_el_historial_real(self, e):
        analisis = servicio_analitica.analizar_precios(DESDE, HASTA)

        por_clave = {(ev.producto_nombre, ev.campo): ev for ev in analisis.evoluciones}
        costo_gaseosa = por_clave[("Gaseosa", "COSTO")]
        assert (costo_gaseosa.inicial_centavos, costo_gaseosa.final_centavos, costo_gaseosa.cambios) == (100, 120, 1)
        assert costo_gaseosa.variacion_porcentual == 20
        venta_alfajor = por_clave[("Alfajor", "VENTA")]
        assert (venta_alfajor.inicial_centavos, venta_alfajor.final_centavos) == (90, 110)
        assert ("Gaseosa", "VENTA") not in por_clave
        assert {ev.producto_nombre for ev in analisis.relevantes} == {"Gaseosa", "Alfajor"}
        assert [p.producto_nombre for p in analisis.costos_sin_precio] == ["Gaseosa"]
        [mes] = analisis.por_mes
        assert (mes.clave, mes.subas_costo, mes.subas_venta, mes.bajas_costo) == (HASTA[:7], 1, 1, 0)
        assert [c.producto_nombre for c in analisis.cambios] == ["Alfajor", "Gaseosa"]

    def test_umbral_y_filtros(self, e):
        analisis = servicio_analitica.analizar_precios(DESDE, HASTA, categoria_id=e.bebidas.id, umbral="25")
        assert [ev.producto_nombre for ev in analisis.evoluciones] == ["Gaseosa"]
        assert analisis.relevantes == []
        with pytest.raises(DatosInvalidosError):
            servicio_analitica.analizar_precios(DESDE, HASTA, umbral="mucho")


class TestDeuda:
    def test_clientes_y_proveedores_separados(self, e):
        habilitar_credito_general(e.owner.id)
        cliente = servicio_clientes.crear_cliente("Ana", e.owner.id)
        servicio_ventas.registrar_venta(
            [ItemVenta(e.gaseosa.id, 1)], "CUENTA_CORRIENTE", usuario_id=e.owner.id, cliente_id=cliente.id
        )

        analisis = servicio_analitica.analizar_deuda(HOY)

        [ana] = analisis.clientes.clientes
        assert (ana.cliente_nombre, ana.saldo_centavos, ana.estado, ana.strikes, ana.credito_cerrado) == (
            "Ana", 200, "NORMAL", 0, False
        )
        assert analisis.clientes.total_centavos == 200
        assert analisis.proveedores_total_centavos == 1200
        [alfa] = analisis.proveedores.filas
        assert alfa.nombre == "Alfa SA"
        assert analisis.evolucion_clientes[-1].saldo_centavos == 200
        assert analisis.evolucion_proveedores[-1].saldo_centavos == 1200

    def test_sin_deuda(self, e):
        analisis = servicio_analitica.analizar_deuda(HOY)
        assert analisis.clientes.clientes == []
        assert analisis.evolucion_clientes == []


class TestProveedores:
    def test_compras_calificacion_reclamos_costos_y_dependencia(self, e):
        servicio_evaluaciones_proveedor.registrar_evaluacion(e.a.id, e.owner.id, 2, "Entregas tarde")
        servicio_reclamos_proveedor.crear_reclamo(e.a.id, e.owner.id, "ENTREGA", "Faltó una caja", producto_id=e.gaseosa.id)

        analisis = servicio_analitica.analizar_proveedores(DESDE, HASTA)

        alfa, beta = analisis.proveedores
        assert (alfa.nombre, alfa.cantidad_compras, alfa.unidades, alfa.total_centavos) == ("Alfa SA", 2, 20, 2200)
        assert alfa.participacion == 100
        assert (alfa.calificacion, alfa.evaluaciones, alfa.reclamos_total, alfa.reclamos_abiertos) == (2, 1, 1, 1)
        assert alfa.saldo_centavos == 1200
        assert (alfa.costos_subieron, alfa.costos_bajaron) == (1, 0)
        [costo] = alfa.costos
        assert (costo.producto_nombre, costo.costo_inicial_centavos, costo.costo_final_centavos, costo.compras) == (
            "Gaseosa", 100, 120, 2
        )
        [dependencia] = alfa.dependencias
        assert (dependencia.categoria_nombre, dependencia.porcentaje, dependencia.alta) == ("Gaseosas", 100, True)
        assert alfa.requiere_seguimiento
        assert (beta.cantidad_compras, beta.participacion, beta.calificacion, beta.requiere_seguimiento) == (0, None, None, False)
        assert [p.nombre for p in analisis.con_seguimiento] == ["Alfa SA"]


class TestReposicion:
    def test_sugerencia_explicada_con_rotacion_y_proveedor(self, e):
        servicio_stock.actualizar_producto(e.gaseosa.id, "1", "Gaseosa", 120, 200, 20, categoria_id=e.gaseosas.id)
        servicio_evaluaciones_proveedor.registrar_evaluacion(e.a.id, e.owner.id, 4, "Bien")

        [sugerencia] = servicio_stock.listar_reposicion()

        assert (sugerencia.stock_actual, sugerencia.stock_minimo, sugerencia.cantidad_sugerida) == (15, 20, 5)
        assert sugerencia.unidades_vendidas_periodo == 5
        # 5 u. en 30 días: 15 u. cubren 90 días; para 14 días no falta nada.
        assert (sugerencia.cobertura_dias, sugerencia.sugerida_por_rotacion) == (90, 0)
        assert sugerencia.calificacion_proveedor == 4
        assert sugerencia.costo_es_del_proveedor and sugerencia.costo_unitario_centavos == 120
        texto = " ".join(sugerencia.explicacion)
        assert "Por mínimo: 20 (mínimo) − 15 (stock) = 5 u." in texto
        assert "5 u. vendidas en 30 días" in texto
        assert "calificado con 4 de 5" in texto

    def test_sin_ventas_lo_dice(self, e):
        servicio_stock.actualizar_producto(e.alfajor.id, "2", "Alfajor", 50, 110, 3)

        [sugerencia] = servicio_stock.listar_reposicion()

        assert sugerencia.sugerida_por_rotacion is None and sugerencia.cobertura_dias is None
        assert any("sin ventas" in linea for linea in sugerencia.explicacion)


class TestResumenEjecutivo:
    def test_integra_los_analisis_y_las_decisiones(self, e):
        servicio_stock.actualizar_producto(e.alfajor.id, "2", "Alfajor", 50, 110, 3)

        resumen = servicio_analitica.resumen_ejecutivo(HOY)

        assert (resumen.fecha_desde, resumen.fecha_hasta) == (DESDE, HASTA)
        assert resumen.ventas.total_centavos == 1120
        assert resumen.margen.total.costo_real_centavos == 500
        assert resumen.deuda.proveedores_total_centavos == 1200
        assert resumen.reposicion_pendiente == 1
        assert resumen.stock_critico == 1
        claves = {decision.clave for decision in resumen.decisiones}
        assert {"costo-sin-precio", "reposicion"} <= claves
        assert "deuda-critica" not in claves
        costo = next(d for d in resumen.decisiones if d.clave == "costo-sin-precio")
        assert costo.cantidad == 1 and costo.href.startswith("/analitica/precios")


class TestDeudaProveedoresConLibroInconsistente:
    def test_los_totales_coinciden_con_el_reporte_y_excluyen_libros_inconsistentes(self):
        from domain.analitica import AnalisisDeudaClientes
        from domain.deuda_proveedores import FilaDeudaProveedor, ReporteDeudaProveedores
        from services.servicio_analitica import AnalisisDeuda

        consistente = FilaDeudaProveedor(1, "Alfa", True, True, 1000, 400, 100, 500, 0, None, None)
        inconsistente = FilaDeudaProveedor(2, "Beta", True, False, 500, None, None, None, None, None, None)
        reporte = ReporteDeudaProveedores(None, "todas", (consistente, inconsistente))
        analisis = AnalisisDeuda(AnalisisDeudaClientes(HOY, []), reporte, [], [])

        assert analisis.proveedores_total_centavos == reporte.total_saldo_centavos == 1000
        assert [analisis.proveedores_por_situacion(s) for s in ("vencida", "proxima", "vigente", "sin_vencimiento")] == [400, 100, 500, 0]
