"""V1.7-C: kardex (movimientos de stock por producto) -- reconstruido con los eventos reales, solo lectura."""

import contextlib

import pytest

from db.repositorios import movimientos_stock as repositorio_movimientos
from domain.compra import ItemCompra
from domain.venta import ItemVenta
from excepciones import DatosInvalidosError, ProductoNoEncontradoError
from services import (
    servicio_caja,
    servicio_compras,
    servicio_inventario,
    servicio_movimientos_stock,
    servicio_proveedores,
    servicio_stock,
    servicio_ventas,
)
from tests.utilidades_clientes import consultar, escribir, usuario_logueado

from ._asgi_cliente import solicitud

pytestmark = pytest.mark.sin_caja_abierta  # el fixture abre su propia caja

STOCK_DE_ALTA = 20


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies = usuario_logueado("OWNER", "duenio")
    _, cajera = usuario_logueado("CASHIER", "cajera")
    servicio_caja.abrir_caja(0, None, owner.id)
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 300, stock_actual=STOCK_DE_ALTA)
    otro = servicio_stock.registrar_producto("7790000000002", "Gaseosa", 400, 700, stock_actual=50)
    return type(
        "E", (), {"ruta": base_datos_temporal, "owner": owner, "co": cookies, "cajera": cajera, "prov": proveedor,
                  "p": producto, "q": otro},
    )


def _comprar(e, producto, cantidad):
    return servicio_compras.registrar_compra(e.prov.id, e.owner.id, [ItemCompra(producto.id, cantidad, 100)])


def _vender(e, producto, cantidad):
    return servicio_ventas.registrar_venta([ItemVenta(producto.id, cantidad)], "EFECTIVO", usuario_id=e.owner.id)


def _fechar(e, tabla, fila_id, columna, fecha):
    escribir(e.ruta, f"UPDATE {tabla} SET {columna} = ? WHERE id = ?", (fecha, fila_id))


def _historia_completa(e):
    """Alta con 20 u. (no es un movimiento) y, en orden cronológico real:
    compra A +10, venta -4, venta -3 (anulada: +3), compra B +5 (anulada: -5), ajustes +2 y -1, recuento -2."""
    compra_a = _comprar(e, e.p, 10)
    venta_1 = _vender(e, e.p, 4)
    venta_2 = _vender(e, e.p, 3)
    servicio_ventas.anular_venta(venta_2.id, "ERROR_CARGA", None, e.owner.id)
    compra_b = _comprar(e, e.p, 5)
    servicio_compras.anular_compra(compra_b.id, "ERROR_CARGA", None, e.owner.id)
    ajuste_mas = servicio_stock.ajustar_stock(e.p.id, 2, "OTRO", e.owner.id, observaciones="Apareció mercadería")
    ajuste_menos = servicio_stock.ajustar_stock(e.p.id, -1, "ROTURA", e.owner.id)
    inventario = servicio_inventario.crear_inventario(e.owner.id, [e.p.id])
    servicio_inventario.registrar_conteo(inventario.id, e.p.id, 25, e.owner.id)  # esperado 27 -> diferencia -2
    servicio_inventario.confirmar_inventario(inventario.id, e.owner.id)

    _fechar(e, "compras", compra_a.id, "fecha", "2026-01-10 09:00:00")
    _fechar(e, "ventas", venta_1.id, "fecha", "2026-01-11 10:00:00")
    _fechar(e, "ventas", venta_2.id, "fecha", "2026-01-12 10:00:00")
    _fechar(e, "ventas", venta_2.id, "fecha_anulacion", "2026-01-13 11:00:00")
    _fechar(e, "compras", compra_b.id, "fecha", "2026-01-14 09:00:00")
    _fechar(e, "compras", compra_b.id, "fecha_anulacion", "2026-01-15 09:00:00")
    _fechar(e, "ajustes_stock", ajuste_mas.id, "fecha", "2026-01-16 12:00:00")
    _fechar(e, "ajustes_stock", ajuste_menos.id, "fecha", "2026-01-17 12:00:00")
    escribir(e.ruta, "UPDATE ajustes_stock SET fecha = '2026-01-18 12:00:00' WHERE motivo = 'RECUENTO'")
    return type("H", (), {"compra_a": compra_a, "compra_b": compra_b, "venta_1": venta_1, "venta_2": venta_2})


def _resumen(kardex):
    return [(m.tipo, m.cantidad, m.saldo) for m in kardex.movimientos]


class TestFuentesYSaldo:
    def test_cada_evento_aparece_exactamente_una_vez_con_su_signo_y_saldo(self, e):
        _historia_completa(e)

        kardex = servicio_movimientos_stock.generar_kardex(e.p.id)

        assert _resumen(kardex) == [
            ("COMPRA", 10, 30),
            ("VENTA", -4, 26),
            ("VENTA", -3, 23),
            ("ANULACION_VENTA", 3, 26),
            ("COMPRA", 5, 31),
            ("ANULACION_COMPRA", -5, 26),
            ("AJUSTE", 2, 28),
            ("AJUSTE", -1, 27),
            ("RECUENTO", -2, 25),
        ]

    def test_saldo_inicial_reconstruido_y_saldo_final_igual_al_stock_actual(self, e):
        _historia_completa(e)

        kardex = servicio_movimientos_stock.generar_kardex(e.p.id)
        stock = consultar(e.ruta, "SELECT stock_actual FROM productos WHERE id = ?", (e.p.id,))[0][0]

        assert kardex.saldo_inicial == STOCK_DE_ALTA  # el stock del alta no es un movimiento: se reconstruye
        assert kardex.saldo_final == kardex.stock_actual == stock == 25
        assert (kardex.total_entradas, kardex.total_salidas) == (20, 15)

    def test_venta_anulada_es_salida_original_mas_entrada_de_reversion(self, e):
        historia = _historia_completa(e)

        movimientos = [m for m in servicio_movimientos_stock.generar_kardex(e.p.id).movimientos
                       if m.referencia == f"Venta #{historia.venta_2.id}"]

        assert [(m.tipo, m.cantidad, m.fecha) for m in movimientos] == [
            ("VENTA", -3, "2026-01-12 10:00:00"),
            ("ANULACION_VENTA", 3, "2026-01-13 11:00:00"),
        ]

    def test_compra_anulada_es_entrada_original_mas_salida_de_reversion(self, e):
        historia = _historia_completa(e)

        movimientos = [m for m in servicio_movimientos_stock.generar_kardex(e.p.id).movimientos
                       if m.referencia == f"Compra #{historia.compra_b.id}"]

        assert [(m.tipo, m.cantidad, m.fecha) for m in movimientos] == [
            ("COMPRA", 5, "2026-01-14 09:00:00"),
            ("ANULACION_COMPRA", -5, "2026-01-15 09:00:00"),
        ]

    def test_el_inventario_fisico_es_un_recuento_y_no_se_duplica(self, e):
        _historia_completa(e)

        movimientos = servicio_movimientos_stock.generar_kardex(e.p.id).movimientos

        recuentos = [m for m in movimientos if m.tipo == "RECUENTO"]
        assert len(recuentos) == 1 and recuentos[0].referencia == "Inventario #1"
        assert sorted({m.tipo for m in movimientos}) == [
            "AJUSTE", "ANULACION_COMPRA", "ANULACION_VENTA", "COMPRA", "RECUENTO", "VENTA",
        ]
        assert len(movimientos) == 9  # 2 compras + 1 reversión, 2 ventas + 1 reversión, 2 ajustes, 1 recuento

    def test_no_mezcla_movimientos_de_otros_productos(self, e):
        _historia_completa(e)
        _comprar(e, e.q, 7)

        assert len(servicio_movimientos_stock.generar_kardex(e.q.id).movimientos) == 1

    def test_los_ajustes_verifican_su_saldo_contra_el_stock_resultante_que_guardaron(self, e):
        _historia_completa(e)
        verificados = [m.saldo_verificado for m in servicio_movimientos_stock.generar_kardex(e.p.id).movimientos]
        assert verificados == [None] * 6 + [True, True, True]

        escribir(e.ruta, "UPDATE ajustes_stock SET stock_resultante = stock_resultante + 1 WHERE motivo = 'ROTURA'")
        alterado = servicio_movimientos_stock.generar_kardex(e.p.id).movimientos

        assert [m.saldo_verificado for m in alterado if m.tipo == "AJUSTE"] == [True, False]

    def test_producto_sin_movimientos_muestra_su_stock_sin_inventar_eventos(self, e):
        kardex = servicio_movimientos_stock.generar_kardex(e.q.id)

        assert kardex.movimientos == [] and kardex.saldo_inicial == kardex.saldo_final == kardex.stock_actual == 50

    def test_producto_inactivo_se_puede_consultar(self, e):
        _historia_completa(e)
        escribir(e.ruta, "UPDATE productos SET activo = 0 WHERE id = ?", (e.p.id,))

        kardex = servicio_movimientos_stock.generar_kardex(e.p.id)

        assert kardex.activo is False and len(kardex.movimientos) == 9

    def test_producto_inexistente(self, e):
        with pytest.raises(ProductoNoEncontradoError):
            servicio_movimientos_stock.generar_kardex(9999)


class TestRango:
    def test_saldo_inicial_y_final_coherentes_con_movimientos_anteriores_y_posteriores(self, e):
        _historia_completa(e)

        kardex = servicio_movimientos_stock.generar_kardex(e.p.id, "2026-01-12", "2026-01-15")

        assert _resumen(kardex) == [
            ("VENTA", -3, 23),
            ("ANULACION_VENTA", 3, 26),
            ("COMPRA", 5, 31),
            ("ANULACION_COMPRA", -5, 26),
        ]
        assert kardex.saldo_inicial == 26  # después de compra A y venta 1, antes del rango
        assert kardex.saldo_final == 26 != kardex.stock_actual  # hubo movimientos posteriores: 26 -> 25 actual

    def test_hasta_incluye_el_dia_completo(self, e):
        _historia_completa(e)

        kardex = servicio_movimientos_stock.generar_kardex(e.p.id, "2026-01-11", "2026-01-11")

        assert _resumen(kardex) == [("VENTA", -4, 26)] and kardex.saldo_inicial == 30

    def test_solo_desde_y_solo_hasta(self, e):
        _historia_completa(e)

        desde = servicio_movimientos_stock.generar_kardex(e.p.id, fecha_desde="2026-01-17")
        hasta = servicio_movimientos_stock.generar_kardex(e.p.id, fecha_hasta="2026-01-10")

        assert _resumen(desde) == [("AJUSTE", -1, 27), ("RECUENTO", -2, 25)] and desde.saldo_inicial == 28
        assert _resumen(hasta) == [("COMPRA", 10, 30)] and hasta.saldo_inicial == STOCK_DE_ALTA

    def test_rango_sin_movimientos_conserva_un_saldo_coherente(self, e):
        _historia_completa(e)

        kardex = servicio_movimientos_stock.generar_kardex(e.p.id, "2026-03-01", "2026-03-31")

        assert kardex.movimientos == [] and kardex.saldo_inicial == kardex.saldo_final == 25

    @pytest.mark.parametrize(
        "desde, hasta",
        [("2026-13-01", None), ("basura", None), (None, "2026-02-30"), ("2026-02-01", "2026-01-01"),
         ("2026-01-01'--", None), ("0000-00-00", "9999-99-99"), (None, "9999-12-31")],
    )
    def test_fechas_invalidas_son_error_controlado(self, e, desde, hasta):
        with pytest.raises(DatosInvalidosError):
            servicio_movimientos_stock.generar_kardex(e.p.id, desde, hasta)


class TestOrdenEstable:
    def test_con_la_misma_fecha_ordena_originales_antes_que_reversiones_y_luego_por_id(self, e):
        compra = _comprar(e, e.p, 10)
        venta_1 = _vender(e, e.p, 2)
        venta_2 = _vender(e, e.p, 1)
        ajuste = servicio_stock.ajustar_stock(e.p.id, 1, "OTRO", e.owner.id, observaciones="x")
        servicio_ventas.anular_venta(venta_1.id, "ERROR_CARGA", None, e.owner.id)
        instante = "2026-02-01 10:00:00"
        escribir(e.ruta, "UPDATE compras SET fecha = ?", (instante,))
        escribir(e.ruta, "UPDATE ventas SET fecha = ?, fecha_anulacion = CASE WHEN estado = 'ANULADA' THEN ? END", (instante, instante))
        escribir(e.ruta, "UPDATE ajustes_stock SET fecha = ?", (instante,))

        tipos = [(m.tipo, m.referencia) for m in servicio_movimientos_stock.generar_kardex(e.p.id).movimientos]

        assert tipos == [
            ("COMPRA", f"Compra #{compra.id}"),
            ("VENTA", f"Venta #{venta_1.id}"),
            ("VENTA", f"Venta #{venta_2.id}"),
            ("AJUSTE", f"Ajuste #{ajuste.id}"),
            ("ANULACION_VENTA", f"Venta #{venta_1.id}"),
        ]
        assert tipos == [(m.tipo, m.referencia) for m in servicio_movimientos_stock.generar_kardex(e.p.id).movimientos]

    def test_la_anulacion_de_compra_queda_despues_de_su_compra_aunque_empaten(self, e):
        compra = _comprar(e, e.p, 4)
        servicio_compras.anular_compra(compra.id, "ERROR_CARGA", None, e.owner.id)
        instante = "2026-02-01 10:00:00"
        escribir(e.ruta, "UPDATE compras SET fecha = ?, fecha_anulacion = ?", (instante, instante))

        kardex = servicio_movimientos_stock.generar_kardex(e.p.id)

        assert _resumen(kardex) == [("COMPRA", 4, 24), ("ANULACION_COMPRA", -4, 20)]


class TestEscala:
    def test_cantidad_constante_de_consultas_sin_importar_cuantos_movimientos_hay(self, e, monkeypatch):
        original = repositorio_movimientos.obtener_conexion
        sentencias: list[str] = []

        @contextlib.contextmanager
        def con_traza():
            with original() as conexion:
                conexion.set_trace_callback(sentencias.append)
                yield conexion

        monkeypatch.setattr(repositorio_movimientos, "obtener_conexion", con_traza)
        _comprar(e, e.p, 1)
        servicio_movimientos_stock.generar_kardex(e.p.id)
        con_pocos = len(sentencias)
        for _ in range(25):
            _comprar(e, e.p, 1)
        sentencias.clear()

        kardex = servicio_movimientos_stock.generar_kardex(e.p.id)

        assert len(kardex.movimientos) == 26 and len(sentencias) == con_pocos


class TestPantalla:
    def test_muestra_saldos_movimientos_y_marca_el_saldo_como_reconstruido(self, e):
        _historia_completa(e)

        html = solicitud("GET", f"/productos/{e.p.id}/movimientos", cookies=e.co).texto

        assert "Saldo inicial reconstruido" in html
        assert f'id="kardex-saldo-inicial" class="text-lg font-semibold tabular-nums">{STOCK_DE_ALTA}<' in html
        assert 'id="kardex-saldo-final" class="text-lg font-semibold tabular-nums">25<' in html
        assert html.count('data-tipo="ANULACION_VENTA"') == 1 and html.count('data-tipo="ANULACION_COMPRA"') == 1
        assert html.count('data-tipo="RECUENTO"') == 1

    def test_el_filtro_de_fechas_se_conserva_y_recorta(self, e):
        _historia_completa(e)

        html = solicitud(
            "GET", f"/productos/{e.p.id}/movimientos?fecha_desde=2026-01-17&fecha_hasta=2026-01-18", cookies=e.co
        ).texto

        assert 'value="2026-01-17"' in html and 'data-tipo="COMPRA"' not in html and 'data-tipo="RECUENTO"' in html

    @pytest.mark.parametrize("consulta", ["fecha_desde=basura", "fecha_hasta=2026-02-30", "fecha_desde=2026-05-01&fecha_hasta=2026-01-01"])
    def test_fechas_invalidas_no_dan_500(self, e, consulta):
        respuesta = solicitud("GET", f"/productos/{e.p.id}/movimientos?{consulta}", cookies=e.co)

        assert respuesta.status == 303 and b"tipo=error" in dict(respuesta.headers)[b"location"]

    def test_campos_vacios_del_formulario_equivalen_a_sin_filtro(self, e):
        assert solicitud("GET", f"/productos/{e.p.id}/movimientos?fecha_desde=&fecha_hasta=", cookies=e.co).status == 200

    def test_producto_inactivo_y_producto_inexistente(self, e):
        escribir(e.ruta, "UPDATE productos SET activo = 0 WHERE id = ?", (e.q.id,))

        inactivo = solicitud("GET", f"/productos/{e.q.id}/movimientos", cookies=e.co)
        inexistente = solicitud("GET", "/productos/9999/movimientos", cookies=e.co)

        assert inactivo.status == 200 and "(producto inactivo)" in inactivo.texto
        assert inexistente.status == 303 and b"tipo=error" in dict(inexistente.headers)[b"location"]

    def test_solo_owner_y_no_escribe_nada(self, e):
        assert solicitud("GET", f"/productos/{e.p.id}/movimientos", cookies=e.cajera).status == 403
        assert solicitud("GET", f"/productos/{e.p.id}/movimientos").status == 303
        tablas = ("productos", "ventas", "compras", "ajustes_stock", "auditoria")
        antes = [consultar(e.ruta, f"SELECT COUNT(*) FROM {t}")[0][0] for t in tablas]

        assert solicitud("GET", f"/productos/{e.p.id}/movimientos", cookies=e.co).status == 200

        assert antes == [consultar(e.ruta, f"SELECT COUNT(*) FROM {t}")[0][0] for t in tablas]

    def test_hay_acceso_desde_la_edicion_y_desde_el_listado(self, e):
        assert f"/productos/{e.p.id}/movimientos" in solicitud("GET", f"/productos/{e.p.id}/editar", cookies=e.co).texto
        assert f'href="/productos/{e.p.id}/movimientos"' in solicitud("GET", "/productos", cookies=e.co).texto
        assert "/movimientos" not in solicitud("GET", "/productos", cookies=e.cajera).texto
