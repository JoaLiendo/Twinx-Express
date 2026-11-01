"""Sobre SOAP 1.1 y lectura segura de las respuestas de ARCA."""

import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from excepciones import RespuestaARCAInvalidaError
from services.fiscal.arca.transporte import RespuestaHTTP

NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"


class SoapFault(RespuestaARCAInvalidaError):
    """El servicio respondió un SOAP Fault. `codigo` es el faultcode sin prefijo de namespace."""

    def __init__(self, codigo: str, mensaje: str) -> None:
        super().__init__(f"ARCA respondió un error ({codigo}): {mensaje}")
        self.codigo = codigo
        self.mensaje = mensaje


def texto(valor: object) -> str:
    """Valor escapado para incluir como texto XML."""
    return escape(str(valor))


def sobre(cuerpo: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<soap:Envelope xmlns:soap="{NS_SOAP}"><soap:Body>{cuerpo}</soap:Body></soap:Envelope>'
    ).encode("utf-8")


def parsear_xml(datos: bytes) -> ET.Element:
    """Parsea XML rechazando DTD y entidades (nunca se expanden: sin XXE ni "billion laughs")."""
    if b"<!DOCTYPE" in datos or b"<!ENTITY" in datos:
        raise RespuestaARCAInvalidaError("La respuesta de ARCA trae una DTD: se rechaza por seguridad.")
    try:
        return ET.fromstring(datos)
    except ET.ParseError as error:
        raise RespuestaARCAInvalidaError("ARCA respondió un XML malformado.") from error


def local(etiqueta: str) -> str:
    return etiqueta.rsplit("}", 1)[-1]


def hijo(elemento: ET.Element | None, nombre: str) -> ET.Element | None:
    """Primer hijo directo con ese nombre local (ignora namespaces)."""
    if elemento is None:
        return None
    for nodo in elemento:
        if local(nodo.tag) == nombre:
            return nodo
    return None


def hijos(elemento: ET.Element | None, nombre: str) -> list[ET.Element]:
    if elemento is None:
        return []
    return [nodo for nodo in elemento if local(nodo.tag) == nombre]


def valor(elemento: ET.Element | None, nombre: str) -> str | None:
    nodo = hijo(elemento, nombre)
    if nodo is None or nodo.text is None:
        return None
    return nodo.text.strip()


def cuerpo_de_respuesta(respuesta: RespuestaHTTP) -> ET.Element:
    """Primer elemento dentro de `soap:Body`.

    Raises:
        SoapFault: si el cuerpo es un Fault (con cualquier estado HTTP, típicamente 500).
        RespuestaARCAInvalidaError: XML inválido, sin Body, o un estado HTTP de error sin Fault.
    """
    raiz = parsear_xml(respuesta.cuerpo) if respuesta.cuerpo else None
    if raiz is None or local(raiz.tag) != "Envelope":
        raise RespuestaARCAInvalidaError(f"ARCA respondió HTTP {respuesta.estado} sin un sobre SOAP válido.")
    cuerpo = hijo(raiz, "Body")
    if cuerpo is None or len(cuerpo) == 0:
        raise RespuestaARCAInvalidaError("La respuesta SOAP de ARCA no tiene cuerpo.")
    contenido = cuerpo[0]
    if local(contenido.tag) == "Fault":
        codigo = (valor(contenido, "faultcode") or "desconocido").rsplit(":", 1)[-1]
        raise SoapFault(codigo, (valor(contenido, "faultstring") or "sin detalle")[:300])
    if respuesta.estado != 200:
        raise RespuestaARCAInvalidaError(f"ARCA respondió HTTP {respuesta.estado}.")
    return contenido
