"""V1.16-H: otros impuestos nacionales indirectos en la web (POS, formularios del dueño, documentos y
permisos). Ningún POST manipulado puede saltar la validación ni cambiar la clasificación sin ser OWNER."""

import pytest

from db.repositorios import fiscal as repositorio_fiscal
from domain.fiscal.otros_impuestos import MENSAJE_REQUIERE_TRATAMIENTO, NO_APLICA, REQUIERE_TRATAMIENTO, SIN_CLASIFICAR
from services import servicio_stock
from services.fiscal import credenciales, servicio_facturacion, servicio_parametros
from tests import certificados_prueba as cp
from tests import utilidades_fiscales as uf
from tests.utilidades_clientes import usuario_logueado

from ._asgi_cliente import solicitud

pytestmark = pytest.mark.usefixtures("caja_abierta")

_AYUDA = "Si el producto está alcanzado por impuestos nacionales indirectos adicionales al IVA y Twinx todavía no los calcula"


@pytest.fixture
def owner():
    return usuario_logueado("OWNER", "duenio")


@pytest.fixture
def cashier():
    return usuario_logueado("CASHIER", "cajera")


@pytest.fixture
def fiscal(arca_falso, owner):
    uf.guardar_perfil_validado()
    servicio_parametros.refrescar(arca_falso)
    credenciales.importar("HOMOLOGACION", cp.pem(cp.certificado()), cp.clave_pem(), None, uf.CUIT_EMISOR, None)
    arca_falso.llamadas.clear()
    return arca_falso


def _formulario_producto(**extra) -> dict:
    return {
        "codigo_barras": "7790000000777", "nombre": "Cerveza H", "precio_costo": "1.00", "precio_venta": "2.00",
        "stock_actual": "5", "stock_minimo": "1", "categoria_id": "", "unidad_medida": "UNIDAD", **extra,
    }


class TestPOS:
    @pytest.mark.parametrize("clasificacion, texto", [
        (SIN_CLASIFICAR, "Clasificá los otros impuestos nacionales indirectos"),
        (REQUIERE_TRATAMIENTO, MENSAJE_REQUIERE_TRATAMIENTO),
    ])
    def test_post_directo_no_salta_la_validacion(self, fiscal, cashier, clasificacion, texto):
        """Invariante 12: el endpoint del POS (y su POST manipulado) bloquea antes de ARCA."""
        venta = uf.vender([(uf.producto(12100, uf.IVA_21, otros_impuestos=clasificacion), 1)])
        for ruta in (f"/api/fiscal/ventas/{venta.id}/fiscalizar", f"/api/fiscal/ventas/{venta.id}/fiscalizar"):
            respuesta = solicitud("POST", ruta, cookies=cashier[1])
            assert respuesta.status == 422
            datos = respuesta.json()
            assert datos["codigo"] == "FISCALIZACION_BLOQUEADA" and any(texto in m for m in datos["motivos"])
        detalle = solicitud("POST", f"/ventas/{venta.id}/fiscalizar", cookies=cashier[1])
        assert detalle.status in (303, 422)
        assert fiscal.llamadas == [] and fiscal.autorizaciones == 0
        assert servicio_facturacion.comprobantes_de_venta(venta.id) == []


class TestDocumentos:
    def test_a4_80mm_y_pdf_muestran_iva_contenido_y_otros_impuestos(self, fiscal, cashier, owner):
        """Invariantes 10 y 11."""
        comprobante = servicio_facturacion.fiscalizar_venta(uf.vender([(uf.producto(12100, uf.IVA_21), 1)]).id, owner[0].id)
        for sufijo in ("", "/ticket"):
            html = solicitud("GET", f"/fiscal/comprobantes/{comprobante.id}{sufijo}", cookies=cashier[1]).texto
            assert "IVA Contenido: $ 21,00" in html
            assert "Otros Impuestos Nacionales Indirectos: $ 0,00" in html
        pdf = solicitud("GET", f"/fiscal/comprobantes/{comprobante.id}/pdf", cookies=cashier[1]).cuerpo
        assert b"IVA Contenido: $ 21,00" in pdf and b"Otros Impuestos Nacionales Indirectos: $ 0,00" in pdf


class TestPanelDeClasificacion:
    def test_owner_clasifica_iva_y_otros_juntos(self, owner):
        producto = uf.producto(otros_impuestos=SIN_CLASIFICAR)
        html = solicitud("GET", "/fiscal/productos", cookies=owner[1]).texto
        assert f'name="otros_{producto.id}"' in html and _AYUDA in html
        solicitud("POST", "/fiscal/productos", cookies=owner[1],
                  formulario={f"iva_{producto.id}": "GRAVADO:5", f"otros_{producto.id}": NO_APLICA})
        assert repositorio_fiscal.obtener_tratamiento(producto.id) == uf.IVA_21
        assert repositorio_fiscal.obtener_otros_impuestos(producto.id) == NO_APLICA

    def test_valor_manipulado_no_guarda_nada(self, owner):
        producto = uf.producto(otros_impuestos=SIN_CLASIFICAR)
        respuesta = solicitud("POST", "/fiscal/productos", cookies=owner[1],
                              formulario={f"iva_{producto.id}": "GRAVADO:5", f"otros_{producto.id}": "CERO"})
        assert respuesta.status >= 300
        assert repositorio_fiscal.obtener_tratamiento(producto.id) is None
        assert repositorio_fiscal.obtener_otros_impuestos(producto.id) == SIN_CLASIFICAR

    def test_panel_fiscal_cuenta_pendientes(self, fiscal, owner):
        uf.producto(otros_impuestos=SIN_CLASIFICAR)
        uf.producto(otros_impuestos=REQUIERE_TRATAMIENTO)
        html = solicitud("GET", "/fiscal", cookies=owner[1]).texto
        assert "1 sin clasificar · 1 con tratamiento especial" in html


class TestFormularioDeProducto:
    def test_alta_con_clasificacion(self, owner):
        assert _AYUDA in solicitud("GET", "/productos/nuevo", cookies=owner[1]).texto
        solicitud("POST", "/productos/nuevo", cookies=owner[1], formulario=_formulario_producto(otros_impuestos=REQUIERE_TRATAMIENTO))
        producto = servicio_stock.buscar_por_codigo_barras("7790000000777")
        assert repositorio_fiscal.obtener_otros_impuestos(producto.id) == REQUIERE_TRATAMIENTO

    def test_alta_sin_elegir_queda_sin_clasificar(self, owner):
        solicitud("POST", "/productos/nuevo", cookies=owner[1], formulario=_formulario_producto())
        producto = servicio_stock.buscar_por_codigo_barras("7790000000777")
        assert repositorio_fiscal.obtener_otros_impuestos(producto.id) == SIN_CLASIFICAR

    def test_alta_con_valor_manipulado_no_crea_el_producto(self, owner):
        respuesta = solicitud("POST", "/productos/nuevo", cookies=owner[1], formulario=_formulario_producto(otros_impuestos="CERO"))
        assert respuesta.status >= 300
        assert servicio_stock.buscar_por_codigo_barras("7790000000777") is None

    def test_edicion_muestra_y_cambia_la_clasificacion(self, owner):
        producto = uf.producto(otros_impuestos=REQUIERE_TRATAMIENTO)
        html = solicitud("GET", f"/productos/{producto.id}/editar", cookies=owner[1]).texto
        assert '<option value="REQUIERE_TRATAMIENTO" selected>' in html
        formulario = _formulario_producto(codigo_barras=producto.codigo_barras, nombre=producto.nombre)
        solicitud("POST", f"/productos/{producto.id}/editar", cookies=owner[1], formulario=formulario)
        assert repositorio_fiscal.obtener_otros_impuestos(producto.id) == REQUIERE_TRATAMIENTO  # sin el campo: intacta
        solicitud("POST", f"/productos/{producto.id}/editar", cookies=owner[1], formulario={**formulario, "otros_impuestos": NO_APLICA})
        assert repositorio_fiscal.obtener_otros_impuestos(producto.id) == NO_APLICA


class TestPermisos:
    def test_cashier_no_cambia_la_clasificacion_por_ninguna_via(self, owner, cashier):
        """Invariante 8, también por POST manipulado."""
        producto = uf.producto(otros_impuestos=REQUIERE_TRATAMIENTO)
        intentos = [
            ("/fiscal/productos", {f"otros_{producto.id}": NO_APLICA}),
            (f"/productos/{producto.id}/editar", _formulario_producto(codigo_barras=producto.codigo_barras, otros_impuestos=NO_APLICA)),
            ("/productos/nuevo", _formulario_producto(otros_impuestos=NO_APLICA)),
        ]
        for ruta, formulario in intentos:
            assert solicitud("POST", ruta, cookies=cashier[1], formulario=formulario).status == 403, ruta
        assert repositorio_fiscal.obtener_otros_impuestos(producto.id) == REQUIERE_TRATAMIENTO
        assert servicio_stock.buscar_por_codigo_barras("7790000000777") is None
