"""Transporte HTTPS hacia ARCA con timeouts explícitos y la distinción que importa ante una falla:
¿llegó a enviarse el pedido?

- Falla al conectar (DNS, conexión rechazada, timeout de conexión, TLS): NO se envió. Reintentar es seguro.
- Falla después de conectar (envío cortado, timeout de lectura, conexión caída): SÍ pudo haberse
  procesado. Para una autorización el resultado es INCIERTO y jamás se reintenta a ciegas.
"""

import http.client
import logging
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import config
from excepciones import ErrorComunicacionARCA, RespuestaARCAInvalidaError
from services.fiscal.arca.endpoints import exigir_host_permitido

logger = logging.getLogger(__name__)

TAMANO_MAXIMO_RESPUESTA = 5 * 1024 * 1024


@dataclass(frozen=True)
class RespuestaHTTP:
    estado: int
    cuerpo: bytes
    duracion_ms: int


class Transporte(Protocol):
    def enviar(self, url: str, cuerpo: bytes, soap_action: str) -> RespuestaHTTP: ...


class TransporteHTTPS:
    """HTTPS con verificación de certificado y nombre de host (contexto por defecto de `ssl`)."""

    def __init__(
        self,
        timeout_conexion: float = config.TIMEOUT_CONEXION_ARCA,
        timeout_lectura: float = config.TIMEOUT_LECTURA_ARCA,
        contexto_ssl: ssl.SSLContext | None = None,
    ) -> None:
        self._timeout_conexion = timeout_conexion
        self._timeout_lectura = timeout_lectura
        self._contexto = contexto_ssl or ssl.create_default_context()

    def enviar(self, url: str, cuerpo: bytes, soap_action: str) -> RespuestaHTTP:
        partes = urlsplit(url)
        if partes.scheme != "https" or not partes.hostname:
            raise ErrorComunicacionARCA("ARCA solo se consulta por HTTPS.", enviado=False)
        exigir_host_permitido(url)
        inicio = time.monotonic()
        conexion = http.client.HTTPSConnection(
            partes.hostname, partes.port or 443, timeout=self._timeout_conexion, context=self._contexto
        )
        try:
            try:
                conexion.connect()
            except (OSError, ssl.SSLError) as error:
                logger.warning("ARCA no disponible al conectar (%s): %s", partes.hostname, type(error).__name__)
                raise ErrorComunicacionARCA(
                    f"No se pudo conectar con ARCA ({type(error).__name__}).", enviado=False
                ) from error
            try:
                conexion.sock.settimeout(self._timeout_lectura)
                conexion.request(
                    "POST",
                    partes.path + (f"?{partes.query}" if partes.query else ""),
                    body=cuerpo,
                    headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": f'"{soap_action}"'},
                )
                respuesta = conexion.getresponse()
                datos = respuesta.read(TAMANO_MAXIMO_RESPUESTA + 1)
            except (OSError, socket.timeout, http.client.HTTPException) as error:
                logger.warning("ARCA no respondió tras enviar el pedido (%s): %s", partes.hostname, type(error).__name__)
                raise ErrorComunicacionARCA(
                    f"Se envió el pedido a ARCA y no hubo respuesta ({type(error).__name__}).", enviado=True
                ) from error
        finally:
            conexion.close()
        if len(datos) > TAMANO_MAXIMO_RESPUESTA:
            raise RespuestaARCAInvalidaError("La respuesta de ARCA excede el tamaño admitido.")
        return RespuestaHTTP(respuesta.status, datos, int((time.monotonic() - inicio) * 1000))
