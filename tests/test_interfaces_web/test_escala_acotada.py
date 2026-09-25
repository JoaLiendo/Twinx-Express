"""V1.5-F: Historial paginado, caja acotada a la sesión relevante y dashboard con los últimos 5."""

import re

import pytest

from db.repositorios import caja as repositorio_caja
from db.repositorios import ventas as repositorio_ventas
from domain.venta import ItemVenta
from services import servicio_caja, servicio_ventas
from tests.utilidades_clientes import crear_producto, usuario_logueado

from ._asgi_cliente import solicitud

TOTAL_VENTAS = 250  # 3 páginas de 100: 100 + 100 + 50
_ID_EN_FILA = re.compile(r"window\.location\.href='/ventas/(\d+)'")


@pytest.fixture
def historial(base_datos_temporal, caja_abierta):
    """250 ventas (ids 1..250, pago TARJETA las impares y EFECTIVO las pares) y cookies de un OWNER."""
    owner, cookies = usuario_logueado("OWNER", "duenio")
    producto = crear_producto(stock=10_000)
    for indice in range(TOTAL_VENTAS):
        tipo_pago = "EFECTIVO" if indice % 2 else "TARJETA"
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], tipo_pago, usuario_id=owner.id)
    return cookies


def _pagina(cookies, consulta: str = "") -> tuple[list[int], str]:
    respuesta = solicitud("GET", "/ventas/historial" + consulta, cookies=cookies)
    assert respuesta.status == 200
    return [int(numero) for numero in _ID_EN_FILA.findall(respuesta.texto)], respuesta.texto


class TestHistorialPaginado:
    def test_paginas_sin_duplicados_ni_huecos_y_en_orden(self, historial):
        pagina_1, _ = _pagina(historial)
        pagina_2, _ = _pagina(historial, "?pagina=2")
        pagina_3, _ = _pagina(historial, "?pagina=3")

        assert (len(pagina_1), len(pagina_2), len(pagina_3)) == (100, 100, 50)
        assert pagina_1 + pagina_2 + pagina_3 == list(range(TOTAL_VENTAS, 0, -1))

    def test_los_filtros_se_aplican_y_se_conservan_en_los_enlaces(self, historial):
        pagina_1, html = _pagina(historial, "?tipo_pago=TARJETA")
        pagina_2, _ = _pagina(historial, "?tipo_pago=TARJETA&pagina=2")

        assert len(pagina_1) == 100 and len(pagina_2) == 25
        assert pagina_1 + pagina_2 == list(range(TOTAL_VENTAS - 1, 0, -2))  # las TARJETA son las ids impares
        assert "tipo_pago=TARJETA&amp;pagina=2" in html or "tipo_pago=TARJETA&pagina=2" in html
        assert "125 ventas · página 1 de 2" in html

    def test_el_conteo_es_del_filtro_completo_no_de_la_pagina(self, historial):
        assert servicio_ventas.contar_historial() == TOTAL_VENTAS
        assert servicio_ventas.contar_historial(tipo_pago="EFECTIVO") == 125
        assert servicio_ventas.contar_historial(estado="ANULADA") == 0
        _, html = _pagina(historial, "?pagina=3")
        assert "250 ventas · página 3 de 3" in html

    @pytest.mark.parametrize(
        "pagina", ["abc", "0", "-5", "", "1.5", pytest.param("9" * 5000, id="mas-de-4300-digitos")]
    )
    def test_pagina_invalida_equivale_a_la_primera(self, historial, pagina):
        ids, _ = _pagina(historial, f"?pagina={pagina}")

        assert ids == list(range(TOTAL_VENTAS, TOTAL_VENTAS - 100, -1))

    @pytest.mark.parametrize("pagina", ["99", "9" * 30])
    def test_pagina_fuera_de_rango_muestra_la_ultima(self, historial, pagina):
        ids, html = _pagina(historial, f"?pagina={pagina}")

        assert ids == list(range(50, 0, -1))
        assert "página 3 de 3" in html

    def test_el_recorte_ocurre_en_sql_no_en_python(self, historial, monkeypatch):
        def _prohibido(*_argumentos, **_kwargs):
            raise AssertionError("el Historial no debe cargar todas las ventas")

        monkeypatch.setattr(repositorio_ventas, "listar_resumen", _prohibido)

        ids, _ = _pagina(historial, "?pagina=2")

        assert len(ids) == 100

    def test_sin_ventas_no_hay_paginacion(self, base_datos_temporal):
        _, cookies = usuario_logueado("OWNER", "duenio")

        ids, html = _pagina(cookies)

        assert ids == [] and "Paginación" not in html


@pytest.fixture
def owner_web(base_datos_temporal):
    return usuario_logueado("OWNER", "duenio")


def _abrir_con_movimientos(prefijo: str, cantidad: int, usuario_id: int) -> None:
    servicio_caja.abrir_caja(1000, f"{prefijo}-apertura", usuario_id)
    for indice in range(cantidad):
        servicio_caja.registrar_ingreso(100, f"{prefijo}-ingreso-{indice:03d}", usuario_id)


def _ingresos_en(html: str, prefijo: str) -> int:
    return len(re.findall(rf"{prefijo}-ingreso-\d{{3}}", html))


class TestCajaAcotadaALaSesionRelevante:
    def test_muestra_solo_la_sesion_abierta_y_no_mezcla_las_anteriores(self, owner_web):
        owner, cookies = owner_web
        _abrir_con_movimientos("s1", 120, owner.id)
        servicio_caja.cerrar_caja(1000 + 120 * 100, "cierre-s1", owner.id)
        _abrir_con_movimientos("s2", 150, owner.id)

        html = solicitud("GET", "/caja", cookies=cookies).texto

        assert _ingresos_en(html, "s2") == 150
        assert "s1-" not in html

    def test_sin_sesion_abierta_muestra_la_ultima_cerrada(self, owner_web):
        owner, cookies = owner_web
        _abrir_con_movimientos("s1", 30, owner.id)
        servicio_caja.cerrar_caja(4000, "cierre-s1", owner.id)
        _abrir_con_movimientos("s2", 40, owner.id)
        servicio_caja.cerrar_caja(5000, "cierre-s2", owner.id)

        html = solicitud("GET", "/caja", cookies=cookies).texto

        assert _ingresos_en(html, "s2") == 40 and "cierre-s2" in html
        assert "s1-" not in html

    def test_una_sesion_abierta_tiene_prioridad_sobre_la_cerrada(self, owner_web):
        owner, cookies = owner_web
        _abrir_con_movimientos("s1", 10, owner.id)
        servicio_caja.cerrar_caja(2000, "cierre-s1", owner.id)
        _abrir_con_movimientos("s2", 5, owner.id)

        html = solicitud("GET", "/caja", cookies=cookies).texto

        assert _ingresos_en(html, "s2") == 5
        assert "s1-" not in html

    def test_arqueo_y_cierre_siguen_calculando_sobre_la_sesion_completa(self, owner_web):
        owner, cookies = owner_web
        _abrir_con_movimientos("s1", 50, owner.id)
        servicio_caja.cerrar_caja(6000, "cierre-s1", owner.id)
        _abrir_con_movimientos("s2", 300, owner.id)

        assert servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos == 1000 + 300 * 100
        assert solicitud("GET", "/caja/arqueo", cookies=cookies).status == 200
        cierre = servicio_caja.cerrar_caja(1000 + 300 * 100, "cierre-s2", owner.id)
        assert cierre.diferencia_centavos == 0

    def test_sin_ninguna_sesion_el_panel_carga_vacio(self, owner_web):
        _, cookies = owner_web

        respuesta = solicitud("GET", "/caja", cookies=cookies)

        assert respuesta.status == 200 and "Todavía no hay movimientos de caja." in respuesta.texto


class TestDashboardUltimosMovimientos:
    def test_devuelve_como_maximo_los_5_mas_recientes_en_orden(self, owner_web):
        owner, _ = owner_web
        _abrir_con_movimientos("d", 11, owner.id)  # apertura + 11 ingresos = 12 movimientos

        recientes = servicio_caja.listar_movimientos_recientes(5)

        assert [m.descripcion for m in recientes] == [f"d-ingreso-{i:03d}" for i in range(10, 5, -1)]

    def test_el_dashboard_muestra_solo_esos_5_sin_cargar_el_historial(self, owner_web, monkeypatch):
        owner, cookies = owner_web
        _abrir_con_movimientos("d", 11, owner.id)

        def _prohibido():
            raise AssertionError("el dashboard no debe cargar todos los movimientos")

        monkeypatch.setattr(repositorio_caja, "listar_movimientos", _prohibido)

        html = solicitud("GET", "/", cookies=cookies).texto

        assert _ingresos_en(html, "d") == 5
        assert "d-ingreso-010" in html and "d-ingreso-006" in html and "d-ingreso-005" not in html
        assert html.index("d-ingreso-010") < html.index("d-ingreso-006")

    def test_sin_movimientos_devuelve_lista_vacia(self, owner_web):
        assert servicio_caja.listar_movimientos_recientes(5) == []
