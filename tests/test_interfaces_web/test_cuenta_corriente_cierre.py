"""V1.6-A: cierre funcional de cuenta corriente -- filtro por cliente en el Historial, ticket de venta a
cuenta con cliente y saldo resultante, y deuda total en el listado de clientes."""

import re

import pytest

from db.repositorios import clientes as repositorio_clientes
from domain.venta import ItemVenta
from services import servicio_clientes, servicio_cuenta_corriente, servicio_stock, servicio_ventas
from tests.utilidades_clientes import forzar_cliente_inactivo, usuario_logueado

from ._asgi_cliente import solicitud

pytestmark = pytest.mark.usefixtures("caja_abierta")

CC = "CUENTA_CORRIENTE"
AMPLIO = "fecha_desde=2000-01-01&fecha_hasta=2099-12-31"
_ID_EN_FILA = re.compile(r"window\.location\.href='/ventas/(\d+)'")


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies_owner = usuario_logueado("OWNER", "duenio")
    cajera, cookies_cajera = usuario_logueado("CASHIER", "cajera")
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=1000)
    ana = servicio_clientes.crear_cliente("Ana Gómez", owner.id)
    beto = servicio_clientes.crear_cliente("Beto Ruiz", owner.id)
    return type(
        "Escenario",
        (),
        {"ruta": base_datos_temporal, "owner": owner, "cajera": cajera, "co": cookies_owner, "cc": cookies_cajera,
         "producto": producto, "ana": ana, "beto": beto},
    )


def _vender(e, cliente=None, tipo_pago=CC):
    return servicio_ventas.registrar_venta(
        [ItemVenta(e.producto.id, 1)], tipo_pago, usuario_id=e.cajera.id, cliente_id=cliente.id if cliente else None
    )


def _ids(e, consulta: str = "", cookies=None) -> tuple[list[int], str]:
    respuesta = solicitud("GET", f"/ventas/historial?{AMPLIO}&{consulta}", cookies=cookies or e.co)
    assert respuesta.status == 200
    return [int(numero) for numero in _ID_EN_FILA.findall(respuesta.texto)], respuesta.texto


class TestHistorialFiltraPorCliente:
    def test_cada_cliente_ve_solo_sus_ventas_y_sin_filtro_se_ven_todas(self, e):
        v_ana = [_vender(e, e.ana).id, _vender(e, e.ana).id]
        v_beto = _vender(e, e.beto).id
        v_efectivo = _vender(e, tipo_pago="EFECTIVO").id

        ids_ana, html = _ids(e, f"cliente_id={e.ana.id}")
        ids_beto, _ = _ids(e, f"cliente_id={e.beto.id}")
        ids_todas, _ = _ids(e)

        assert ids_ana == sorted(v_ana, reverse=True)
        assert ids_beto == [v_beto]
        assert ids_todas == sorted([*v_ana, v_beto, v_efectivo], reverse=True)
        assert 'id="filtro-cliente"' in html and "Ana Gómez" in html

    def test_se_combina_con_los_demas_filtros(self, e):
        v_ana = _vender(e, e.ana).id
        _vender(e, e.beto)
        _vender(e, tipo_pago="TARJETA")

        con_cc, _ = _ids(e, f"cliente_id={e.ana.id}&tipo_pago={CC}")
        con_tarjeta, _ = _ids(e, f"cliente_id={e.ana.id}&tipo_pago=TARJETA")
        anuladas, _ = _ids(e, f"cliente_id={e.ana.id}&estado=ANULADA")

        assert con_cc == [v_ana] and con_tarjeta == [] and anuladas == []

    def test_los_campos_vacios_del_formulario_significan_sin_filtro(self, e):
        """El "Todos" del medio de pago y del estado, y una fecha borrada, llegan como cadena vacía."""
        v_ana = _vender(e, e.ana).id
        v_efectivo = _vender(e, tipo_pago="EFECTIVO").id

        respuesta = solicitud(
            "GET", "/ventas/historial?fecha_desde=&fecha_hasta=&tipo_pago=&estado=&cliente_id=", cookies=e.co
        )
        con_cliente, _ = _ids(e, f"tipo_pago=&estado=&cliente_id={e.ana.id}")

        assert respuesta.status == 200
        assert sorted(int(n) for n in _ID_EN_FILA.findall(respuesta.texto)) == [v_ana, v_efectivo]
        assert con_cliente == [v_ana]

    def test_el_formulario_de_filtros_conserva_el_cliente(self, e):
        _, html = _ids(e, f"cliente_id={e.ana.id}")

        assert f'name="cliente_id" value="{e.ana.id}"' in html

    def test_la_paginacion_conserva_el_cliente_y_el_conteo_es_del_cliente(self, e):
        de_ana = [_vender(e, e.ana).id for _ in range(105)]
        for _ in range(10):
            _vender(e, e.beto)

        pagina_1, html = _ids(e, f"cliente_id={e.ana.id}")
        pagina_2, _ = _ids(e, f"cliente_id={e.ana.id}&pagina=2")

        assert pagina_1 + pagina_2 == sorted(de_ana, reverse=True)
        assert (len(pagina_1), len(pagina_2)) == (100, 5)
        assert "105 ventas · página 1 de 2" in html
        assert f"cliente_id={e.ana.id}&amp;pagina=2" in html or f"&amp;cliente_id={e.ana.id}&amp;pagina=2" in html

    @pytest.mark.parametrize("cliente_id", ["abc", "999999", "-1", "1.5", "9" * 30, "9" * 5000])
    def test_un_cliente_invalido_o_inexistente_es_un_error_controlado(self, e, cliente_id):
        respuesta = solicitud("GET", f"/ventas/historial?{AMPLIO}&cliente_id={cliente_id}", cookies=e.co)

        assert respuesta.status == 303  # redirige con un mensaje de error, nunca 500

    def test_cliente_id_vacio_equivale_a_sin_filtro(self, e):
        v = _vender(e, e.ana).id

        ids, html = _ids(e, "cliente_id=")

        assert ids == [v] and 'id="filtro-cliente"' not in html

    def test_la_ficha_del_cliente_enlaza_a_sus_ventas(self, e):
        html = solicitud("GET", f"/clientes/{e.ana.id}", cookies=e.cc).texto

        assert f'href="/ventas/historial?cliente_id={e.ana.id}"' in html


class TestTicketDeVentaACuenta:
    def _ticket(self, e, venta_id, cookies=None) -> str:
        respuesta = solicitud("GET", f"/ventas/{venta_id}/ticket", cookies=cookies or e.cc)
        assert respuesta.status == 200
        return respuesta.texto

    def test_muestra_cliente_y_el_saldo_resultante_de_esa_venta(self, e):
        primera = _vender(e, e.ana)  # saldo tras la venta: 2,00
        segunda = _vender(e, e.ana)  # 4,00

        assert "Cliente: Ana Gómez" in self._ticket(e, primera.id)
        assert "Saldo cuenta corriente: $2,00" in self._ticket(e, primera.id)
        assert "Saldo cuenta corriente: $4,00" in self._ticket(e, segunda.id)

    def test_el_saldo_es_el_de_esa_venta_aunque_despues_haya_un_cobro_u_otra_venta(self, e):
        primera = _vender(e, e.ana)
        servicio_cuenta_corriente.registrar_cobro(e.ana.id, 150, e.cajera.id)
        _vender(e, e.ana)

        assert "Saldo cuenta corriente: $2,00" in self._ticket(e, primera.id)
        assert servicio_clientes.obtener_saldo(e.ana.id) == 250  # el saldo actual es otro

    def test_el_saldo_no_incluye_a_otros_clientes(self, e):
        _vender(e, e.beto)
        venta_ana = _vender(e, e.ana)

        assert "Saldo cuenta corriente: $2,00" in self._ticket(e, venta_ana.id)

    def test_una_venta_normal_no_muestra_el_bloque_de_cuenta_corriente(self, e):
        venta = _vender(e, tipo_pago="EFECTIVO")

        texto = self._ticket(e, venta.id)

        assert "ticket-cuenta-corriente" not in texto and "Saldo cuenta corriente" not in texto

    def test_lo_ve_el_owner_igual_que_antes(self, e):
        venta = _vender(e, e.ana)

        assert "Cliente: Ana Gómez" in self._ticket(e, venta.id, cookies=e.co)


class TestDeudaTotal:
    def _deuda_en_pantalla(self, e, consulta: str = "") -> tuple[str, str]:
        html = solicitud("GET", f"/clientes{consulta}", cookies=e.cc).texto
        total = re.search(r'id="deuda-total"[^>]*>([^<]+)<', html).group(1).strip()
        cantidad = re.search(r'id="deuda-clientes"[^>]*>\s*([^<]+?)\s*<', html).group(1)
        return total, cantidad

    def test_suma_solo_los_saldos_positivos_de_varios_clientes(self, e):
        _vender(e, e.ana)
        _vender(e, e.ana)  # Ana debe 4,00
        _vender(e, e.beto)  # Beto debe 2,00
        servicio_clientes.crear_cliente("Sin deuda", e.owner.id)

        deuda = servicio_clientes.obtener_deuda_total()

        assert (deuda.total_centavos, deuda.cantidad_clientes) == (600, 2)
        assert self._deuda_en_pantalla(e) == ("$6,00", "2 clientes con deuda")

    def test_un_cobro_reduce_la_deuda_y_un_cliente_saldado_deja_de_contar(self, e):
        _vender(e, e.ana)
        _vender(e, e.beto)
        servicio_cuenta_corriente.registrar_cobro(e.ana.id, 50, e.cajera.id)

        parcial = servicio_clientes.obtener_deuda_total()
        servicio_cuenta_corriente.registrar_cobro(e.beto.id, 200, e.cajera.id)
        saldado = servicio_clientes.obtener_deuda_total()

        assert (parcial.total_centavos, parcial.cantidad_clientes) == (350, 2)
        assert (saldado.total_centavos, saldado.cantidad_clientes) == (150, 1)
        assert self._deuda_en_pantalla(e) == ("$1,50", "1 cliente con deuda")

    def test_sin_deuda_es_cero(self, e):
        assert self._deuda_en_pantalla(e) == ("$0,00", "0 clientes con deuda")

    def test_un_cliente_inactivo_con_deuda_sigue_sumando(self, e):
        _vender(e, e.ana)
        forzar_cliente_inactivo(e.ruta, e.ana.id)

        deuda = servicio_clientes.obtener_deuda_total()

        assert (deuda.total_centavos, deuda.cantidad_clientes) == (200, 1)

    def test_no_depende_de_los_filtros_ni_del_listado_mostrado(self, e):
        _vender(e, e.ana)
        _vender(e, e.beto)

        assert self._deuda_en_pantalla(e, "?q=Ana") == ("$4,00", "2 clientes con deuda")
        assert self._deuda_en_pantalla(e, "?estado=inactivos") == ("$4,00", "2 clientes con deuda")

    def test_se_agrega_en_sql_sin_recorrer_clientes_ni_movimientos(self, e, monkeypatch):
        _vender(e, e.ana)

        def _prohibido(*_argumentos, **_kwargs):
            raise AssertionError("la deuda total no debe cargar clientes ni movimientos para sumar")

        monkeypatch.setattr(repositorio_clientes, "listar_con_saldo", _prohibido)
        monkeypatch.setattr(repositorio_clientes, "listar_movimientos_cuenta", _prohibido)
        monkeypatch.setattr(repositorio_clientes, "listar", _prohibido)

        assert servicio_clientes.obtener_deuda_total().total_centavos == 200
