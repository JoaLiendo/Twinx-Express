"""V1.16-D: fiscalización con CAE contra el ARCA falso. Casos adversariales de numeración, incertidumbre,
conciliación, concurrencia y recuperación tras reinicio."""

import dataclasses
from datetime import date
import threading

import pytest

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import clientes as repositorio_clientes
from db.repositorios import fiscal as repositorio_fiscal
from domain.cliente import Cliente
from domain.fiscal.protocolo import MensajeARCA
from excepciones import (
    ConfiguracionFiscalError,
    ErrorFiscal,
    ErrorWSAA,
    FiscalizacionBloqueadaError,
    FiscalizacionNoDisponibleError,
    OperacionFiscalInvalidaError,
    PermisoDenegadoError,
    SecuenciaFiscalBloqueadaError,
)
from services import servicio_ventas
from services.fiscal import servicio_facturacion as facturacion
from services.fiscal import servicio_parametros
from tests import utilidades_fiscales as uf

UMBRAL = 1_000_000_000


@pytest.fixture
def fiscal(caja_abierta, arca_falso):
    uf.guardar_perfil_validado()
    servicio_parametros.refrescar(arca_falso)
    arca_falso.llamadas.clear()
    return arca_falso


@pytest.fixture
def owner(base_datos_temporal):
    return uf.crear_owner()


def _venta(precio=12100, tratamiento=uf.IVA_21, cantidad=1, **kwargs):
    return uf.vender([(uf.producto(precio, tratamiento), cantidad)], **kwargs)


def _cliente(**datos) -> int:
    with obtener_conexion() as conexion:
        return repositorio_clientes.crear_cliente_en_conexion(conexion, Cliente(**datos)).id


def _acciones() -> list[str]:
    return [e.accion for e in repositorio_auditoria.listar(limite=200)]


class TestAutorizacion:
    def test_factura_b_anonima(self, fiscal, owner):
        venta = _venta()
        comprobante = facturacion.fiscalizar_venta(venta.id, owner)
        assert comprobante.estado == "AUTORIZADO" and comprobante.clase == "B" and comprobante.tipo_comprobante == 6
        assert comprobante.numero == 1 and len(comprobante.codigo_autorizacion) == 14
        assert comprobante.vencimiento_autorizacion and comprobante.receptor.doc_tipo == 99
        assert (comprobante.importes.neto_centavos, comprobante.importes.iva_centavos) == (10000, 2100)
        assert fiscal.registro(3, 6, 1).cae == comprobante.codigo_autorizacion
        assert {"FISCAL_SOLICITUD", "FISCAL_AUTORIZADO"} <= set(_acciones())

    def test_factura_a_a_un_inscripto(self, fiscal, owner):
        cliente = _cliente(nombre="Distribuidora", tipo_cliente="PERSONA", condicion_fiscal="RESPONSABLE_INSCRIPTO", cuit=uf.CUIT_RECEPTOR_RI)
        comprobante = facturacion.fiscalizar_venta(_venta(cliente_id=cliente).id, owner)
        assert (comprobante.clase, comprobante.tipo_comprobante, comprobante.receptor.doc_tipo) == ("A", 1, 80)
        assert comprobante.receptor.condicion_iva_receptor_id == 1

    def test_factura_c_monotributo_sin_clasificar_productos(self, caja_abierta, arca_falso, owner):
        uf.guardar_perfil_validado("MONOTRIBUTO")
        servicio_parametros.refrescar(arca_falso)
        comprobante = facturacion.fiscalizar_venta(_venta(tratamiento=None).id, owner)
        assert (comprobante.clase, comprobante.importes.iva_centavos, comprobante.importes.alicuotas) == ("C", 0, ())

    def test_multiples_alicuotas_reconcilian_con_la_venta(self, fiscal, owner):
        venta = uf.vender([(uf.producto(12100, uf.IVA_21), 3), (uf.producto(11050, uf.IVA_105), 2),
                           (uf.producto(999, uf.EXENTO), 1), (uf.producto(1, uf.NO_GRAVADO), 7)])
        comprobante = facturacion.fiscalizar_venta(venta.id, owner)
        assert comprobante.estado == "AUTORIZADO"
        assert comprobante.importes.total_centavos == venta.total_centavos

    def test_con_observaciones(self, fiscal, owner):
        fiscal.observar_proximo = [MensajeARCA(10217, "Observación")]
        comprobante = facturacion.fiscalizar_venta(_venta().id, owner)
        assert comprobante.estado == "AUTORIZADO" and comprobante.observaciones[0]["codigo"] == 10217

    def test_cashier_fiscaliza(self, fiscal):
        assert facturacion.fiscalizar_venta(_venta().id, uf.crear_cashier()).estado == "AUTORIZADO"


class TestBloqueos:
    def test_producto_sin_iva_no_crea_comprobante(self, fiscal, owner):
        venta = _venta(tratamiento=None)
        with pytest.raises(FiscalizacionBloqueadaError, match="IVA"):
            facturacion.fiscalizar_venta(venta.id, owner)
        assert facturacion.comprobantes_de_venta(venta.id) == [] and fiscal.llamadas == []

    def test_cliente_sin_condicion(self, fiscal, owner):
        venta = _venta(cliente_id=_cliente(nombre="Histórico"))
        with pytest.raises(FiscalizacionBloqueadaError, match="condición fiscal"):
            facturacion.fiscalizar_venta(venta.id, owner)

    @pytest.mark.parametrize("total, bloquea", [(UMBRAL - 1, False), (UMBRAL, True), (UMBRAL + 1, True)])
    def test_consumidor_final_anonimo_y_umbral(self, fiscal, owner, total, bloquea):
        venta = _venta(precio=total)
        if bloquea:
            with pytest.raises(FiscalizacionBloqueadaError, match="identificar"):
                facturacion.fiscalizar_venta(venta.id, owner)
        else:
            assert facturacion.fiscalizar_venta(venta.id, owner).estado == "AUTORIZADO"

    def test_sin_perfil_validado(self, caja_abierta, arca_falso, owner):
        with obtener_conexion() as conexion:
            repositorio_fiscal.guardar_perfil_en_conexion(conexion, uf.perfil())
        with pytest.raises(ConfiguracionFiscalError):
            facturacion.fiscalizar_venta(_venta().id, owner)

    def test_sin_tablas_de_arca(self, caja_abierta, arca_falso, owner):
        uf.guardar_perfil_validado()
        with pytest.raises(FiscalizacionBloqueadaError, match="parámetros"):
            facturacion.fiscalizar_venta(_venta().id, owner)

    def test_venta_anulada(self, fiscal, owner):
        venta = _venta()
        servicio_ventas.anular_venta(venta.id, "ERROR_CARGA", None, owner)
        with pytest.raises(OperacionFiscalInvalidaError):
            facturacion.fiscalizar_venta(venta.id, owner)

    def test_a_con_leyenda_bloquea(self, caja_abierta, arca_falso, owner):
        uf.guardar_perfil_validado(habilitacion_clase_a="OPERACION_SUJETA_A_RETENCION")
        servicio_parametros.refrescar(arca_falso)
        cliente = _cliente(nombre="RI", condicion_fiscal="RESPONSABLE_INSCRIPTO", cuit=uf.CUIT_RECEPTOR_RI)
        with pytest.raises(FiscalizacionBloqueadaError, match="5762"):
            facturacion.fiscalizar_venta(_venta(cliente_id=cliente).id, owner)


class TestNumeracion:
    def test_consecutiva_desde_arca(self, fiscal, owner):
        numeros = [facturacion.fiscalizar_venta(_venta().id, owner).numero for _ in range(3)]
        assert numeros == [1, 2, 3]

    def test_local_atrasado_usa_el_de_arca(self, fiscal, owner):
        facturacion.fiscalizar_venta(_venta().id, owner)
        fiscal.emitir_externo(3, 6)
        fiscal.emitir_externo(3, 6)
        assert facturacion.fiscalizar_venta(_venta().id, owner).numero == 4

    def test_local_adelantado_bloquea(self, fiscal, owner):
        facturacion.fiscalizar_venta(_venta().id, owner)
        facturacion.fiscalizar_venta(_venta().id, owner)
        fiscal._ultimos[(3, 6)] = 1  # ARCA dice menos de lo que Twinx ya autorizó
        venta = _venta()
        with pytest.raises(ErrorFiscal, match="inconsistente"):
            facturacion.fiscalizar_venta(venta.id, owner)
        assert facturacion.comprobantes_de_venta(venta.id)[0].estado == "PENDIENTE"
        assert "FISCAL_ERROR" in _acciones()

    def test_doble_click_no_duplica(self, fiscal, owner):
        venta = _venta()
        primero = facturacion.fiscalizar_venta(venta.id, owner)
        segundo = facturacion.fiscalizar_venta(venta.id, owner)
        assert primero.id == segundo.id and fiscal.autorizaciones == 1

    def test_dos_threads_misma_venta(self, fiscal, owner):
        venta = _venta()
        resultados, errores = [], []

        def fiscalizar():
            try:
                resultados.append(facturacion.fiscalizar_venta(venta.id, owner))
            except Exception as error:  # noqa: BLE001 - el test recolecta cualquier falla del hilo
                errores.append(error)

        hilos = [threading.Thread(target=fiscalizar) for _ in range(4)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join()
        assert not errores
        assert fiscal.autorizaciones == 1 and {r.id for r in resultados} == {resultados[0].id}

    def test_dos_threads_distintas_ventas_numeros_unicos(self, fiscal, owner):
        ventas = [_venta() for _ in range(5)]
        resultados = []
        hilos = [threading.Thread(target=lambda v=v: resultados.append(facturacion.fiscalizar_venta(v.id, owner))) for v in ventas]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join()
        assert sorted(r.numero for r in resultados) == [1, 2, 3, 4, 5]


class TestRechazo:
    def test_rechazo_sin_cae_y_reintento_con_nuevo_comprobante(self, fiscal, owner):
        venta = _venta()
        fiscal.rechazar_proximo = [MensajeARCA(10013, "Documento inválido")]
        rechazado = facturacion.fiscalizar_venta(venta.id, owner)
        assert rechazado.estado == "RECHAZADO" and rechazado.codigo_autorizacion is None
        assert rechazado.observaciones[0]["codigo"] == 10013
        nuevo = facturacion.fiscalizar_venta(venta.id, owner)
        assert nuevo.id != rechazado.id and nuevo.estado == "AUTORIZADO" and nuevo.numero == 1


class TestARCANoDisponible:
    def test_caido_antes_de_pedir_numero(self, fiscal, owner):
        fiscal.caido = True
        venta = _venta()
        with pytest.raises(FiscalizacionNoDisponibleError):
            facturacion.fiscalizar_venta(venta.id, owner)
        pendiente = facturacion.comprobantes_de_venta(venta.id)[0]
        assert pendiente.estado == "PENDIENTE" and pendiente.numero is None
        fiscal.caido = False
        assert facturacion.fiscalizar_venta(venta.id, owner).estado == "AUTORIZADO"

    def test_conexion_rechazada_al_enviar_libera_el_numero(self, fiscal, owner):
        fiscal.cae_no_enviado = True
        venta = _venta()
        with pytest.raises(FiscalizacionNoDisponibleError):
            facturacion.fiscalizar_venta(venta.id, owner)
        assert facturacion.comprobantes_de_venta(venta.id)[0].numero is None
        assert facturacion.fiscalizar_venta(venta.id, owner).numero == 1

    def test_credenciales_rechazadas_no_consume_numero(self, fiscal, owner):
        fiscal.credenciales_rechazadas = True
        venta = _venta()
        with pytest.raises(ErrorWSAA):
            facturacion.fiscalizar_venta(venta.id, owner)
        assert facturacion.comprobantes_de_venta(venta.id)[0].estado == "PENDIENTE"
        assert "invalidar_credenciales" in fiscal.llamadas


class TestIncertidumbre:
    def test_timeout_sin_procesar_se_concilia_a_pendiente_y_reintenta_seguro(self, fiscal, owner):
        fiscal.timeout_sin_procesar = True
        venta = _venta()
        comprobante = facturacion.fiscalizar_venta(venta.id, owner)
        assert comprobante.estado == "PENDIENTE" and comprobante.numero is None
        assert fiscal.autorizaciones == 0 and "FISCAL_INCIERTO" in _acciones() and "FISCAL_RECONCILIADO" in _acciones()
        assert facturacion.fiscalizar_venta(venta.id, owner).numero == 1

    def test_respuesta_perdida_con_arca_habiendo_autorizado(self, fiscal, owner):
        fiscal.perder_respuesta = True
        comprobante = facturacion.fiscalizar_venta(_venta().id, owner)
        assert comprobante.estado == "AUTORIZADO" and fiscal.autorizaciones == 1
        assert comprobante.codigo_autorizacion == fiscal.registro(3, 6, 1).cae

    def test_respuesta_invalida_tras_procesar(self, fiscal, owner):
        fiscal.respuesta_invalida = True
        comprobante = facturacion.fiscalizar_venta(_venta().id, owner)
        assert comprobante.estado == "AUTORIZADO" and fiscal.autorizaciones == 1

    def test_incierto_bloquea_la_secuencia_hasta_conciliar(self, fiscal, owner):
        fiscal.caer_tras_cae = True
        primero = facturacion.fiscalizar_venta(_venta().id, owner)
        assert primero.estado == "INCIERTO"
        fiscal.caido = False
        fiscal.fallar_antes_de_enviar = 1  # la conciliación previa tampoco llega a ARCA
        segunda = _venta()
        with pytest.raises(SecuenciaFiscalBloqueadaError):
            facturacion.fiscalizar_venta(segunda.id, owner)
        assert facturacion.comprobantes_de_venta(segunda.id)[0].estado == "PENDIENTE"
        assert fiscal.autorizaciones == 1
        segundo = facturacion.fiscalizar_venta(segunda.id, owner)
        assert facturacion.obtener(primero.id).estado == "AUTORIZADO"
        assert segundo.numero == 2 and fiscal.autorizaciones == 2

    def test_conciliacion_manual_solo_owner(self, fiscal, owner):
        fiscal.caer_tras_cae = True
        comprobante = facturacion.fiscalizar_venta(_venta().id, owner)
        fiscal.caido = False
        with pytest.raises(PermisoDenegadoError):
            facturacion.conciliar(comprobante.id, uf.crear_cashier())
        assert facturacion.conciliar(comprobante.id, owner).estado == "AUTORIZADO"

    def test_numero_ocupado_por_otro_comprobante(self, fiscal, owner):
        fiscal.caer_tras_cae = True
        comprobante = facturacion.fiscalizar_venta(_venta().id, owner)
        registro = fiscal.registro(3, 6, 1)
        otra = dataclasses.replace(registro.solicitud, doc_nro="20111111112", doc_tipo=80)
        fiscal._registros[(3, 6, 1)] = dataclasses.replace(registro, solicitud=otra)
        fiscal.caido = False
        assert facturacion.conciliar(comprobante.id, owner).estado == "PENDIENTE"

    def test_conciliacion_contradictoria_sigue_incierta(self, fiscal, owner):
        fiscal.caer_tras_cae = True
        comprobante = facturacion.fiscalizar_venta(_venta().id, owner)
        del fiscal._registros[(3, 6, 1)]  # ARCA "no lo tiene" pero su último es 1
        fiscal.caido = False
        assert facturacion.conciliar(comprobante.id, owner).estado == "INCIERTO"
        assert "FISCAL_ERROR" in _acciones()

    def test_reinicio_durante_el_envio(self, fiscal, owner):
        venta = _venta()
        comprobante = facturacion.fiscalizar_venta(venta.id, owner)
        # Simula un proceso que murió con el comprobante en ENVIANDO (ARCA sí lo había autorizado).
        otro = _venta()
        cid = uf.insertar(uf.nuevo_comprobante_para_venta(otro.id))
        uf.transicionar(cid, "ENVIANDO", numero=2, fecha_comprobante=comprobante.fecha_comprobante)
        fiscal.emitir_externo(3, 6)
        solicitud = facturacion.solicitud_de(facturacion.obtener(cid), 2, date.fromisoformat(comprobante.fecha_comprobante))
        from tests.fake_arca import Registro

        fiscal._registros[(3, 6, 2)] = Registro(solicitud, "70000000009999", "20991231")
        assert facturacion.recuperar_al_iniciar() == 1
        assert facturacion.obtener(cid).estado == "INCIERTO"
        facturacion.conciliar_inciertos()
        recuperado = facturacion.obtener(cid)
        assert recuperado.estado == "AUTORIZADO" and recuperado.codigo_autorizacion == "70000000009999"

    def test_reinicio_con_incierto_y_arca_caido_no_lanza(self, fiscal, owner):
        fiscal.caer_tras_cae = True
        comprobante = facturacion.fiscalizar_venta(_venta().id, owner)
        facturacion.conciliar_inciertos()
        assert facturacion.obtener(comprobante.id).estado == "INCIERTO"


class TestDescartar:
    def test_owner_descarta_pendiente_y_refiscaliza(self, fiscal, owner):
        fiscal.caido = True
        venta = _venta()
        with pytest.raises(FiscalizacionNoDisponibleError):
            facturacion.fiscalizar_venta(venta.id, owner)
        pendiente = facturacion.comprobantes_de_venta(venta.id)[0]
        with pytest.raises(PermisoDenegadoError):
            facturacion.descartar(pendiente.id, uf.crear_cashier())
        assert facturacion.descartar(pendiente.id, owner).estado == "DESCARTADO"
        fiscal.caido = False
        assert facturacion.fiscalizar_venta(venta.id, owner).estado == "AUTORIZADO"

    def test_no_se_descarta_un_autorizado(self, fiscal, owner):
        comprobante = facturacion.fiscalizar_venta(_venta().id, owner)
        with pytest.raises(OperacionFiscalInvalidaError):
            facturacion.descartar(comprobante.id, owner)


class TestSecretos:
    def test_intentos_sin_token_ni_sign(self, fiscal, owner):
        comprobante = facturacion.fiscalizar_venta(_venta().id, owner)
        for intento in facturacion.intentos(comprobante.id):
            texto = str(intento.detalle)
            assert "Token" not in texto and "Sign" not in texto
