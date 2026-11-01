"""Certificados X.509 y claves privadas de ARCA, uno por ambiente (homologación ≠ producción).

Dónde vive cada cosa (nunca en SQLite, backups, Git, logs, auditoría ni HTML):
    <DIRECTORIO_SECRETOS_FISCALES>/<ambiente>/certificado.pem      certificado público (PEM)
    <DIRECTORIO_SECRETOS_FISCALES>/<ambiente>/clave_privada.dpapi  clave privada PKCS#8, cifrada con DPAPI

La contraseña de la clave (si la tiene) se usa una sola vez, al importar, para descifrarla; no se guarda.
La clave privada nunca sale de este módulo salvo hacia la firma CMS de WSAA.
"""

import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import config
from db.repositorios import auditoria as repositorio_auditoria
from domain.auditoria import ACCION_CERTIFICADO_FISCAL_CAMBIADO
from domain.fiscal.comprobante import AMBIENTE_HOMOLOGACION, AMBIENTES
from excepciones import CredencialesFiscalesError
from services.fiscal import dpapi

logger = logging.getLogger(__name__)

NOMBRE_CERTIFICADO = "certificado.pem"
NOMBRE_CLAVE = "clave_privada.dpapi"
DIAS_ALERTA_VENCIMIENTO = 30
TAMANO_MAXIMO_ARCHIVO = 64 * 1024

VIGENTE = "VIGENTE"
POR_VENCER = "POR_VENCER"
VENCIDO = "VENCIDO"
AUSENTE = "AUSENTE"
ILEGIBLE = "ILEGIBLE"


@dataclass(frozen=True)
class InfoCertificado:
    """Lo que se puede mostrar de un certificado: nunca la clave privada."""

    ambiente: str
    estado: str
    sujeto: str | None = None
    emisor: str | None = None
    cuit: str | None = None
    vence: str | None = None
    dias_restantes: int | None = None
    huella: str | None = None
    detalle: str | None = None


def directorio_ambiente(ambiente: str) -> Path:
    if ambiente not in AMBIENTES:
        raise CredencialesFiscalesError(f"Ambiente fiscal inválido: {ambiente!r}.")
    return config.DIRECTORIO_SECRETOS_FISCALES / ambiente.lower()


def _nombre(nombre: x509.Name, oid) -> str | None:
    atributos = nombre.get_attributes_for_oid(oid)
    return str(atributos[0].value) if atributos else None


def _cuit_del_certificado(certificado: x509.Certificate) -> str | None:
    """ARCA emite los certificados con `serialNumber=CUIT 20111111112` en el sujeto."""
    serial = _nombre(certificado.subject, NameOID.SERIAL_NUMBER) or ""
    digitos = "".join(c for c in serial if c.isdigit())
    return digitos if len(digitos) == 11 else None


def _es_de_homologacion(certificado: x509.Certificate) -> bool:
    """La autoridad de homologación de ARCA firma como «Computadores Test»; la de producción no lleva
    «Test». Es la señal disponible localmente; WSAA es la autoridad final (rechaza un certificado de otro
    ambiente con `cms.cert.untrusted`)."""
    return "test" in (_nombre(certificado.issuer, NameOID.COMMON_NAME) or "").lower()


def _cargar_certificado(contenido: bytes) -> x509.Certificate:
    try:
        if b"-----BEGIN" in contenido:
            return x509.load_pem_x509_certificate(contenido)
        return x509.load_der_x509_certificate(contenido)
    except ValueError as error:
        raise CredencialesFiscalesError("El archivo del certificado no es un certificado X.509 válido.") from error


def _cargar_clave(contenido: bytes, password: str | None) -> rsa.RSAPrivateKey:
    clave_password = password.encode("utf-8") if password else None
    try:
        if b"-----BEGIN" in contenido:
            clave = serialization.load_pem_private_key(contenido, password=clave_password)
        else:
            clave = serialization.load_der_private_key(contenido, password=clave_password)
    except TypeError as error:
        raise CredencialesFiscalesError("La clave privada está protegida: ingresá su contraseña.") from error
    except ValueError as error:
        raise CredencialesFiscalesError("La clave privada no es válida o la contraseña es incorrecta.") from error
    if not isinstance(clave, rsa.RSAPrivateKey):
        raise CredencialesFiscalesError("ARCA usa claves RSA: la clave privada indicada no lo es.")
    return clave


def _info(ambiente: str, certificado: x509.Certificate, ahora: datetime) -> InfoCertificado:
    vence = certificado.not_valid_after_utc
    dias = (vence - ahora).days
    if ahora >= vence:
        estado = VENCIDO
    elif dias < DIAS_ALERTA_VENCIMIENTO:
        estado = POR_VENCER
    else:
        estado = VIGENTE
    return InfoCertificado(
        ambiente=ambiente,
        estado=estado,
        sujeto=_nombre(certificado.subject, NameOID.COMMON_NAME) or certificado.subject.rfc4514_string(),
        emisor=_nombre(certificado.issuer, NameOID.COMMON_NAME) or certificado.issuer.rfc4514_string(),
        cuit=_cuit_del_certificado(certificado),
        vence=vence.astimezone().strftime("%Y-%m-%d %H:%M"),
        dias_restantes=max(dias, 0),
        huella=hashlib.sha256(certificado.public_bytes(serialization.Encoding.DER)).hexdigest(),
    )


def _escribir_atomico(ruta: Path, contenido: bytes) -> None:
    temporal = ruta.with_suffix(ruta.suffix + ".tmp")
    temporal.write_bytes(contenido)
    os.replace(temporal, ruta)


def importar(
    ambiente: str,
    certificado_bytes: bytes,
    clave_bytes: bytes,
    password: str | None,
    cuit_emisor: str | None,
    usuario_id: int | None,
    ahora: datetime | None = None,
) -> InfoCertificado:
    """Valida y guarda el par certificado + clave privada del ambiente.

    Rechaza: archivos que no son certificado/clave, clave que no corresponde al certificado, certificado
    vencido o todavía no vigente, certificado de otro ambiente y certificado de otro CUIT que el emisor.

    Raises:
        CredencialesFiscalesError: con un mensaje que nunca incluye material secreto.
    """
    ahora = ahora or datetime.now(UTC)
    if len(certificado_bytes) > TAMANO_MAXIMO_ARCHIVO or len(clave_bytes) > TAMANO_MAXIMO_ARCHIVO:
        raise CredencialesFiscalesError("El archivo es demasiado grande para ser un certificado o una clave.")
    certificado = _cargar_certificado(certificado_bytes)
    clave = _cargar_clave(clave_bytes, password)
    publica = serialization.PublicFormat.SubjectPublicKeyInfo
    if clave.public_key().public_bytes(serialization.Encoding.DER, publica) != certificado.public_key().public_bytes(
        serialization.Encoding.DER, publica
    ):
        raise CredencialesFiscalesError("La clave privada no corresponde a este certificado.")
    if ahora < certificado.not_valid_before_utc:
        raise CredencialesFiscalesError("El certificado todavía no está vigente.")
    if ahora >= certificado.not_valid_after_utc:
        raise CredencialesFiscalesError("El certificado está vencido: generá uno nuevo en ARCA.")
    if _es_de_homologacion(certificado) != (ambiente == AMBIENTE_HOMOLOGACION):
        otro = "homologación" if _es_de_homologacion(certificado) else "producción"
        raise CredencialesFiscalesError(
            f"El certificado es de {otro} y se intentó cargar en {AMBIENTES[ambiente].lower()}: no se mezclan."
        )
    cuit = _cuit_del_certificado(certificado)
    if cuit_emisor and cuit and cuit != cuit_emisor:
        raise CredencialesFiscalesError("El certificado pertenece a otro CUIT que el del emisor configurado.")

    directorio = directorio_ambiente(ambiente)
    directorio.mkdir(parents=True, exist_ok=True)
    clave_pkcs8 = clave.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    _escribir_atomico(directorio / NOMBRE_CLAVE, dpapi.proteger(clave_pkcs8))
    _escribir_atomico(directorio / NOMBRE_CERTIFICADO, certificado.public_bytes(serialization.Encoding.PEM))
    info = _info(ambiente, certificado, ahora)
    repositorio_auditoria.registrar(
        usuario_id,
        ACCION_CERTIFICADO_FISCAL_CAMBIADO,
        "CERTIFICADO_FISCAL",
        None,
        f"{AMBIENTES[ambiente]}: «{info.sujeto}», vence {info.vence}, huella {info.huella[:16]}",
    )
    logger.info("Certificado fiscal importado: ambiente=%s vence=%s huella=%s", ambiente, info.vence, info.huella[:16])
    return info


def estado(ambiente: str, ahora: datetime | None = None) -> InfoCertificado:
    """Estado del certificado del ambiente para el panel (nunca lanza)."""
    directorio = directorio_ambiente(ambiente)
    ruta_certificado, ruta_clave = directorio / NOMBRE_CERTIFICADO, directorio / NOMBRE_CLAVE
    if not ruta_certificado.is_file() or not ruta_clave.is_file():
        return InfoCertificado(ambiente=ambiente, estado=AUSENTE)
    try:
        certificado = _cargar_certificado(ruta_certificado.read_bytes())
    except (OSError, CredencialesFiscalesError):
        return InfoCertificado(ambiente=ambiente, estado=ILEGIBLE, detalle="El certificado guardado no puede leerse.")
    return _info(ambiente, certificado, ahora or datetime.now(UTC))


@dataclass(frozen=True)
class MaterialFirma:
    """Certificado + clave para firmar el TRA. Vive solo en memoria y solo mientras se firma."""

    certificado: x509.Certificate
    clave: rsa.RSAPrivateKey
    cuit: str | None


def cargar_material_firma(ambiente: str, ahora: datetime | None = None) -> MaterialFirma:
    """Raises: CredencialesFiscalesError si falta, está vencido, es de otro ambiente o no se descifra."""
    directorio = directorio_ambiente(ambiente)
    try:
        certificado = _cargar_certificado((directorio / NOMBRE_CERTIFICADO).read_bytes())
        cifrada = (directorio / NOMBRE_CLAVE).read_bytes()
    except FileNotFoundError as error:
        raise CredencialesFiscalesError(f"No hay certificado de {AMBIENTES[ambiente].lower()} cargado.") from error
    if (ahora or datetime.now(UTC)) >= certificado.not_valid_after_utc:
        raise CredencialesFiscalesError("El certificado fiscal está vencido: generá uno nuevo en ARCA.")
    if _es_de_homologacion(certificado) != (ambiente == AMBIENTE_HOMOLOGACION):
        raise CredencialesFiscalesError("El certificado guardado no corresponde al ambiente en uso.")
    clave = serialization.load_der_private_key(dpapi.desproteger(cifrada), password=None)
    if not isinstance(clave, rsa.RSAPrivateKey):
        raise CredencialesFiscalesError("La clave privada guardada no es RSA.")
    return MaterialFirma(certificado=certificado, clave=clave, cuit=_cuit_del_certificado(certificado))
