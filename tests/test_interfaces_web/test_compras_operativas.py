"""V1.8-A: compras y proveedores operativos -- ficha con estado, listado paginado y filtrable, última compra e
historial de compras por producto. Todo de solo lectura y sin migración."""

import contextlib
import re
from html import unescape
from urllib.parse import parse_qs, urlparse

import pytest

from db.repositorios import compras as repositorio_compras
from domain.compra import ItemCompra
from excepciones import DatosInvalidosError, ProductoNoEncontradoError
from services import servicio_compras, servicio_proveedores, servicio_stock
from tests.utilidades_clientes import consultar, escribir, usuario_logueado

from ._asgi_cliente import solicitud


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies = usuario_logueado("OWNER", "duenio")
    _, cajera = usuario_logueado("CASHIER", "cajera")
    prov_a = servicio_proveedores.crear_proveedor("Distribuidora Alfa")
    prov_b = servicio_proveedores.crear_proveedor("Mayorista Beta")
    p1 = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 300, stock_actual=50)
    p2 = servicio_stock.registrar_producto("7790000000002", "Gaseosa", 400, 700, stock_actual=50)
    return type("E", (), {"ruta": base_datos_temporal, "owner": owner, "co": cookies, "cajera": cajera,
                          "a": prov_a, "b": prov_b, "p1": p1, "p2": p2})


def _comprar(e, proveedor, *lineas, fecha=None):
    """`lineas`: (producto, cantidad, costo). `fecha` fuerza el instante de la compra."""
    compra = servicio_compras.registrar_compra(
        proveedor.id, e.owner.id, [ItemCompra(p.id, cantidad, costo) for p, cantidad, costo in lineas]
    )
    if fecha:
        escribir(e.ruta, "UPDATE compras SET fecha = ? WHERE id = ?", (fecha, compra.id))
    return compra


def _anular(e, compra, motivo="ERROR_CARGA"):
    return servicio_compras.anular_compra(compra.id, motivo, None, e.owner.id)


def _ids(pagina):
    return [c.id for c in pagina.compras]


def _query(respuesta):
    return parse_qs(urlparse(dict(respuesta.headers)[b"location"].decode()).query)


class TestFichaDelProveedor:
    def test_activa_normal_y_anulada_marcada_con_motivo_y_fecha(self, e):
        activa = _comprar(e, e.a, (e.p1, 5, 150))
        anulada = _comprar(e, e.a, (e.p2, 2, 500))
        _anular(e, anulada, "DEVOLUCION_A_PROVEEDOR")

        html = solicitud("GET", f"/proveedores/{e.a.id}", cookies=e.co).texto

        assert f'data-compra="{activa.id}" data-estado="ACTIVA"' in html
        assert f'data-compra="{anulada.id}" data-estado="ANULADA"' in html
        fila_anulada = html[html.index(f'data-compra="{anulada.id}"'):]
        fila_anulada = fila_anulada[: fila_anulada.index("</tr>")]
        assert "ANULADA" in fila_anulada and "DEVOLUCION_A_PROVEEDOR" in fila_anulada
        fecha_anulacion = consultar(e.ruta, "SELECT fecha_anulacion FROM compras WHERE id = ?", (anulada.id,))[0][0]
        assert fecha_anulacion in fila_anulada
        fila_activa = html[html.index(f'data-compra="{activa.id}"'):]
        fila_activa = fila_activa[: fila_activa.index("</tr>")]
        assert "ANULADA" not in fila_activa and "ACTIVA" in fila_activa

    def test_la_anulada_no_se_borra_del_historial(self, e):
        anulada = _comprar(e, e.a, (e.p1, 5, 150))
        _anular(e, anulada)

        ficha = servicio_proveedores.obtener_ficha(e.a.id)

        assert [c.id for c in ficha.compras] == [anulada.id] and ficha.compras[0].estado == "ANULADA"

    def test_la_ficha_muestra_solo_las_ultimas_compras_y_enlaza_al_historial_completo(self, e, monkeypatch):
        monkeypatch.setattr(servicio_proveedores, "COMPRAS_EN_FICHA", 2)
        ids = [_comprar(e, e.a, (e.p1, 1, 100)).id for _ in range(3)]

        ficha = servicio_proveedores.obtener_ficha(e.a.id)
        html = solicitud("GET", f"/proveedores/{e.a.id}", cookies=e.co).texto

        assert [c.id for c in ficha.compras] == [ids[2], ids[1]] and ficha.total_compras == 3
        assert f'data-compra="{ids[0]}"' not in html
        assert f'id="ver-todas-las-compras" href="/compras?proveedor_id={e.a.id}"' in html and "de 3 compras" in html

    def test_sin_compras_no_ofrece_ver_todas(self, e):
        html = solicitud("GET", f"/proveedores/{e.b.id}", cookies=e.co).texto

        assert "Todavía no se le compró" in html and "ver-todas-las-compras" not in html


class TestPaginacion:
    def test_paginas_estables_total_y_limite(self, e, monkeypatch):
        monkeypatch.setattr(servicio_compras, "COMPRAS_POR_PAGINA", 2)
        ids = [_comprar(e, e.a, (e.p1, 1, 100)).id for _ in range(5)]

        primera = servicio_compras.listar_pagina(pagina=1)
        segunda = servicio_compras.listar_pagina(pagina=2)
        tercera = servicio_compras.listar_pagina(pagina=3)

        assert _ids(primera) == [ids[4], ids[3]] and _ids(segunda) == [ids[2], ids[1]] and _ids(tercera) == [ids[0]]
        assert (primera.total, primera.total_paginas, tercera.pagina) == (5, 3, 3)
        assert servicio_compras.listar_pagina(pagina=1) == primera  # estable

    @pytest.mark.parametrize("pagina, esperada", [(0, 1), (-4, 1), (99, 3)])
    def test_pagina_fuera_de_rango_se_lleva_a_una_valida(self, e, monkeypatch, pagina, esperada):
        monkeypatch.setattr(servicio_compras, "COMPRAS_POR_PAGINA", 2)
        for _ in range(5):
            _comprar(e, e.a, (e.p1, 1, 100))

        assert servicio_compras.listar_pagina(pagina=pagina).pagina == esperada

    def test_el_recorte_se_hace_en_sql_con_limit_y_offset(self, e, monkeypatch):
        monkeypatch.setattr(servicio_compras, "COMPRAS_POR_PAGINA", 2)
        for _ in range(5):
            _comprar(e, e.a, (e.p1, 1, 100))
        sentencias: list[str] = []
        original = repositorio_compras.obtener_conexion

        @contextlib.contextmanager
        def con_traza():
            with original() as conexion:
                conexion.set_trace_callback(sentencias.append)
                yield conexion

        monkeypatch.setattr(repositorio_compras, "obtener_conexion", con_traza)

        pagina = servicio_compras.listar_pagina(pagina=2)

        assert len(pagina.compras) == 2
        assert any("LIMIT 2 OFFSET 2" in s for s in sentencias)
        assert any(s.lstrip().startswith("SELECT COUNT(*)") for s in sentencias)

    def test_los_enlaces_de_pagina_conservan_los_filtros(self, e, monkeypatch):
        monkeypatch.setattr(servicio_compras, "COMPRAS_POR_PAGINA", 2)
        for _ in range(3):
            _comprar(e, e.a, (e.p1, 1, 100))
        _comprar(e, e.b, (e.p1, 1, 100))

        html = solicitud("GET", f"/compras?proveedor_id={e.a.id}&estado=ACTIVA&producto_id={e.p1.id}", cookies=e.co).texto

        enlace = unescape(re.search(r'href="(/compras\?[^"]*pagina=2)"', html).group(1))
        parametros = parse_qs(urlparse(enlace).query)
        assert parametros["proveedor_id"] == [str(e.a.id)] and parametros["estado"] == ["ACTIVA"]
        assert parametros["producto_id"] == [str(e.p1.id)] and "3 compras · página 1 de 2" in html
        segunda = solicitud("GET", enlace, cookies=e.co).texto
        assert segunda.count('data-compra="') == 1 and "página 2 de 2" in segunda


class TestFiltros:
    def test_por_proveedor(self, e):
        a = _comprar(e, e.a, (e.p1, 1, 100))
        b = _comprar(e, e.b, (e.p1, 1, 100))

        assert _ids(servicio_compras.listar_pagina(proveedor_id=e.a.id)) == [a.id]
        assert _ids(servicio_compras.listar_pagina(proveedor_id=e.b.id)) == [b.id]

    def test_por_rango_de_fechas_incluye_el_dia_completo_de_ambos_extremos(self, e):
        antes = _comprar(e, e.a, (e.p1, 1, 100), fecha="2026-02-28 23:59:59")
        primera = _comprar(e, e.a, (e.p1, 1, 100), fecha="2026-03-01 00:00:00")
        ultima = _comprar(e, e.a, (e.p1, 1, 100), fecha="2026-03-05 23:59:59")
        despues = _comprar(e, e.a, (e.p1, 1, 100), fecha="2026-03-06 00:00:00")

        dentro = servicio_compras.listar_pagina(fecha_desde="2026-03-01", fecha_hasta="2026-03-05")

        assert sorted(_ids(dentro)) == sorted([primera.id, ultima.id]) and dentro.total == 2
        assert _ids(servicio_compras.listar_pagina(fecha_hasta="2026-02-28")) == [antes.id]
        assert _ids(servicio_compras.listar_pagina(fecha_desde="2026-03-06")) == [despues.id]

    def test_las_fechas_se_comparan_sin_date_sobre_la_columna_e_indexadas(self, e):
        with repositorio_compras.obtener_conexion() as con:
            plan = " ".join(
                str(f[3])
                for f in con.execute(
                    "EXPLAIN QUERY PLAN SELECT c.id FROM compras c WHERE c.fecha >= ? AND c.fecha < date(?, '+1 day')",
                    ("2026-03-01", "2026-03-05"),
                )
            )

        assert "idx_compras_fecha" in plan

    def test_por_estado(self, e):
        activa = _comprar(e, e.a, (e.p1, 1, 100))
        anulada = _comprar(e, e.a, (e.p1, 1, 100))
        _anular(e, anulada)

        assert _ids(servicio_compras.listar_pagina(estado="ACTIVA")) == [activa.id]
        assert _ids(servicio_compras.listar_pagina(estado="ANULADA")) == [anulada.id]
        assert sorted(_ids(servicio_compras.listar_pagina(estado=None))) == sorted([activa.id, anulada.id])

    def test_por_producto_sin_duplicar_compras_de_varias_lineas(self, e):
        con_ambos = _comprar(e, e.a, (e.p1, 1, 100), (e.p2, 3, 400))
        solo_p2 = _comprar(e, e.a, (e.p2, 1, 400))

        p1 = servicio_compras.listar_pagina(producto_id=e.p1.id)
        p2 = servicio_compras.listar_pagina(producto_id=e.p2.id)

        assert _ids(p1) == [con_ambos.id] and p1.total == 1
        assert _ids(p2) == [solo_p2.id, con_ambos.id] and p2.total == 2  # el conteo tampoco duplica
        assert p2.compras[1].cantidad_lineas == 2

    def test_combinaciones(self, e):
        c1 = _comprar(e, e.a, (e.p1, 1, 100), fecha="2026-03-02 10:00:00")
        c2 = _comprar(e, e.a, (e.p1, 1, 100), fecha="2026-03-03 10:00:00")
        _comprar(e, e.b, (e.p1, 1, 100), fecha="2026-03-03 10:00:00")
        _comprar(e, e.a, (e.p2, 1, 400), fecha="2026-03-03 10:00:00")
        _anular(e, c2)

        resultado = servicio_compras.listar_pagina(
            proveedor_id=e.a.id, fecha_desde="2026-03-01", fecha_hasta="2026-03-31", estado="ACTIVA", producto_id=e.p1.id
        )

        assert _ids(resultado) == [c1.id]

    @pytest.mark.parametrize(
        "kwargs",
        [{"fecha_desde": "basura"}, {"fecha_hasta": "2026-02-30"}, {"fecha_desde": "2026-05-01", "fecha_hasta": "2026-01-01"},
         {"estado": "BORRADA"}, {"fecha_hasta": "9999-12-31"}],
    )
    def test_valores_invalidos_son_error_de_dominio(self, e, kwargs):
        with pytest.raises(DatosInvalidosError):
            servicio_compras.listar_pagina(**kwargs)

    @pytest.mark.parametrize(
        "consulta",
        ["proveedor_id=abc", "producto_id=1.5", "estado=BORRADA", "fecha_desde=basura", "pagina=abc&estado=X",
         "producto_id=" + "9" * 40, "proveedor_id=" + "9" * 5000],
    )
    def test_en_la_web_nunca_es_un_500(self, e, consulta):
        respuesta = solicitud("GET", f"/compras?{consulta}", cookies=e.co)

        assert respuesta.status < 500

    def test_la_pagina_web_ofrece_estado_y_producto_y_conserva_la_seleccion(self, e):
        anulada = _comprar(e, e.a, (e.p1, 1, 100))
        _anular(e, anulada)
        escribir(e.ruta, "UPDATE productos SET activo = 0 WHERE id = ?", (e.p2.id,))

        html = solicitud("GET", f"/compras?estado=ANULADA&producto_id={e.p1.id}", cookies=e.co).texto

        assert f'data-compra="{anulada.id}" data-estado="ANULADA"' in html
        assert 'value="ANULADA" selected' in html and f'value="{e.p1.id}" selected' in html
        assert "Gaseosa (inactivo)" in html

    def test_todos_equivale_a_sin_filtro_de_estado(self, e):
        _comprar(e, e.a, (e.p1, 1, 100))

        assert solicitud("GET", "/compras?estado=TODOS", cookies=e.co).texto.count('data-compra="') == 1


class TestUltimaCompraDelProducto:
    def test_es_la_ultima_activa_con_proveedor_fecha_y_costo(self, e):
        _comprar(e, e.a, (e.p1, 5, 150), fecha="2026-03-01 09:00:00")
        ultima = _comprar(e, e.b, (e.p1, 3, 200), fecha="2026-03-10 09:00:00")

        resultado = servicio_compras.obtener_ultima_compra_de_producto(e.p1.id)

        assert (resultado.compra_id, resultado.proveedor_nombre, resultado.fecha) == (ultima.id, "Mayorista Beta", "2026-03-10 09:00:00")
        assert (resultado.cantidad, resultado.costo_unitario_centavos) == (3, 200)

    def test_una_compra_anulada_posterior_no_reemplaza_a_la_ultima_valida(self, e):
        valida = _comprar(e, e.a, (e.p1, 5, 150))
        posterior = _comprar(e, e.b, (e.p1, 3, 999))
        _anular(e, posterior)

        resultado = servicio_compras.obtener_ultima_compra_de_producto(e.p1.id)

        assert resultado.compra_id == valida.id and resultado.costo_unitario_centavos == 150

    def test_costo_anterior_demostrable_por_el_evento_de_la_propia_compra(self, e):
        _comprar(e, e.a, (e.p1, 5, 150))  # 100 -> 150
        segunda = _comprar(e, e.a, (e.p1, 5, 200))  # 150 -> 200

        assert servicio_compras.obtener_ultima_compra_de_producto(e.p1.id).costo_anterior_centavos == 150
        assert servicio_compras.obtener_ultima_compra_de_producto(e.p1.id).compra_id == segunda.id

    def test_si_la_compra_no_cambio_el_costo_el_anterior_es_el_mismo(self, e):
        _comprar(e, e.a, (e.p1, 5, 100))  # el costo vigente ya era 100

        assert servicio_compras.obtener_ultima_compra_de_producto(e.p1.id).costo_anterior_centavos == 100

    def test_compra_historica_no_inventa_el_costo_anterior(self, e):
        compra = _comprar(e, e.a, (e.p1, 5, 150))
        escribir(e.ruta, "UPDATE compras SET costo_trazable = 0 WHERE id = ?", (compra.id,))
        escribir(e.ruta, "UPDATE detalle_compra SET historial_precio_id = NULL WHERE compra_id = ?", (compra.id,))

        resultado = servicio_compras.obtener_ultima_compra_de_producto(e.p1.id)

        assert resultado.costo_anterior_centavos is None and resultado.costo_unitario_centavos == 150

    def test_sin_compras_es_none(self, e):
        assert servicio_compras.obtener_ultima_compra_de_producto(e.p2.id) is None

    def test_la_edicion_del_producto_la_muestra_con_enlace_al_historial(self, e):
        compra = _comprar(e, e.a, (e.p1, 5, 150))

        html = solicitud("GET", f"/productos/{e.p1.id}/editar", cookies=e.co).texto

        assert f'id="ultima-compra"' in html and f"#{compra.id}" in html and "Distribuidora Alfa" in html
        assert "costo anterior $1,00" in html and f'href="/productos/{e.p1.id}/compras"' in html
        assert "Todavía no se compró" in solicitud("GET", f"/productos/{e.p2.id}/editar", cookies=e.co).texto


class TestHistorialDeComprasPorProducto:
    def test_activas_y_anuladas_con_orden_estable(self, e):
        c1 = _comprar(e, e.a, (e.p1, 5, 150), fecha="2026-03-01 09:00:00")
        c2 = _comprar(e, e.b, (e.p1, 2, 200), (e.p2, 1, 400), fecha="2026-03-02 09:00:00")
        _anular(e, c2)
        _comprar(e, e.a, (e.p2, 9, 400))  # de otro producto

        historial = servicio_compras.obtener_historial_de_producto(e.p1.id)

        assert [(l.compra_id, l.estado, l.cantidad, l.costo_unitario_centavos, l.subtotal_centavos, l.proveedor_nombre)
                for l in historial.lineas] == [
            (c2.id, "ANULADA", 2, 200, 400, "Mayorista Beta"),
            (c1.id, "ACTIVA", 5, 150, 750, "Distribuidora Alfa"),
        ]
        assert historial.lineas[0].motivo_anulacion == "ERROR_CARGA" and historial.lineas[0].fecha_anulacion
        assert servicio_compras.obtener_historial_de_producto(e.p1.id) == historial

    def test_filtra_por_fecha_y_pagina_en_sql(self, e, monkeypatch):
        monkeypatch.setattr(servicio_compras, "COMPRAS_POR_PAGINA", 2)
        ids = [_comprar(e, e.a, (e.p1, 1, 100), fecha=f"2026-03-0{d} 10:00:00").id for d in range(1, 6)]

        rango = servicio_compras.obtener_historial_de_producto(e.p1.id, "2026-03-02", "2026-03-04")
        pagina_2 = servicio_compras.obtener_historial_de_producto(e.p1.id, pagina=2)

        assert [l.compra_id for l in rango.lineas] == [ids[3], ids[2]] and rango.total == 3 and rango.total_paginas == 2
        assert [l.compra_id for l in pagina_2.lineas] == [ids[2], ids[1]] and pagina_2.total == 5

    def test_producto_inexistente_e_inactivo(self, e):
        _comprar(e, e.a, (e.p2, 4, 400))
        escribir(e.ruta, "UPDATE productos SET activo = 0 WHERE id = ?", (e.p2.id,))

        with pytest.raises(ProductoNoEncontradoError):
            servicio_compras.obtener_historial_de_producto(9999)
        assert len(servicio_compras.obtener_historial_de_producto(e.p2.id).lineas) == 1

    def test_pantalla_marca_las_anuladas_y_no_da_500_con_entradas_hostiles(self, e):
        activa = _comprar(e, e.a, (e.p1, 5, 150))
        anulada = _comprar(e, e.a, (e.p1, 2, 200))
        _anular(e, anulada)

        html = solicitud("GET", f"/productos/{e.p1.id}/compras", cookies=e.co).texto

        assert f'data-compra="{activa.id}" data-estado="ACTIVA"' in html and f'data-compra="{anulada.id}" data-estado="ANULADA"' in html
        for consulta in ("fecha_desde=basura", "fecha_hasta=2026-13-45", "pagina=abc", "pagina=99999999999999999999"):
            assert solicitud("GET", f"/productos/{e.p1.id}/compras?{consulta}", cookies=e.co).status < 500
        for id_hostil in ("999999", "9" * 30, "abc"):
            assert solicitud("GET", f"/productos/{id_hostil}/compras", cookies=e.co).status < 500
        assert solicitud("GET", "/productos/9999/compras", cookies=e.co).status == 303

    def test_solo_owner_y_no_escribe(self, e):
        _comprar(e, e.a, (e.p1, 5, 150))
        assert solicitud("GET", f"/productos/{e.p1.id}/compras", cookies=e.cajera).status == 403
        assert solicitud("GET", f"/productos/{e.p1.id}/compras").status == 303
        tablas = ("compras", "detalle_compra", "productos", "historial_precios", "auditoria")
        antes = [consultar(e.ruta, f"SELECT COUNT(*) FROM {t}")[0][0] for t in tablas]

        assert solicitud("GET", f"/productos/{e.p1.id}/compras", cookies=e.co).status == 200

        assert antes == [consultar(e.ruta, f"SELECT COUNT(*) FROM {t}")[0][0] for t in tablas]
