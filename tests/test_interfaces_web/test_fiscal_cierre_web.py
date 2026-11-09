"""Cierre operativo, bloque E (contra el ARCA falso, nunca el real): estado de preparación (E2), borrador sin
validez fiscal (E3) y listado informativo de autorizados que no es el Libro IVA (E6)."""

import pytest

from services import servicio_ventas
from services.fiscal import credenciales, servicio_facturacion, servicio_panel, servicio_parametros, servicio_preparacion
from tests import certificados_prueba as cp
from tests import utilidades_fiscales as uf
from tests.utilidades_clientes import usuario_logueado

from ._asgi_cliente import solicitud

pytestmark = pytest.mark.usefixtures("caja_abierta")


@pytest.fixture
def owner():
    return usuario_logueado("OWNER", "duenio")


@pytest.fixture
def fiscal(arca_falso, owner):
    uf.guardar_perfil_validado()
    servicio_parametros.refrescar(arca_falso)
    credenciales.importar("HOMOLOGACION", cp.pem(cp.certificado()), cp.clave_pem(), None, uf.CUIT_EMISOR, None)
    return arca_falso


def _venta(precio=12100):
    return uf.vender([(uf.producto(precio, uf.IVA_21), 1)])


class TestPreparacion:
    def test_sin_perfil_todo_pendiente_y_sin_consultar_arca(self, owner, arca_falso):
        html = solicitud("GET", "/fiscal", cookies=owner[1]).texto
        assert 'id="fiscal-preparacion"' in html and 'data-emite-con-validez="false"' in html
        assert "faltan pasos de configuración" in html and "comprobantes internos" in html
        assert 'data-paso="perfil" data-estado="PENDIENTE"' in html
        assert 'data-paso="certificado" data-estado="PENDIENTE"' in html
        assert 'data-paso="homologacion" data-estado="EXTERNO"' in html
        assert arca_falso.autorizaciones == 0

    def test_con_todo_cargado_en_homologacion_distingue_pruebas_de_validez(self, fiscal, owner):
        preparacion = servicio_preparacion.preparacion(servicio_panel.estado())
        estados = {p.clave: p.estado for p in preparacion.pasos}
        assert estados["perfil"] == "OK" and estados["certificado"] == "OK" and estados["punto_venta"] == "OK"
        assert estados["tablas"] == "OK" and estados["homologacion"] == "EXTERNO" and estados["produccion"] == "EXTERNO"
        assert estados["conectividad"] == "ADVERTENCIA"
        assert preparacion.solo_pruebas and not preparacion.puede_emitir_con_validez
        assert "NO tienen validez fiscal" in preparacion.situacion
        responsables = {p.clave: p.responsable for p in preparacion.pasos}
        assert responsables["homologacion"] == "ARCA / contador" and responsables["produccion"] == "Técnico"
        html = solicitud("GET", "/fiscal", cookies=owner[1]).texto
        assert "HOMOLOGACIÓN (pruebas)" in html and 'href="/fiscal/listado"' in html

    def test_un_punto_de_venta_cargado_pero_no_validado_no_cuenta_como_listo(self, owner, arca_falso):
        uf.guardar_perfil_validado(punto_venta=5)
        from db.repositorios import fiscal as repositorio_fiscal
        from db.conexion import obtener_conexion

        with obtener_conexion() as conexion:
            conexion.execute("UPDATE perfil_fiscal SET punto_venta_cae_validado = NULL")
        preparacion = servicio_preparacion.preparacion(servicio_panel.estado())
        paso = next(p for p in preparacion.pasos if p.clave == "punto_venta")
        assert paso.estado == "PENDIENTE" and "todavía no lo confirmó" in paso.detalle

    def test_el_cajero_no_ve_el_panel(self, owner):
        _, cajero = usuario_logueado("CASHIER", "cajera")
        assert solicitud("GET", "/fiscal", cookies=cajero).status == 403
        assert solicitud("GET", "/fiscal/listado", cookies=cajero).status == 403


class TestBorrador:
    def test_el_borrador_no_crea_comprobante_ni_numera_ni_toca_arca(self, fiscal, owner):
        venta = _venta()
        detalle = solicitud("GET", f"/ventas/{venta.id}", cookies=owner[1]).texto
        assert 'id="venta-borrador-fiscal"' in detalle
        html = solicitud("GET", f"/fiscal/ventas/{venta.id}/borrador", cookies=owner[1]).texto
        assert "BORRADOR — SIN VALIDEZ FISCAL" in html and 'data-estado="BORRADOR"' in html
        assert "00003-BORRADOR" in html and "no solicitado" in html
        assert "fe/qr" not in html and "comprobante-qr" not in html and "Descargar PDF" not in html
        assert "Factura B".upper() in html.upper() and "$ 121,00" in html
        assert servicio_facturacion.comprobantes_de_venta(venta.id) == []
        assert fiscal.autorizaciones == 0
        # Después del borrador, la emisión real sigue arrancando en el número 1.
        comprobante = servicio_facturacion.fiscalizar_venta(venta.id, owner[0].id)
        assert comprobante.numero == 1 and comprobante.autorizado
        assert 'id="venta-borrador-fiscal"' not in solicitud("GET", f"/ventas/{venta.id}", cookies=owner[1]).texto

    def test_sin_perfil_o_con_producto_sin_iva_explica_y_vuelve_a_la_venta(self, owner, arca_falso):
        venta = _venta()
        respuesta = solicitud("GET", f"/fiscal/ventas/{venta.id}/borrador", cookies=owner[1])
        assert respuesta.status == 303 and "previsualizar" in respuesta.header("location")
        uf.guardar_perfil_validado()
        servicio_parametros.refrescar(arca_falso)
        sin_iva = uf.vender([(uf.producto(500, None), 1)])
        respuesta = solicitud("GET", f"/fiscal/ventas/{sin_iva.id}/borrador", cookies=owner[1])
        assert respuesta.status == 303 and "tipo=warning" in respuesta.header("location")

    def test_el_cajero_no_accede_al_borrador(self, fiscal, owner):
        _, cajero = usuario_logueado("CASHIER", "cajera")
        assert solicitud("GET", f"/fiscal/ventas/{_venta().id}/borrador", cookies=cajero).status == 403


class TestListado:
    def test_lista_solo_autorizados_con_totales_y_aviso_de_no_libro_iva(self, fiscal, owner):
        a = servicio_facturacion.fiscalizar_venta(_venta().id, owner[0].id)
        b = servicio_facturacion.fiscalizar_venta(_venta(24200).id, owner[0].id)
        fiscal.caido = True
        pendiente = _venta()
        with pytest.raises(Exception):
            servicio_facturacion.fiscalizar_venta(pendiente.id, owner[0].id)
        fiscal.caido = False
        desde = a.fecha_comprobante
        html = solicitud("GET", f"/fiscal/listado?fecha_desde={desde}&fecha_hasta={desde}", cookies=owner[1]).texto
        assert 'id="aviso-no-libro-iva"' in html and "No es el Libro IVA Ventas" in html
        assert 'id="fiscal-total-cantidad">2' in html
        assert f'data-comprobante="{a.id}"' in html and f'data-comprobante="{b.id}"' in html
        assert "$ 363,00" in html or "$363,00" in html
        assert "homologación" in html
        csv = solicitud("GET", f"/fiscal/listado/exportar?fecha_desde={desde}&fecha_hasta={desde}", cookies=owner[1])
        assert csv.status == 200 and "codigo_autorizacion" in csv.texto and a.codigo_autorizacion in csv.texto
        assert csv.texto.count("\n") == 3  # encabezado + 2 autorizados; el pendiente no figura
        totales = servicio_facturacion.totales_de(servicio_facturacion.listar_autorizados(desde, "2999-01-01"))
        assert totales.cantidad == 2 and totales.total_centavos == 36300 and totales.iva_centavos == 6300

    def test_sin_comprobantes_la_pantalla_no_falla(self, owner):
        assert "Sin comprobantes autorizados" in solicitud("GET", "/fiscal/listado", cookies=owner[1]).texto
