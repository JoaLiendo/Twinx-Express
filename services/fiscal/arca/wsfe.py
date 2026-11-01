"""Cliente WSFEv1 (namespace `http://ar.gov.afip.dif.FEV1/`): solo las operaciones que Twinx usa.

Contrastado con el WSDL vigente de homologación (01/11/2026). Los importes salen de centavos enteros y se
serializan con exactamente dos decimales (`Decimal`, nunca `float`); los que vuelven se convierten a
centavos exigiendo que no tengan fracción de centavo.
"""

import logging
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

from domain.dinero import centavos_a_texto
from domain.fiscal.protocolo import (
    CAEAObtenido,
    ComprobanteConsultado,
    EstadoServidores,
    MensajeARCA,
    ParametroARCA,
    PuntoVentaARCA,
    RespuestaAutorizacion,
    RespuestaInforme,
    SolicitudAutorizacion,
)
from excepciones import ErrorWSAA, RespuestaARCAInvalidaError
from services.fiscal.arca import soap
from services.fiscal.arca.transporte import Transporte
from services.fiscal.arca.wsaa import TicketAcceso

logger = logging.getLogger(__name__)

NS_WSFE = "http://ar.gov.afip.dif.FEV1/"

# Código de "sin resultados" de las consultas WSFEv1 (FECompConsultar, FEParamGetPtosVenta...).
ERROR_SIN_RESULTADOS = 602
# Errores de credenciales: ARCA no procesó nada (el Ticket no validó o no incluye al CUIT).
ERRORES_DE_CREDENCIALES = frozenset({600, 601})

TABLAS_PARAMETROS = {
    "TiposCbte": "CbteTipo",
    "TiposConcepto": "ConceptoTipo",
    "TiposDoc": "DocTipo",
    "TiposIva": "IvaTipo",
    "TiposMonedas": "Moneda",
    "TiposOpcional": "OpcionalTipo",
    "TiposTributos": "TributoTipo",
}


class ErrorWSFE(RespuestaARCAInvalidaError):
    """WSFEv1 devolvió `Errors` en una operación que no es una autorización."""

    def __init__(self, operacion: str, errores: tuple[MensajeARCA, ...]) -> None:
        detalle = "; ".join(f"{e.codigo}: {e.mensaje}" for e in errores)[:400]
        super().__init__(f"ARCA rechazó {operacion}: {detalle}")
        self.errores = errores


def importe(centavos: int) -> str:
    return centavos_a_texto(centavos)


def a_centavos(texto: str | None) -> int:
    try:
        valor = Decimal(texto or "0") * 100
    except InvalidOperation as error:
        raise RespuestaARCAInvalidaError("ARCA devolvió un importe inválido.") from error
    if valor != valor.to_integral_value():
        raise RespuestaARCAInvalidaError("ARCA devolvió un importe con fracción de centavo.")
    return int(valor)


def _mensajes(contenedor, nombre_lista: str, nombre_item: str) -> tuple[MensajeARCA, ...]:
    resultado = []
    for nodo in soap.hijos(soap.hijo(contenedor, nombre_lista), nombre_item):
        try:
            codigo = int(soap.valor(nodo, "Code") or "0")
        except ValueError:
            codigo = 0
        resultado.append(MensajeARCA(codigo, (soap.valor(nodo, "Msg") or "")[:500]))
    return tuple(resultado)


def _entero(texto: str | None, campo: str) -> int:
    try:
        return int(texto or "")
    except ValueError as error:
        raise RespuestaARCAInvalidaError(f"ARCA devolvió un {campo} inválido.") from error


def xml_solicitud(solicitud: SolicitudAutorizacion, caea: bool = False) -> str:
    """`FeCabReq` + `FeDetReq` de una solicitud (sin credenciales)."""
    imp = solicitud.importes
    partes = [
        f"<FeCabReq><CantReg>1</CantReg><PtoVta>{solicitud.punto_venta}</PtoVta><CbteTipo>{solicitud.tipo}</CbteTipo></FeCabReq>",
        "<FeDetReq>",
        "<FECAEADetRequest>" if caea else "<FECAEDetRequest>",
        f"<Concepto>{solicitud.concepto}</Concepto><DocTipo>{solicitud.doc_tipo}</DocTipo>"
        f"<DocNro>{soap.texto(solicitud.doc_nro)}</DocNro><CbteDesde>{solicitud.numero}</CbteDesde>"
        f"<CbteHasta>{solicitud.numero}</CbteHasta><CbteFch>{soap.texto(solicitud.fecha)}</CbteFch>"
        f"<ImpTotal>{importe(imp.total_centavos)}</ImpTotal><ImpTotConc>{importe(imp.no_gravado_centavos)}</ImpTotConc>"
        f"<ImpNeto>{importe(imp.neto_centavos)}</ImpNeto><ImpOpEx>{importe(imp.exento_centavos)}</ImpOpEx>"
        f"<ImpTrib>{importe(imp.tributos_centavos)}</ImpTrib><ImpIVA>{importe(imp.iva_centavos)}</ImpIVA>"
        f"<MonId>{soap.texto(solicitud.moneda)}</MonId><MonCotiz>{soap.texto(solicitud.cotizacion)}</MonCotiz>"
        f"<CondicionIVAReceptorId>{solicitud.condicion_iva_receptor_id}</CondicionIVAReceptorId>",
    ]
    if solicitud.asociados:
        partes.append("<CbtesAsoc>")
        for asociado in solicitud.asociados:
            partes.append(
                f"<CbteAsoc><Tipo>{asociado.tipo}</Tipo><PtoVta>{asociado.punto_venta}</PtoVta><Nro>{asociado.numero}</Nro>"
                f"<Cuit>{soap.texto(asociado.cuit)}</Cuit><CbteFch>{soap.texto(asociado.fecha)}</CbteFch></CbteAsoc>"
            )
        partes.append("</CbtesAsoc>")
    if imp.alicuotas:
        partes.append("<Iva>")
        for alicuota in imp.alicuotas:
            partes.append(
                f"<AlicIva><Id>{alicuota.alicuota_iva_id}</Id><BaseImp>{importe(alicuota.base_imponible_centavos)}</BaseImp>"
                f"<Importe>{importe(alicuota.importe_centavos)}</Importe></AlicIva>"
            )
        partes.append("</Iva>")
    if caea:
        partes.append(
            f"<CAEA>{soap.texto(solicitud.caea)}</CAEA><CbteFchHsGen>{soap.texto(solicitud.fecha_hora_generacion)}</CbteFchHsGen>"
        )
    partes.append("</FECAEADetRequest>" if caea else "</FECAEDetRequest>")
    partes.append("</FeDetReq>")
    return "".join(partes)


class ClienteWSFE:
    """Operaciones WSFEv1. `obtener_ticket` entrega un TA vigente (de `ClienteWSAA`)."""

    def __init__(self, transporte: Transporte, url: str, cuit: str, obtener_ticket: Callable[[], TicketAcceso]) -> None:
        self._transporte = transporte
        self._url = url
        self._cuit = cuit
        self._obtener_ticket = obtener_ticket

    def _auth(self) -> str:
        ticket = self._obtener_ticket()
        return f"<Auth><Token>{soap.texto(ticket.token)}</Token><Sign>{soap.texto(ticket.sign)}</Sign><Cuit>{self._cuit}</Cuit></Auth>"

    def _llamar(self, operacion: str, contenido: str = "", con_auth: bool = True):
        cuerpo = soap.sobre(
            f'<{operacion} xmlns="{NS_WSFE}">{self._auth() if con_auth else ""}{contenido}</{operacion}>'
        )
        respuesta = self._transporte.enviar(self._url, cuerpo, NS_WSFE + operacion)
        logger.info("WSFEv1 %s respondió en %s ms", operacion, respuesta.duracion_ms)
        resultado = soap.hijo(soap.cuerpo_de_respuesta(respuesta), f"{operacion}Result")
        if resultado is None:
            raise RespuestaARCAInvalidaError(f"ARCA no devolvió {operacion}Result.")
        return resultado

    @staticmethod
    def _exigir_sin_errores(operacion: str, resultado) -> None:
        errores = _mensajes(resultado, "Errors", "Err")
        if errores:
            if any(e.codigo in ERRORES_DE_CREDENCIALES for e in errores):
                raise ErrorWSAA(f"ARCA no validó las credenciales en {operacion}: {errores[0].mensaje[:200]}")
            raise ErrorWSFE(operacion, errores)

    def dummy(self) -> EstadoServidores:
        resultado = self._llamar("FEDummy", con_auth=False)
        return EstadoServidores(
            soap.valor(resultado, "AppServer") or "?",
            soap.valor(resultado, "DbServer") or "?",
            soap.valor(resultado, "AuthServer") or "?",
        )

    def ultimo_autorizado(self, punto_venta: int, tipo: int) -> int:
        resultado = self._llamar("FECompUltimoAutorizado", f"<PtoVta>{punto_venta}</PtoVta><CbteTipo>{tipo}</CbteTipo>")
        self._exigir_sin_errores("FECompUltimoAutorizado", resultado)
        if _entero(soap.valor(resultado, "PtoVta"), "punto de venta") != punto_venta or _entero(
            soap.valor(resultado, "CbteTipo"), "tipo"
        ) != tipo:
            raise RespuestaARCAInvalidaError("ARCA respondió el último autorizado de otra secuencia.")
        return _entero(soap.valor(resultado, "CbteNro"), "número de comprobante")

    def consultar(self, punto_venta: int, tipo: int, numero: int) -> ComprobanteConsultado | None:
        """`None` solo si ARCA dice explícitamente que no existe (error 602)."""
        resultado = self._llamar(
            "FECompConsultar",
            f"<FeCompConsReq><CbteTipo>{tipo}</CbteTipo><CbteNro>{numero}</CbteNro><PtoVta>{punto_venta}</PtoVta></FeCompConsReq>",
        )
        errores = _mensajes(resultado, "Errors", "Err")
        if errores and all(e.codigo == ERROR_SIN_RESULTADOS for e in errores):
            return None
        self._exigir_sin_errores("FECompConsultar", resultado)
        datos = soap.hijo(resultado, "ResultGet")
        if datos is None:
            raise RespuestaARCAInvalidaError("FECompConsultar no devolvió datos.")
        return ComprobanteConsultado(
            tipo=_entero(soap.valor(datos, "CbteTipo"), "tipo"),
            punto_venta=_entero(soap.valor(datos, "PtoVta"), "punto de venta"),
            numero=_entero(soap.valor(datos, "CbteDesde"), "número"),
            fecha=soap.valor(datos, "CbteFch") or "",
            doc_tipo=_entero(soap.valor(datos, "DocTipo"), "tipo de documento"),
            doc_nro=soap.valor(datos, "DocNro") or "",
            total_centavos=a_centavos(soap.valor(datos, "ImpTotal")),
            codigo_autorizacion=soap.valor(datos, "CodAutorizacion"),
            emision_tipo=soap.valor(datos, "EmisionTipo"),
            vencimiento=soap.valor(datos, "FchVto"),
            resultado=soap.valor(datos, "Resultado"),
            fecha_proceso=soap.valor(datos, "FchProceso"),
            observaciones=_mensajes(datos, "Observaciones", "Obs"),
        )

    def solicitar_cae(self, solicitud: SolicitudAutorizacion) -> RespuestaAutorizacion:
        """Una sola solicitud. Cualquier falla después de enviarla la decide quien llama (INCIERTO)."""
        resultado = self._llamar("FECAESolicitar", f"<FeCAEReq>{xml_solicitud(solicitud)}</FeCAEReq>")
        errores = _mensajes(resultado, "Errors", "Err")
        eventos = _mensajes(resultado, "Events", "Evt")
        cabecera = soap.hijo(resultado, "FeCabResp")
        detalle = soap.hijo(soap.hijo(resultado, "FeDetResp"), "FECAEDetResponse")
        if cabecera is None or detalle is None:
            if errores:
                if any(e.codigo in ERRORES_DE_CREDENCIALES for e in errores):
                    raise ErrorWSAA(f"ARCA no validó las credenciales: {errores[0].mensaje[:200]}")
                return RespuestaAutorizacion("R", None, None, None, None, None, errores=errores, eventos=eventos)
            raise RespuestaARCAInvalidaError("FECAESolicitar no devolvió cabecera ni detalle.")
        if _entero(soap.valor(cabecera, "PtoVta"), "punto de venta") != solicitud.punto_venta or _entero(
            soap.valor(cabecera, "CbteTipo"), "tipo"
        ) != solicitud.tipo:
            raise RespuestaARCAInvalidaError("ARCA respondió por otro punto de venta o tipo de comprobante.")
        resultado_det = soap.valor(detalle, "Resultado")
        if resultado_det not in ("A", "R"):
            raise RespuestaARCAInvalidaError(f"Resultado de autorización desconocido: {resultado_det!r}.")
        numero = _entero(soap.valor(detalle, "CbteDesde"), "número")
        if numero != solicitud.numero:
            raise RespuestaARCAInvalidaError("ARCA respondió por otro número de comprobante.")
        cae = soap.valor(detalle, "CAE")
        return RespuestaAutorizacion(
            resultado=resultado_det,
            numero=numero,
            fecha_comprobante=soap.valor(detalle, "CbteFch"),
            cae=cae if resultado_det == "A" else None,
            vencimiento=soap.valor(detalle, "CAEFchVto") if resultado_det == "A" else None,
            fecha_proceso=soap.valor(cabecera, "FchProceso"),
            reproceso=soap.valor(cabecera, "Reproceso"),
            observaciones=_mensajes(detalle, "Observaciones", "Obs"),
            errores=errores,
            eventos=eventos,
        )

    def puntos_venta(self) -> list[PuntoVentaARCA]:
        resultado = self._llamar("FEParamGetPtosVenta")
        errores = _mensajes(resultado, "Errors", "Err")
        if errores and all(e.codigo == ERROR_SIN_RESULTADOS for e in errores):
            return []
        self._exigir_sin_errores("FEParamGetPtosVenta", resultado)
        return [
            PuntoVentaARCA(
                numero=_entero(soap.valor(nodo, "Nro"), "punto de venta"),
                emision_tipo=(soap.valor(nodo, "EmisionTipo") or "").strip(),
                bloqueado=(soap.valor(nodo, "Bloqueado") or "N").upper() == "S",
                fecha_baja=None if (soap.valor(nodo, "FchBaja") or "NULL").upper() == "NULL" else soap.valor(nodo, "FchBaja"),
            )
            for nodo in soap.hijos(soap.hijo(resultado, "ResultGet"), "PtoVenta")
        ]

    def parametros(self, tabla: str) -> list[ParametroARCA]:
        item = TABLAS_PARAMETROS[tabla]
        operacion = f"FEParamGet{tabla}"
        resultado = self._llamar(operacion)
        self._exigir_sin_errores(operacion, resultado)
        return [
            ParametroARCA(
                id=soap.valor(nodo, "Id") or "",
                descripcion=soap.valor(nodo, "Desc") or "",
                desde=soap.valor(nodo, "FchDesde"),
                hasta=soap.valor(nodo, "FchHasta"),
            )
            for nodo in soap.hijos(soap.hijo(resultado, "ResultGet"), item)
        ]

    def condiciones_iva_receptor(self, clase: str) -> list[ParametroARCA]:
        resultado = self._llamar("FEParamGetCondicionIvaReceptor", f"<ClaseCmp>{soap.texto(clase)}</ClaseCmp>")
        self._exigir_sin_errores("FEParamGetCondicionIvaReceptor", resultado)
        return [
            ParametroARCA(
                id=soap.valor(nodo, "Id") or "",
                descripcion=soap.valor(nodo, "Desc") or "",
                clase=soap.valor(nodo, "Cmp_Clase") or clase,
            )
            for nodo in soap.hijos(soap.hijo(resultado, "ResultGet"), "CondicionIvaReceptor")
        ]

    def cotizacion(self, moneda: str) -> Decimal:
        resultado = self._llamar("FEParamGetCotizacion", f"<MonId>{soap.texto(moneda)}</MonId>")
        self._exigir_sin_errores("FEParamGetCotizacion", resultado)
        try:
            return Decimal(soap.valor(soap.hijo(resultado, "ResultGet"), "MonCotiz") or "")
        except InvalidOperation as error:
            raise RespuestaARCAInvalidaError("ARCA devolvió una cotización inválida.") from error

    # --- CAEA (métodos verificados en el WSDL vigente) ---------------------------------------------

    @staticmethod
    def _caea_de(resultado) -> CAEAObtenido | None:
        datos = soap.hijo(resultado, "ResultGet")
        codigo = soap.valor(datos, "CAEA")
        if datos is None or not codigo:
            return None
        return CAEAObtenido(
            codigo=codigo,
            periodo=_entero(soap.valor(datos, "Periodo"), "período"),
            orden=_entero(soap.valor(datos, "Orden"), "orden"),
            vigente_desde=soap.valor(datos, "FchVigDesde") or "",
            vigente_hasta=soap.valor(datos, "FchVigHasta") or "",
            tope_informe=soap.valor(datos, "FchTopeInf") or "",
            fecha_proceso=soap.valor(datos, "FchProceso"),
            observaciones=_mensajes(datos, "Observaciones", "Obs"),
        )

    def solicitar_caea(self, periodo: int, orden: int) -> CAEAObtenido:
        resultado = self._llamar("FECAEASolicitar", f"<Periodo>{periodo}</Periodo><Orden>{orden}</Orden>")
        caea = self._caea_de(resultado)
        if caea is None:
            self._exigir_sin_errores("FECAEASolicitar", resultado)
            raise RespuestaARCAInvalidaError("FECAEASolicitar no devolvió un CAEA.")
        return caea

    def consultar_caea(self, periodo: int, orden: int) -> CAEAObtenido | None:
        resultado = self._llamar("FECAEAConsultar", f"<Periodo>{periodo}</Periodo><Orden>{orden}</Orden>")
        caea = self._caea_de(resultado)
        if caea is None:
            errores = _mensajes(resultado, "Errors", "Err")
            if any(e.codigo in ERRORES_DE_CREDENCIALES for e in errores):
                self._exigir_sin_errores("FECAEAConsultar", resultado)
            return None
        return caea

    def informar_caea(self, solicitud: SolicitudAutorizacion) -> RespuestaInforme:
        resultado = self._llamar("FECAEARegInformativo", f"<FeCAEARegInfReq>{xml_solicitud(solicitud, caea=True)}</FeCAEARegInfReq>")
        errores = _mensajes(resultado, "Errors", "Err")
        detalle = soap.hijo(soap.hijo(resultado, "FeDetResp"), "FECAEADetResponse")
        if detalle is None:
            if errores:
                self._exigir_sin_errores_de_credenciales(errores)
                return RespuestaInforme("R", errores=errores)
            raise RespuestaARCAInvalidaError("FECAEARegInformativo no devolvió detalle.")
        estado = soap.valor(detalle, "Resultado")
        if estado not in ("A", "R"):
            raise RespuestaARCAInvalidaError(f"Resultado de informe CAEA desconocido: {estado!r}.")
        return RespuestaInforme(
            estado,
            soap.valor(soap.hijo(resultado, "FeCabResp"), "FchProceso"),
            _mensajes(detalle, "Observaciones", "Obs"),
            errores,
        )

    def informar_sin_movimiento(self, punto_venta: int, caea: str) -> RespuestaInforme:
        resultado = self._llamar("FECAEASinMovimientoInformar", f"<PtoVta>{punto_venta}</PtoVta><CAEA>{soap.texto(caea)}</CAEA>")
        errores = _mensajes(resultado, "Errors", "Err")
        self._exigir_sin_errores_de_credenciales(errores)
        estado = soap.valor(resultado, "Resultado") or ("R" if errores else "")
        if estado not in ("A", "R"):
            raise RespuestaARCAInvalidaError(f"Resultado de informe sin movimiento desconocido: {estado!r}.")
        return RespuestaInforme(estado, soap.valor(resultado, "FchProceso"), errores=errores)

    def consultar_sin_movimiento(self, punto_venta: int, caea: str) -> bool:
        resultado = self._llamar("FECAEASinMovimientoConsultar", f"<CAEA>{soap.texto(caea)}</CAEA><PtoVta>{punto_venta}</PtoVta>")
        return bool(soap.hijos(soap.hijo(resultado, "ResultGet"), "FECAEASinMov"))

    @staticmethod
    def _exigir_sin_errores_de_credenciales(errores: tuple[MensajeARCA, ...]) -> None:
        if any(e.codigo in ERRORES_DE_CREDENCIALES for e in errores):
            raise ErrorWSAA(f"ARCA no validó las credenciales: {errores[0].mensaje[:200]}")
