"""V1.16-B: credenciales fiscales (DPAPI, fuera de la base) y cliente WSAA contra un WSAA falso.

La firma CMS se verifica de forma independiente (parser DER mínimo + verificación RSA de los atributos
firmados): prueba que lo que Twinx envía a WSAA es un SignedData válido con el TRA adjunto.
"""

import base64
import hashlib
import json
import socket
import ssl
import threading
from datetime import UTC, datetime, timedelta
from xml.sax.saxutils import escape

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

import config
from db.repositorios import auditoria as repositorio_auditoria
from excepciones import (
    CredencialesFiscalesError,
    ErrorComunicacionARCA,
    ErrorWSAA,
    ProduccionFiscalNoHabilitadaError,
    RespuestaARCAInvalidaError,
)
from services.fiscal import credenciales, dpapi
from services.fiscal.arca import endpoints, wsaa
from services.fiscal.arca.transporte import RespuestaHTTP, TransporteHTTPS
from tests import certificados_prueba as cp

AHORA = datetime.now(UTC)


def _importar(ambiente="HOMOLOGACION", homologacion=True, **kwargs):
    cert = cp.certificado(homologacion=homologacion, **kwargs)
    return credenciales.importar(ambiente, cp.pem(cert), cp.clave_pem(), None, cp.CUIT, None)


# --- DER mínimo para verificar el CMS ------------------------------------------------------------

def _leer(datos: bytes, pos: int) -> tuple[int, int, int, int]:
    """(tag, inicio_contenido, largo, fin) del TLV en `pos`."""
    tag = datos[pos]
    largo = datos[pos + 1]
    inicio = pos + 2
    if largo & 0x80:
        n = largo & 0x7F
        largo = int.from_bytes(datos[inicio:inicio + n], "big")
        inicio += n
    return tag, inicio, largo, inicio + largo


def _hijos(datos: bytes, inicio: int, fin: int) -> list[tuple[int, int, int, int]]:
    resultado, pos = [], inicio
    while pos < fin:
        tlv = _leer(datos, pos)
        resultado.append((pos, *tlv))
        pos = tlv[3]
    return resultado


def _verificar_cms(cms_b64: str, certificado) -> bytes:
    """Devuelve el contenido adjunto si la firma es válida; falla el test si no."""
    der = base64.b64decode(cms_b64)
    _, ini, _, fin = _leer(der, 0)
    content_info = _hijos(der, ini, fin)
    _, _, ini0, _, fin0 = content_info[1]  # [0] EXPLICIT
    _, _, ini_sd, _, fin_sd = _hijos(der, ini0, fin0)[0]
    signed_data = _hijos(der, ini_sd, fin_sd)
    encap = signed_data[2]
    _oid, contenido_explicito = _hijos(der, encap[2], encap[4])
    assert contenido_explicito[1] == 0xA0, "el contenido debe ir adjunto (no detached)"
    _, _, ini_oct, largo_oct, _ = _hijos(der, contenido_explicito[2], contenido_explicito[4])[0]
    contenido = der[ini_oct:ini_oct + largo_oct]
    signer_infos = signed_data[-1]
    signer_info = _hijos(der, signer_infos[2], signer_infos[4])[0]
    campos = _hijos(der, signer_info[2], signer_info[4])
    atributos = next(c for c in campos if c[1] == 0xA0)
    firma = campos[-1]
    atributos_der = b"\x31" + der[atributos[0] + 1:atributos[4]]
    certificado.public_key().verify(der[firma[2]:firma[4]], atributos_der, padding.PKCS1v15(), hashes.SHA256())
    assert hashlib.sha256(contenido).digest() in der[atributos[2]:atributos[4]], "messageDigest no coincide"
    return contenido


# --- WSAA falso -------------------------------------------------------------------------------------

def _respuesta_login(token="TOKEN-SECRETO", sign="SIGN-SECRETO", horas=12, generacion=None):
    generacion = generacion or datetime.now(UTC)
    ticket = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><loginTicketResponse version="1.0"><header>'
        "<source>CN=wsaahomo</source><destination>SERIALNUMBER=CUIT 20111111112, CN=twinx</destination>"
        f"<uniqueId>1</uniqueId><generationTime>{generacion.isoformat()}</generationTime>"
        f"<expirationTime>{(generacion + timedelta(hours=horas)).isoformat()}</expirationTime></header>"
        f"<credentials><token>{token}</token><sign>{sign}</sign></credentials></loginTicketResponse>"
    )
    cuerpo = (
        '<?xml version="1.0" encoding="utf-8"?><soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
        '<soapenv:Body><loginCmsResponse xmlns="http://wsaa.view.sua.dvadac.desein.afip.gov">'
        f"<loginCmsReturn>{escape(ticket)}</loginCmsReturn></loginCmsResponse></soapenv:Body></soapenv:Envelope>"
    )
    return RespuestaHTTP(200, cuerpo.encode(), 5)


def _fault(codigo: str, mensaje: str = "detalle") -> RespuestaHTTP:
    cuerpo = (
        '<?xml version="1.0"?><soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"><soapenv:Body>'
        f"<soapenv:Fault><faultcode xmlns:ns1=\"http://xml.apache.org/axis/\">ns1:{codigo}</faultcode>"
        f"<faultstring>{mensaje}</faultstring></soapenv:Fault></soapenv:Body></soapenv:Envelope>"
    )
    return RespuestaHTTP(500, cuerpo.encode(), 5)


class TransporteFalso:
    def __init__(self, *respuestas):
        self.respuestas = list(respuestas)
        self.pedidos: list[bytes] = []

    def enviar(self, url, cuerpo, soap_action):
        self.pedidos.append(cuerpo)
        respuesta = self.respuestas.pop(0)
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta


def _cliente(transporte, persistir=False, reloj=None, ambiente="HOMOLOGACION"):
    return wsaa.ClienteWSAA(
        transporte, endpoints.ENDPOINTS[ambiente].wsaa, ambiente, wsaa.CacheTickets(persistir=persistir),
        reloj=reloj or (lambda: datetime.now(UTC)),
    )


@pytest.mark.usefixtures("base_datos_temporal")
class TestCredenciales:
    def test_importa_y_la_clave_queda_cifrada_fuera_de_data(self, fiscal_aislado):
        info = _importar()
        assert info.estado == credenciales.VIGENTE and info.cuit == cp.CUIT
        archivo = fiscal_aislado / "homologacion" / credenciales.NOMBRE_CLAVE
        cifrado = archivo.read_bytes()
        der = cp.clave().private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        assert der not in cifrado and b"PRIVATE KEY" not in cifrado
        assert config.DIRECTORIO_DATA not in archivo.parents

    def test_auditoria_sin_secretos(self):
        _importar()
        resumen = repositorio_auditoria.listar(accion="CERTIFICADO_FISCAL_CAMBIADO")[0].resumen
        assert "PRIVATE" not in resumen and "vence" in resumen

    def test_clave_que_no_corresponde(self):
        cert = cp.certificado()
        with pytest.raises(CredencialesFiscalesError, match="no corresponde"):
            credenciales.importar("HOMOLOGACION", cp.pem(cert), cp.clave_pem(1), None, cp.CUIT, None)

    def test_certificado_vencido(self):
        with pytest.raises(CredencialesFiscalesError, match="vencido"):
            _importar(desde=AHORA - timedelta(days=400), hasta=AHORA - timedelta(days=1))

    def test_ambiente_equivocado_en_ambos_sentidos(self):
        with pytest.raises(CredencialesFiscalesError, match="no se mezclan"):
            _importar(ambiente="HOMOLOGACION", homologacion=False)
        with pytest.raises(CredencialesFiscalesError, match="no se mezclan"):
            _importar(ambiente="PRODUCCION", homologacion=True)

    def test_cuit_distinto_del_emisor(self):
        with pytest.raises(CredencialesFiscalesError, match="otro CUIT"):
            _importar(cuit="30712345671")

    def test_clave_con_password(self):
        cert = cp.pem(cp.certificado())
        with pytest.raises(CredencialesFiscalesError, match="contraseña"):
            credenciales.importar("HOMOLOGACION", cert, cp.clave_pem(password="s3creta"), None, cp.CUIT, None)
        with pytest.raises(CredencialesFiscalesError, match="contraseña"):
            credenciales.importar("HOMOLOGACION", cert, cp.clave_pem(password="s3creta"), "otra", cp.CUIT, None)
        credenciales.importar("HOMOLOGACION", cert, cp.clave_pem(password="s3creta"), "s3creta", cp.CUIT, None)

    def test_archivos_basura(self):
        with pytest.raises(CredencialesFiscalesError):
            credenciales.importar("HOMOLOGACION", b"no soy un certificado", cp.clave_pem(), None, None, None)
        with pytest.raises(CredencialesFiscalesError):
            credenciales.importar("HOMOLOGACION", cp.pem(cp.certificado()), b"-----BEGIN basura", None, None, None)

    def test_estados_para_el_panel(self):
        assert credenciales.estado("HOMOLOGACION").estado == credenciales.AUSENTE
        _importar(hasta=AHORA + timedelta(days=10))
        assert credenciales.estado("HOMOLOGACION").estado == credenciales.POR_VENCER
        assert credenciales.estado("HOMOLOGACION", AHORA + timedelta(days=20)).estado == credenciales.VENCIDO

    def test_material_vencido_no_firma(self):
        _importar()
        with pytest.raises(CredencialesFiscalesError, match="vencido"):
            credenciales.cargar_material_firma("HOMOLOGACION", AHORA + timedelta(days=500))

    def test_dpapi_ida_y_vuelta_y_blob_ajeno(self):
        assert dpapi.desproteger(dpapi.proteger(b"hola")) == b"hola"
        with pytest.raises(CredencialesFiscalesError):
            dpapi.desproteger(b"no es un blob dpapi")


@pytest.mark.usefixtures("base_datos_temporal")
class TestWSAA:
    def test_cms_valido_con_tra_adjunto(self):
        _importar()
        material = credenciales.cargar_material_firma("HOMOLOGACION")
        tra = wsaa.construir_tra("wsfe", AHORA, 42)
        contenido = _verificar_cms(wsaa.firmar_tra(tra, material), material.certificado)
        assert contenido == tra
        assert b"<service>wsfe</service>" in contenido and b"<uniqueId>42</uniqueId>" in contenido

    def test_tra_con_horas_con_zona_y_margen(self):
        tra = wsaa.construir_tra("wsfe", AHORA, 1).decode()
        generacion = datetime.fromisoformat(tra.split("<generationTime>")[1].split("<")[0])
        expiracion = datetime.fromisoformat(tra.split("<expirationTime>")[1].split("<")[0])
        assert generacion.tzinfo is not None and generacion < AHORA < expiracion

    def test_ticket_valido_y_reutilizado(self):
        _importar()
        transporte = TransporteFalso(_respuesta_login())
        cliente = _cliente(transporte)
        ticket = cliente.ticket()
        assert ticket.token == "TOKEN-SECRETO" and "TOKEN" not in repr(ticket)
        assert cliente.ticket() is ticket
        assert len(transporte.pedidos) == 1

    def test_ticket_por_vencer_se_renueva(self):
        _importar()
        reloj = [datetime.now(UTC)]
        transporte = TransporteFalso(_respuesta_login(horas=1), _respuesta_login(token="OTRO"))
        cliente = _cliente(transporte, reloj=lambda: reloj[0])
        cliente.ticket()
        reloj[0] += timedelta(minutes=55)
        assert cliente.ticket().token == "OTRO"

    def test_ticket_ya_vencido_se_rechaza(self):
        _importar()
        transporte = TransporteFalso(_respuesta_login(horas=1, generacion=datetime.now(UTC) - timedelta(hours=2)))
        with pytest.raises(RespuestaARCAInvalidaError, match="vencido"):
            _cliente(transporte).ticket()

    def test_reinicio_reutiliza_la_copia_dpapi(self, fiscal_aislado):
        _importar()
        _cliente(TransporteFalso(_respuesta_login()), persistir=True).ticket()
        copia = (fiscal_aislado / "homologacion" / "ta_wsfe.dpapi").read_bytes()
        assert b"TOKEN-SECRETO" not in copia and b"SIGN-SECRETO" not in copia
        sin_red = TransporteFalso()
        assert _cliente(sin_red, persistir=True).ticket().token == "TOKEN-SECRETO"
        assert sin_red.pedidos == []

    def test_cambio_de_certificado_invalida_la_copia(self):
        _importar()
        _cliente(TransporteFalso(_respuesta_login()), persistir=True).ticket()
        credenciales.importar("HOMOLOGACION", cp.pem(cp.certificado(indice_clave=2)), cp.clave_pem(2), None, cp.CUIT, None)
        transporte = TransporteFalso(_respuesta_login(token="NUEVO"))
        assert _cliente(transporte, persistir=True).ticket().token == "NUEVO"

    @pytest.mark.parametrize(
        "codigo, esperado",
        [("coe.alreadyAuthenticated", "ya entregó"), ("cms.cert.expired", "vencido"), ("cms.cert.untrusted", "otro ambiente"),
         ("xml.bad", "rechazó")],
    )
    def test_soap_fault(self, codigo, esperado):
        _importar()
        with pytest.raises(ErrorWSAA, match=esperado):
            _cliente(TransporteFalso(_fault(codigo))).ticket()

    def test_xml_malformado_y_http_500(self):
        _importar()
        with pytest.raises(RespuestaARCAInvalidaError, match="malformado"):
            _cliente(TransporteFalso(RespuestaHTTP(200, b"<soap:Envelope", 1))).ticket()
        with pytest.raises(RespuestaARCAInvalidaError, match="500"):
            _cliente(TransporteFalso(RespuestaHTTP(500, b"<html>error</html>", 1))).ticket()

    def test_dtd_rechazada(self):
        _importar()
        malicioso = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>'
        with pytest.raises(RespuestaARCAInvalidaError, match="DTD"):
            _cliente(TransporteFalso(RespuestaHTTP(200, malicioso, 1))).ticket()

    def test_timeout_se_propaga(self):
        _importar()
        with pytest.raises(ErrorComunicacionARCA):
            _cliente(TransporteFalso(ErrorComunicacionARCA("sin respuesta", enviado=True))).ticket()

    def test_sin_certificado_de_produccion_no_hay_ticket_de_produccion(self):
        _importar()  # solo homologación
        with pytest.raises(CredencialesFiscalesError):
            _cliente(TransporteFalso(_respuesta_login()), ambiente="PRODUCCION").ticket()

    def test_ticket_no_aparece_en_logs(self, caplog):
        _importar()
        with caplog.at_level("DEBUG"):
            _cliente(TransporteFalso(_respuesta_login())).ticket()
        assert "TOKEN-SECRETO" not in caplog.text and "SIGN-SECRETO" not in caplog.text


# --- Transporte real contra servidores locales ------------------------------------------------------

def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def servidor_tls_mudo(tmp_path):
    """Servidor TLS local que completa el handshake, lee el pedido y nunca responde."""
    cert = cp.certificado(cn="localhost")
    ruta_cert, ruta_clave = tmp_path / "c.pem", tmp_path / "k.pem"
    ruta_cert.write_bytes(cp.pem(cert))
    ruta_clave.write_bytes(cp.clave_pem())
    servidor_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    servidor_ctx.load_cert_chain(ruta_cert, ruta_clave)
    escucha = socket.socket()
    escucha.bind(("127.0.0.1", 0))
    escucha.listen(1)
    detener = threading.Event()

    def atender():
        conexion, _ = escucha.accept()
        with servidor_ctx.wrap_socket(conexion, server_side=True) as tls:
            tls.recv(65536)
            detener.wait(5)

    hilo = threading.Thread(target=atender, daemon=True)
    hilo.start()
    cliente_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    cliente_ctx.check_hostname = False
    cliente_ctx.verify_mode = ssl.CERT_NONE
    yield escucha.getsockname()[1], cliente_ctx
    detener.set()
    escucha.close()


class TestTransporte:
    def test_solo_https(self):
        with pytest.raises(ErrorComunicacionARCA) as error:
            TransporteHTTPS().enviar("http://wswhomo.afip.gov.ar/x", b"", "")
        assert error.value.enviado is False

    def test_conexion_rechazada_no_se_envio(self):
        with pytest.raises(ErrorComunicacionARCA) as error:
            TransporteHTTPS(timeout_conexion=2).enviar(f"https://127.0.0.1:{_puerto_libre()}/x", b"<x/>", "")
        assert error.value.enviado is False

    def test_timeout_de_lectura_tras_enviar_es_incierto(self, servidor_tls_mudo):
        puerto, contexto = servidor_tls_mudo
        transporte = TransporteHTTPS(timeout_conexion=2, timeout_lectura=0.5, contexto_ssl=contexto)
        with pytest.raises(ErrorComunicacionARCA) as error:
            transporte.enviar(f"https://127.0.0.1:{puerto}/wsfev1/service.asmx", b"<x/>", "accion")
        assert error.value.enviado is True

    def test_produccion_bloqueada_en_tests(self):
        with pytest.raises(ProduccionFiscalNoHabilitadaError):
            TransporteHTTPS().enviar(endpoints.ENDPOINTS["PRODUCCION"].wsfe, b"", "")
        with pytest.raises(ProduccionFiscalNoHabilitadaError):
            TransporteHTTPS().enviar(endpoints.ENDPOINTS["PRODUCCION"].wsaa, b"", "")
