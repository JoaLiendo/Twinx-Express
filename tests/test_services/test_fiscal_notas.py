"""V1.16-F: nota de crédito total. La factura original nunca cambia, el efecto comercial ocurre una sola vez
y solo con la NC autorizada; un fallo de red no produce una doble devolución."""

import pytest

from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import fiscal as repositorio_fiscal
from domain.fiscal.protocolo import MensajeARCA
from excepciones import (
    FiscalizacionNoDisponibleError,
    OperacionFiscalInvalidaError,
    PermisoDenegadoError,
    VentaDeCajaCerradaError,
)
from services import servicio_caja, servicio_stock, servicio_ventas
from services.fiscal import servicio_facturacion as facturacion
from services.fiscal import servicio_notas, servicio_parametros
from tests import utilidades_fiscales as uf


@pytest.fixture
def fiscal(caja_abierta, arca_falso):
    uf.guardar_perfil_validado()
    servicio_parametros.refrescar(arca_falso)
    return arca_falso


@pytest.fixture
def owner(base_datos_temporal):
    return uf.crear_owner()


def _facturada(owner_id, cantidad=3):
    producto = uf.producto(12100, uf.IVA_21, stock=10)
    venta = uf.vender([(producto, cantidad)])
    factura = facturacion.fiscalizar_venta(venta.id, owner_id)
    assert factura.estado == "AUTORIZADO"
    return producto, venta, factura


def _stock(producto_id):
    return servicio_stock.obtener_por_id(producto_id).stock_actual


def _cancelacion(venta_id):
    return repositorio_fiscal.cancelaciones_de_venta(venta_id)[-1]


class TestNotaCredito:
    def test_nc_total_autorizada_anula_y_restaura_stock_una_vez(self, fiscal, owner):
        producto, venta, factura = _facturada(owner)
        assert _stock(producto.id) == 7
        nc = servicio_notas.emitir_nota_credito_total(venta.id, "ARREPENTIMIENTO_CLIENTE", None, owner)
        assert nc.estado == "AUTORIZADO" and nc.tipo_comprobante == 8 and nc.comprobante_asociado_id == factura.id
        assert nc.numero == 1 and nc.codigo_autorizacion != factura.codigo_autorizacion
        asociado = fiscal.registro(3, 8, 1).solicitud.asociados[0]
        assert (asociado.tipo, asociado.numero) == (6, factura.numero)
        assert servicio_ventas.obtener_resumen_por_id(venta.id).estado == "ANULADA"
        assert _stock(producto.id) == 10 and _cancelacion(venta.id).estado == "APLICADA"
        servicio_notas.aplicar_pendientes()
        assert _stock(producto.id) == 10
        acciones = [e.accion for e in repositorio_auditoria.listar(limite=50)]
        assert "FISCAL_NC_AUTORIZADA" in acciones and "VENTA_ANULADA" in acciones

    def test_la_factura_original_no_cambia(self, fiscal, owner):
        _, venta, factura = _facturada(owner)
        servicio_notas.emitir_nota_credito_total(venta.id, "ERROR_CARGA", None, owner)
        despues = facturacion.obtener(factura.id)
        assert (despues.estado, despues.numero, despues.codigo_autorizacion, despues.importes) == (
            factura.estado, factura.numero, factura.codigo_autorizacion, factura.importes)

    def test_nc_duplicada_rechazada(self, fiscal, owner):
        _, venta, _ = _facturada(owner)
        servicio_notas.emitir_nota_credito_total(venta.id, "ERROR_CARGA", None, owner)
        with pytest.raises(Exception) as error:
            servicio_notas.emitir_nota_credito_total(venta.id, "ERROR_CARGA", None, owner)
        assert type(error.value).__name__ in ("OperacionFiscalInvalidaError", "VentaYaAnuladaError")
        assert fiscal.autorizaciones == 2

    def test_nc_sobre_comprobante_no_autorizado(self, fiscal, owner):
        fiscal.caido = True
        venta = uf.vender([(uf.producto(1000, uf.IVA_21), 1)])
        with pytest.raises(FiscalizacionNoDisponibleError):
            facturacion.fiscalizar_venta(venta.id, owner)
        fiscal.caido = False
        with pytest.raises(OperacionFiscalInvalidaError, match="autorizada"):
            servicio_notas.emitir_nota_credito_total(venta.id, "ERROR_CARGA", None, owner)

    def test_nc_sin_factura(self, fiscal, owner):
        venta = uf.vender([(uf.producto(1000, uf.IVA_21), 1)])
        with pytest.raises(OperacionFiscalInvalidaError):
            servicio_notas.emitir_nota_credito_total(venta.id, "ERROR_CARGA", None, owner)

    def test_nc_rechazada_no_toca_la_venta_y_se_puede_reintentar(self, fiscal, owner):
        producto, venta, _ = _facturada(owner)
        fiscal.rechazar_proximo = [MensajeARCA(10197, "Comprobante asociado inválido")]
        nc = servicio_notas.emitir_nota_credito_total(venta.id, "ERROR_CARGA", None, owner)
        assert nc.estado == "RECHAZADO" and _cancelacion(venta.id).estado == "RECHAZADA"
        assert servicio_ventas.obtener_resumen_por_id(venta.id).estado == "ACTIVA" and _stock(producto.id) == 7
        assert servicio_notas.emitir_nota_credito_total(venta.id, "ERROR_CARGA", None, owner).estado == "AUTORIZADO"
        assert _stock(producto.id) == 10

    def test_nc_incierta_no_devuelve_hasta_conciliar_y_solo_una_vez(self, fiscal, owner):
        producto, venta, _ = _facturada(owner)
        fiscal.caer_tras_cae = True
        nc = servicio_notas.emitir_nota_credito_total(venta.id, "ERROR_CARGA", None, owner)
        assert nc.estado == "INCIERTO" and _cancelacion(venta.id).estado == "PENDIENTE"
        assert servicio_ventas.obtener_resumen_por_id(venta.id).estado == "ACTIVA" and _stock(producto.id) == 7
        servicio_notas.conciliar_y_aplicar()  # ARCA sigue caído
        assert _stock(producto.id) == 7
        fiscal.caido = False
        servicio_notas.conciliar_y_aplicar()
        assert facturacion.obtener(nc.id).estado == "AUTORIZADO" and _stock(producto.id) == 10
        servicio_notas.conciliar_y_aplicar()
        assert _stock(producto.id) == 10 and fiscal.autorizaciones == 2

    def test_nc_pendiente_por_arca_caido_y_reintento(self, fiscal, owner):
        producto, venta, _ = _facturada(owner)
        fiscal.caido = True
        with pytest.raises(FiscalizacionNoDisponibleError):
            servicio_notas.emitir_nota_credito_total(venta.id, "ERROR_CARGA", None, owner)
        nc = next(c for c in facturacion.comprobantes_de_venta(venta.id) if c.rol == "NOTA_CREDITO")
        assert nc.estado == "PENDIENTE" and _stock(producto.id) == 7
        fiscal.caido = False
        assert facturacion.reintentar(nc.id, owner).estado == "AUTORIZADO"
        servicio_notas.aplicar_pendientes()
        assert _stock(producto.id) == 10

    def test_nc_descartada_rechaza_la_cancelacion(self, fiscal, owner):
        _, venta, _ = _facturada(owner)
        fiscal.caido = True
        with pytest.raises(FiscalizacionNoDisponibleError):
            servicio_notas.emitir_nota_credito_total(venta.id, "ERROR_CARGA", None, owner)
        nc = next(c for c in facturacion.comprobantes_de_venta(venta.id) if c.rol == "NOTA_CREDITO")
        facturacion.descartar(nc.id, owner)
        servicio_notas.aplicar_pendientes()
        assert _cancelacion(venta.id).estado == "RECHAZADA"

    def test_caja_cerrada_mientras_la_nc_estaba_incierta(self, fiscal, owner):

        producto, venta, _ = _facturada(owner)
        fiscal.caer_tras_cae = True
        servicio_notas.emitir_nota_credito_total(venta.id, "ERROR_CARGA", None, owner)
        servicio_caja.cerrar_caja(0, usuario_id=owner)
        fiscal.caido = False
        servicio_notas.conciliar_y_aplicar()
        assert _cancelacion(venta.id).estado == "SIN_APLICAR" and _stock(producto.id) == 7
        assert "FISCAL_ERROR" in [e.accion for e in repositorio_auditoria.listar(limite=50)]


class TestBloqueosPreviosAArca:
    def test_cashier_no_emite_nc(self, fiscal, owner):
        _, venta, _ = _facturada(owner)
        with pytest.raises(PermisoDenegadoError):
            servicio_notas.emitir_nota_credito_total(venta.id, "ERROR_CARGA", None, uf.crear_cashier())

    def test_motivo_otro_exige_observacion(self, fiscal, owner):
        _, venta, _ = _facturada(owner)
        with pytest.raises(Exception, match="observación"):
            servicio_notas.emitir_nota_credito_total(venta.id, "OTRO", None, owner)

    def test_venta_de_caja_cerrada_se_bloquea_antes_de_arca(self, fiscal, owner):

        _, venta, _ = _facturada(owner)
        servicio_caja.cerrar_caja(0, usuario_id=owner)
        antes = fiscal.autorizaciones
        with pytest.raises(VentaDeCajaCerradaError):
            servicio_notas.emitir_nota_credito_total(venta.id, "ERROR_CARGA", None, owner)
        assert fiscal.autorizaciones == antes

    def test_monotributo_emite_nc_c(self, caja_abierta, arca_falso, owner):
        uf.guardar_perfil_validado("MONOTRIBUTO")
        servicio_parametros.refrescar(arca_falso)
        venta = uf.vender([(uf.producto(5000), 1)])
        facturacion.fiscalizar_venta(venta.id, owner)
        assert servicio_notas.emitir_nota_credito_total(venta.id, "ERROR_CARGA", None, owner).tipo_comprobante == 13
