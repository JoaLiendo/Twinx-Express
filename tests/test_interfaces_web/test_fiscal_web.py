"""V1.16-E: integración web de la facturación (POS, documentos, panel y permisos) contra el ARCA falso."""

import zipfile

import pytest
from cryptography.hazmat.primitives import serialization

from db.repositorios import fiscal as repositorio_fiscal
from services import servicio_backup, servicio_ventas
from services.control_escrituras import ControlEscrituras
from services.fiscal import credenciales, servicio_facturacion, servicio_parametros
from tests import certificados_prueba as cp
from tests import utilidades_fiscales as uf
from tests.utilidades_clientes import usuario_logueado

from ._asgi_cliente import solicitud

pytestmark = pytest.mark.usefixtures("caja_abierta")


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
    return arca_falso


def _venta(tratamiento=uf.IVA_21, precio=12100):
    return uf.vender([(uf.producto(precio, tratamiento), 1)])


def _autorizado(owner_id: int):
    return servicio_facturacion.fiscalizar_venta(_venta().id, owner_id)


class TestPOS:
    def test_cashier_fiscaliza_desde_el_pos(self, fiscal, cashier):
        venta = _venta()
        respuesta = solicitud("POST", f"/api/fiscal/ventas/{venta.id}/fiscalizar", cookies=cashier[1])
        assert respuesta.status == 200
        datos = respuesta.json()
        assert datos["autorizado"] and datos["estado"] == "AUTORIZADO" and datos["numero"] == "00003-00000001"

    def test_bloqueo_explica_que_falta(self, fiscal, cashier):
        venta = _venta(tratamiento=None)
        respuesta = solicitud("POST", f"/api/fiscal/ventas/{venta.id}/fiscalizar", cookies=cashier[1])
        assert respuesta.status == 422
        datos = respuesta.json()
        assert datos["codigo"] == "FISCALIZACION_BLOQUEADA" and any("IVA" in m for m in datos["motivos"])

    def test_arca_caido_no_hace_desaparecer_la_venta(self, fiscal, cashier):
        fiscal.caido = True
        venta = _venta()
        respuesta = solicitud("POST", f"/api/fiscal/ventas/{venta.id}/fiscalizar", cookies=cashier[1])
        assert respuesta.status == 422 and respuesta.json()["codigo"] == "FISCALIZACION_NO_DISPONIBLE"
        detalle = solicitud("GET", f"/ventas/{venta.id}", cookies=cashier[1])
        assert detalle.status == 200 and "Fiscalización pendiente" in detalle.texto

    def test_pos_marca_fiscalizacion_activa(self, fiscal, cashier):
        assert 'data-fiscal-activo="true"' in solicitud("GET", "/ventas", cookies=cashier[1]).texto

    def test_pos_sin_configurar(self, cashier):
        assert 'data-fiscal-activo="false"' in solicitud("GET", "/ventas", cookies=cashier[1]).texto

    def test_doble_post_no_duplica(self, fiscal, cashier):
        venta = _venta()
        primero = solicitud("POST", f"/api/fiscal/ventas/{venta.id}/fiscalizar", cookies=cashier[1]).json()
        segundo = solicitud("POST", f"/api/fiscal/ventas/{venta.id}/fiscalizar", cookies=cashier[1]).json()
        assert primero["comprobante_id"] == segundo["comprobante_id"] and fiscal.autorizaciones == 1


class TestDocumentos:
    def test_a4_80mm_y_pdf_de_un_autorizado(self, fiscal, cashier, owner):
        comprobante = _autorizado(owner[0].id)
        a4 = solicitud("GET", f"/fiscal/comprobantes/{comprobante.id}", cookies=cashier[1]).texto
        assert comprobante.codigo_autorizacion in a4 and "00003-00000001" in a4
        assert "https://www.arca.gob.ar/fe/qr/?p=" in a4 and "<svg" in a4 and "SIN VALIDEZ FISCAL" in a4
        termico = solicitud("GET", f"/fiscal/comprobantes/{comprobante.id}/ticket", cookies=cashier[1]).texto
        assert comprobante.codigo_autorizacion in termico and "80mm" in termico
        pdf = solicitud("GET", f"/fiscal/comprobantes/{comprobante.id}/pdf", cookies=cashier[1])
        assert pdf.status == 200 and pdf.cuerpo.startswith(b"%PDF-1.4") and comprobante.codigo_autorizacion.encode() in pdf.cuerpo
        assert pdf.header("content-type") == "application/pdf"

    @pytest.mark.parametrize("sufijo", ["", "/ticket", "/pdf"])
    def test_sin_cae_no_hay_documento_ni_qr(self, fiscal, owner, sufijo):
        fiscal.caido = True
        venta = _venta()
        with pytest.raises(Exception):
            servicio_facturacion.fiscalizar_venta(venta.id, owner[0].id)
        pendiente = servicio_facturacion.comprobantes_de_venta(venta.id)[0]
        respuesta = solicitud("GET", f"/fiscal/comprobantes/{pendiente.id}{sufijo}", cookies=owner[1])
        assert respuesta.status == 303 and b"%PDF" not in respuesta.cuerpo and b"fe/qr" not in respuesta.cuerpo

    def test_ticket_interno_dice_no_fiscal(self, fiscal, owner):
        comprobante = _autorizado(owner[0].id)
        ticket = solicitud("GET", f"/ventas/{comprobante.venta_id}/ticket", cookies=owner[1]).texto
        assert "NO VÁLIDO COMO FACTURA" in ticket and comprobante.codigo_autorizacion not in ticket and "fe/qr" not in ticket

    def test_imprimir_no_fiscaliza(self, fiscal, owner):
        venta = _venta()
        fiscal.llamadas.clear()
        solicitud("GET", f"/ventas/{venta.id}/ticket", cookies=owner[1])
        assert servicio_facturacion.comprobantes_de_venta(venta.id) == [] and fiscal.llamadas == []


class TestDetalleEHistorial:
    def test_detalle_muestra_comprobante_y_oculta_anular(self, fiscal, owner):
        comprobante = _autorizado(owner[0].id)
        html = solicitud("GET", f"/ventas/{comprobante.venta_id}", cookies=owner[1]).texto
        assert 'id="venta-fiscal"' in html and "Autorizada" in html and 'id="venta-fiscal-no-anulable"' in html
        assert f"/ventas/{comprobante.venta_id}/anular" not in html

    def test_anular_por_post_manipulado_se_rechaza(self, fiscal, owner):
        comprobante = _autorizado(owner[0].id)
        respuesta = solicitud("POST", f"/ventas/{comprobante.venta_id}/anular", cookies=owner[1], formulario={"motivo": "ERROR_CARGA"})
        assert "nota%20de%20cr" in respuesta.header("location")
        assert servicio_ventas.obtener_resumen_por_id(comprobante.venta_id).estado == "ACTIVA"

    def test_historial_muestra_estado_fiscal(self, fiscal, owner):
        _autorizado(owner[0].id)
        assert "data-estado-fiscal" in solicitud("GET", "/ventas/historial", cookies=owner[1]).texto


class TestIncierto:
    ACCION = "Reenviar con el mismo número"

    def _incierto(self, fiscal, owner):
        fiscal.timeout_sin_procesar = True
        venta = _venta()
        return venta, servicio_facturacion.fiscalizar_venta(venta.id, owner[0].id)

    def test_owner_resuelve_con_el_mismo_numero_desde_la_venta(self, fiscal, owner):
        venta, comprobante = self._incierto(fiscal, owner)
        assert self.ACCION in solicitud("GET", f"/ventas/{venta.id}", cookies=owner[1]).texto
        assert solicitud("POST", f"/fiscal/comprobantes/{comprobante.id}/reintentar", cookies=owner[1]).status == 303
        final = servicio_facturacion.obtener(comprobante.id)
        assert final.estado == "AUTORIZADO" and final.numero == 1 and fiscal.autorizaciones == 1

    def test_cashier_no_ve_la_accion_de_reenvio(self, fiscal, owner, cashier):
        venta, _ = self._incierto(fiscal, owner)
        assert self.ACCION not in solicitud("GET", f"/ventas/{venta.id}", cookies=cashier[1]).texto


class TestNotaCreditoWeb:
    def test_owner_cancela_con_nota_de_credito(self, fiscal, owner):
        comprobante = _autorizado(owner[0].id)
        detalle = solicitud("GET", f"/ventas/{comprobante.venta_id}", cookies=owner[1]).texto
        assert 'id="venta-nota-credito"' in detalle
        assert 'id="form-nota-credito"' in solicitud("GET", f"/fiscal/ventas/{comprobante.venta_id}/nota-credito", cookies=owner[1]).texto
        respuesta = solicitud("POST", f"/fiscal/ventas/{comprobante.venta_id}/nota-credito", cookies=owner[1],
                              formulario={"motivo": "ERROR_CARGA"})
        assert respuesta.status == 303 and "Nota%20de%20Cr" in respuesta.header("location")
        detalle = solicitud("GET", f"/ventas/{comprobante.venta_id}", cookies=owner[1]).texto
        assert 'data-cancelacion-fiscal="APLICADA"' in detalle and 'id="venta-nota-credito"' not in detalle

    def test_cashier_no_emite_nota_de_credito(self, fiscal, owner, cashier):
        comprobante = _autorizado(owner[0].id)
        ruta = f"/fiscal/ventas/{comprobante.venta_id}/nota-credito"
        assert solicitud("GET", ruta, cookies=cashier[1]).status == 403
        assert solicitud("POST", ruta, cookies=cashier[1], formulario={"motivo": "ERROR_CARGA"}).status == 403
        assert 'id="venta-nota-credito"' not in solicitud("GET", f"/ventas/{comprobante.venta_id}", cookies=cashier[1]).texto


class TestPermisos:
    @pytest.mark.parametrize(
        "metodo, ruta",
        [("GET", "/fiscal"), ("GET", "/fiscal/configuracion"), ("POST", "/fiscal/configuracion"),
         ("POST", "/fiscal/configuracion/validar-punto-venta"), ("GET", "/fiscal/certificados"), ("POST", "/fiscal/certificados"),
         ("GET", "/fiscal/productos"), ("POST", "/fiscal/productos"), ("POST", "/fiscal/conciliar-inciertos"),
         ("POST", "/fiscal/comprobantes/1/conciliar"), ("POST", "/fiscal/comprobantes/1/descartar"),
         ("POST", "/fiscal/probar-conexion")],
    )
    def test_cashier_no_accede_a_la_configuracion_fiscal(self, fiscal, cashier, metodo, ruta):
        assert solicitud(metodo, ruta, cookies=cashier[1], formulario={"cuit": "20111111112"}).status == 403

    def test_sin_sesion(self):
        assert solicitud("POST", "/api/fiscal/ventas/1/fiscalizar").status == 401


class TestConfiguracionOwner:
    def test_guardar_y_validar_punto_de_venta(self, arca_falso, owner):
        formulario = {"cuit": "20-11111111-2", "razon_social": "Kiosco", "condicion_iva": "MONOTRIBUTO",
                      "domicilio": "Calle 1", "punto_venta_cae": "3",
                      # Campos manipulados que el formulario no ofrece: se ignoran.
                      "ambiente": "PRODUCCION", "numero": "999", "codigo_autorizacion": "71234567890123"}
        assert solicitud("POST", "/fiscal/configuracion", cookies=owner[1], formulario=formulario).status == 303
        perfil = repositorio_fiscal.obtener_perfil()
        assert perfil.ambiente == "HOMOLOGACION" and perfil.condicion_iva == "MONOTRIBUTO"
        solicitud("POST", "/fiscal/configuracion/validar-punto-venta", cookies=owner[1], formulario={"modalidad": "CAE"})
        assert repositorio_fiscal.obtener_perfil().punto_venta_cae_listo

    def test_importar_certificado_por_formulario(self, owner, fiscal_aislado):
        respuesta = solicitud(
            "POST", "/fiscal/certificados", cookies=owner[1], formulario={"ambiente": "HOMOLOGACION", "password": ""},
            archivos={"certificado": ("c.crt", cp.pem(cp.certificado())), "clave": ("c.key", cp.clave_pem())},
        )
        assert respuesta.status == 303 and "cargado" in respuesta.header("location")
        assert credenciales.estado("HOMOLOGACION").estado == credenciales.VIGENTE
        pagina = solicitud("GET", "/fiscal/certificados", cookies=owner[1]).texto
        assert "PRIVATE KEY" not in pagina

    def test_certificado_de_produccion_en_homologacion_rechazado(self, owner):
        respuesta = solicitud(
            "POST", "/fiscal/certificados", cookies=owner[1], formulario={"ambiente": "HOMOLOGACION"},
            archivos={"certificado": ("c.crt", cp.pem(cp.certificado(homologacion=False))), "clave": ("c.key", cp.clave_pem())},
        )
        assert respuesta.status == 303 and "error" in respuesta.header("location")

    def test_clasificar_iva_por_formulario(self, owner):
        producto = uf.producto()
        solicitud("POST", "/fiscal/productos", cookies=owner[1], formulario={f"iva_{producto.id}": "GRAVADO:5"})
        assert repositorio_fiscal.obtener_tratamiento(producto.id) == uf.IVA_21

    def test_panel(self, fiscal, owner):
        _autorizado(owner[0].id)
        html = solicitud("GET", "/fiscal", cookies=owner[1]).texto
        assert 'id="fiscal-listo"' in html and "activa" in html and "Homologación" in html and 'id="fiscal-autorizados"' in html


class TestBackupSinSecretos:
    def test_backup_no_contiene_clave_ni_ticket(self, fiscal, owner, tmp_path, fiscal_aislado):
        _autorizado(owner[0].id)
        assert (fiscal_aislado / "homologacion" / credenciales.NOMBRE_CLAVE).is_file()
        ruta = servicio_backup.crear_backup(tmp_path / "bk", ControlEscrituras())
        clave_der = cp.clave().private_bytes(
            serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        cifrada = (fiscal_aislado / "homologacion" / credenciales.NOMBRE_CLAVE).read_bytes()
        with zipfile.ZipFile(ruta) as paquete:
            assert not any("fiscal" in nombre or nombre.endswith((".dpapi", ".pem", ".key")) for nombre in paquete.namelist())
            for nombre in paquete.namelist():
                contenido = paquete.read(nombre)
                assert clave_der not in contenido and cifrada not in contenido and b"PRIVATE KEY" not in contenido
