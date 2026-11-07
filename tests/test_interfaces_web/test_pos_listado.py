"""Selección manual del POS: la lista de productos que sirve `GET /ventas`.

La búsqueda y los filtros corren en el navegador (`static/js/pos_listado.js`, probado en
`tests_js/pos_listado.test.js` y en `tests_e2e/tests/20-pos-listado-manual.spec.js`); acá se prueba lo
que entrega el servidor: el formato de lista, los datos de cada fila, los filtros disponibles y que ningún
rol ni parámetro manipulado exponga costos."""

import re

from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth, servicio_categorias, servicio_stock

from ._asgi_cliente import solicitud

COSTO_DISTINTIVO_CENTAVOS = 98_765


def _cookies(rol: str) -> dict[str, str]:
    nombre_usuario = f"usuario_{rol.lower()}"
    repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre_usuario,
            nombre_completo="Usuario de prueba",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol=rol,
        )
    )
    return {NOMBRE_COOKIE_SESION: servicio_auth.iniciar_sesion(nombre_usuario, "clave-correcta-123").token}


def _catalogo() -> dict:
    bebidas = servicio_categorias.crear_categoria("Bebidas")
    gaseosas = servicio_categorias.crear_categoria("Gaseosas", bebidas.id)
    coca = servicio_stock.registrar_producto(
        "7790895000997", "Coca-Cola 500 ml", COSTO_DISTINTIVO_CENTAVOS, 150_000,
        stock_actual=12, stock_minimo=4, categoria_id=gaseosas.id,
    )
    agua = servicio_stock.registrar_producto(
        "7798062540017", "Agua mineral", 100, 90_000, stock_actual=0, categoria_id=bebidas.id
    )
    alfajor = servicio_stock.registrar_producto("7790580123456", "Alfajor triple", 100, 80_000, stock_actual=2, stock_minimo=5)
    return {"bebidas": bebidas, "gaseosas": gaseosas, "coca": coca, "agua": agua, "alfajor": alfajor}


def _fila(html: str, producto_id: int) -> str:
    coincidencia = re.search(rf'<tr data-producto-fila\s+data-id="{producto_id}".*?</tr>', html, re.DOTALL)
    assert coincidencia is not None, f"no hay fila para el producto {producto_id}"
    return coincidencia.group(0)


def _html(ruta: str = "/ventas", rol: str = "CASHIER") -> str:
    respuesta = solicitud("GET", ruta, cookies=_cookies(rol))
    assert respuesta.status == 200
    return respuesta.texto


class TestFormatoDeLista:
    def test_ya_no_usa_la_grilla_de_tarjetas(self, base_datos_temporal):
        _catalogo()
        html = _html()

        assert "data-producto-card" not in html
        assert "grid-cols-2 sm:grid-cols-3 xl:grid-cols-4" not in html
        assert 'id="listado-productos"' in html
        assert html.count("<tr data-producto-fila") == 3

    def test_columnas_de_la_tabla(self, base_datos_temporal):
        _catalogo()
        html = _html()
        cabecera = re.search(r"<thead.*?</thead>", html, re.DOTALL).group(0)

        columnas = [re.sub(r"<[^>]+>", "", celda).strip() for celda in re.findall(r"<th[^>]*>.*?</th>", cabecera, re.DOTALL)]
        assert columnas == ["Código", "Producto", "Categoría", "Stock", "Precio", "Acción"]

    def test_la_fila_identifica_al_producto(self, base_datos_temporal):
        catalogo = _catalogo()
        fila = _fila(_html(), catalogo["coca"].id)

        assert 'data-codigo="7790895000997"' in fila
        assert "Coca-Cola 500 ml" in fila
        assert "Bebidas › Gaseosas" in fila
        assert 'data-stock="12"' in fila
        assert "$1.500,00" in fila
        assert "Agregar" in fila

    def test_el_filtro_por_categoria_respeta_la_jerarquia(self, base_datos_temporal):
        catalogo = _catalogo()
        html = _html()

        fila_coca = _fila(html, catalogo["coca"].id)
        assert f'data-categorias="{catalogo["bebidas"].id} {catalogo["gaseosas"].id}"' in fila_coca
        assert f'data-categorias="{catalogo["bebidas"].id}"' in _fila(html, catalogo["agua"].id)
        assert 'data-categorias=""' in _fila(html, catalogo["alfajor"].id)
        assert f'<option value="{catalogo["bebidas"].id}">Bebidas</option>' in html
        assert f'<option value="{catalogo["gaseosas"].id}">— Gaseosas</option>' in html

    def test_filtros_y_busqueda_disponibles(self, base_datos_temporal):
        _catalogo()
        html = _html()

        assert 'id="buscar-producto"' in html
        assert 'placeholder="Buscar por nombre o código (↑ ↓ elige, Enter agrega)"' in html
        for valor in ("con_stock", "bajo", "sin_stock"):
            assert f'<option value="{valor}">' in html
        assert "No encontramos productos con esos filtros." in html
        assert "data-limpiar-filtros" in html
        assert "3 productos encontrados" in html

    def test_ya_no_hay_formulario_que_recargue_la_pagina(self, base_datos_temporal):
        _catalogo()
        html = _html()

        assert 'action="/ventas"' not in html
        assert 'name="q"' not in html

    def test_el_script_de_la_lista_se_carga_antes_que_pos_js(self, base_datos_temporal):
        _catalogo()
        html = _html()

        assert html.index("/static/js/pos_listado.js") < html.index("/static/js/pos.js")


class TestEstadoDeStock:
    def test_sin_stock_no_se_puede_agregar(self, base_datos_temporal):
        catalogo = _catalogo()
        fila = _fila(_html(), catalogo["agua"].id)

        assert 'aria-disabled="true"' in fila
        assert re.search(r"<button[^>]*data-agregar\s+disabled", fila)
        assert 'data-stock-estado="sin_stock"' in fila

    def test_stock_bajo_y_disponible(self, base_datos_temporal):
        catalogo = _catalogo()
        html = _html()

        assert 'data-stock-estado="bajo"' in _fila(html, catalogo["alfajor"].id)
        fila_coca = _fila(html, catalogo["coca"].id)
        assert 'data-stock-estado="disponible"' in fila_coca
        assert 'aria-disabled="true"' not in fila_coca

    def test_un_producto_dado_de_baja_no_aparece(self, base_datos_temporal):
        catalogo = _catalogo()
        servicio_stock.eliminar_producto(catalogo["alfajor"].id)

        html = _html()

        assert f'data-id="{catalogo["alfajor"].id}"' not in html
        assert "2 productos encontrados" in html


class TestCostosNoExpuestos:
    def _sin_costo(self, html: str) -> None:
        for forma in ("98765", "987,65", "987.65"):
            assert forma not in html
        assert "costo" not in html.lower()

    def test_el_cajero_no_ve_costos(self, base_datos_temporal):
        _catalogo()
        self._sin_costo(_html(rol="CASHIER"))

    def test_parametros_manipulados_no_exponen_costos(self, base_datos_temporal):
        _catalogo()
        html = _html(
            "/ventas?q=coca&mostrar_costo=true&campos=precio_costo_centavos&es_owner=true&rol=OWNER&mostrar_inactivos=true",
            rol="CASHIER",
        )

        self._sin_costo(html)
        assert html.count("<tr data-producto-fila") == 3

    def test_el_lector_tampoco_expone_costos_con_parametros_extra(self, base_datos_temporal):
        _catalogo()
        respuesta = solicitud(
            "GET",
            "/api/productos/buscar-codigo/7790895000997?campos=precio_costo_centavos&mostrar_costo=true",
            cookies=_cookies("CASHIER"),
        )

        assert respuesta.status == 200
        assert set(respuesta.json()) == {"id", "codigo_barras", "nombre", "precio_venta_centavos", "stock_actual"}

    def test_el_dueno_ve_la_misma_lista_sin_regresiones(self, base_datos_temporal):
        _catalogo()
        html = _html(rol="OWNER")

        assert html.count("<tr data-producto-fila") == 3
        self._sin_costo(html)
