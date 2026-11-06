"""V1.16-D: fiscalización con CAE contra el ARCA falso. Casos adversariales de numeración, incertidumbre,
conciliación, concurrencia y recuperación tras reinicio."""

import dataclasses
from datetime import UTC, date, datetime, timedelta
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
    TicketYaVigenteError,
)
from services import servicio_ventas
from services.fiscal import servicio_facturacion as facturacion
from services.fiscal import servicio_parametros
from services.fiscal.arca.wsaa import CacheTickets
from services.fiscal.gateway import GatewayARCAReal, establecer_fabrica
from tests import utilidades_fiscales as uf
from tests.test_services.test_fiscal_credenciales_wsaa import _fault as wsaa_fault
from tests.test_services.test_fiscal_credenciales_wsaa import _importar as wsaa_importar
from tests.test_services.test_fiscal_credenciales_wsaa import _respuesta_login as wsaa_login
from tests.test_services.test_fiscal_wsfe import _cliente as wsfe_cliente
from tests.test_services.test_fiscal_wsfe import _respuesta_cae as wsfe_respuesta_cae
from tests.test_services.test_fiscal_wsfe import _sobre as wsfe_sobre

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
    def test_timeout_con_consulta_negativa_conserva_el_numero_en_incierto(self, fiscal, owner):
        fiscal.timeout_sin_procesar = True
        comprobante = facturacion.fiscalizar_venta(_venta().id, owner)
        assert comprobante.estado == "INCIERTO" and comprobante.numero == 1
        assert fiscal.autorizaciones == 0 and "FISCAL_INCIERTO" in _acciones()
        assert "FECompConsultar" in fiscal.llamadas

    def test_procesamiento_tardio_de_arca_recupera_n_y_nunca_emite_n_mas_uno(self, fiscal, owner):
        fiscal.timeout_sin_procesar = True
        venta = _venta()
        comprobante = facturacion.fiscalizar_venta(venta.id, owner)
        fiscal.solicitar_cae(facturacion.solicitud_de(comprobante, 1, date.today()))  # ARCA lo asienta después de la consulta
        recuperado = facturacion.fiscalizar_venta(venta.id, owner)
        assert recuperado.estado == "AUTORIZADO" and recuperado.numero == 1
        assert recuperado.codigo_autorizacion == fiscal.registro(3, 6, 1).cae
        assert fiscal.autorizaciones == 1 and fiscal.registro(3, 6, 2) is None

    def test_consultas_negativas_repetidas_no_liberan_ni_reenvian(self, fiscal, owner):
        fiscal.timeout_sin_procesar = True
        venta = _venta()
        comprobante = facturacion.fiscalizar_venta(venta.id, owner)
        enviados = fiscal.llamadas.count("FECAESolicitar")
        assert facturacion.fiscalizar_venta(venta.id, owner).estado == "INCIERTO"
        assert facturacion.conciliar(comprobante.id, owner).numero == 1
        facturacion.conciliar_inciertos()
        assert facturacion.obtener(comprobante.id).estado == "INCIERTO"
        assert fiscal.llamadas.count("FECAESolicitar") == enviados

    def test_timeout_bloquea_la_secuencia_hasta_que_arca_informa_n(self, fiscal, owner):
        fiscal.timeout_sin_procesar = True
        comprobante = facturacion.fiscalizar_venta(_venta().id, owner)
        segunda = _venta()
        with pytest.raises(SecuenciaFiscalBloqueadaError):
            facturacion.fiscalizar_venta(segunda.id, owner)
        fiscal.solicitar_cae(facturacion.solicitud_de(comprobante, 1, date.today()))
        assert facturacion.fiscalizar_venta(segunda.id, owner).numero == 2
        assert facturacion.obtener(comprobante.id).estado == "AUTORIZADO"
        assert fiscal.autorizaciones == 2

    def test_reenvio_del_dueno_autoriza_el_mismo_numero_si_arca_nunca_lo_proceso(self, fiscal, owner):
        fiscal.timeout_sin_procesar = True
        comprobante = facturacion.fiscalizar_venta(_venta().id, owner)
        reenviado = facturacion.reintentar(comprobante.id, owner)
        assert reenviado.estado == "AUTORIZADO" and reenviado.numero == 1
        assert reenviado.fecha_comprobante == comprobante.fecha_comprobante
        assert fiscal.autorizaciones == 1
        with obtener_conexion() as conexion:
            hashes = conexion.execute(
                "SELECT request_hash FROM intentos_fiscales WHERE comprobante_id = ? AND operacion = 'FECAESolicitar'",
                (comprobante.id,),
            ).fetchall()
        assert len(hashes) == 2 and hashes[0][0] == hashes[1][0]

    def test_reenvio_exige_owner(self, fiscal, owner):
        fiscal.timeout_sin_procesar = True
        comprobante = facturacion.fiscalizar_venta(_venta().id, owner)
        with pytest.raises(PermisoDenegadoError):
            facturacion.reintentar(comprobante.id, uf.crear_cashier())
        assert fiscal.llamadas.count("FECAESolicitar") == 1

    def test_reenvio_con_n_asentado_en_carrera_recupera_n_por_10016(self, fiscal, owner):
        fiscal.timeout_sin_procesar = True
        comprobante = facturacion.fiscalizar_venta(_venta().id, owner)
        original = fiscal.solicitar_cae

        def solicitar(solicitud):
            fiscal.solicitar_cae = original
            original(solicitud)  # el pedido viejo gana justo antes del reenvío
            return original(solicitud)

        fiscal.solicitar_cae = solicitar
        recuperado = facturacion.reintentar(comprobante.id, owner)
        assert recuperado.estado == "AUTORIZADO" and recuperado.numero == 1
        assert recuperado.codigo_autorizacion == fiscal.registro(3, 6, 1).cae and fiscal.autorizaciones == 1

    def test_reenvio_no_enviado_sigue_incierto_con_su_numero(self, fiscal, owner):
        fiscal.timeout_sin_procesar = True
        comprobante = facturacion.fiscalizar_venta(_venta().id, owner)
        fiscal.cae_no_enviado = True
        with pytest.raises(FiscalizacionNoDisponibleError):
            facturacion.reintentar(comprobante.id, owner)
        sigue = facturacion.obtener(comprobante.id)
        assert sigue.estado == "INCIERTO" and sigue.numero == 1

    def test_reenvio_con_timeout_sigue_incierto_con_su_numero(self, fiscal, owner):
        fiscal.timeout_sin_procesar = True
        comprobante = facturacion.fiscalizar_venta(_venta().id, owner)
        fiscal.timeout_sin_procesar = True
        sigue = facturacion.reintentar(comprobante.id, owner)
        assert sigue.estado == "INCIERTO" and sigue.numero == 1 and fiscal.autorizaciones == 0

    def test_reenvio_rechazado_por_arca_queda_rechazado_sin_cae(self, fiscal, owner):
        fiscal.timeout_sin_procesar = True
        comprobante = facturacion.fiscalizar_venta(_venta().id, owner)
        fiscal.rechazar_proximo = [MensajeARCA(10013, "Documento inválido")]
        rechazado = facturacion.reintentar(comprobante.id, owner)
        assert rechazado.estado == "RECHAZADO" and rechazado.codigo_autorizacion is None and fiscal.autorizaciones == 0

    def test_conciliacion_contradictoria_no_se_reenvia(self, fiscal, owner):
        fiscal.caer_tras_cae = True
        comprobante = facturacion.fiscalizar_venta(_venta().id, owner)
        del fiscal._registros[(3, 6, 1)]  # ARCA "no lo tiene" pero su último es 1
        fiscal.caido = False
        enviados = fiscal.llamadas.count("FECAESolicitar")
        with pytest.raises(SecuenciaFiscalBloqueadaError):
            facturacion.reintentar(comprobante.id, owner)
        assert facturacion.obtener(comprobante.id).estado == "INCIERTO"
        assert fiscal.llamadas.count("FECAESolicitar") == enviados

    @pytest.mark.parametrize("codigo", [500, 501, 502])
    def test_error_interno_de_arca_queda_incierto_y_se_consulta(self, fiscal, owner, codigo):
        cliente, transporte = wsfe_cliente(wsfe_sobre("FECAESolicitar", f"<Errors><Err><Code>{codigo}</Code><Msg>Interno</Msg></Err></Errors>"))
        fiscal.solicitar_cae = cliente.solicitar_cae  # el parser real, no un doble que ya devuelva lo esperado
        venta = _venta()
        comprobante = facturacion.fiscalizar_venta(venta.id, owner)
        assert comprobante.estado == "INCIERTO" and comprobante.numero == 1
        assert "FECompConsultar" in fiscal.llamadas
        assert facturacion.fiscalizar_venta(venta.id, owner).id == comprobante.id  # la venta no se libera
        assert len(transporte.pedidos) == 1  # no se reenvía solo

    @pytest.mark.parametrize("codigo", [500, 501, 502])
    def test_error_interno_con_arca_que_ya_asento_recupera_el_cae(self, fiscal, owner, codigo):
        cliente, _ = wsfe_cliente(wsfe_sobre("FECAESolicitar", f"<Errors><Err><Code>{codigo}</Code><Msg>Interno</Msg></Err></Errors>"))
        original = fiscal.solicitar_cae

        def asentar_y_fallar(solicitud):
            original(solicitud)
            return cliente.solicitar_cae(solicitud)

        fiscal.solicitar_cae = asentar_y_fallar
        comprobante = facturacion.fiscalizar_venta(_venta().id, owner)
        assert comprobante.estado == "AUTORIZADO" and comprobante.numero == 1 and fiscal.autorizaciones == 1

    def test_ticket_que_cruza_el_margen_durante_la_emision_se_reutiliza_y_sigue_en_disco(self, fiscal, owner, fiscal_aislado):
        wsaa_importar()
        reloj = [datetime.now(UTC)]
        logins = []

        class Transporte:
            def enviar(self, url, cuerpo, soap_action):
                if "LoginCms" in url:
                    logins.append(url)
                    return wsaa_login(generacion=reloj[0]) if len(logins) == 1 else wsaa_fault("coe.alreadyAuthenticated")
                if soap_action.endswith("FECompUltimoAutorizado"):
                    reloj[0] += timedelta(hours=11, minutes=51)  # el TA entra en el margen de renovación preventiva
                    return wsfe_sobre("FECompUltimoAutorizado", "<PtoVta>3</PtoVta><CbteTipo>6</CbteTipo><CbteNro>0</CbteNro>")
                return wsfe_respuesta_cae(numero=1)

        real = GatewayARCAReal("HOMOLOGACION", uf.CUIT_EMISOR, CacheTickets(persistir=True))
        real._wsaa._transporte = real._wsfe._transporte = Transporte()
        real._wsaa._reloj = lambda: reloj[0]
        establecer_fabrica(lambda perfil: real)
        comprobante = facturacion.fiscalizar_venta(_venta().id, owner)
        assert comprobante.estado == "AUTORIZADO" and len(logins) == 2
        assert (fiscal_aislado / "homologacion" / "ta_wsfe.dpapi").is_file()

    def test_already_authenticated_al_enviar_no_consume_numero_ni_invalida_credenciales(self, fiscal, owner):
        def sin_ticket_conservado(solicitud):
            raise TicketYaVigenteError("ARCA ya entregó un Ticket de Acceso")

        fiscal.solicitar_cae = sin_ticket_conservado
        venta = _venta()
        with pytest.raises(ErrorWSAA):
            facturacion.fiscalizar_venta(venta.id, owner)
        vivo = facturacion.comprobantes_de_venta(venta.id)[0]
        assert vivo.estado == "PENDIENTE" and vivo.numero is None
        assert "invalidar_credenciales" not in fiscal.llamadas

    def test_numero_tomado_por_otro_sistema_conserva_el_rechazo_10016(self, fiscal, owner):
        original = fiscal.ultimo_autorizado

        def ultimo(punto_venta, tipo):
            fiscal.ultimo_autorizado = original
            valor = original(punto_venta, tipo)
            fiscal.emitir_externo(punto_venta, tipo)  # otro sistema gana la carrera tras leer el último
            return valor

        fiscal.ultimo_autorizado = ultimo
        venta = _venta()
        rechazado = facturacion.fiscalizar_venta(venta.id, owner)
        assert rechazado.estado == "RECHAZADO" and "10016" in str(rechazado.observaciones)
        assert fiscal.llamadas.count("FECAESolicitar") == 1  # 10016 no se reintenta solo
        nuevo = facturacion.fiscalizar_venta(venta.id, owner)
        assert nuevo.estado == "AUTORIZADO" and nuevo.numero == 2 and fiscal.autorizaciones == 1

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
