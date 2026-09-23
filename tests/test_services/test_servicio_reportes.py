"""Pruebas de services.servicio_reportes (módulo de Reportes): agregación
de ventas por período. La resolución de datos ya está probada en
tests/test_db/test_ventas.py -- acá se prueba la agregación en sí
(totales, agrupación por medio de pago y por día, rango por defecto)."""

from datetime import date, timedelta

from db.conexion import obtener_conexion
from db.repositorios import productos as repositorio_productos
from db.repositorios import usuarios as repositorio_usuarios
from db.repositorios import ventas as repositorio_ventas
from domain.producto import Producto
from domain.usuario import Usuario
from domain.venta import ItemVenta
from services import servicio_reportes


def _crear_producto(codigo="7790000000001"):
    return repositorio_productos.crear_producto(
        Producto(codigo_barras=codigo, nombre="Alfajor", precio_costo_centavos=100, precio_venta_centavos=200)
    )


def _crear_usuario(nombre_usuario="cajera1", rol="CASHIER"):
    return repositorio_usuarios.crear_usuario(
        Usuario(nombre_usuario=nombre_usuario, nombre_completo="Test", password_hash="hash", rol=rol)
    )


def _registrar_venta_con_costo(producto, cantidad, precio_unitario, costo_unitario, usuario_id=None):
    """Venta "nueva" (Reportes V2): con costo histórico conocido y,
    opcionalmente, usuario."""
    with obtener_conexion() as conexion:
        return repositorio_ventas.registrar_venta_con_detalle(
            conexion,
            precio_unitario * cantidad,
            "EFECTIVO",
            [(ItemVenta(producto.id, cantidad), precio_unitario)],
            usuario_id=usuario_id,
            costos_unitarios_por_producto_id={producto.id: costo_unitario},
        )


def _registrar_venta(total_centavos, tipo_pago, producto, cantidad=1, precio_unitario=200):
    with obtener_conexion() as conexion:
        return repositorio_ventas.registrar_venta_con_detalle(
            conexion, total_centavos, tipo_pago, [(ItemVenta(producto.id, cantidad), precio_unitario)]
        )


def _anular_directamente(venta_id, motivo="ERROR_CARGA"):
    """Marca una venta como ANULADA directo por SQL (Visibilidad de
    Anulaciones): estos tests prueban agregación de reportes, no el
    mecanismo transaccional de anulación (ya cubierto en
    tests/test_servicio_ventas.py::TestAnularVenta) -- no hace falta
    pasar por `servicio_ventas.anular_venta` ni abrir una caja para
    fijar `motivo_anulacion`, mismo criterio que el resto de este
    archivo ya usa para manipular `fecha` directo."""
    with obtener_conexion() as conexion:
        conexion.execute(
            "UPDATE ventas SET estado = 'ANULADA', motivo_anulacion = ?, "
            "fecha_anulacion = datetime('now', 'localtime') WHERE id = ?",
            (motivo, venta_id),
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


class TestRentabilidad:
    """Reportes V2: margen bruto y porcentual del período, a partir de
    `repositorio_ventas.calcular_rentabilidad_en_rango`."""

    def test_una_linea(self, base_datos_temporal):
        producto = _crear_producto()
        _registrar_venta_con_costo(producto, 2, precio_unitario=200, costo_unitario=100)

        reporte = servicio_reportes.generar_reporte_ventas()

        assert reporte.rentabilidad.costo_total_centavos == 200
        assert reporte.rentabilidad.margen_bruto_centavos == 200  # 400 - 200
        assert reporte.rentabilidad.margen_porcentual == 50.0  # 200 / 400 * 100
        assert reporte.rentabilidad.cantidad_ventas_sin_costo_historico == 0

    def test_multiples_lineas(self, base_datos_temporal):
        p1 = _crear_producto("7790000000001")
        p2 = _crear_producto("7790000000002")
        _registrar_venta_con_costo(p1, 1, precio_unitario=200, costo_unitario=100)
        _registrar_venta_con_costo(p2, 1, precio_unitario=300, costo_unitario=150)

        reporte = servicio_reportes.generar_reporte_ventas()

        assert reporte.rentabilidad.costo_total_centavos == 250
        assert reporte.rentabilidad.margen_bruto_centavos == 250  # 500 - 250

    def test_costo_cero(self, base_datos_temporal):
        producto = _crear_producto()
        _registrar_venta_con_costo(producto, 1, precio_unitario=200, costo_unitario=0)

        reporte = servicio_reportes.generar_reporte_ventas()

        assert reporte.rentabilidad.costo_total_centavos == 0
        assert reporte.rentabilidad.margen_bruto_centavos == 200
        assert reporte.rentabilidad.margen_porcentual == 100.0

    def test_costo_igual_al_precio(self, base_datos_temporal):
        producto = _crear_producto()
        _registrar_venta_con_costo(producto, 1, precio_unitario=200, costo_unitario=200)

        reporte = servicio_reportes.generar_reporte_ventas()

        assert reporte.rentabilidad.margen_bruto_centavos == 0
        assert reporte.rentabilidad.margen_porcentual == 0.0

    def test_costo_mayor_al_precio_da_margen_negativo(self, base_datos_temporal):
        producto = _crear_producto()
        _registrar_venta_con_costo(producto, 1, precio_unitario=200, costo_unitario=350)

        reporte = servicio_reportes.generar_reporte_ventas()

        assert reporte.rentabilidad.margen_bruto_centavos == -150
        assert reporte.rentabilidad.margen_porcentual == -75.0  # -150 / 200 * 100

    def test_multiples_ventas_se_acumulan(self, base_datos_temporal):
        producto = _crear_producto()
        _registrar_venta_con_costo(producto, 1, precio_unitario=200, costo_unitario=100)
        _registrar_venta_con_costo(producto, 1, precio_unitario=200, costo_unitario=100)

        reporte = servicio_reportes.generar_reporte_ventas()

        assert reporte.rentabilidad.costo_total_centavos == 200
        assert reporte.rentabilidad.margen_bruto_centavos == 200

    def test_venta_antigua_sin_costo_se_excluye_pero_cuenta_en_facturacion_general(self, base_datos_temporal):
        producto = _crear_producto()
        _registrar_venta(200, "EFECTIVO", producto)

        reporte = servicio_reportes.generar_reporte_ventas()

        assert reporte.cantidad_ventas == 1
        assert reporte.total_facturado_centavos == 200  # facturación general SÍ la incluye
        assert reporte.rentabilidad.costo_total_centavos == 0
        assert reporte.rentabilidad.margen_bruto_centavos == 0
        assert reporte.rentabilidad.cantidad_ventas_sin_costo_historico == 1

    def test_mezcla_de_ventas_antiguas_y_nuevas(self, base_datos_temporal):
        producto = _crear_producto()
        _registrar_venta_con_costo(producto, 1, precio_unitario=200, costo_unitario=100)
        _registrar_venta(300, "EFECTIVO", producto, precio_unitario=300)

        reporte = servicio_reportes.generar_reporte_ventas()

        assert reporte.cantidad_ventas == 2
        assert reporte.total_facturado_centavos == 500
        assert reporte.rentabilidad.costo_total_centavos == 100
        assert reporte.rentabilidad.margen_bruto_centavos == 100  # solo la venta con costo conocido
        assert reporte.rentabilidad.cantidad_ventas_sin_costo_historico == 1

    def test_periodo_sin_ningun_costo_conocido_deja_margen_porcentual_en_none(self, base_datos_temporal):
        """None, no 0: "sin datos" y "margen de 0%" son cosas distintas."""
        producto = _crear_producto()
        _registrar_venta(200, "EFECTIVO", producto)

        reporte = servicio_reportes.generar_reporte_ventas()

        assert reporte.rentabilidad.margen_porcentual is None

    def test_sin_ventas_deja_margen_porcentual_en_none(self, base_datos_temporal):
        reporte = servicio_reportes.generar_reporte_ventas()

        assert reporte.rentabilidad.costo_total_centavos == 0
        assert reporte.rentabilidad.margen_bruto_centavos == 0
        assert reporte.rentabilidad.margen_porcentual is None
        assert reporte.rentabilidad.cantidad_ventas_sin_costo_historico == 0

    def test_montos_son_enteros_y_solo_el_porcentaje_es_float(self, base_datos_temporal):
        producto = _crear_producto()
        _registrar_venta_con_costo(producto, 1, precio_unitario=200, costo_unitario=100)

        reporte = servicio_reportes.generar_reporte_ventas()

        assert isinstance(reporte.rentabilidad.costo_total_centavos, int)
        assert isinstance(reporte.rentabilidad.margen_bruto_centavos, int)
        assert isinstance(reporte.rentabilidad.margen_porcentual, float)


class TestProductosMasVendidosConMargen:
    def test_incluye_costo_y_margen_por_producto(self, base_datos_temporal):
        producto = _crear_producto()
        _registrar_venta_con_costo(producto, 2, precio_unitario=200, costo_unitario=100)

        reporte = servicio_reportes.generar_reporte_ventas()

        item = reporte.productos_mas_vendidos[0]
        assert item.costo_total_centavos == 200
        assert item.margen_bruto_centavos == 200

    def test_producto_sin_costo_conocido_deja_margen_en_none(self, base_datos_temporal):
        producto = _crear_producto()
        _registrar_venta(200, "EFECTIVO", producto)

        reporte = servicio_reportes.generar_reporte_ventas()

        item = reporte.productos_mas_vendidos[0]
        assert item.costo_total_centavos is None
        assert item.margen_bruto_centavos is None


class TestVentasPorUsuario:
    def test_owner_y_cashier_aparecen_con_su_propia_venta(self, base_datos_temporal):
        producto = _crear_producto()
        owner = _crear_usuario(nombre_usuario="duenio", rol="OWNER")
        cajera = _crear_usuario(nombre_usuario="cajera1", rol="CASHIER")
        _registrar_venta_con_costo(producto, 1, precio_unitario=200, costo_unitario=100, usuario_id=owner.id)
        _registrar_venta_con_costo(producto, 1, precio_unitario=300, costo_unitario=100, usuario_id=cajera.id)

        reporte = servicio_reportes.generar_reporte_ventas()

        por_usuario = {v.usuario_id: v for v in reporte.ventas_por_usuario}
        assert por_usuario[owner.id].cantidad_ventas == 1
        assert por_usuario[owner.id].total_vendido_centavos == 200
        assert por_usuario[cajera.id].total_vendido_centavos == 300
        assert por_usuario[owner.id].nombre_completo == "Test"

    def test_usuario_desactivado_sigue_apareciendo(self, base_datos_temporal):
        producto = _crear_producto()
        usuario = _crear_usuario()
        _registrar_venta_con_costo(producto, 1, precio_unitario=200, costo_unitario=100, usuario_id=usuario.id)
        repositorio_usuarios.actualizar_activo(usuario.id, False)

        reporte = servicio_reportes.generar_reporte_ventas()

        assert len(reporte.ventas_por_usuario) == 1
        assert reporte.ventas_por_usuario[0].usuario_activo is False

    def test_usuario_id_null_no_aparece_pero_venta_cuenta_en_el_total_general(self, base_datos_temporal):
        producto = _crear_producto()
        _registrar_venta(200, "EFECTIVO", producto)

        reporte = servicio_reportes.generar_reporte_ventas()

        assert reporte.cantidad_ventas == 1
        assert reporte.ventas_por_usuario == []

    def test_sin_ventas_devuelve_lista_vacia(self, base_datos_temporal):
        reporte = servicio_reportes.generar_reporte_ventas()

        assert reporte.ventas_por_usuario == []


class TestResumenAnulaciones:
    """Visibilidad de Anulaciones: `ReporteVentas.resumen_anulaciones`,
    calculado con `listar_resumen(..., estado="ANULADA")` -- separado
    de los cuatro cálculos analíticos, que deben seguir dando lo mismo
    con o sin ventas anuladas de por medio."""

    def test_sin_anulaciones_devuelve_ceros(self, base_datos_temporal):
        producto = _crear_producto()
        _registrar_venta(200, "EFECTIVO", producto)

        reporte = servicio_reportes.generar_reporte_ventas()

        assert reporte.resumen_anulaciones.cantidad == 0
        assert reporte.resumen_anulaciones.monto_total_centavos == 0
        assert reporte.resumen_anulaciones.por_motivo == []

    def test_una_anulacion(self, base_datos_temporal):
        producto = _crear_producto()
        venta = _registrar_venta(300, "EFECTIVO", producto)
        _anular_directamente(venta.id, motivo="ERROR_CARGA")

        reporte = servicio_reportes.generar_reporte_ventas()

        assert reporte.resumen_anulaciones.cantidad == 1
        assert reporte.resumen_anulaciones.monto_total_centavos == 300
        assert len(reporte.resumen_anulaciones.por_motivo) == 1
        item = reporte.resumen_anulaciones.por_motivo[0]
        assert item.motivo == "ERROR_CARGA"
        assert item.cantidad == 1
        assert item.monto_centavos == 300

    def test_multiples_motivos_se_agrupan_por_separado(self, base_datos_temporal):
        producto = _crear_producto()
        v1 = _registrar_venta(200, "EFECTIVO", producto)
        v2 = _registrar_venta(500, "EFECTIVO", producto)
        v3 = _registrar_venta(100, "EFECTIVO", producto)
        _anular_directamente(v1.id, motivo="ERROR_CARGA")
        _anular_directamente(v2.id, motivo="ARREPENTIMIENTO_CLIENTE")
        _anular_directamente(v3.id, motivo="ERROR_CARGA")

        reporte = servicio_reportes.generar_reporte_ventas()

        assert reporte.resumen_anulaciones.cantidad == 3
        assert reporte.resumen_anulaciones.monto_total_centavos == 800
        por_motivo = {item.motivo: item for item in reporte.resumen_anulaciones.por_motivo}
        assert por_motivo["ERROR_CARGA"].cantidad == 2
        assert por_motivo["ERROR_CARGA"].monto_centavos == 300  # 200 + 100
        assert por_motivo["ARREPENTIMIENTO_CLIENTE"].cantidad == 1
        assert por_motivo["ARREPENTIMIENTO_CLIENTE"].monto_centavos == 500

    def test_desglose_ordenado_por_monto_descendente(self, base_datos_temporal):
        producto = _crear_producto()
        v1 = _registrar_venta(100, "EFECTIVO", producto)
        v2 = _registrar_venta(900, "EFECTIVO", producto)
        _anular_directamente(v1.id, motivo="PRODUCTO_INCORRECTO")
        _anular_directamente(v2.id, motivo="ERROR_CARGA")

        reporte = servicio_reportes.generar_reporte_ventas()

        motivos_en_orden = [item.motivo for item in reporte.resumen_anulaciones.por_motivo]
        assert motivos_en_orden == ["ERROR_CARGA", "PRODUCTO_INCORRECTO"]

    def test_anulacion_fuera_del_periodo_queda_excluida(self, base_datos_temporal):
        producto = _crear_producto()
        venta_vieja = _registrar_venta(200, "EFECTIVO", producto)
        _anular_directamente(venta_vieja.id, motivo="ERROR_CARGA")
        with obtener_conexion() as conexion:
            conexion.execute("UPDATE ventas SET fecha = ? WHERE id = ?", ("2020-01-01 10:00:00", venta_vieja.id))

        reporte = servicio_reportes.generar_reporte_ventas(
            fecha_desde="2020-01-02", fecha_hasta=date.today().isoformat()
        )

        assert reporte.resumen_anulaciones.cantidad == 0
        assert reporte.resumen_anulaciones.por_motivo == []

    def test_activas_y_anuladas_en_el_mismo_periodo_no_se_mezclan(self, base_datos_temporal):
        producto = _crear_producto()
        _registrar_venta_con_costo(producto, 1, precio_unitario=200, costo_unitario=100)
        venta_anulada = _registrar_venta_con_costo(producto, 1, precio_unitario=300, costo_unitario=100)
        _anular_directamente(venta_anulada.id, motivo="ERROR_CARGA")

        reporte = servicio_reportes.generar_reporte_ventas()

        assert reporte.cantidad_ventas == 1  # solo la activa
        assert reporte.total_facturado_centavos == 200
        assert reporte.resumen_anulaciones.cantidad == 1
        assert reporte.resumen_anulaciones.monto_total_centavos == 300

    def test_kpis_existentes_no_cambian_por_la_presencia_de_anulaciones(self, base_datos_temporal):
        """Regresión explícita: cantidad_ventas/total_facturado/ticket
        promedio/rentabilidad/productos_mas_vendidos/ventas_por_usuario
        deben dar exactamente lo mismo con o sin una venta anulada de
        por medio -- ninguna de las 4 consultas analíticas que las
        calculan cambió su semántica."""
        producto = _crear_producto()
        _registrar_venta_con_costo(producto, 1, precio_unitario=200, costo_unitario=100)

        reporte_sin_anulacion = servicio_reportes.generar_reporte_ventas()

        venta_a_anular = _registrar_venta_con_costo(producto, 1, precio_unitario=500, costo_unitario=100)
        _anular_directamente(venta_a_anular.id, motivo="ERROR_CARGA")

        reporte_con_anulacion = servicio_reportes.generar_reporte_ventas()

        assert reporte_con_anulacion.cantidad_ventas == reporte_sin_anulacion.cantidad_ventas
        assert reporte_con_anulacion.total_facturado_centavos == reporte_sin_anulacion.total_facturado_centavos
        assert reporte_con_anulacion.ticket_promedio_centavos == reporte_sin_anulacion.ticket_promedio_centavos
        assert reporte_con_anulacion.rentabilidad == reporte_sin_anulacion.rentabilidad
        assert reporte_con_anulacion.productos_mas_vendidos == reporte_sin_anulacion.productos_mas_vendidos
        assert reporte_con_anulacion.ventas_por_usuario == reporte_sin_anulacion.ventas_por_usuario
