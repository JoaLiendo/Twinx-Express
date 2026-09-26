"""V1.8-B: exportaciones CSV operativas -- mismos filtros y permisos que la pantalla de origen, todo el conjunto
filtrado (no la página) y protección contra inyección de fórmulas. Sin migración."""

import csv
import io
import re
from datetime import date

import pytest

from domain.compra import ItemCompra
from domain.venta import ItemVenta
from services import (
    servicio_caja,
    servicio_clientes,
    servicio_compras,
    servicio_cuenta_corriente,
    servicio_proveedores,
    servicio_reportes,
    servicio_stock,
    servicio_ventas,
)
from services.servicio_exportacion_csv import (
    COLUMNAS_COMPRAS,
    COLUMNAS_DEUDA,
    COLUMNAS_KARDEX,
    COLUMNAS_ROTACION,
    COLUMNAS_VENTAS,
    dinero,
    generar_csv,
    neutralizar_texto,
    numero,
)
from tests.utilidades_clientes import consultar, escribir, usuario_logueado

from ._asgi_cliente import solicitud

pytestmark = pytest.mark.sin_caja_abierta  # el fixture abre su propia caja

CC = "CUENTA_CORRIENTE"


def _filas(respuesta):
    """Filas del CSV como diccionarios (cabecera incluida en las claves)."""
    assert respuesta.status == 200, respuesta.status
    texto = respuesta.cuerpo.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(texto, newline="")))


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies = usuario_logueado("OWNER", "duenio")
    _, cajera = usuario_logueado("CASHIER", "cajera")
    servicio_caja.abrir_caja(0, None, owner.id)
    prov_a = servicio_proveedores.crear_proveedor("Distribuidora Alfa")
    prov_b = servicio_proveedores.crear_proveedor("Mayorista Beta")
    p1 = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 300, stock_actual=50)
    p2 = servicio_stock.registrar_producto("7790000000002", "Gaseosa", 400, 700, stock_actual=50)
    return type("E", (), {"ruta": base_datos_temporal, "owner": owner, "co": cookies, "cajera": cajera,
                          "a": prov_a, "b": prov_b, "p1": p1, "p2": p2})


def _comprar(e, proveedor, *lineas, fecha=None):
    compra = servicio_compras.registrar_compra(
        proveedor.id, e.owner.id, [ItemCompra(p.id, c, k) for p, c, k in lineas]
    )
    if fecha:
        escribir(e.ruta, "UPDATE compras SET fecha = ? WHERE id = ?", (fecha, compra.id))
    return compra


def _vender(e, *lineas, tipo="EFECTIVO", cliente=None, fecha=None):
    venta = servicio_ventas.registrar_venta(
        [ItemVenta(p.id, c) for p, c in lineas], tipo, usuario_id=e.owner.id, cliente_id=cliente.id if cliente else None
    )
    if fecha:
        escribir(e.ruta, "UPDATE ventas SET fecha = ? WHERE id = ?", (fecha, venta.id))
    return venta


def _bajar(e, ruta):
    return solicitud("GET", ruta, cookies=e.co)


class TestInyeccionDeFormulas:
    @pytest.mark.parametrize("prefijo", ["=", "+", "-", "@"])
    def test_un_texto_que_empieza_como_formula_se_neutraliza(self, prefijo):
        peligroso = f"{prefijo}HYPERLINK(\"http://x\",\"a\")"

        assert neutralizar_texto(peligroso) == "'" + peligroso

    @pytest.mark.parametrize("texto", ["\t=1+1", "\r=1+1"])
    def test_tabulacion_y_retorno_de_carro_tambien(self, texto):
        assert neutralizar_texto(texto).startswith("'")

    @pytest.mark.parametrize("texto", ["Alfajor", "a=b", "x+y", "Ñandú", "1234", "", None])
    def test_el_resto_del_texto_queda_igual(self, texto):
        assert neutralizar_texto(texto) == (texto or "")

    def test_los_numeros_que_generamos_no_se_alteran_ni_siquiera_los_negativos(self):
        salida = generar_csv(["n", "d", "t"], [(numero(-5), dinero(-150), "-5")]).decode("utf-8-sig")

        assert salida.splitlines()[1] == "-5,-1.50,'-5"

    @pytest.mark.parametrize("nombre", ["=cmd|'/C calc'!A0", "+SUM(1;1)", "-2+3", "@SUM(A1)"])
    def test_en_una_exportacion_real_de_compras_ventas_y_deuda(self, e, nombre):
        proveedor = servicio_proveedores.crear_proveedor(nombre)
        producto = servicio_stock.registrar_producto("7790000009999", nombre, 100, 300, stock_actual=10)
        cliente = servicio_clientes.crear_cliente(nombre, e.owner.id)
        _comprar(e, proveedor, (producto, 2, 100))
        _vender(e, (producto, 1), tipo=CC, cliente=cliente)

        compras = _filas(_bajar(e, f"/compras/exportar?proveedor_id={proveedor.id}"))
        ventas = _filas(_bajar(e, f"/ventas/historial/exportar?cliente_id={cliente.id}"))
        deuda = _filas(_bajar(e, "/reportes/cuenta-corriente/exportar"))

        assert compras[0]["proveedor"] == "'" + nombre and compras[0]["producto"] == "'" + nombre
        assert ventas[0]["cliente"] == "'" + nombre and ventas[0]["producto"] == "'" + nombre
        assert [f["cliente"] for f in deuda] == ["'" + nombre]


class TestFormatoCsv:
    def test_cabeceras_http_bom_y_nombre_estable(self, e):
        _comprar(e, e.a, (e.p1, 2, 100))

        respuesta = _bajar(e, "/compras/exportar")

        cabeceras = {k.decode().lower(): v.decode() for k, v in respuesta.headers}
        assert cabeceras["content-type"] == "text/csv; charset=utf-8"
        assert cabeceras["content-disposition"] == f'attachment; filename="compras_{date.today().isoformat()}.csv"'
        assert respuesta.cuerpo.startswith(b"\xef\xbb\xbf")
        assert respuesta.cuerpo.decode("utf-8-sig").splitlines()[0] == ",".join(COLUMNAS_COMPRAS)

    def test_coma_comillas_y_salto_de_linea_se_citan_y_ida_y_vuelta_es_exacta(self, e):
        raro = 'Alfajor, "triple"\nsegunda línea'
        producto = servicio_stock.registrar_producto("7790000008888", raro, 100, 300, stock_actual=10)
        _comprar(e, e.a, (producto, 1, 100))

        filas = _filas(_bajar(e, f"/compras/exportar?producto_id={producto.id}"))

        assert filas[0]["producto"] == raro and len(filas) == 1

    def test_los_importes_salen_como_texto_decimal_simple(self, e):
        _comprar(e, e.a, (e.p1, 3, 12345))

        fila = _filas(_bajar(e, "/compras/exportar"))[0]

        assert (fila["costo_unitario"], fila["subtotal"], fila["total_compra"]) == ("123.45", "370.35", "370.35")


class TestCompras:
    def _dataset(self, e):
        c1 = _comprar(e, e.a, (e.p1, 1, 100), (e.p2, 2, 400), fecha="2026-03-02 10:00:00")
        c2 = _comprar(e, e.a, (e.p1, 1, 100), fecha="2026-03-10 10:00:00")
        c3 = _comprar(e, e.b, (e.p1, 1, 100), fecha="2026-03-10 11:00:00")
        c4 = _comprar(e, e.a, (e.p2, 1, 400), fecha="2026-04-01 10:00:00")
        servicio_compras.anular_compra(c2.id, "ERROR_CARGA", None, e.owner.id)
        return c1, c2, c3, c4

    @staticmethod
    def _compras(filas):
        return sorted({int(f["compra"]) for f in filas})

    def test_una_fila_por_linea_con_estado_motivo_y_total_de_la_compra(self, e):
        c1, c2, _, _ = self._dataset(e)

        filas = _filas(_bajar(e, "/compras/exportar"))

        de_c1 = [f for f in filas if f["compra"] == str(c1.id)]
        assert [(f["producto"], f["cantidad"], f["subtotal"], f["total_compra"]) for f in de_c1] == [
            ("Alfajor", "1", "1.00", "9.00"), ("Gaseosa", "2", "8.00", "9.00")
        ]
        anulada = next(f for f in filas if f["compra"] == str(c2.id))
        assert (anulada["estado"], anulada["motivo_anulacion"]) == ("ANULADA", "ERROR_CARGA") and anulada["fecha_anulacion"]
        assert {f["estado"] for f in filas if f["compra"] != str(c2.id)} == {"ACTIVA"}

    @pytest.mark.parametrize(
        "consulta, indices",
        [
            ("proveedor_id={a}", [0, 1, 3]),
            ("proveedor_id={b}", [2]),
            ("fecha_desde=2026-03-10&fecha_hasta=2026-03-10", [1, 2]),
            ("fecha_desde=2026-03-05", [1, 2, 3]),
            ("estado=ANULADA", [1]),
            ("estado=ACTIVA", [0, 2, 3]),
            ("estado=TODOS", [0, 1, 2, 3]),
            ("producto_id={p2}", [0, 3]),
            ("proveedor_id={a}&estado=ACTIVA&producto_id={p1}&fecha_desde=2026-03-01&fecha_hasta=2026-03-31", [0]),
        ],
    )
    def test_respeta_exactamente_los_filtros_del_listado(self, e, consulta, indices):
        compras = self._dataset(e)
        consulta = consulta.format(a=e.a.id, b=e.b.id, p1=e.p1.id, p2=e.p2.id)

        exportadas = self._compras(_filas(_bajar(e, f"/compras/exportar?{consulta}")))

        esperadas = sorted(compras[i].id for i in indices)
        assert exportadas == esperadas
        # y coincide con lo que muestra la pantalla para esos mismos filtros (todas las páginas)
        pantalla = re.findall(r'data-compra="(\d+)"', _bajar(e, f"/compras?{consulta}").texto)
        assert sorted(int(i) for i in pantalla) == esperadas

    def test_exporta_todas_las_paginas_no_solo_la_visible(self, e, monkeypatch):
        monkeypatch.setattr(servicio_compras, "COMPRAS_POR_PAGINA", 2)
        ids = [_comprar(e, e.a, (e.p1, 1, 100)).id for _ in range(5)]

        pantalla = re.findall(r'data-compra="(\d+)"', _bajar(e, "/compras").texto)
        exportadas = self._compras(_filas(_bajar(e, "/compras/exportar")))

        assert len(pantalla) == 2 and exportadas == sorted(ids)

    @pytest.mark.parametrize("consulta", ["estado=BORRADA", "fecha_desde=basura", "proveedor_id=abc", "producto_id=1.5",
                                           "fecha_desde=2026-05-01&fecha_hasta=2026-01-01"])
    def test_filtros_invalidos_son_error_controlado(self, e, consulta):
        respuesta = _bajar(e, f"/compras/exportar?{consulta}")

        assert respuesta.status == 303 and b"tipo=error" in dict(respuesta.headers)[b"location"]

    def test_sin_resultados_devuelve_solo_la_cabecera(self, e):
        assert _filas(_bajar(e, "/compras/exportar")) == []


class TestVentas:
    def _dataset(self, e):
        cliente = servicio_clientes.crear_cliente("Cliente CC", e.owner.id)
        v1 = _vender(e, (e.p1, 2), (e.p2, 1))
        v2 = _vender(e, (e.p1, 1), tipo="TARJETA")
        v3 = _vender(e, (e.p2, 1), tipo=CC, cliente=cliente)
        servicio_ventas.anular_venta(v2.id, "ERROR_CARGA", None, e.owner.id)
        return cliente, v1, v2, v3

    def test_una_fila_por_linea_con_estado_usuario_cliente_y_pago(self, e):
        cliente, v1, v2, v3 = self._dataset(e)

        filas = _filas(_bajar(e, "/ventas/historial/exportar"))

        assert list(filas[0].keys()) == list(COLUMNAS_VENTAS)
        de_v1 = [f for f in filas if f["venta"] == str(v1.id)]
        assert [(f["producto"], f["cantidad"], f["precio_unitario"], f["subtotal"], f["total_venta"]) for f in de_v1] == [
            ("Alfajor", "2", "3.00", "6.00", "13.00"), ("Gaseosa", "1", "7.00", "7.00", "13.00")
        ]
        assert {f["usuario"] for f in filas} == {"Duenio"} and de_v1[0]["tipo_pago"] == "EFECTIVO"
        de_v3 = next(f for f in filas if f["venta"] == str(v3.id))
        assert (de_v3["cliente"], de_v3["tipo_pago"]) == ("Cliente CC", CC)
        anulada = next(f for f in filas if f["venta"] == str(v2.id))
        assert (anulada["estado"], anulada["motivo_anulacion"]) == ("ANULADA", "ERROR_CARGA")

    @pytest.mark.parametrize(
        "consulta, indices",
        [("", [0, 1, 2]), ("estado=ANULADA", [1]), ("estado=ACTIVA", [0, 2]), ("tipo_pago=TARJETA", [1]),
         ("tipo_pago=EFECTIVO&estado=ACTIVA", [0]), ("cliente_id={c}", [2]), ("estado=&tipo_pago=", [0, 1, 2])],
    )
    def test_mismos_filtros_que_el_historial(self, e, consulta, indices):
        cliente, *ventas = self._dataset(e)
        consulta = consulta.format(c=cliente.id)

        exportadas = sorted({int(f["venta"]) for f in _filas(_bajar(e, f"/ventas/historial/exportar?{consulta}"))})

        esperadas = sorted(ventas[i].id for i in indices)
        pantalla = re.findall(r'href="/ventas/(\d+)"', _bajar(e, f"/ventas/historial?{consulta}").texto)
        assert exportadas == esperadas and sorted({int(i) for i in pantalla}) == esperadas

    def test_rango_de_fechas(self, e):
        v1 = _vender(e, (e.p1, 1), fecha="2026-03-02 10:00:00")
        v2 = _vender(e, (e.p1, 1), fecha="2026-03-09 10:00:00")

        dentro = _filas(_bajar(e, "/ventas/historial/exportar?fecha_desde=2026-03-01&fecha_hasta=2026-03-05"))

        assert [f["venta"] for f in dentro] == [str(v1.id)] and str(v2.id) not in [f["venta"] for f in dentro]

    def test_exporta_mas_de_una_pagina(self, e, monkeypatch):
        monkeypatch.setattr(servicio_ventas, "VENTAS_POR_PAGINA", 2)
        ids = [_vender(e, (e.p1, 1)).id for _ in range(5)]

        pantalla = re.findall(r'href="/ventas/(\d+)"', _bajar(e, "/ventas/historial").texto)
        exportadas = sorted({int(f["venta"]) for f in _filas(_bajar(e, "/ventas/historial/exportar"))})

        assert len(set(pantalla)) == 2 and exportadas == sorted(ids)

    def test_el_cajero_puede_exportar_porque_puede_ver_el_historial(self, e):
        _vender(e, (e.p1, 1))

        assert solicitud("GET", "/ventas/historial", cookies=e.cajera).status == 200
        assert len(_filas(solicitud("GET", "/ventas/historial/exportar", cookies=e.cajera))) == 1


class TestDeudaDeClientes:
    def test_mismo_agregado_que_el_reporte_sin_saldos_en_cero_ni_clientes_inactivos_sin_deuda(self, e):
        ana = servicio_clientes.crear_cliente("Ana", e.owner.id)
        beto = servicio_clientes.crear_cliente("Beto", e.owner.id)
        cami = servicio_clientes.crear_cliente("Cami", e.owner.id)
        _vender(e, (e.p1, 2), tipo=CC, cliente=ana)  # 600
        _vender(e, (e.p2, 1), tipo=CC, cliente=beto)  # 700
        _vender(e, (e.p1, 1), tipo=CC, cliente=cami)  # 300
        servicio_cuenta_corriente.registrar_cobro(cami.id, 300, e.owner.id)  # saldo 0
        servicio_cuenta_corriente.registrar_cobro(beto.id, 300, e.owner.id)  # saldo 400

        filas = _filas(_bajar(e, "/reportes/cuenta-corriente/exportar"))

        reporte = servicio_reportes.generar_reporte_deuda().filas
        assert list(filas[0].keys()) == list(COLUMNAS_DEUDA)
        assert [(f["cliente"], f["estado"], f["saldo"]) for f in filas] == [
            (r.cliente_nombre, "ACTIVO" if r.activo else "INACTIVO", f"{r.saldo_centavos // 100}.{r.saldo_centavos % 100:02d}")
            for r in reporte
        ]
        assert [(f["cliente"], f["saldo"]) for f in filas] == [("Ana", "6.00"), ("Beto", "4.00")]  # sin Cami (saldo 0)
        assert [f["cliente_id"] for f in filas] == [str(ana.id), str(beto.id)]


class TestRotacion:
    def test_mismo_dataset_que_la_pantalla_y_respeta_el_rango(self, e):
        _vender(e, (e.p1, 3), fecha="2026-06-15 10:00:00")
        _vender(e, (e.p2, 2), fecha="2026-05-01 10:00:00")  # fuera del rango
        consulta = "fecha_desde=2026-06-01&fecha_hasta=2026-06-30"

        filas = _filas(_bajar(e, f"/reportes/rotacion/exportar?{consulta}"))

        reporte = servicio_reportes.generar_reporte_rotacion("2026-06-01", "2026-06-30")
        assert list(filas[0].keys()) == list(COLUMNAS_ROTACION)
        assert [(f["producto"], f["estado"], f["unidades_vendidas"], f["stock_actual"]) for f in filas] == [
            (r.nombre, r.estado, str(r.unidades_vendidas), str(r.stock_actual)) for r in reporte.filas
        ]
        por_nombre = {f["producto"]: f for f in filas}
        assert por_nombre["Alfajor"]["estado"] == "CON_VENTAS" and por_nombre["Alfajor"]["unidades_vendidas"] == "3"
        assert por_nombre["Gaseosa"]["estado"] == "SIN_VENTAS" and por_nombre["Gaseosa"]["unidades_vendidas"] == "0"
        assert por_nombre["Gaseosa"]["ultima_venta"] == "2026-05-01 10:00:00"
        assert por_nombre["Gaseosa"]["valor_stock"] == "192.00"  # 48 u. * $4,00 a costo actual

    def test_otro_rango_cambia_el_resultado(self, e):
        _vender(e, (e.p2, 2), fecha="2026-05-01 10:00:00")

        filas = _filas(_bajar(e, "/reportes/rotacion/exportar?fecha_desde=2026-05-01&fecha_hasta=2026-05-31"))

        assert {f["producto"]: f["estado"] for f in filas}["Gaseosa"] == "CON_VENTAS"

    def test_una_venta_anulada_no_cuenta(self, e):
        venta = _vender(e, (e.p1, 3))
        servicio_ventas.anular_venta(venta.id, "ERROR_CARGA", None, e.owner.id)

        filas = _filas(_bajar(e, "/reportes/rotacion/exportar"))

        assert {f["producto"]: f["unidades_vendidas"] for f in filas}["Alfajor"] == "0"

    def test_fechas_invalidas_son_error_controlado(self, e):
        respuesta = _bajar(e, "/reportes/rotacion/exportar?fecha_desde=basura&fecha_hasta=2026-06-30")

        assert respuesta.status == 303 and b"tipo=error" in dict(respuesta.headers)[b"location"]


class TestKardex:
    def _historia(self, e):
        c1 = _comprar(e, e.a, (e.p1, 10, 100), fecha="2026-01-10 09:00:00")
        v1 = _vender(e, (e.p1, 4), fecha="2026-01-11 10:00:00")
        v2 = _vender(e, (e.p1, 3), fecha="2026-01-12 10:00:00")
        servicio_ventas.anular_venta(v2.id, "ERROR_CARGA", None, e.owner.id)
        escribir(e.ruta, "UPDATE ventas SET fecha_anulacion = '2026-01-13 11:00:00' WHERE id = ?", (v2.id,))
        c2 = _comprar(e, e.a, (e.p1, 5, 100), fecha="2026-01-14 09:00:00")
        servicio_compras.anular_compra(c2.id, "ERROR_CARGA", None, e.owner.id)
        escribir(e.ruta, "UPDATE compras SET fecha_anulacion = '2026-01-15 09:00:00' WHERE id = ?", (c2.id,))
        return c1, v1, v2, c2

    def test_movimientos_reversiones_saldo_y_orden_como_la_pantalla(self, e):
        self._historia(e)

        filas = _filas(_bajar(e, f"/productos/{e.p1.id}/movimientos/exportar"))

        assert list(filas[0].keys()) == list(COLUMNAS_KARDEX)
        assert [(f["tipo"], f["entrada"], f["salida"], f["saldo"]) for f in filas] == [
            ("SALDO_INICIAL_RECONSTRUIDO", "", "", "50"),
            ("COMPRA", "10", "", "60"),
            ("VENTA", "", "4", "56"),
            ("VENTA", "", "3", "53"),
            ("ANULACION_VENTA", "3", "", "56"),
            ("COMPRA", "5", "", "61"),
            ("ANULACION_COMPRA", "", "5", "56"),
        ]
        stock = consultar(e.ruta, "SELECT stock_actual FROM productos WHERE id = ?", (e.p1.id,))[0][0]
        assert filas[-1]["saldo"] == str(stock)
        pantalla = re.findall(r'data-tipo="([A-Z_]+)"', _bajar(e, f"/productos/{e.p1.id}/movimientos").texto)
        assert [f["tipo"] for f in filas[1:]] == pantalla

    def test_el_saldo_inicial_se_marca_como_reconstruido_no_historico(self, e):
        self._historia(e)

        primera = _filas(_bajar(e, f"/productos/{e.p1.id}/movimientos/exportar"))[0]

        assert primera["tipo"] == "SALDO_INICIAL_RECONSTRUIDO" and "no es un dato histórico" in primera["descripcion"]

    def test_respeta_el_rango(self, e):
        self._historia(e)

        filas = _filas(_bajar(e, f"/productos/{e.p1.id}/movimientos/exportar?fecha_desde=2026-01-12&fecha_hasta=2026-01-15"))

        assert [(f["tipo"], f["saldo"]) for f in filas] == [
            ("SALDO_INICIAL_RECONSTRUIDO", "56"), ("VENTA", "53"), ("ANULACION_VENTA", "56"), ("COMPRA", "61"),
            ("ANULACION_COMPRA", "56"),
        ]
        assert filas[0]["fecha"] == "2026-01-12"

    def test_producto_inexistente_y_fechas_invalidas(self, e):
        assert _bajar(e, "/productos/9999/movimientos/exportar").status == 303
        respuesta = _bajar(e, f"/productos/{e.p1.id}/movimientos/exportar?fecha_desde=basura")
        assert respuesta.status == 303 and b"tipo=error" in dict(respuesta.headers)[b"location"]

    def test_producto_inactivo(self, e):
        _comprar(e, e.a, (e.p2, 3, 400))
        escribir(e.ruta, "UPDATE productos SET activo = 0 WHERE id = ?", (e.p2.id,))

        assert len(_filas(_bajar(e, f"/productos/{e.p2.id}/movimientos/exportar"))) == 2


class TestPermisos:
    RUTAS = ["/compras/exportar", "/reportes/rotacion/exportar", "/reportes/cuenta-corriente/exportar",
             "/productos/{p}/movimientos/exportar"]

    @pytest.mark.parametrize("ruta", RUTAS)
    def test_solo_owner_igual_que_la_pantalla_de_origen(self, e, ruta):
        ruta = ruta.format(p=e.p1.id)

        assert solicitud("GET", ruta, cookies=e.cajera).status == 403
        assert solicitud("GET", ruta).status == 303
        assert solicitud("GET", ruta, cookies=e.co).status == 200

    def test_ventas_sin_sesion_esta_bloqueada(self, e):
        assert solicitud("GET", "/ventas/historial/exportar").status == 303

    @pytest.mark.parametrize("ruta", RUTAS + ["/ventas/historial/exportar"])
    def test_exportar_no_escribe_nada(self, e, ruta):
        ruta = ruta.format(p=e.p1.id)
        tablas = ("productos", "ventas", "compras", "ajustes_stock", "auditoria", "historial_precios", "caja_movimientos")
        antes = [consultar(e.ruta, f"SELECT COUNT(*) FROM {t}")[0][0] for t in tablas]

        assert solicitud("GET", ruta, cookies=e.co).status == 200

        assert antes == [consultar(e.ruta, f"SELECT COUNT(*) FROM {t}")[0][0] for t in tablas]


class TestBotones:
    def test_cada_pantalla_ofrece_exportar_csv_con_sus_filtros(self, e):
        _comprar(e, e.a, (e.p1, 1, 100))
        _vender(e, (e.p1, 1))
        servicio_clientes.crear_cliente("Ana", e.owner.id)

        compras = _bajar(e, f"/compras?proveedor_id={e.a.id}&estado=ACTIVA").texto
        ventas = _bajar(e, "/ventas/historial?estado=ACTIVA&tipo_pago=EFECTIVO").texto
        rotacion = _bajar(e, "/reportes/rotacion?fecha_desde=2026-06-01&fecha_hasta=2026-06-30").texto
        deuda = _bajar(e, "/reportes/cuenta-corriente").texto
        kardex = _bajar(e, f"/productos/{e.p1.id}/movimientos?fecha_desde=2026-01-01&fecha_hasta=2026-01-31").texto

        assert re.search(r'href="/compras/exportar\?[^"]*proveedor_id=%d' % e.a.id, compras) and "estado=ACTIVA" in compras
        assert re.search(r'href="/ventas/historial/exportar\?[^"]*estado=ACTIVA', ventas) and "tipo_pago=EFECTIVO" in ventas
        assert 'href="/reportes/rotacion/exportar?fecha_desde=2026-06-01&amp;fecha_hasta=2026-06-30"' in rotacion
        assert 'href="/reportes/cuenta-corriente/exportar"' in deuda
        assert f'href="/productos/{e.p1.id}/movimientos/exportar?' in kardex and "fecha_desde=2026-01-01" in kardex
        assert all('data-accion="exportar-csv"' in html for html in (compras, ventas, rotacion, deuda, kardex))
