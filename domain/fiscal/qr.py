"""Código QR de los comprobantes electrónicos (especificación oficial de ARCA, versión 1).

Fuente: «Especificaciones del QR incluido en las facturas electrónicas»
(https://www.afip.gob.ar/fe/qr/documentos/QRespecificaciones.pdf, consultado el 01/11/2026):

    {URL}?p={DATOS_CMP_BASE64}   con   URL = https://www.arca.gob.ar/fe/qr/

y DATOS = JSON versión 1 en Base64. Campos (en este orden): ver, fecha, cuit, ptoVta, tipoCmp, nroCmp,
importe, moneda, ctz, tipoDocRec y nroDocRec (de corresponder), tipoCodAut ("E" = CAE, "A" = CAEA) y
codAut. Solo existe para un comprobante autorizado: nunca se arma un QR sin autorización real.
"""

import base64
import json
from decimal import Decimal

from domain.fiscal import tablas_arca
from domain.fiscal.comprobante import MODALIDAD_CAEA, ComprobanteFiscal
from excepciones import OperacionFiscalInvalidaError

URL_QR = "https://www.arca.gob.ar/fe/qr/"
VERSION_QR = 1


def _numero(valor: Decimal) -> int | Decimal:
    """Entero si no tiene decimales (como en el ejemplo oficial: `"importe":12100`), si no hasta 2 decimales."""
    return int(valor) if valor == valor.to_integral_value() else valor.quantize(Decimal("0.01"))


def datos_qr(comprobante: ComprobanteFiscal) -> dict:
    """El JSON versión 1 del comprobante (dict ordenado según la especificación)."""
    if not comprobante.autorizado or not comprobante.codigo_autorizacion or comprobante.numero is None:
        raise OperacionFiscalInvalidaError("El QR fiscal solo existe para un comprobante autorizado.")
    datos: dict = {
        "ver": VERSION_QR,
        "fecha": comprobante.fecha_comprobante,
        "cuit": int(comprobante.emisor.cuit),
        "ptoVta": comprobante.punto_venta,
        "tipoCmp": comprobante.tipo_comprobante,
        "nroCmp": comprobante.numero,
        "importe": _numero(Decimal(comprobante.importes.total_centavos) / 100),
        "moneda": comprobante.moneda,
        "ctz": _numero(Decimal(comprobante.cotizacion)),
    }
    if comprobante.receptor.doc_tipo != tablas_arca.DOC_SIN_IDENTIFICAR:
        datos["tipoDocRec"] = comprobante.receptor.doc_tipo
        datos["nroDocRec"] = int(comprobante.receptor.doc_nro)
    datos["tipoCodAut"] = "A" if comprobante.modalidad == MODALIDAD_CAEA else "E"
    datos["codAut"] = int(comprobante.codigo_autorizacion)
    return datos


def json_qr(datos: dict) -> str:
    """JSON compacto, sin espacios y en el orden dado. Los `Decimal` se escriben con su valor exacto."""
    partes = []
    for clave, valor in datos.items():
        if isinstance(valor, Decimal):
            texto = format(valor, "f")
        else:
            texto = json.dumps(valor, ensure_ascii=False)
        partes.append(f"{json.dumps(clave)}:{texto}")
    return "{" + ",".join(partes) + "}"


def url_qr_desde_datos(datos: dict) -> str:
    return URL_QR + "?p=" + base64.b64encode(json_qr(datos).encode("utf-8")).decode("ascii")


def url_qr(comprobante: ComprobanteFiscal) -> str:
    return url_qr_desde_datos(datos_qr(comprobante))
