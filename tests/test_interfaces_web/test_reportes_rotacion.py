"""V1.7-A: reporte de rotación / bajo movimiento -- agregado en SQL, solo ventas activas, solo consulta."""

import contextlib
from datetime import date

import pytest

from db.conexion import obtener_conexion
from db.repositorios import ventas as repositorio_ventas
from domain.venta import ItemVenta
from services import servicio_caja, servicio_reportes, servicio_stock, servicio_ventas
from tests.utilidades_clientes import escribir, usuario_logueado

from ._asgi_cliente import solicitud

pytestmark = pytest.mark.sin_caja_abierta  # el fixture abre su propia caja

HOY = date(2026, 7, 15)
DESDE, HASTA = "2026-06-01", "2026-06-30"


def _vender(e, producto, unidades, fecha):
    venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, unidades)], "EFECTIVO", usuario_id=e.owner.id)
    escribir(e.ruta, "UPDATE ventas SET fecha = ? WHERE id = ?", (fecha, venta.id))
    return venta


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies = usuario_logueado("OWNER", "duenio")
    servicio_caja.abrir_caja(0, None, owner.id)
    crear = servicio_stock.registrar_producto
    p = {
        "reciente": crear("1", "Reciente", 100, 200, stock_actual=50),
        "sin": crear("2", "Sin ventas", 300, 500, stock_actual=10),
        "afuera": crear("3", "Vendido afuera", 50, 90, stock_actual=20),
        "cero": crear("4", "Sin stock", 10, 20, stock_actual=0),
        "anulada": crear("5", "Solo anulada", 70, 100, stock_actual=40),
        "limite": crear("6", "Limite", 20, 40, stock_actual=5),
    }
    ctx = type("E", (), {"ruta": base_datos_temporal, "owner": owner, "co": cookies, "p": p})
    _vender(ctx, p["reciente"], 3, "2026-06-15 10:00:00")
    _vender(ctx, p["afuera"], 4, "2026-05-31 23:59:59")  # justo antes del rango
    _vender(ctx, p["limite"], 1, "2026-06-01 00:00:00")  # primer instante del rango
    _vender(ctx, p["limite"], 2, "2026-06-30 23:59:59")  # último instante del rango
    _vender(ctx, p["limite"], 1, "2026-07-01 00:00:00")  # primer instante fuera (fin exclusivo)
    venta = _vender(ctx, p["anulada"], 5, "2026-06-10 09:00:00")
    servicio_ventas.anular_venta(venta.id, "ERROR_CARGA", None, owner.id)
    return ctx


def _reporte(desde=DESDE, hasta=HASTA):
    return servicio_reportes.generar_reporte_rotacion(desde, hasta, hoy=HOY)


def _filas(desde=DESDE, hasta=HASTA):
    return {f.nombre: f for f in _reporte(desde, hasta).filas}


class TestClasificacion:
    def test_clasifica_y_cuenta_unidades_solo_de_ventas_activas_dentro_del_rango(self, e):
        assert {n: (f.estado, f.unidades_vendidas) for n, f in _filas().items()} == {
            "Reciente": ("CON_VENTAS", 3),
            "Sin ventas": ("SIN_VENTAS", 0),
            "Vendido afuera": ("SIN_VENTAS", 0),
            "Solo anulada": ("SIN_VENTAS", 0),
            "Limite": ("CON_VENTAS", 3),  # 1 + 2; la del 07-01 00:00:00 queda fuera (fin exclusivo)
        }

    def test_excluye_productos_sin_stock(self, e):
        assert "Sin stock" not in _filas()

    def test_ultima_venta_activa_de_toda_la_historia_y_dias(self, e):
        filas = _filas()

        assert filas["Reciente"].fecha_ultima_venta == "2026-06-15 10:00:00"
        assert filas["Reciente"].dias_desde_ultima_venta == 30
        assert filas["Vendido afuera"].fecha_ultima_venta == "2026-05-31 23:59:59"
        assert filas["Vendido afuera"].dias_desde_ultima_venta == 45
        assert filas["Limite"].fecha_ultima_venta == "2026-07-01 00:00:00"
        assert filas["Limite"].dias_desde_ultima_venta == 14
        assert filas["Sin ventas"].fecha_ultima_venta is None and filas["Sin ventas"].dias_desde_ultima_venta is None
        assert filas["Solo anulada"].fecha_ultima_venta is None  # la venta anulada no cuenta

    def test_valorizacion_a_costo_actual_no_a_precio_de_venta(self, e):
        filas = _filas()

        assert filas["Sin ventas"].valor_stock_centavos == 10 * 300
        assert filas["Reciente"].valor_stock_centavos == 47 * 100  # 50 - 3 vendidas

    def test_incluye_inactivos_con_stock_como_la_valorizacion(self, e):
        escribir(e.ruta, "UPDATE productos SET activo = 0 WHERE id = ?", (e.p["afuera"].id,))

        fila = _filas()["Vendido afuera"]

        assert fila.activo is False and fila.stock_actual == 16

    def test_resumen_de_inmovilizado(self, e):
        reporte = _reporte()

        assert reporte.productos_sin_ventas == 3
        assert reporte.unidades_inmovilizadas == 10 + 16 + 40
        assert reporte.valor_inmovilizado_centavos == 10 * 300 + 16 * 50 + 40 * 70

    def test_orden_estable_sin_ventas_primero_luego_mayor_valor(self, e):
        # Sin ventas por valor desc: 3000, 2800, 800; con ventas por valor desc: Reciente 4700, Limite 20.
        assert [f.nombre for f in _reporte().filas] == [
            "Sin ventas", "Solo anulada", "Vendido afuera", "Reciente", "Limite",
        ]


class TestFiltros:
    def test_otro_rango_cambia_el_resultado(self, e):
        filas = _filas("2026-05-01", "2026-05-31")

        assert (filas["Vendido afuera"].estado, filas["Vendido afuera"].unidades_vendidas) == ("CON_VENTAS", 4)
        assert filas["Reciente"].estado == "SIN_VENTAS"

    @pytest.mark.parametrize(
        "consulta",
        [
            "fecha_desde=2026-13-01&fecha_hasta=2026-06-30",
            "fecha_desde=basura&fecha_hasta=2026-06-30",
            "fecha_desde=2026-06-30&fecha_hasta=2026-06-01",
            "fecha_desde=2026-06-01%27--&fecha_hasta=2026-06-30",
            "fecha_desde=0000-00-00&fecha_hasta=9999-99-99",
        ],
    )
    def test_fechas_invalidas_son_error_controlado(self, e, consulta):
        respuesta = solicitud("GET", f"/reportes/rotacion?{consulta}", cookies=e.co)

        # Convención de la app: error de dominio -> redirección con el mensaje, nunca 500.
        assert respuesta.status == 303 and b"tipo=error" in dict(respuesta.headers)[b"location"]

    def test_campos_vacios_usan_el_rango_por_defecto(self, e):
        assert solicitud("GET", "/reportes/rotacion?fecha_desde=&fecha_hasta=", cookies=e.co).status == 200


class TestPaginaYPermisos:
    def test_la_pagina_muestra_filas_resumen_y_navegacion(self, e):
        html = solicitud("GET", f"/reportes/rotacion?fecha_desde={DESDE}&fecha_hasta={HASTA}", cookies=e.co).texto

        assert f'data-producto="{e.p["sin"].id}" data-estado="SIN_VENTAS"' in html
        assert f'data-producto="{e.p["reciente"].id}" data-estado="CON_VENTAS"' in html
        assert 'id="rotacion-sin-ventas" class="text-lg font-semibold tabular-nums">3<' in html
        assert 'href="/reportes/rotacion"' in html

    def test_solo_owner_y_no_escribe(self, e):
        _, cajera = usuario_logueado("CASHIER", "cajera")
        assert solicitud("GET", "/reportes/rotacion", cookies=cajera).status == 403
        assert solicitud("GET", "/reportes/rotacion").status == 303

        with obtener_conexion() as con:
            antes = con.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
        assert solicitud("GET", "/reportes/rotacion", cookies=e.co).status == 200
        with obtener_conexion() as con:
            assert con.execute("SELECT COUNT(*) FROM ventas").fetchone()[0] == antes


class TestEscala:
    def test_no_materializa_ventas_y_usa_un_numero_constante_de_consultas(self, e, monkeypatch):
        def prohibido(*args, **kwargs):
            raise AssertionError("el reporte no debe cargar el listado de ventas")

        for nombre in ("listar_en_rango", "listar_resumen", "listar_ventas_de_sesion"):
            monkeypatch.setattr(repositorio_ventas, nombre, prohibido)

        original = repositorio_ventas.obtener_conexion
        sentencias: list[str] = []

        @contextlib.contextmanager
        def con_traza():
            with original() as conexion:
                conexion.set_trace_callback(sentencias.append)
                yield conexion

        monkeypatch.setattr(repositorio_ventas, "obtener_conexion", con_traza)

        _reporte()
        con_pocos = len(sentencias)
        for i in range(30):
            servicio_stock.registrar_producto(f"9{i:03d}", f"Extra {i}", 10, 20, stock_actual=1)
        sentencias.clear()
        assert len(_reporte().filas) == 35

        assert len(sentencias) == con_pocos == 1

    def test_el_filtro_de_rango_usa_el_indice_de_fecha(self, e):
        with obtener_conexion() as con:
            plan = " ".join(
                str(fila[3])
                for fila in con.execute(
                    "EXPLAIN QUERY PLAN SELECT d.producto_id, SUM(d.cantidad) FROM ventas v"
                    " JOIN detalle_venta d ON d.venta_id = v.id"
                    " WHERE v.estado = 'ACTIVA' AND v.fecha >= ? AND v.fecha < ? GROUP BY d.producto_id",
                    (DESDE, HASTA),
                )
            )

        assert "idx_ventas_fecha" in plan
