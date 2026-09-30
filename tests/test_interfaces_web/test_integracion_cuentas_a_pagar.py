"""V1.10-E: integración de cuentas a pagar en la ficha del proveedor, el listado y el detalle de compras.

El saldo total es el real (libro); todo importe por compra o por vencimiento es una estimación FIFO y así se
rotula. El listado de compras sigue simple: solo la fecha de vencimiento, sin estados FIFO."""

import re
from datetime import timedelta

import pytest

from domain.compra import ItemCompra
from services import servicio_compras, servicio_pagos_proveedor
from tests.utilidades_clientes import escribir

from ._asgi_cliente import solicitud
from .test_reporte_deuda_proveedores import HOY, RUTA, _comprar, _forzar_libro_imposible, e  # noqa: F401 (fixture `e`)

FIFO = "Estimado por antigüedad (FIFO)"


def _pagina(e, ruta):
    respuesta = solicitud("GET", ruta, cookies=e.co)
    assert respuesta.status == 200, respuesta.status
    return respuesta.texto


def _texto(html, id_):
    coincidencia = re.search(rf'id="{id_}"[^>]*>(.*?)</', html, re.S)
    assert coincidencia, f"falta el elemento #{id_}"
    return " ".join(coincidencia.group(1).split())


def _tabla(html, id_):
    coincidencia = re.search(rf'<table id="{id_}".*?</table>', html, re.S)
    return coincidencia.group(0) if coincidencia else ""


def _filas(tabla):
    """`{compra_id: (data-estado, fila_html)}` de una tabla con filas `<tr data-compra=...>`."""
    return {
        int(id_): (estado, cuerpo)
        for id_, estado, cuerpo in re.findall(r'<tr data-compra="(\d+)"(?: data-estado="([^"]*)")?[^>]*>(.*?)</tr>', tabla, re.S)
    }


def _celda(fila, columna):
    coincidencia = re.search(rf'data-columna="{columna}"[^>]*>(.*?)</td>', fila, re.S)
    assert coincidencia, f"falta la columna {columna}"
    return " ".join(re.sub(r"<[^>]+>", " ", coincidencia.group(1)).split())


def _pagar(e, proveedor, monto):
    return servicio_pagos_proveedor.registrar_pago(proveedor.id, monto, "TRANSFERENCIA", e.owner.id)


def _ficha(e, proveedor=None):
    return _pagina(e, f"/proveedores/{(proveedor or e.a).id}")


def _contado(e, monto=5000):
    return servicio_compras.registrar_compra(e.a.id, e.owner.id, [ItemCompra(e.p.id, 1, monto)], condicion_pago="CONTADO")


class TestFichaResumen:
    def test_proveedor_sin_deuda(self, e):
        html = _ficha(e)

        assert _texto(html, "cuenta-saldo") == "$0,00"
        assert _texto(html, "cuenta-proximo-vencimiento") == "Sin próximo vencimiento"
        assert 'id="sin-compras-abiertas"' in html
        assert _tabla(html, "tabla-compras-abiertas") == ""

    def test_una_compra_de_cada_estado(self, e):
        _comprar(e, e.a, 10000, dias=-5)
        _comprar(e, e.a, 20000, dias=3)
        _comprar(e, e.a, 30000, dias=30)
        _comprar(e, e.a, 40000)

        html = _ficha(e)

        assert _texto(html, "cuenta-saldo") == "$1.000,00"
        assert _texto(html, "cuenta-vencida") == "$100,00"
        assert _texto(html, "cuenta-proxima") == "$200,00"
        assert _texto(html, "cuenta-vigente") == "$300,00"
        assert _texto(html, "cuenta-sin-vencimiento") == "$400,00"

    def test_el_resumen_rotula_los_importes_derivados_como_fifo(self, e):
        _comprar(e, e.a, 10000, dias=3)

        html = _ficha(e)

        assert FIFO in re.search(r'id="cuenta-a-pagar".*?</section>', html, re.S).group(0)

    def test_el_saldo_del_resumen_coincide_con_el_saldo_existente(self, e):
        _comprar(e, e.a, 10000, dias=3)
        _comprar(e, e.a, 5000)
        _pagar(e, e.a, 2500)

        html = _ficha(e)
        existente = re.search(r'id="saldo-proveedor">.*?</div>', html, re.S).group(0)

        assert _texto(html, "cuenta-saldo") == "$125,00"
        assert "$125,00" in existente

    def test_no_elimina_compras_ni_libro_existentes(self, e):
        _comprar(e, e.a, 10000, dias=3)
        _pagar(e, e.a, 2500)

        html = _ficha(e)

        assert 'id="tabla-compras-proveedor"' in html and 'id="tabla-movimientos-proveedor"' in html

    def test_los_titulos_de_seccion_no_se_repiten(self, e):
        _comprar(e, e.a, 10000, dias=3)

        titulos = re.findall(r"<h2[^>]*>(.*?)</h2>", _ficha(e), re.S)

        assert len(titulos) == len(set(titulos)), titulos

    def test_nunca_habla_de_saldo_de_la_compra(self, e):
        _comprar(e, e.a, 10000, dias=3)

        assert "Saldo de la compra" not in _ficha(e)


class TestProximoVencimientoEnFicha:
    def test_muestra_fecha_e_importe_estimado(self, e):
        _comprar(e, e.a, 10000, dias=-5)
        _comprar(e, e.a, 20000, dias=3)
        _comprar(e, e.a, 30000, dias=30)

        texto = _texto(_ficha(e), "cuenta-proximo-vencimiento")

        assert (HOY + timedelta(days=3)).isoformat() in texto and "$200,00" in texto

    def test_ignora_vencidas_y_sin_vencimiento(self, e):
        _comprar(e, e.a, 10000, dias=-5)
        _comprar(e, e.a, 40000)

        assert _texto(_ficha(e), "cuenta-proximo-vencimiento") == "Sin próximo vencimiento"


class TestComprasAbiertas:
    def test_solo_credito_activas_con_pendiente_en_orden_de_antiguedad(self, e):
        c1 = _comprar(e, e.a, 10000, dias=-5)
        c2 = _comprar(e, e.a, 20000, dias=3)
        parcial = _comprar(e, e.a, 5000, dias=1)
        anulada = _comprar(e, e.a, 7000, dias=2)
        servicio_compras.anular_compra(anulada.id, "ERROR_CARGA", None, e.owner.id)
        contado = _contado(e)
        c3 = _comprar(e, e.a, 30000)
        _pagar(e, e.a, 10000 + 20000 + 1000)  # cubre c1 y c2 enteras y 10,00 de `parcial`

        filas = _filas(_tabla(_ficha(e), "tabla-compras-abiertas"))

        assert list(filas) == [parcial.id, c3.id]                # más antigua primero
        assert contado.id not in filas and anulada.id not in filas
        assert c1.id not in filas and c2.id not in filas         # cubiertas por el pago
        assert _celda(filas[parcial.id][1], "pendiente") == "$40,00"
        assert _celda(filas[c3.id][1], "pendiente") == "$300,00"

    def test_columnas_y_estado(self, e):
        c1 = _comprar(e, e.a, 10000, dias=-5)
        c2 = _comprar(e, e.a, 20000, dias=3)
        c3 = _comprar(e, e.a, 30000, dias=30)
        c4 = _comprar(e, e.a, 40000)

        tabla = _tabla(_ficha(e), "tabla-compras-abiertas")
        filas = _filas(tabla)

        for encabezado in ("Compra #", "Fecha", "Total", "Vencimiento", "Pendiente estimado", "Estado"):
            assert encabezado in tabla
        assert {i: filas[i][0] for i in filas} == {
            c1.id: "VENCIDA", c2.id: "PROXIMA_A_VENCER", c3.id: "PENDIENTE", c4.id: "SIN_VENCIMIENTO",
        }
        assert _celda(filas[c2.id][1], "total") == "$200,00"
        assert _celda(filas[c2.id][1], "vencimiento") == (HOY + timedelta(days=3)).isoformat()
        assert _celda(filas[c4.id][1], "vencimiento") == "Sin vencimiento"
        assert f"#{c1.id}" in _celda(filas[c1.id][1], "compra")

    def test_pago_parcial_baja_el_pendiente_no_el_total(self, e):
        c1 = _comprar(e, e.a, 10000, dias=3)
        _pagar(e, e.a, 4000)

        fila = _filas(_tabla(_ficha(e), "tabla-compras-abiertas"))[c1.id][1]

        assert _celda(fila, "total") == "$100,00" and _celda(fila, "pendiente") == "$60,00"

    def test_pago_total_deja_la_tabla_vacia(self, e):
        _comprar(e, e.a, 10000, dias=3)
        _pagar(e, e.a, 10000)

        html = _ficha(e)

        assert _tabla(html, "tabla-compras-abiertas") == "" and 'id="sin-compras-abiertas"' in html

    def test_pago_que_cruza_compras_deja_solo_la_segunda(self, e):
        c1 = _comprar(e, e.a, 10000, dias=-5)
        c2 = _comprar(e, e.a, 20000, dias=3)
        _pagar(e, e.a, 15000)

        filas = _filas(_tabla(_ficha(e), "tabla-compras-abiertas"))

        assert list(filas) == [c2.id] and _celda(filas[c2.id][1], "pendiente") == "$150,00"
        assert c1.id not in filas

    def test_la_nota_fifo_acompana_a_la_tabla(self, e):
        _comprar(e, e.a, 10000, dias=3)

        seccion = re.search(r'id="compras-abiertas".*?</section>', _ficha(e), re.S).group(0)

        assert FIFO in seccion and 'id="tabla-compras-abiertas"' in seccion


class TestFichaInconsistente:
    def test_no_es_500_no_da_cifras_fifo_y_conserva_el_libro(self, e):
        _comprar(e, e.a, 10000, dias=-5)
        _forzar_libro_imposible(e, e.a)

        html = _ficha(e)

        assert "Datos inconsistentes" in _texto(html, "cuenta-inconsistente")
        for id_ in ("cuenta-vencida", "cuenta-proxima", "cuenta-vigente", "cuenta-sin-vencimiento", "cuenta-proximo-vencimiento"):
            assert f'id="{id_}"' not in html
        assert _tabla(html, "tabla-compras-abiertas") == ""
        assert 'id="tabla-movimientos-proveedor"' in html and 'id="tabla-compras-proveedor"' in html

    def test_saldo_confiable_se_mantiene(self, e):
        _comprar(e, e.a, 10000)
        escribir(
            e.ruta,
            "INSERT INTO movimientos_proveedor (proveedor_id, tipo, monto_centavos, medio_pago, usuario_id)"
            " VALUES (?, 'PAGO', 15000, 'TRANSFERENCIA', ?)",
            (e.a.id, e.owner.id),
        )
        _comprar(e, e.a, 10000)   # saldo final 50,00, pero el pago fue imposible en su punto causal

        html = _ficha(e)

        assert "Datos inconsistentes" in _texto(html, "cuenta-inconsistente")
        assert _texto(html, "cuenta-saldo") == "$50,00"

    def test_saldo_negativo_no_se_muestra_como_cifra(self, e):
        _comprar(e, e.a, 10000)
        _forzar_libro_imposible(e, e.a)

        assert _texto(_ficha(e), "cuenta-saldo") == "—"


class TestListadoDeCompras:
    def test_credito_con_fecha(self, e):
        c = _comprar(e, e.a, 10000, dias=3)

        assert _celda(_filas(_tabla_listado(_pagina(e, "/compras")))[c.id][1], "vence") == (HOY + timedelta(days=3)).isoformat()

    def test_credito_sin_fecha(self, e):
        c = _comprar(e, e.a, 10000)

        assert _celda(_filas(_tabla_listado(_pagina(e, "/compras")))[c.id][1], "vence") == "Sin vencimiento"

    def test_contado_muestra_guion(self, e):
        c = _contado(e)

        assert _celda(_filas(_tabla_listado(_pagina(e, "/compras")))[c.id][1], "vence") == "—"

    def test_encabezado_y_sin_estados_fifo(self, e):
        _comprar(e, e.a, 10000, dias=-5)
        _comprar(e, e.a, 20000, dias=3)
        _pagar(e, e.a, 10000)

        html = _pagina(e, "/compras")

        assert ">Vence<" in html
        for prohibido in ("FIFO", "Pendiente estimado", "VENCIDA", "PAGADA", "PROXIMA_A_VENCER", "Vencida"):
            assert prohibido not in html


def _tabla_listado(html):
    coincidencia = re.search(r"<table.*?</table>", html, re.S)
    assert coincidencia, "el listado no tiene tabla"
    return coincidencia.group(0)


class TestDetalleDeCompra:
    def test_credito_con_pendiente_parcial(self, e):
        c = _comprar(e, e.a, 10000, dias=3)
        _pagar(e, e.a, 4000)

        html = _pagina(e, f"/compras/{c.id}")

        assert _texto(html, "compra-pendiente-estimado") == "$60,00"
        assert "Pendiente estimado" in html and FIFO in html
        assert _texto(html, "compra-vencimiento") == (HOY + timedelta(days=3)).isoformat()

    def test_credito_totalmente_cubierta(self, e):
        c = _comprar(e, e.a, 10000, dias=3)
        _pagar(e, e.a, 10000)

        assert _texto(_pagina(e, f"/compras/{c.id}"), "compra-pendiente-estimado") == "$0,00"

    def test_credito_sin_vencimiento(self, e):
        c = _comprar(e, e.a, 10000)

        html = _pagina(e, f"/compras/{c.id}")

        assert _texto(html, "compra-vencimiento") == "Sin vencimiento"
        assert _texto(html, "compra-pendiente-estimado") == "$100,00"

    def test_credito_anulada_no_muestra_pendiente_como_deuda(self, e):
        c = _comprar(e, e.a, 10000, dias=3)
        servicio_compras.anular_compra(c.id, "ERROR_CARGA", None, e.owner.id)

        html = _pagina(e, f"/compras/{c.id}")

        assert 'id="compra-pendiente-estimado"' not in html
        assert "Pendiente estimado" not in html

    def test_contado_no_muestra_pendiente(self, e):
        c = _contado(e)

        html = _pagina(e, f"/compras/{c.id}")

        assert "Pendiente estimado" not in html and FIFO not in html

    def test_libro_inconsistente_no_da_500_ni_cifra(self, e):
        c = _comprar(e, e.a, 10000, dias=3)
        _forzar_libro_imposible(e, e.a)

        html = _pagina(e, f"/compras/{c.id}")

        assert "Datos inconsistentes" in _texto(html, "compra-pendiente-estimado")


class TestAvisoDeExcluidosEnElReporte:
    def test_filtro_de_situacion_informa_los_inconsistentes_excluidos(self, e):
        _comprar(e, e.a, 10000, dias=-5)
        _forzar_libro_imposible(e, e.a)
        _comprar(e, e.b, 5000, dias=-3)

        html = _pagina(e, f"{RUTA}?situacion=vencida")

        assert "no se pudieron clasificar" in _texto(html, "aviso-excluidos")
        assert "Beta SRL" in html and "Alfa SA</a>" not in html

    def test_sin_filtro_de_situacion_la_inconsistente_aparece_y_no_hay_aviso_de_excluidos(self, e):
        _comprar(e, e.a, 10000, dias=-5)
        _forzar_libro_imposible(e, e.a)

        html = _pagina(e, RUTA)

        assert "Alfa SA</a>" in html and "Datos inconsistentes" in html
        assert 'id="aviso-excluidos"' not in html

    def test_proveedor_especifico_inconsistente_aparece(self, e):
        _comprar(e, e.a, 10000, dias=-5)
        _forzar_libro_imposible(e, e.a)

        assert "Alfa SA</a>" in _pagina(e, f"{RUTA}?proveedor_id={e.a.id}")

    def test_sin_resultados_por_exclusion_tambien_avisa(self, e):
        _comprar(e, e.a, 10000, dias=-5)
        _forzar_libro_imposible(e, e.a)

        html = _pagina(e, f"{RUTA}?proveedor_id={e.a.id}&situacion=vencida")

        assert "Sin deuda a proveedores" in html and 'id="aviso-excluidos"' in html


@pytest.mark.parametrize("ruta", ["/proveedores/1", "/compras/1"])
def test_permisos_sin_cambios(e, ruta):
    _comprar(e, e.a, 10000, dias=3)

    assert solicitud("GET", ruta, cookies=e.cajera).status == 403
    assert solicitud("GET", ruta, cookies=e.co).status == 200
