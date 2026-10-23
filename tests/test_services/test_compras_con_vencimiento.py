"""V1.12-B: ingreso de mercadería con vencimiento de producto opcional, varias fechas por línea,
idempotencia, anulación coherente y formulario web."""

import pytest

from domain.compra import ItemCompra, PartidaIngreso, parsear_vencimientos_de_producto
from excepciones import ClaveIdempotenciaReutilizadaError, DatosInvalidosError
from services import servicio_compras, servicio_partidas, servicio_proveedores, servicio_stock
from tests.utilidades_clientes import consultar, contar, crear_owner, usuario_logueado

from tests.test_interfaces_web._asgi_cliente import solicitud

FUTURO_1, FUTURO_2 = "2099-11-20", "2099-12-15"


@pytest.fixture
def e(base_datos_temporal):
    owner = crear_owner()
    return type("E", (), {
        "ruta": base_datos_temporal, "owner": owner,
        "prov": servicio_proveedores.crear_proveedor("Lácteos SA"),
        "p": servicio_stock.registrar_producto("1", "Yogur", 100, 300),
    })


def _partidas(producto):
    return [(p.cantidad_original, p.fecha_vencimiento, p.costo_unitario_centavos)
            for p in servicio_partidas.listar_partidas(producto.id)]


def _comprar(e, item, **kwargs):
    return servicio_compras.registrar_compra(e.prov.id, e.owner.id, [item], **kwargs)


class TestParseo:
    @pytest.mark.parametrize(
        "texto, esperado",
        [
            ("", ()),
            (FUTURO_1, (PartidaIngreso(10, FUTURO_1),)),
            (f"6@{FUTURO_1}; 4@{FUTURO_2}", (PartidaIngreso(6, FUTURO_1), PartidaIngreso(4, FUTURO_2))),
            (f"6@{FUTURO_1}", (PartidaIngreso(6, FUTURO_1), PartidaIngreso(4, None))),  # el resto, sin fecha
        ],
    )
    def test_formatos(self, texto, esperado):
        assert parsear_vencimientos_de_producto(texto, 10) == esperado

    @pytest.mark.parametrize("texto", ["2099-02-30", "20/11/2099", f"x@{FUTURO_1}", f"11@{FUTURO_1}", f"6@{FUTURO_1};6@{FUTURO_2}"])
    def test_invalidos(self, texto):
        with pytest.raises(DatosInvalidosError):
            parsear_vencimientos_de_producto(texto, 10)

    def test_item_valida_suma_y_repetidos(self):
        with pytest.raises(DatosInvalidosError, match="no suman"):
            ItemCompra(1, 10, 100, (PartidaIngreso(3, FUTURO_1),))
        with pytest.raises(DatosInvalidosError, match="dos veces"):
            ItemCompra(1, 10, 100, (PartidaIngreso(5, FUTURO_1), PartidaIngreso(5, FUTURO_1)))


def test_sin_vencimiento_una_sola_partida_sin_fecha(e):
    _comprar(e, ItemCompra(e.p.id, 10, 120))
    assert _partidas(e.p) == [(10, None, 120)]


def test_varias_fechas_en_la_misma_linea_sin_duplicar_el_producto(e):
    compra = _comprar(e, ItemCompra(e.p.id, 10, 120, (PartidaIngreso(6, FUTURO_1), PartidaIngreso(4, FUTURO_2))),
                      condicion_pago="CREDITO")
    assert contar(e.ruta, "detalle_compra") == 1
    assert _partidas(e.p) == [(6, FUTURO_1, 120), (4, FUTURO_2, 120)]
    assert servicio_partidas.vencimientos_de_compra(compra.id) == {e.p.id: [(4, FUTURO_2), (6, FUTURO_1)]}
    assert contar(e.ruta, "movimientos_proveedor") == 1  # el cargo del crédito, igual que siempre


def test_vencimiento_anterior_a_la_compra_se_rechaza_sin_registrar_nada(e):
    with pytest.raises(DatosInvalidosError, match="anterior a la fecha de la compra"):
        _comprar(e, ItemCompra(e.p.id, 5, 120, (PartidaIngreso(5, "2001-01-01"),)))
    assert contar(e.ruta, "compras") == 0 and contar(e.ruta, "partidas_stock") == 0
    assert servicio_stock.obtener_por_id(e.p.id).stock_actual == 0


def test_reintento_idempotente_compara_tambien_los_vencimientos(e):
    item = ItemCompra(e.p.id, 10, 120, (PartidaIngreso(6, FUTURO_1), PartidaIngreso(4, FUTURO_2)))
    primera = _comprar(e, item, clave_idempotencia="k-c")
    assert _comprar(e, item, clave_idempotencia="k-c").id == primera.id
    assert contar(e.ruta, "partidas_stock") == 2
    with pytest.raises(ClaveIdempotenciaReutilizadaError):
        _comprar(e, ItemCompra(e.p.id, 10, 120, (PartidaIngreso(10, FUTURO_1),)), clave_idempotencia="k-c")


def test_anulacion_retira_las_partidas_de_la_compra(e):
    compra = _comprar(e, ItemCompra(e.p.id, 10, 120, (PartidaIngreso(6, FUTURO_1), PartidaIngreso(4, FUTURO_2))))
    servicio_compras.anular_compra(compra.id, "ERROR_CARGA", None, e.owner.id)
    assert [p.cantidad_disponible for p in servicio_partidas.listar_partidas(e.p.id)] == [0, 0]
    assert servicio_stock.obtener_por_id(e.p.id).stock_actual == 0
    assert servicio_partidas.productos_no_conciliados() == []


def test_formulario_web_simple_y_con_varias_fechas(e):
    _, cookies = usuario_logueado("OWNER", "duenio2")
    otro = servicio_stock.registrar_producto("2", "Leche", 100, 250)
    respuesta = solicitud("POST", "/compras/nueva", cookies=cookies, formulario={
        "proveedor_id": str(e.prov.id), "producto_id": [str(e.p.id), str(otro.id)], "cantidad": ["10", "3"],
        "costo_unitario": ["1.20", "1.00"], "vencimiento_producto": [f"6@{FUTURO_1};4@{FUTURO_2}", FUTURO_1],
        "clave_idempotencia": "k-web",
    })
    assert "tipo=success" in (respuesta.header("location") or "")
    assert _partidas(e.p) == [(6, FUTURO_1, 120), (4, FUTURO_2, 120)]
    assert _partidas(otro) == [(3, FUTURO_1, 100)]
    compra_id = consultar(e.ruta, "SELECT id FROM compras")[0][0]
    detalle = solicitud("GET", f"/compras/{compra_id}", cookies=cookies).texto
    assert "Vence:" in detalle and "20/11/2099 (6)" in detalle


def test_formulario_web_sin_el_campo_nuevo_sigue_funcionando(e):
    _, cookies = usuario_logueado("OWNER", "duenio2")
    solicitud("POST", "/compras/nueva", cookies=cookies, formulario={
        "proveedor_id": str(e.prov.id), "producto_id": str(e.p.id), "cantidad": "2", "costo_unitario": "1",
    })
    assert _partidas(e.p) == [(2, None, 100)]


def test_formulario_web_con_vencimientos_desalineados_no_registra(e):
    _, cookies = usuario_logueado("OWNER", "duenio2")
    respuesta = solicitud("POST", "/compras/nueva", cookies=cookies, formulario={
        "proveedor_id": str(e.prov.id), "producto_id": str(e.p.id), "cantidad": "2", "costo_unitario": "1",
        "vencimiento_producto": ["", ""],
    })
    assert "tipo=error" in (respuesta.header("location") or "")
    assert contar(e.ruta, "compras") == 0
