"""V1.12-E: liquidación por vencimiento. El sistema propone (descuento objetivo acotado por margen mínimo
sobre el costo real de lo que vence), nunca cambia un precio solo; el OWNER confirma y el cambio queda en
el historial de precios y en la auditoría."""

from datetime import date, timedelta

import pytest

from domain.compra import ItemCompra, PartidaIngreso
from domain.liquidacion import ParametrosLiquidacion, proponer_precio
from excepciones import DatosInvalidosError
from services import servicio_compras, servicio_configuracion, servicio_liquidacion, servicio_proveedores, servicio_stock
from tests.utilidades_clientes import consultar, usuario_logueado

from tests.test_interfaces_web._asgi_cliente import solicitud

PARAMETROS = ParametrosLiquidacion(descuento_objetivo=20, margen_minimo=10)


def _dia(dias: int) -> str:
    return (date.today() + timedelta(days=dias)).isoformat()


class TestPropuesta:
    def test_descuento_objetivo_cuando_el_margen_lo_permite(self):
        p = proponer_precio(1000, 500, True, PARAMETROS)
        assert (p.precio_propuesto_centavos, p.precio_piso_centavos, p.aviso) == (800, 550, None)

    def test_el_margen_minimo_acota_el_descuento(self):
        p = proponer_precio(1000, 800, True, PARAMETROS)
        assert (p.precio_propuesto_centavos, p.precio_piso_centavos) == (880, 880)

    def test_sin_propuesta_si_no_conviene_bajar(self):
        p = proponer_precio(1000, 950, True, PARAMETROS)
        assert p.precio_propuesto_centavos is None and "no conviene" in p.aviso

    def test_costo_desconocido_avisa(self):
        p = proponer_precio(1000, None, False, PARAMETROS)
        assert p.precio_propuesto_centavos == 800 and "desconocido" in p.aviso

    def test_costo_vigente_en_vez_del_de_la_partida_avisa(self):
        assert "costo vigente" in proponer_precio(1000, 500, False, PARAMETROS).aviso

    def test_descuento_cero_no_propone(self):
        assert proponer_precio(1000, 500, True, ParametrosLiquidacion(0, 10)).precio_propuesto_centavos is None

    @pytest.mark.parametrize("descuento, margen", [("91", "10"), ("-1", "10"), ("x", "10"), ("20", "501")])
    def test_parametros_invalidos(self, descuento, margen):
        with pytest.raises(DatosInvalidosError):
            ParametrosLiquidacion.desde_texto(descuento, margen)


@pytest.fixture
def e(base_datos_temporal):
    owner, co = usuario_logueado("OWNER", "duenio")
    _, ca = usuario_logueado("CASHIER", "cajera")
    prov = servicio_proveedores.crear_proveedor("Lácteos")
    yogur = servicio_stock.registrar_producto("1", "Yogur", 400, 1000)
    lejano = servicio_stock.registrar_producto("2", "Queso", 400, 1000)
    servicio_compras.registrar_compra(prov.id, owner.id, [
        ItemCompra(yogur.id, 10, 500, (PartidaIngreso(4, _dia(3)), PartidaIngreso(6, _dia(60)))),
        ItemCompra(lejano.id, 5, 500, (PartidaIngreso(5, _dia(60)),)),
    ])
    return type("E", (), {"ruta": base_datos_temporal, "owner": owner, "co": co, "ca": ca, "p": yogur, "lejano": lejano})


def test_candidatos_solo_lo_proximo_a_vencer_con_costo_de_la_partida(e):
    candidatos = servicio_liquidacion.listar_candidatos()
    assert [c.producto.nombre for c in candidatos] == ["Yogur"]
    propuesta = candidatos[0].propuesta
    assert (propuesta.precio_vigente_centavos, propuesta.costo_relevante_centavos, propuesta.costo_de_partida,
            propuesta.precio_propuesto_centavos) == (1000, 500, True, 800)
    assert servicio_stock.obtener_por_id(e.p.id).precio_venta_centavos == 1000  # proponer no cambia nada


def test_aplicar_registra_historial_y_auditoria_y_es_idempotente(e):
    servicio_liquidacion.aplicar_precio(e.p.id, 790, 1000, e.owner.id)  # el OWNER modificó la propuesta
    servicio_liquidacion.aplicar_precio(e.p.id, 790, 1000, e.owner.id)  # reenvío: sin cambios nuevos
    assert servicio_stock.obtener_por_id(e.p.id).precio_venta_centavos == 790
    historial = consultar(e.ruta, "SELECT campo, precio_anterior_centavos, precio_nuevo_centavos, origen FROM historial_precios"
                                  " WHERE producto_id = ? AND campo = 'VENTA'", (e.p.id,))
    assert [tuple(f) for f in historial] == [("VENTA", 1000, 790, "EDICION")]
    auditoria = consultar(e.ruta, "SELECT resumen FROM auditoria WHERE accion = 'PRECIO_LIQUIDACION_APLICADO'")
    assert [f[0] for f in auditoria] == ["Yogur: $10.00 -> $7.90 (liquidación por vencimiento)"]


def test_no_pisa_un_precio_que_cambio_mientras_tanto(e):
    servicio_liquidacion.aplicar_precio(e.p.id, 900, 1000, e.owner.id)
    with pytest.raises(DatosInvalidosError, match="cambió mientras tanto"):
        servicio_liquidacion.aplicar_precio(e.p.id, 800, 1000, e.owner.id)
    with pytest.raises(DatosInvalidosError):
        servicio_liquidacion.aplicar_precio(e.p.id, 0, 900, e.owner.id)


def test_parametros_configurables(e):
    servicio_configuracion.guardar_parametros_liquidacion("30", "5", e.owner.id)
    assert servicio_liquidacion.listar_candidatos()[0].propuesta.precio_propuesto_centavos == 700
    assert consultar(e.ruta, "SELECT COUNT(*) FROM auditoria WHERE resumen LIKE 'Liquidación:%'")[0][0] == 1


def test_web_owner_aplica_y_cajero_no_accede(e):
    html = solicitud("GET", "/precios/liquidacion", cookies=e.co).texto
    assert f'data-producto-id="{e.p.id}"' in html and 'value="8.00"' in html and "(de la partida)" in html
    respuesta = solicitud("POST", f"/precios/liquidacion/{e.p.id}/aplicar", cookies=e.co,
                          formulario={"precio": "7.50", "precio_anterior_centavos": "1000"})
    assert "tipo=success" in (respuesta.header("location") or "")
    assert servicio_stock.obtener_por_id(e.p.id).precio_venta_centavos == 750
    assert solicitud("GET", "/precios/liquidacion", cookies=e.ca).status == 403
    assert solicitud("POST", f"/precios/liquidacion/{e.p.id}/aplicar", cookies=e.ca,
                     formulario={"precio": "1", "precio_anterior_centavos": "750"}).status == 403
