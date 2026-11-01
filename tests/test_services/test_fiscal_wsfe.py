"""V1.16-C: cliente WSFEv1 (respuestas SOAP con la forma del WSDL vigente), validación del punto de venta
y caché de tablas de ARCA. Nunca se llama a ARCA real."""

from datetime import UTC, date, datetime, timedelta

import pytest

from db.repositorios import fiscal as repositorio_fiscal
from domain.fiscal.iva import AlicuotaFiscal, ImportesFiscales
from domain.fiscal.protocolo import ComprobanteAsociado, PuntoVentaARCA, SolicitudAutorizacion
from excepciones import ConfiguracionFiscalError, ErrorWSAA, FiscalizacionBloqueadaError, PermisoDenegadoError, RespuestaARCAInvalidaError
from services.fiscal import servicio_conexion, servicio_parametros
from services.fiscal.arca import wsfe
from services.fiscal.arca.transporte import RespuestaHTTP
from services.fiscal.arca.wsaa import TicketAcceso
from tests import utilidades_fiscales as uf

NS = "http://ar.gov.afip.dif.FEV1/"


def _sobre(operacion: str, contenido: str, estado: int = 200) -> RespuestaHTTP:
    xml = (
        '<?xml version="1.0" encoding="utf-8"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"'
        ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><soap:Body>'
        f'<{operacion}Response xmlns="{NS}"><{operacion}Result>{contenido}</{operacion}Result></{operacion}Response>'
        "</soap:Body></soap:Envelope>"
    )
    return RespuestaHTTP(estado, xml.encode(), 3)


class Transporte:
    def __init__(self, *respuestas):
        self.respuestas = list(respuestas)
        self.pedidos = []

    def enviar(self, url, cuerpo, soap_action):
        self.pedidos.append((soap_action, cuerpo.decode()))
        return self.respuestas.pop(0)


def _ticket():
    ahora = datetime.now(UTC)
    return TicketAcceso("TKN", "SGN", ahora, ahora + timedelta(hours=12), "h")


def _cliente(*respuestas):
    transporte = Transporte(*respuestas)
    return wsfe.ClienteWSFE(transporte, "https://wswhomo.afip.gov.ar/wsfev1/service.asmx", uf.CUIT_EMISOR, _ticket), transporte


def _solicitud(numero=8, tipo=6, asociados=()):
    importes = ImportesFiscales(12100, 0, 10000, 0, 0, 2100, (AlicuotaFiscal(5, 10000, 2100),))
    return SolicitudAutorizacion(3, tipo, numero, "20261101", 1, 99, "0", 5, importes, "PES", "1", asociados)


class TestOperaciones:
    def test_dummy_sin_credenciales(self):
        cliente, transporte = _cliente(_sobre("FEDummy", "<AppServer>OK</AppServer><DbServer>OK</DbServer><AuthServer>OK</AuthServer>"))
        assert cliente.dummy().ok
        accion, cuerpo = transporte.pedidos[0]
        assert accion == NS + "FEDummy" and "<Auth>" not in cuerpo

    def test_ultimo_autorizado(self):
        cliente, transporte = _cliente(_sobre("FECompUltimoAutorizado", "<PtoVta>3</PtoVta><CbteTipo>6</CbteTipo><CbteNro>41</CbteNro>"))
        assert cliente.ultimo_autorizado(3, 6) == 41
        assert "<Token>TKN</Token>" in transporte.pedidos[0][1] and f"<Cuit>{uf.CUIT_EMISOR}</Cuit>" in transporte.pedidos[0][1]

    def test_ultimo_autorizado_de_otra_secuencia_es_invalido(self):
        cliente, _ = _cliente(_sobre("FECompUltimoAutorizado", "<PtoVta>9</PtoVta><CbteTipo>6</CbteTipo><CbteNro>41</CbteNro>"))
        with pytest.raises(RespuestaARCAInvalidaError):
            cliente.ultimo_autorizado(3, 6)

    def test_error_de_credenciales(self):
        errores = "<Errors><Err><Code>600</Code><Msg>ValidacionDeToken: No validaron las credenciales</Msg></Err></Errors>"
        cliente, _ = _cliente(_sobre("FECompUltimoAutorizado", f"<PtoVta>3</PtoVta><CbteTipo>6</CbteTipo><CbteNro>0</CbteNro>{errores}"))
        with pytest.raises(ErrorWSAA):
            cliente.ultimo_autorizado(3, 6)

    def test_consultar_existente(self):
        contenido = (
            "<ResultGet><Concepto>1</Concepto><DocTipo>99</DocTipo><DocNro>0</DocNro><CbteDesde>8</CbteDesde><CbteHasta>8</CbteHasta>"
            "<CbteFch>20261101</CbteFch><ImpTotal>121</ImpTotal><ImpTotConc>0</ImpTotConc><ImpNeto>100</ImpNeto><ImpOpEx>0</ImpOpEx>"
            "<ImpTrib>0</ImpTrib><ImpIVA>21</ImpIVA><MonId>PES</MonId><MonCotiz>1</MonCotiz><Resultado>A</Resultado>"
            "<CodAutorizacion>76123456789012</CodAutorizacion><EmisionTipo>CAE</EmisionTipo><FchVto>20261111</FchVto>"
            "<FchProceso>20261101120000</FchProceso><PtoVta>3</PtoVta><CbteTipo>6</CbteTipo></ResultGet>"
        )
        cliente, _ = _cliente(_sobre("FECompConsultar", contenido))
        consultado = cliente.consultar(3, 6, 8)
        assert (consultado.numero, consultado.total_centavos, consultado.codigo_autorizacion) == (8, 12100, "76123456789012")

    def test_consultar_inexistente_602(self):
        errores = "<Errors><Err><Code>602</Code><Msg>No existen datos en nuestros registros para los parametros ingresados.</Msg></Err></Errors>"
        cliente, _ = _cliente(_sobre("FECompConsultar", errores))
        assert cliente.consultar(3, 6, 99) is None

    def test_consultar_con_otro_error_no_concluye_inexistencia(self):
        errores = "<Errors><Err><Code>500</Code><Msg>Error interno</Msg></Err></Errors>"
        cliente, _ = _cliente(_sobre("FECompConsultar", errores))
        with pytest.raises(wsfe.ErrorWSFE):
            cliente.consultar(3, 6, 99)

    def test_importe_con_fraccion_de_centavo_es_invalido(self):
        with pytest.raises(RespuestaARCAInvalidaError):
            wsfe.a_centavos("1.005")
        assert wsfe.a_centavos("1.1") == 110 and wsfe.a_centavos("121") == 12100


def _respuesta_cae(resultado="A", numero=8, cae="76123456789012", obs="", pto=3, tipo=6):
    detalle = (
        f"<FECAEDetResponse><Concepto>1</Concepto><DocTipo>99</DocTipo><DocNro>0</DocNro><CbteDesde>{numero}</CbteDesde>"
        f"<CbteHasta>{numero}</CbteHasta><CbteFch>20261101</CbteFch><Resultado>{resultado}</Resultado>{obs}"
        f"<CAE>{cae if resultado == 'A' else ''}</CAE><CAEFchVto>{'20261111' if resultado == 'A' else ''}</CAEFchVto></FECAEDetResponse>"
    )
    cabecera = (
        f"<FeCabResp><Cuit>{uf.CUIT_EMISOR}</Cuit><PtoVta>{pto}</PtoVta><CbteTipo>{tipo}</CbteTipo><FchProceso>20261101120000</FchProceso>"
        f"<CantReg>1</CantReg><Resultado>{resultado}</Resultado><Reproceso>N</Reproceso></FeCabResp>"
    )
    return _sobre("FECAESolicitar", f"{cabecera}<FeDetResp>{detalle}</FeDetResp>")


class TestFECAESolicitar:
    def test_pedido_con_importes_exactos_y_orden_del_wsdl(self):
        cliente, transporte = _cliente(_respuesta_cae())
        cliente.solicitar_cae(_solicitud())
        cuerpo = transporte.pedidos[0][1]
        assert "<ImpTotal>121.00</ImpTotal>" in cuerpo and "<ImpNeto>100.00</ImpNeto>" in cuerpo and "<ImpIVA>21.00</ImpIVA>" in cuerpo
        assert "<AlicIva><Id>5</Id><BaseImp>100.00</BaseImp><Importe>21.00</Importe></AlicIva>" in cuerpo
        assert "<CondicionIVAReceptorId>5</CondicionIVAReceptorId>" in cuerpo
        orden = ["<Concepto>", "<DocTipo>", "<DocNro>", "<CbteDesde>", "<CbteHasta>", "<CbteFch>", "<ImpTotal>", "<ImpTotConc>",
                 "<ImpNeto>", "<ImpOpEx>", "<ImpTrib>", "<ImpIVA>", "<MonId>", "<MonCotiz>", "<CondicionIVAReceptorId>", "<Iva>"]
        posiciones = [cuerpo.index(etiqueta) for etiqueta in orden]
        assert posiciones == sorted(posiciones)

    def test_comprobante_asociado_va_antes_del_iva(self):
        cliente, transporte = _cliente(_respuesta_cae(tipo=8))
        cliente.solicitar_cae(_solicitud(tipo=8, asociados=(ComprobanteAsociado(6, 3, 7, uf.CUIT_EMISOR, "20261101"),)))
        cuerpo = transporte.pedidos[0][1]
        assert cuerpo.index("<CbtesAsoc>") < cuerpo.index("<Iva>")
        assert "<CbteAsoc><Tipo>6</Tipo><PtoVta>3</PtoVta><Nro>7</Nro>" in cuerpo

    def test_aprobado_con_observaciones(self):
        obs = "<Observaciones><Obs><Code>10217</Code><Msg>Observación informativa</Msg></Obs></Observaciones>"
        cliente, _ = _cliente(_respuesta_cae(obs=obs))
        respuesta = cliente.solicitar_cae(_solicitud())
        assert (respuesta.resultado, respuesta.cae, respuesta.vencimiento) == ("A", "76123456789012", "20261111")
        assert respuesta.observaciones[0].codigo == 10217

    def test_rechazado_sin_cae(self):
        obs = "<Observaciones><Obs><Code>10016</Code><Msg>El numero no se corresponde</Msg></Obs></Observaciones>"
        cliente, _ = _cliente(_respuesta_cae(resultado="R", cae="76123456789012", obs=obs))
        respuesta = cliente.solicitar_cae(_solicitud())
        assert respuesta.resultado == "R" and respuesta.cae is None and respuesta.vencimiento is None

    def test_solo_errores_es_rechazo(self):
        cliente, _ = _cliente(_sobre("FECAESolicitar", "<Errors><Err><Code>10000</Code><Msg>Error</Msg></Err></Errors>"))
        respuesta = cliente.solicitar_cae(_solicitud())
        assert respuesta.resultado == "R" and respuesta.errores[0].codigo == 10000

    def test_respuesta_de_otro_numero_es_invalida(self):
        cliente, _ = _cliente(_respuesta_cae(numero=9))
        with pytest.raises(RespuestaARCAInvalidaError):
            cliente.solicitar_cae(_solicitud(numero=8))

    def test_resultado_desconocido_es_invalido(self):
        cliente, _ = _cliente(_respuesta_cae(resultado="X"))
        with pytest.raises(RespuestaARCAInvalidaError):
            cliente.solicitar_cae(_solicitud())

    def test_soap_fault_http_500(self):
        fault = (
            '<?xml version="1.0"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body><soap:Fault>'
            "<faultcode>soap:Server</faultcode><faultstring>Server was unable to process request.</faultstring></soap:Fault>"
            "</soap:Body></soap:Envelope>"
        )
        cliente, _ = _cliente(RespuestaHTTP(500, fault.encode(), 1))
        with pytest.raises(RespuestaARCAInvalidaError, match="Server"):
            cliente.solicitar_cae(_solicitud())

    def test_pedido_canonico_sin_credenciales(self):
        assert "TKN" not in str(_solicitud().como_dict()) and "Token" not in str(_solicitud().como_dict())


class TestParametros:
    def test_puntos_de_venta(self):
        contenido = (
            "<ResultGet><PtoVenta><Nro>3</Nro><EmisionTipo>CAE</EmisionTipo><Bloqueado>N</Bloqueado><FchBaja>NULL</FchBaja></PtoVenta>"
            "<PtoVenta><Nro>4</Nro><EmisionTipo>CAEA</EmisionTipo><Bloqueado>S</Bloqueado><FchBaja>NULL</FchBaja></PtoVenta></ResultGet>"
        )
        cliente, _ = _cliente(_sobre("FEParamGetPtosVenta", contenido))
        puntos = cliente.puntos_venta()
        assert puntos[0] == PuntoVentaARCA(3, "CAE", False, None) and not puntos[1].habilitado

    def test_sin_puntos_de_venta(self):
        cliente, _ = _cliente(_sobre("FEParamGetPtosVenta", "<Errors><Err><Code>602</Code><Msg>Sin Resultados</Msg></Err></Errors>"))
        assert cliente.puntos_venta() == []

    def test_tabla_de_iva(self):
        contenido = "<ResultGet><IvaTipo><Id>5</Id><Desc>21%</Desc><FchDesde>20090220</FchDesde><FchHasta>NULL</FchHasta></IvaTipo></ResultGet>"
        cliente, transporte = _cliente(_sobre("FEParamGetTiposIva", contenido))
        fila = cliente.parametros("TiposIva")[0]
        assert (fila.id, fila.descripcion) == ("5", "21%") and fila.vigente_en("20261101")
        assert transporte.pedidos[0][0] == NS + "FEParamGetTiposIva"

    def test_condicion_iva_receptor_por_clase(self):
        contenido = "<ResultGet><CondicionIvaReceptor><Id>5</Id><Desc>Consumidor Final</Desc><Cmp_Clase>B</Cmp_Clase></CondicionIvaReceptor></ResultGet>"
        cliente, transporte = _cliente(_sobre("FEParamGetCondicionIvaReceptor", contenido))
        assert cliente.condiciones_iva_receptor("B")[0].clase == "B"
        assert "<ClaseCmp>B</ClaseCmp>" in transporte.pedidos[0][1]


@pytest.mark.usefixtures("base_datos_temporal")
class TestValidarPuntoVenta:
    def test_confirma_y_descarga_tablas(self, arca_falso):
        owner = uf.crear_owner()
        from services.fiscal import servicio_perfil

        servicio_perfil.guardar_perfil(uf.perfil(punto_venta=3), owner)
        assert servicio_conexion.validar_punto_venta(owner) == []
        assert repositorio_fiscal.obtener_perfil().punto_venta_cae_listo
        assert {t.tabla for t in servicio_parametros.tablas("HOMOLOGACION")} >= {"TiposIva", "TiposCbte", "CondicionIvaReceptor_B"}

    def test_inexistente_no_se_valida(self, arca_falso):
        owner = uf.crear_owner()
        from services.fiscal import servicio_perfil

        servicio_perfil.guardar_perfil(uf.perfil(punto_venta=99), owner)
        with pytest.raises(ConfiguracionFiscalError, match="no tiene"):
            servicio_conexion.validar_punto_venta(owner)
        assert not repositorio_fiscal.obtener_perfil().punto_venta_cae_listo

    def test_modalidad_equivocada(self, arca_falso):
        owner = uf.crear_owner()
        from services.fiscal import servicio_perfil

        servicio_perfil.guardar_perfil(uf.perfil(punto_venta=4), owner)  # el 4 es CAEA en el ARCA falso
        with pytest.raises(ConfiguracionFiscalError, match="CAEA"):
            servicio_conexion.validar_punto_venta(owner)

    def test_bloqueado(self, arca_falso):
        owner = uf.crear_owner()
        from services.fiscal import servicio_perfil

        arca_falso.puntos = [PuntoVentaARCA(3, "CAE", True, None)]
        servicio_perfil.guardar_perfil(uf.perfil(punto_venta=3), owner)
        with pytest.raises(ConfiguracionFiscalError, match="bloqueado"):
            servicio_conexion.validar_punto_venta(owner)

    def test_cashier_no_valida(self, arca_falso):
        uf.guardar_perfil_validado()
        with pytest.raises(PermisoDenegadoError):
            servicio_conexion.validar_punto_venta(uf.crear_cashier())

    def test_ultimos_autorizados(self, arca_falso):
        uf.guardar_perfil_validado()
        arca_falso.emitir_externo(3, 6)
        ultimos = servicio_conexion.ultimos_autorizados(uf.crear_owner())
        assert ultimos["Factura B"] == 1 and ultimos["Factura A"] == 0


@pytest.mark.usefixtures("base_datos_temporal")
class TestValidarCodigos:
    def _importes(self, alicuota=5):
        return ImportesFiscales(12100, 0, 10000, 0, 0, 2100, (AlicuotaFiscal(alicuota, 10000, 2100),))

    def test_sin_tablas_bloquea(self):
        with pytest.raises(FiscalizacionBloqueadaError) as error:
            servicio_parametros.validar_codigos("HOMOLOGACION", "B", 6, 99, 5, self._importes(), date.today())
        assert any("Falta la tabla" in m for m in error.value.motivos)

    def test_con_tablas_valida(self, arca_falso):
        servicio_parametros.refrescar(arca_falso)
        servicio_parametros.validar_codigos("HOMOLOGACION", "B", 6, 99, 5, self._importes(), date.today())

    def test_condicion_no_habilitada_para_la_clase(self, arca_falso):
        servicio_parametros.refrescar(arca_falso)
        with pytest.raises(FiscalizacionBloqueadaError, match="parámetros"):
            servicio_parametros.validar_codigos("HOMOLOGACION", "A", 1, 80, 5, self._importes(), date.today())

    def test_refresco_fallido_conserva_la_copia(self, arca_falso):
        servicio_parametros.refrescar(arca_falso)
        arca_falso.caido = True
        assert len(servicio_parametros.refrescar(arca_falso)) == 10
        servicio_parametros.validar_codigos("HOMOLOGACION", "B", 6, 99, 5, self._importes(), date.today())
