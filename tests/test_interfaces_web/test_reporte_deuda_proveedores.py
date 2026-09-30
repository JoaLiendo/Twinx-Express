"""V1.10-D: pantalla `/reportes/deuda-proveedores` y su exportación CSV -- OWNER únicamente, mismos filtros y
mismo servicio en pantalla y CSV, protección contra inyección de fórmulas y libro inconsistente sin 500 ni
cifras inventadas. Sin migración."""

import csv
import io
from datetime import date, timedelta

import pytest

from domain.compra import ItemCompra
from services import servicio_compras, servicio_pagos_proveedor, servicio_proveedores, servicio_stock
from services.servicio_exportacion_csv import COLUMNAS_DEUDA_PROVEEDORES
from tests.utilidades_clientes import escribir, usuario_logueado

from ._asgi_cliente import solicitud

RUTA = "/reportes/deuda-proveedores"
EXPORTAR = RUTA + "/exportar"
HOY = date.today()


def _filas(respuesta):
    assert respuesta.status == 200, respuesta.status
    return list(csv.DictReader(io.StringIO(respuesta.cuerpo.decode("utf-8-sig"), newline="")))


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies = usuario_logueado("OWNER", "duenio")
    _, cajera = usuario_logueado("CASHIER", "cajera")
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 300, stock_actual=10)
    return type("E", (), {
        "ruta": base_datos_temporal, "owner": owner, "co": cookies, "cajera": cajera, "p": producto,
        "a": servicio_proveedores.crear_proveedor("Alfa SA"), "b": servicio_proveedores.crear_proveedor("Beta SRL"),
    })


def _comprar(e, proveedor, monto, dias=None):
    vencimiento = (HOY + timedelta(days=dias)).isoformat() if dias is not None and dias >= 0 else None
    compra = servicio_compras.registrar_compra(
        proveedor.id, e.owner.id, [ItemCompra(e.p.id, 1, monto)], condicion_pago="CREDITO", fecha_vencimiento=vencimiento
    )
    if dias is not None and dias < 0:
        escribir(e.ruta, "UPDATE compras SET fecha = ? WHERE id = ?", ((HOY - timedelta(days=60)).isoformat() + " 10:00:00", compra.id))
        escribir(e.ruta, "UPDATE compras SET fecha_vencimiento = ? WHERE id = ?", ((HOY + timedelta(days=dias)).isoformat(), compra.id))
    return compra


def _datos(e):
    _comprar(e, e.a, 10000, dias=-5)   # A: vencida 100,00
    _comprar(e, e.b, 25000, dias=3)    # B: próxima 250,00
    _comprar(e, e.b, 5000)             # B: sin vencimiento 50,00


def _forzar_libro_imposible(e, proveedor):
    escribir(
        e.ruta,
        "INSERT INTO movimientos_proveedor (proveedor_id, tipo, monto_centavos, medio_pago, usuario_id)"
        " VALUES (?, 'PAGO', 99999999, 'TRANSFERENCIA', ?)",
        (proveedor.id, e.owner.id),
    )


class TestPermisos:
    @pytest.mark.parametrize("ruta", [RUTA, EXPORTAR])
    def test_owner_accede(self, e, ruta):
        assert solicitud("GET", ruta, cookies=e.co).status == 200

    @pytest.mark.parametrize("ruta", [RUTA, EXPORTAR])
    def test_cashier_recibe_403(self, e, ruta):
        assert solicitud("GET", ruta, cookies=e.cajera).status == 403

    @pytest.mark.parametrize("ruta", [RUTA, EXPORTAR])
    def test_sin_sesion_redirige_a_login(self, e, ruta):
        respuesta = solicitud("GET", ruta)

        assert respuesta.status == 303
        assert respuesta.header("location").startswith("/login")


class TestPantalla:
    def test_renderiza_la_tabla_con_sus_columnas_y_totales(self, e):
        _datos(e)

        html = solicitud("GET", RUTA, cookies=e.co).texto

        for encabezado in ("Proveedor", "Saldo total", "Vencida", "Próxima", "Vigente", "Sin vencimiento", "Última compra", "Último pago"):
            assert encabezado in html
        assert "Alfa SA" in html and "Beta SRL" in html
        assert ('id="total-saldo">$400,00') in html  # 100 + 250 + 50
        assert 'id="total-saldo"' in html

    def test_nota_fifo_visible(self, e):
        _datos(e)

        assert "Estimado por antigüedad (FIFO)" in solicitud("GET", RUTA, cookies=e.co).texto

    def test_filtros_se_preservan_en_el_formulario(self, e):
        _datos(e)

        html = solicitud("GET", f"{RUTA}?proveedor_id={e.b.id}&situacion=proxima", cookies=e.co).texto

        assert f'<option value="{e.b.id}" selected' in html
        assert '<option value="proxima" selected' in html
        assert "Alfa SA</td>" not in html and "Beta SRL" in html

    def test_el_link_de_exportar_conserva_los_filtros(self, e):
        _datos(e)

        html = solicitud("GET", f"{RUTA}?proveedor_id={e.b.id}&situacion=proxima", cookies=e.co).texto

        assert f'href="{EXPORTAR}?proveedor_id={e.b.id}&amp;situacion=proxima"' in html

    def test_mensaje_sin_resultados(self, e):
        _datos(e)

        html = solicitud("GET", f"{RUTA}?proveedor_id={e.a.id}&situacion=proxima", cookies=e.co).texto

        assert "Sin deuda a proveedores" in html
        assert 'id="tabla-deuda-proveedores"' not in html

    @pytest.mark.parametrize("consulta", ["situacion=cualquiera", "proveedor_id=abc"])
    def test_filtro_invalido_redirige_con_error_como_el_resto_de_reportes(self, e, consulta):
        respuesta = solicitud("GET", f"{RUTA}?{consulta}", cookies=e.co)

        assert respuesta.status == 303
        assert "tipo=error" in respuesta.header("location")

    def test_la_navegacion_de_reportes_incluye_el_reporte(self, e):
        assert f'href="{RUTA}"' in solicitud("GET", "/reportes/caja", cookies=e.co).texto

    def test_libro_inconsistente_no_da_500_y_se_marca(self, e):
        _datos(e)
        _forzar_libro_imposible(e, e.a)

        respuesta = solicitud("GET", RUTA, cookies=e.co)

        assert respuesta.status == 200
        assert "Datos inconsistentes" in respuesta.texto
        assert "Beta SRL" in respuesta.texto
        assert 'id="total-saldo"' in respuesta.texto


class TestCsv:
    def test_cabeceras_bom_y_columnas(self, e):
        _datos(e)

        respuesta = solicitud("GET", EXPORTAR, cookies=e.co)

        assert respuesta.status == 200
        assert respuesta.header("content-type").startswith("text/csv")
        assert respuesta.header("content-disposition") == f'attachment; filename="deuda_proveedores_{HOY.isoformat()}.csv"'
        assert respuesta.cuerpo.startswith(b"\xef\xbb\xbf")
        assert respuesta.cuerpo.decode("utf-8-sig").splitlines()[0] == ",".join(COLUMNAS_DEUDA_PROVEEDORES)

    def test_columnas_minimas(self):
        assert COLUMNAS_DEUDA_PROVEEDORES == (
            "proveedor_id", "proveedor", "saldo", "vencida", "proxima", "vigente", "sin_vencimiento",
            "ultima_compra", "ultimo_pago", "criterio",
        )

    def test_dinero_fechas_y_criterio(self, e):
        _datos(e)
        servicio_pagos_proveedor.registrar_pago(e.b.id, 5000, "TRANSFERENCIA", e.owner.id)

        filas = {f["proveedor"]: f for f in _filas(solicitud("GET", EXPORTAR, cookies=e.co))}

        assert filas["Alfa SA"]["proveedor_id"] == str(e.a.id)
        assert filas["Alfa SA"]["saldo"] == "100.00" and filas["Alfa SA"]["vencida"] == "100.00"
        assert filas["Beta SRL"]["saldo"] == "250.00"  # el pago de 50,00 cubre lo más antiguo (la próxima)
        assert filas["Beta SRL"]["proxima"] == "200.00" and filas["Beta SRL"]["sin_vencimiento"] == "50.00"
        assert filas["Beta SRL"]["ultimo_pago"] == HOY.isoformat()
        assert filas["Alfa SA"]["ultimo_pago"] == ""
        assert filas["Alfa SA"]["ultima_compra"] == (HOY - timedelta(days=60)).isoformat()
        assert filas["Alfa SA"]["criterio"] == "Estimado por antigüedad (FIFO)"

    def test_mismos_filtros_que_la_pantalla(self, e):
        _datos(e)

        assert [f["proveedor"] for f in _filas(solicitud("GET", f"{EXPORTAR}?situacion=vencida", cookies=e.co))] == ["Alfa SA"]
        assert [f["proveedor"] for f in _filas(solicitud("GET", f"{EXPORTAR}?proveedor_id={e.b.id}", cookies=e.co))] == ["Beta SRL"]
        assert _filas(solicitud("GET", f"{EXPORTAR}?proveedor_id={e.a.id}&situacion=proxima", cookies=e.co)) == []

    def test_filtro_invalido_no_exporta(self, e):
        respuesta = solicitud("GET", f"{EXPORTAR}?situacion=x", cookies=e.co)

        assert respuesta.status == 303
        assert "tipo=error" in respuesta.header("location")

    def test_fila_inconsistente_no_fabrica_valores(self, e):
        _datos(e)
        _forzar_libro_imposible(e, e.a)

        filas = {f["proveedor"]: f for f in _filas(solicitud("GET", EXPORTAR, cookies=e.co))}
        fila = filas["Alfa SA"]

        assert fila["criterio"] == "Datos inconsistentes"
        assert (fila["saldo"], fila["vencida"], fila["proxima"], fila["vigente"], fila["sin_vencimiento"]) == ("", "", "", "", "")
        assert filas["Beta SRL"]["criterio"] == "Estimado por antigüedad (FIFO)"

    @pytest.mark.parametrize("prefijo", ["=", "+", "-", "@"])
    def test_nombre_de_proveedor_que_parece_formula_se_neutraliza(self, e, prefijo):
        peligroso = servicio_proveedores.crear_proveedor(f"{prefijo}CMD|' /C calc'!A0")
        _comprar(e, peligroso, 1000)

        filas = _filas(solicitud("GET", EXPORTAR, cookies=e.co))
        nombres = [f["proveedor"] for f in filas]

        assert f"'{prefijo}CMD|' /C calc'!A0" in nombres
        assert not any(n.startswith(prefijo) for n in nombres)
