"""Certificados X.509 de prueba generados en memoria (nunca credenciales reales de ARCA).

La autoridad "Computadores Test" imita a la de homologación; "Computadores" a la de producción.
"""

from datetime import UTC, datetime, timedelta
from functools import cache

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

CUIT = "20111111112"


@cache
def clave(indice: int = 0) -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def certificado(
    homologacion: bool = True,
    cuit: str = CUIT,
    desde: datetime | None = None,
    hasta: datetime | None = None,
    indice_clave: int = 0,
    cn: str = "twinx",
) -> x509.Certificate:
    autoridad = clave(9)
    ahora = datetime.now(UTC)
    emisor = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Computadores Test" if homologacion else "Computadores"),
                        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "AFIP")])
    sujeto = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn), x509.NameAttribute(NameOID.SERIAL_NUMBER, f"CUIT {cuit}")])
    return (
        x509.CertificateBuilder()
        .subject_name(sujeto)
        .issuer_name(emisor)
        .public_key(clave(indice_clave).public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(desde or ahora - timedelta(days=1))
        .not_valid_after(hasta or ahora + timedelta(days=365))
        .sign(autoridad, hashes.SHA256())
    )


def pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def clave_pem(indice: int = 0, password: str | None = None) -> bytes:
    cifrado = serialization.BestAvailableEncryption(password.encode()) if password else serialization.NoEncryption()
    return clave(indice).private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, cifrado)
