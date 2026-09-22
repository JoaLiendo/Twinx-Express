"""Pruebas de services.servicio_reportes (módulo de Reportes): agregación
de ventas por período. La resolución de datos ya está probada en
tests/test_db/test_ventas.py -- acá se prueba la agregación en sí
(totales, agrupación por medio de pago y por día, rango por defecto)."""

from datetime import date, timedelta

from db.conexion import obtener_conexion
from db.repositorios import productos as repositorio_productos
from db.repositorios import ventas as repositorio_ventas
from domain.producto import Producto
from domain.venta import ItemVenta
from services import servicio_reportes


def _crear_producto(codigo="7790000000001"):
    return repositorio_productos.crear_producto(
        Producto(codigo_barras=codigo, nombre="Alfajor", precio_costo_centavos=100, precio_venta_centavos=200)
    )


def _registrar_venta(total_centavos, tipo_pago, producto, cantidad=1, precio_unitario=200):
    with obtener_conexion() as conexion:
        return repositorio_ventas.registrar_venta_con_detalle(
            conexion, total_centavos, tipo_pago, [(ItemVenta(producto.id, cantidad), precio_unitario)]
        )


class TestGenerarReporteVentas:
    def test_sin_ventas_devuelve_reporte_en_cero(self, base_datos_temporal):
        reporte = servicio_reportes.generar_reporte_ventas()

        assert reporte.cantidad_ventas == 0
        assert reporte.total_facturado_centavos == 0
        assert reporte.ticket_promedio_centavos == 0
        assert reporte.ventas_por_medio_pago == []
        assert reporte.evolucion_por_dia == []
        assert reporte.productos_mas_vendidos == []

    def test_totales_y_ticket_promedio(self, base_datos_temporal):
        producto = _crear_producto()
        _registrar_venta(200, "EFECTIVO", producto)
        _registrar_venta(300, "EFECTIVO", producto)

        reporte = servicio_reportes.generar_reporte_ventas()

        assert reporte.cantidad_ventas == 2
        assert reporte.total_facturado_centavos == 500
        assert reporte.ticket_promedio_centavos == 250

    def test_ticket_promedio_usa_division_entera(self, base_datos_temporal):
        producto = _crear_producto()
        _registrar_venta(100, "EFECTIVO", producto)
        _registrar_venta(100, "EFECTIVO", producto)
        _registrar_venta(101, "EFECTIVO", producto)

        reporte = servicio_reportes.generar_reporte_ventas()

        assert reporte.total_facturado_centavos == 301
        assert reporte.ticket_promedio_centavos == 100  # 301 // 3, nunca float

    def test_agrupa_por_medio_de_pago(self, base_datos_temporal):
        producto = _crear_producto()
        _registrar_venta(200, "EFECTIVO", producto)
        _registrar_venta(300, "EFECTIVO", producto)
        _registrar_venta(500, "TARJETA", producto)

        reporte = servicio_reportes.generar_reporte_ventas()

        por_tipo = {m.tipo_pago: m for m in reporte.ventas_por_medio_pago}
        assert por_tipo["EFECTIVO"].cantidad_ventas == 2
        assert por_tipo["EFECTIVO"].total_centavos == 500
        assert por_tipo["TARJETA"].cantidad_ventas == 1
        assert por_tipo["TARJETA"].total_centavos == 500

    def test_medio_de_pago_mas_facturado_aparece_primero(self, base_datos_temporal):
        producto = _crear_producto()
        _registrar_venta(100, "TARJETA", producto)
        _registrar_venta(900, "EFECTIVO", producto)

        reporte = servicio_reportes.generar_reporte_ventas()

        assert reporte.ventas_por_medio_pago[0].tipo_pago == "EFECTIVO"

    def test_evolucion_por_dia_agrupa_por_fecha(self, base_datos_temporal):
        producto = _crear_producto()
        venta_hoy = _registrar_venta(200, "EFECTIVO", producto)
        venta_ayer = _registrar_venta(300, "EFECTIVO", producto)
        ayer = (date.today() - timedelta(days=1)).isoformat()
        with obtener_conexion() as conexion:
            conexion.execute("UPDATE ventas SET fecha = ? WHERE id = ?", (f"{ayer} 09:00:00", venta_ayer.id))

        reporte = servicio_reportes.generar_reporte_ventas(fecha_desde=ayer, fecha_hasta=date.today().isoformat())

        assert len(reporte.evolucion_por_dia) == 2
        por_fecha = {d.fecha: d for d in reporte.evolucion_por_dia}
        assert por_fecha[ayer].total_centavos == 300
        assert por_fecha[date.today().isoformat()].total_centavos == 200
        # Orden ascendente por fecha (el día más viejo primero).
        assert [d.fecha for d in reporte.evolucion_por_dia] == sorted(por_fecha)
        assert venta_hoy.id != venta_ayer.id  # sanity: son ventas distintas

    def test_productos_mas_vendidos_viene_del_repositorio(self, base_datos_temporal):
        producto = _crear_producto()
        _registrar_venta(400, "EFECTIVO", producto, cantidad=2, precio_unitario=200)

        reporte = servicio_reportes.generar_reporte_ventas()

        assert len(reporte.productos_mas_vendidos) == 1
        assert reporte.productos_mas_vendidos[0].unidades_vendidas == 2

    def test_rango_por_defecto_son_los_ultimos_7_dias_incluyendo_hoy(self, base_datos_temporal):
        producto = _crear_producto()
        _registrar_venta(200, "EFECTIVO", producto)

        reporte = servicio_reportes.generar_reporte_ventas()

        assert reporte.fecha_hasta == date.today().isoformat()
        assert reporte.fecha_desde == (date.today() - timedelta(days=6)).isoformat()
        assert reporte.cantidad_ventas == 1

    def test_rango_a_medio_especificar_usa_el_rango_por_defecto_completo(self, base_datos_temporal):
        """Pasar solo fecha_desde (sin fecha_hasta) es ambiguo: se trata
        igual que no pasar ningún límite, no se adivina el que falta."""
        reporte = servicio_reportes.generar_reporte_ventas(fecha_desde="2020-01-01")

        assert reporte.fecha_desde == (date.today() - timedelta(days=6)).isoformat()
        assert reporte.fecha_hasta == date.today().isoformat()

    def test_rango_explicito_fuera_de_la_ventana_por_defecto_se_respeta(self, base_datos_temporal):
        producto = _crear_producto()
        venta = _registrar_venta(200, "EFECTIVO", producto)
        with obtener_conexion() as conexion:
            conexion.execute("UPDATE ventas SET fecha = ? WHERE id = ?", ("2021-06-15 10:00:00", venta.id))

        reporte = servicio_reportes.generar_reporte_ventas(fecha_desde="2021-06-01", fecha_hasta="2021-06-30")

        assert reporte.cantidad_ventas == 1
        assert reporte.total_facturado_centavos == 200

    def test_rango_sin_ventas_devuelve_reporte_en_cero_sin_error(self, base_datos_temporal):
        producto = _crear_producto()
        _registrar_venta(200, "EFECTIVO", producto)

        reporte = servicio_reportes.generar_reporte_ventas(fecha_desde="1999-01-01", fecha_hasta="1999-01-31")

        assert reporte.cantidad_ventas == 0
        assert reporte.total_facturado_centavos == 0
        assert reporte.ticket_promedio_centavos == 0
