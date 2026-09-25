"""V1.6-B: reposición agrupada por proveedor principal, precarga del formulario de compra e imprimible.

La precarga y el imprimible no guardan nada: la compra solo se registra al confirmarla con el flujo
existente (`POST /compras/nueva` -> `servicio_compras.registrar_compra`)."""

import re

import pytest

from domain.compra import ItemCompra
from services import servicio_compras, servicio_proveedores, servicio_stock
from tests.utilidades_clientes import consultar, escribir, usuario_logueado

from ._asgi_cliente import solicitud


@pytest.fixture
def e(base_datos_temporal):
    """Proveedores Norte (A) y Sur (B) y productos:
    p1, p2 -> principal A (p1 con último costo 12,34 aunque B se lo vendió más caro después; p2 sin compras);
    p3 -> principal B; p4 -> sin proveedor principal; p5 -> justo en el mínimo (no entra)."""
    owner, cookies = usuario_logueado("OWNER", "duenio")
    a = servicio_proveedores.crear_proveedor("Norte")
    b = servicio_proveedores.crear_proveedor("Sur")
    productos = {
        n: servicio_stock.registrar_producto(f"77900000000{n}", f"Prod{n}", 500, 900, stock_actual=100)
        for n in range(1, 6)
    }
    servicio_compras.registrar_compra(a.id, owner.id, [ItemCompra(productos[1].id, 1, 1234)])
    servicio_compras.registrar_compra(b.id, owner.id, [ItemCompra(productos[1].id, 1, 2000)])
    servicio_compras.registrar_compra(b.id, owner.id, [ItemCompra(productos[3].id, 1, 700)])
    servicio_proveedores.vincular_producto(a.id, productos[2].id, owner.id)
    escribir(base_datos_temporal, "UPDATE productos SET stock_minimo = 10, stock_actual = 4")
    escribir(base_datos_temporal, "UPDATE productos SET stock_actual = 10 WHERE nombre = 'Prod5'")
    return type(
        "E", (), {"ruta": base_datos_temporal, "co": cookies, "owner": owner, "a": a, "b": b, "p": productos}
    )


def _tablas(ruta) -> dict[str, int]:
    return {
        t: consultar(ruta, f"SELECT COUNT(*) FROM {t}")[0][0]
        for t in ("compras", "detalle_compra", "ajustes_stock", "auditoria", "historial_precios")
    } | {"stock": consultar(ruta, "SELECT SUM(stock_actual) FROM productos")[0][0]}


def _comprar(e, consulta: str):
    return solicitud("GET", f"/reposicion/comprar?{consulta}", cookies=e.co)


def _sel(e, proveedor, cantidades: dict[int, object]) -> str:
    partes = [f"proveedor_id={proveedor.id}"] if proveedor is not None else []
    for n, cantidad in cantidades.items():
        partes += [f"p={e.p[n].id}", f"cantidad_{e.p[n].id}={cantidad}"]
    return "&".join(partes)


class TestAgrupacion:
    def test_agrupa_por_proveedor_principal_con_el_grupo_sin_proveedor_al_final(self, e):
        grupos = servicio_stock.agrupar_reposicion_por_proveedor()

        assert [g.proveedor_nombre for g in grupos] == ["Norte", "Sur", None]
        assert [sorted(s.nombre for s in g.sugerencias) for g in grupos] == [["Prod1", "Prod2"], ["Prod3"], ["Prod4"]]

    def test_la_regla_no_cambia_el_producto_justo_en_el_minimo_queda_fuera(self, e):
        nombres = {s.nombre: s for g in servicio_stock.agrupar_reposicion_por_proveedor() for s in g.sugerencias}

        assert "Prod5" not in nombres
        assert {s.cantidad_sugerida for s in nombres.values()} == {6}  # 10 - 4

    def test_costo_ultimo_del_proveedor_principal_o_el_vigente_del_producto(self, e):
        por_nombre = {s.nombre: s for g in servicio_stock.agrupar_reposicion_por_proveedor() for s in g.sugerencias}

        assert por_nombre["Prod1"].costo_unitario_centavos == 1234  # el vigente del producto ya es 2000 (compra a B)
        assert por_nombre["Prod1"].costo_es_del_proveedor
        assert (por_nombre["Prod2"].costo_unitario_centavos, por_nombre["Prod2"].costo_es_del_proveedor) == (500, False)

    def test_la_pantalla_muestra_cada_grupo_y_el_sin_proveedor_no_envia_proveedor(self, e):
        html = solicitud("GET", "/reposicion", cookies=e.co).texto

        assert html.count("data-grupo-reposicion") == 3
        assert f'name="proveedor_id" value="{e.a.id}"' in html and f'name="proveedor_id" value="{e.b.id}"' in html
        assert html.count('name="proveedor_id"') == 2  # el grupo sin proveedor principal no inventa uno
        assert "Sin proveedor principal" in html and 'action="/reposicion/comprar"' in html
        assert 'method="post"' not in html


class TestIdentidadDelGrupo:
    def test_dos_proveedores_de_nombre_equivalente_son_grupos_distintos_y_no_se_mezclan(self, e):
        # El esquema exige `nombre` único (sensible a mayúsculas): el caso posible es el mismo nombre
        # con otra capitalización, que ordena igual. La identidad del grupo es el `proveedor_id`.
        centro_1 = servicio_proveedores.crear_proveedor("Distribuidora Centro")
        centro_2 = servicio_proveedores.crear_proveedor("DISTRIBUIDORA CENTRO")
        for n, proveedor in ((6, centro_1), (7, centro_2)):
            e.p[n] = servicio_stock.registrar_producto(f"77900000000{n}", f"Prod{n}", 500, 900, stock_actual=1, stock_minimo=10)
            servicio_proveedores.vincular_producto(proveedor.id, e.p[n].id, e.owner.id)

        grupos = [
            g for g in servicio_stock.agrupar_reposicion_por_proveedor() if (g.proveedor_nombre or "").casefold() == "distribuidora centro"
        ]

        assert sorted(g.proveedor_id for g in grupos) == sorted([centro_1.id, centro_2.id])
        assert sorted([s.nombre for s in g.sugerencias] for g in grupos) == [["Prod6"], ["Prod7"]]
        assert _comprar(e, _sel(e, centro_1, {6: 1})).status == 200
        assert _comprar(e, _sel(e, centro_1, {6: 1, 7: 1})).status == 303  # Prod7 es del otro proveedor homónimo
        assert _comprar(e, _sel(e, centro_2, {6: 1})).status == 303
        assert solicitud("GET", f"/reposicion/imprimir?{_sel(e, centro_2, {6: 1, 7: 1})}", cookies=e.co).status == 303
        assert solicitud("GET", "/reposicion", cookies=e.co).texto.count("data-grupo-reposicion") == 5


class TestProveedorInactivo:
    def test_no_se_puede_precargar_una_compra_pero_si_imprimir_la_lista(self, e):
        assert servicio_proveedores.eliminar_proveedor(e.a.id, e.owner.id)
        html = solicitud("GET", "/reposicion", cookies=e.co).texto
        consulta = _sel(e, e.a, {1: 2})

        assert "Inactivo" in html and html.count('data-accion="comprar"') == 2  # Sur y sin proveedor, no Norte
        assert _comprar(e, consulta).status == 303
        assert solicitud("GET", f"/reposicion/imprimir?{consulta}", cookies=e.co).status == 200


class TestPrecarga:
    def test_precarga_proveedor_productos_cantidades_editadas_y_costos_sugeridos(self, e):
        respuesta = _comprar(e, _sel(e, e.a, {1: 7, 2: 3}))

        html = respuesta.texto
        assert respuesta.status == 200
        assert re.search(rf'<option value="{e.a.id}"\s+selected', html)
        assert html.count('class="linea-compra') == 2 + 1  # 2 líneas + la de la plantilla oculta
        lineas = re.findall(r'<option value="(\d+)"\s+selected>Prod\d</option>', html)
        assert lineas == [str(e.p[1].id), str(e.p[2].id)]
        assert re.findall(r'name="cantidad" min="1" step="1" required value="(\d+)"', html) == ["7", "3"]
        assert re.findall(r'name="costo_unitario"[^>]*value="([\d.]+)"', html) == ["12.34", "5.00"]

    def test_un_producto_no_tildado_no_se_envia_y_no_aparece(self, e):
        html = _comprar(e, _sel(e, e.a, {2: 3})).texto

        assert re.findall(r'<option value="(\d+)"\s+selected>Prod\d</option>', html) == [str(e.p[2].id)]

    def test_no_guarda_nada_ni_toca_el_stock(self, e):
        antes = _tablas(e.ruta)

        _comprar(e, _sel(e, e.a, {1: 7, 2: 3}))
        solicitud("GET", f"/reposicion/imprimir?{_sel(e, e.a, {1: 7})}", cookies=e.co)

        assert _tablas(e.ruta) == antes

    def test_no_mezcla_productos_de_otro_proveedor(self, e):
        respuesta = _comprar(e, _sel(e, e.a, {1: 2, 3: 2}))  # Prod3 es de Sur

        assert respuesta.status == 303  # rechazado con un mensaje, no una precarga mezclada
        assert solicitud("GET", f"/reposicion/imprimir?{_sel(e, e.a, {1: 2, 3: 2})}", cookies=e.co).status == 303

    def test_los_productos_sin_proveedor_se_precargan_sin_inventar_uno(self, e):
        respuesta = _comprar(e, _sel(e, None, {4: 5}))

        assert respuesta.status == 200 and "selected>Prod4" in respuesta.texto.replace('"\n', '" ')
        assert not re.search(r'<option value="\d+"\s+selected>(Norte|Sur)', respuesta.texto)

    def test_producto_justo_en_el_minimo_o_sin_seleccion_es_un_error_controlado(self, e):
        assert _comprar(e, _sel(e, e.a, {5: 1})).status == 303
        assert _comprar(e, f"proveedor_id={e.a.id}").status == 303

    @pytest.mark.parametrize("cantidad", ["0", "-1", "abc", "", "1.5", "9" * 30, "9" * 5000])
    def test_una_cantidad_invalida_es_un_error_controlado(self, e, cantidad):
        assert _comprar(e, _sel(e, e.a, {1: cantidad})).status == 303

    @pytest.mark.parametrize("proveedor", ["abc", "999999", "9" * 5000])
    def test_un_proveedor_invalido_es_un_error_controlado(self, e, proveedor):
        assert _comprar(e, f"proveedor_id={proveedor}&p={e.p[1].id}&cantidad_{e.p[1].id}=1").status == 303

    def test_solo_el_owner_accede_por_url_directa(self, e):
        _, cajera = usuario_logueado("CASHIER", "cajera")
        consulta = _sel(e, e.a, {1: 1})

        for ruta in ("/reposicion/comprar", "/reposicion/imprimir"):
            assert solicitud("GET", f"{ruta}?{consulta}", cookies=cajera).status == 403
            assert solicitud("GET", f"{ruta}?{consulta}").status == 303


class TestCompraFinalPorElFlujoExistente:
    def _confirmar(self, e, cantidades: dict[int, int]):
        html = _comprar(e, _sel(e, e.a, cantidades)).texto
        clave = re.search(r'name="clave_idempotencia" value="([^"]+)"', html).group(1)
        formulario = {
            "clave_idempotencia": clave,
            "proveedor_id": str(e.a.id),
            "observaciones": "",
            "producto_id": [str(e.p[n].id) for n in cantidades],
            "cantidad": [str(c) for c in cantidades.values()],
            "costo_unitario": re.findall(r'name="costo_unitario"[^>]*value="([\d.]+)"', html),
        }
        return formulario, solicitud("POST", "/compras/nueva", cookies=e.co, formulario=formulario)

    def test_la_compra_se_registra_una_sola_vez_con_stock_costo_y_vinculo(self, e):
        stock_antes = consultar(e.ruta, "SELECT stock_actual FROM productos WHERE id = ?", (e.p[1].id,))[0][0]
        vinculos_antes = consultar(e.ruta, "SELECT COUNT(*) FROM producto_proveedor")[0][0]

        formulario, respuesta = self._confirmar(e, {1: 7, 2: 3})

        assert respuesta.status == 303
        producto = consultar(e.ruta, "SELECT stock_actual, precio_costo_centavos FROM productos WHERE id = ?", (e.p[1].id,))[0]
        assert (producto[0], producto[1]) == (stock_antes + 7, 1234)  # el costo vigente pasa a ser el de la compra
        assert consultar(e.ruta, "SELECT COUNT(*) FROM historial_precios WHERE producto_id = ? AND origen = 'COMPRA'", (e.p[1].id,))[0][0] == 3
        assert consultar(e.ruta, "SELECT COUNT(*) FROM producto_proveedor")[0][0] == vinculos_antes
        assert consultar(e.ruta, "SELECT COUNT(*) FROM auditoria WHERE accion = 'COMPRA_REGISTRADA'")[0][0] == 4

        # Reenviar el mismo formulario (misma clave) no duplica compra ni stock.
        reenvio = solicitud("POST", "/compras/nueva", cookies=e.co, formulario=formulario)
        assert reenvio.status == 303
        assert consultar(e.ruta, "SELECT stock_actual FROM productos WHERE id = ?", (e.p[1].id,))[0][0] == stock_antes + 7
        assert consultar(e.ruta, "SELECT COUNT(*) FROM compras")[0][0] == 4


class TestImprimible:
    def test_muestra_proveedor_filas_cantidades_subtotales_y_total(self, e):
        html = solicitud("GET", f"/reposicion/imprimir?{_sel(e, e.a, {1: 7, 2: 3})}", cookies=e.co).texto

        assert 'id="reposicion-proveedor">Norte<' in html
        assert re.findall(r'<td class="num">(\d+)</td>', html) == ["7", "3"]
        assert "$86,38" in html and "$15,00" in html  # 7 x 12,34 y 3 x 5,00
        assert 'id="reposicion-total">$101,38<' in html
        assert "no es una orden de compra" in html

    def test_solo_incluye_lo_elegido_y_el_sin_proveedor_se_rotula(self, e):
        html = solicitud("GET", f"/reposicion/imprimir?{_sel(e, None, {4: 2})}", cookies=e.co).texto

        assert "Sin proveedor principal" in html and "Prod4" in html
        assert "Prod1" not in html and 'id="reposicion-total">$10,00<' in html  # 2 x 5,00
