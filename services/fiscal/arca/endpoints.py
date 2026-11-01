"""URLs oficiales de ARCA por ambiente (https://www.afip.gob.ar/ws/documentacion/wsaa.asp y WSDL de
WSFEv1, consultados el 01/11/2026). Homologación y producción usan certificados distintos y nunca se
mezclan: cada ambiente tiene su par de URLs y su propio certificado."""

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from domain.fiscal.comprobante import AMBIENTE_HOMOLOGACION, AMBIENTE_PRODUCCION
from excepciones import ProduccionFiscalNoHabilitadaError


@dataclass(frozen=True)
class Endpoints:
    wsaa: str
    wsfe: str


ENDPOINTS = {
    AMBIENTE_HOMOLOGACION: Endpoints(
        wsaa="https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
        wsfe="https://wswhomo.afip.gov.ar/wsfev1/service.asmx",
    ),
    AMBIENTE_PRODUCCION: Endpoints(
        wsaa="https://wsaa.afip.gov.ar/ws/services/LoginCms",
        wsfe="https://servicios1.afip.gov.ar/wsfev1/service.asmx",
    ),
}

HOSTS_PRODUCCION = frozenset(urlsplit(url).hostname for url in (ENDPOINTS[AMBIENTE_PRODUCCION].wsaa, ENDPOINTS[AMBIENTE_PRODUCCION].wsfe))

VARIABLE_BLOQUEO_PRODUCCION = "TWINX_BLOQUEAR_PRODUCCION_FISCAL"
"""Con valor "1" (lo fijan la suite de tests y el servidor E2E) ningún transporte abre conexión con un
host de producción, pase lo que pase en la configuración: defensa en profundidad para no emitir nunca un
comprobante real desde un entorno de prueba."""


def exigir_host_permitido(url: str) -> None:
    """Raises: ProduccionFiscalNoHabilitadaError si es un host de producción con el bloqueo activo."""
    if urlsplit(url).hostname in HOSTS_PRODUCCION and os.environ.get(VARIABLE_BLOQUEO_PRODUCCION) == "1":
        raise ProduccionFiscalNoHabilitadaError("Este entorno de prueba tiene bloqueado el acceso a ARCA producción.")
