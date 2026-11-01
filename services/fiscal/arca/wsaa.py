"""Cliente WSAA: TRA firmado en CMS → LoginCms → Ticket de Acceso (Token + Sign).

- TRA `loginTicketRequest` versión 1.0 con `uniqueId`, `generationTime`, `expirationTime` y el servicio
  pedido (`wsfe`). Las horas van con zona horaria y un margen de ±10 minutos contra relojes desfasados.
- CMS SignedData con el contenido adjunto (no detached), SHA-256, firmado con el certificado del
  ambiente; viaja en base64 en `loginCms/in0`.
- El TA dura horas (lo fija WSAA): se reutiliza hasta pocos minutos antes de vencer.

Caché: primero en memoria. Además, cifrada con DPAPI junto a la clave del ambiente (fuera de `data/` y de
todo backup), porque WSAA no entrega un TA nuevo mientras el anterior siga vigente
(`coe.alreadyAuthenticated`): sin esa copia, un reinicio dejaría a Twinx sin poder fiscalizar hasta
que venza. Token y Sign nunca se loguean, auditan, muestran ni guardan en SQLite.
"""

import base64
import hashlib
import json
import logging
import os
import random
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs7

from excepciones import CredencialesFiscalesError, ErrorWSAA, RespuestaARCAInvalidaError
from services.fiscal import credenciales, dpapi
from services.fiscal.arca import soap
from services.fiscal.arca.transporte import Transporte

logger = logging.getLogger(__name__)

SERVICIO_WSFE = "wsfe"
NS_WSAA = "http://wsaa.view.sua.dvadac.desein.afip.gov"
MARGEN_TRA = timedelta(minutes=10)
MARGEN_RENOVACION = timedelta(minutes=10)
NOMBRE_CACHE = "ta_{servicio}.dpapi"


@dataclass(frozen=True)
class TicketAcceso:
    token: str
    sign: str
    generacion: datetime
    expiracion: datetime
    huella_certificado: str

    def vigente(self, ahora: datetime) -> bool:
        return ahora + MARGEN_RENOVACION < self.expiracion

    def __repr__(self) -> str:  # nunca exponer Token ni Sign en una traza o un log
        return f"TicketAcceso(expiracion={self.expiracion.isoformat()}, token=***, sign=***)"


def construir_tra(servicio: str, ahora: datetime, unique_id: int) -> bytes:
    local = ahora.astimezone()
    generacion = (local - MARGEN_TRA).replace(microsecond=0).isoformat()
    expiracion = (local + MARGEN_TRA).replace(microsecond=0).isoformat()
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<loginTicketRequest version="1.0"><header>'
        f"<uniqueId>{unique_id}</uniqueId><generationTime>{generacion}</generationTime>"
        f"<expirationTime>{expiracion}</expirationTime></header>"
        f"<service>{soap.texto(servicio)}</service></loginTicketRequest>"
    ).encode("utf-8")


def firmar_tra(tra: bytes, material: credenciales.MaterialFirma) -> str:
    """CMS SignedData (contenido adjunto, SHA-256) en base64 sin saltos de línea."""
    cms = (
        pkcs7.PKCS7SignatureBuilder()
        .set_data(tra)
        .add_signer(material.certificado, material.clave, hashes.SHA256())
        .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary])
    )
    return base64.b64encode(cms).decode("ascii")


def _fecha(texto: str | None, campo: str) -> datetime:
    try:
        fecha = datetime.fromisoformat(texto or "")
    except ValueError as error:
        raise RespuestaARCAInvalidaError(f"WSAA devolvió un {campo} inválido.") from error
    if fecha.tzinfo is None:
        raise RespuestaARCAInvalidaError(f"WSAA devolvió un {campo} sin zona horaria.")
    return fecha


def parsear_ticket(xml: str, huella_certificado: str) -> TicketAcceso:
    raiz = soap.parsear_xml(xml.encode("utf-8"))
    if soap.local(raiz.tag) != "loginTicketResponse":
        raise RespuestaARCAInvalidaError("WSAA no devolvió un loginTicketResponse.")
    cabecera, credenciales_xml = soap.hijo(raiz, "header"), soap.hijo(raiz, "credentials")
    token, sign = soap.valor(credenciales_xml, "token"), soap.valor(credenciales_xml, "sign")
    if not token or not sign:
        raise RespuestaARCAInvalidaError("WSAA no devolvió Token y Sign.")
    return TicketAcceso(
        token=token,
        sign=sign,
        generacion=_fecha(soap.valor(cabecera, "generationTime"), "generationTime"),
        expiracion=_fecha(soap.valor(cabecera, "expirationTime"), "expirationTime"),
        huella_certificado=huella_certificado,
    )


class CacheTickets:
    """Memoria + copia DPAPI por ambiente y servicio. Una copia ilegible o de otro certificado se ignora."""

    def __init__(self, persistir: bool = True) -> None:
        self._memoria: dict[tuple[str, str], TicketAcceso] = {}
        self._persistir = persistir

    def _ruta(self, ambiente: str, servicio: str):
        return credenciales.directorio_ambiente(ambiente) / NOMBRE_CACHE.format(servicio=servicio)

    def obtener(self, ambiente: str, servicio: str, huella: str, ahora: datetime) -> TicketAcceso | None:
        ticket = self._memoria.get((ambiente, servicio))
        if ticket is None and self._persistir:
            ticket = self._leer(ambiente, servicio)
            if ticket is not None:
                self._memoria[(ambiente, servicio)] = ticket
        if ticket is None or ticket.huella_certificado != huella or not ticket.vigente(ahora):
            return None
        return ticket

    def _leer(self, ambiente: str, servicio: str) -> TicketAcceso | None:
        ruta = self._ruta(ambiente, servicio)
        if not ruta.is_file():
            return None
        try:
            datos = json.loads(dpapi.desproteger(ruta.read_bytes()))
            return TicketAcceso(
                token=datos["token"],
                sign=datos["sign"],
                generacion=datetime.fromisoformat(datos["generacion"]),
                expiracion=datetime.fromisoformat(datos["expiracion"]),
                huella_certificado=datos["huella"],
            )
        except (OSError, ValueError, KeyError, CredencialesFiscalesError):
            logger.warning("Se ignora la copia local del Ticket de Acceso (%s): ilegible", ambiente)
            return None

    def guardar(self, ambiente: str, servicio: str, ticket: TicketAcceso) -> None:
        self._memoria[(ambiente, servicio)] = ticket
        if not self._persistir:
            return
        contenido = json.dumps(
            {
                "token": ticket.token,
                "sign": ticket.sign,
                "generacion": ticket.generacion.isoformat(),
                "expiracion": ticket.expiracion.isoformat(),
                "huella": ticket.huella_certificado,
            }
        ).encode("utf-8")
        ruta = self._ruta(ambiente, servicio)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        temporal = ruta.with_suffix(".tmp")
        temporal.write_bytes(dpapi.proteger(contenido))
        os.replace(temporal, ruta)

    def invalidar(self, ambiente: str) -> None:
        for clave in [clave for clave in self._memoria if clave[0] == ambiente]:
            del self._memoria[clave]
        if self._persistir:
            for ruta in credenciales.directorio_ambiente(ambiente).glob(NOMBRE_CACHE.format(servicio="*")):
                ruta.unlink(missing_ok=True)


class ClienteWSAA:
    def __init__(
        self,
        transporte: Transporte,
        url: str,
        ambiente: str,
        cache: CacheTickets,
        cargar_material: Callable[[str], credenciales.MaterialFirma] = credenciales.cargar_material_firma,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._transporte = transporte
        self._url = url
        self._ambiente = ambiente
        self._cache = cache
        self._cargar_material = cargar_material
        self._reloj = reloj
        self._candado = threading.Lock()

    def ticket(self, servicio: str = SERVICIO_WSFE) -> TicketAcceso:
        """TA vigente (de la caché o uno nuevo).

        Raises:
            CredencialesFiscalesError: certificado ausente, vencido, de otro ambiente o ilegible.
            ErrorWSAA: WSAA rechazó el pedido (mensaje sanitizado).
            ErrorComunicacionARCA / RespuestaARCAInvalidaError: falla de red o respuesta inválida.
        """
        with self._candado:
            ahora = self._reloj()
            material = self._cargar_material(self._ambiente)
            huella = hashlib.sha256(material.certificado.public_bytes(serialization.Encoding.DER)).hexdigest()
            existente = self._cache.obtener(self._ambiente, servicio, huella, ahora)
            if existente is not None:
                return existente
            tra = construir_tra(servicio, ahora, random.SystemRandom().randint(1, 2**31 - 1))
            cms = firmar_tra(tra, material)
            cuerpo = soap.sobre(f'<wsaa:loginCms xmlns:wsaa="{NS_WSAA}"><wsaa:in0>{cms}</wsaa:in0></wsaa:loginCms>')
            try:
                respuesta = soap.cuerpo_de_respuesta(self._transporte.enviar(self._url, cuerpo, ""))
            except soap.SoapFault as fault:
                raise _error_de_fault(fault) from fault
            ticket = parsear_ticket(soap.valor(respuesta, "loginCmsReturn") or "", huella)
            if ticket.expiracion <= ahora:
                raise RespuestaARCAInvalidaError("WSAA devolvió un Ticket de Acceso ya vencido.")
            self._cache.guardar(self._ambiente, servicio, ticket)
            logger.info("Ticket de Acceso obtenido: ambiente=%s servicio=%s vence=%s", self._ambiente, servicio, ticket.expiracion.isoformat())
            return ticket

    def invalidar(self) -> None:
        with self._candado:
            self._cache.invalidar(self._ambiente)


_MENSAJES_FAULT = {
    "alreadyAuthenticated": (
        "ARCA ya entregó un Ticket de Acceso vigente que esta PC no conserva: se podrá pedir otro cuando venza"
        " (puede demorar hasta 12 horas)."
    ),
    "expired": "El certificado fiscal está vencido para ARCA: generá uno nuevo.",
    "untrusted": "ARCA no reconoce el certificado (¿es de otro ambiente?).",
    "notAuthorized": "El certificado no está autorizado para el servicio de facturación (wsfe) en ARCA.",
}


def _error_de_fault(fault: soap.SoapFault) -> ErrorWSAA:
    for clave, mensaje in _MENSAJES_FAULT.items():
        if clave.lower() in fault.codigo.lower():
            return ErrorWSAA(f"{mensaje} [{fault.codigo}]")
    return ErrorWSAA(f"WSAA rechazó el pedido de acceso [{fault.codigo}]: {fault.mensaje}")
