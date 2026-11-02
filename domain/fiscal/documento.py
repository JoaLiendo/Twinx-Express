"""Contenido imprimible de un comprobante fiscal AUTORIZADO, idéntico para la vista A4, el ticket de 80 mm y
el PDF (los tres se arman desde `DocumentoFiscal`, así no pueden contradecirse).

Incluye solo datos fiscales del snapshot (nunca datos actuales del cliente o del producto) y no agrega
logos ni leyendas que sugieran una certificación de ARCA. Factura B a consumidor final: régimen de
Transparencia Fiscal al Consumidor (Ley 27.743, RG ARCA 5614/2024) con el IVA Contenido y los Otros
Impuestos Nacionales Indirectos ($ 0,00: solo se emite si todos los productos están confirmados sin ellos).
"""

from dataclasses import dataclass

from domain.dinero import centavos_a_texto_localizado
from domain.fiscal import otros_impuestos, tablas_arca
from domain.fiscal.comprobante import MODALIDAD_CAEA, ComprobanteFiscal
from domain.fiscal.qr import url_qr
from excepciones import OperacionFiscalInvalidaError

_CONDICIONES_RECEPTOR = {
    "RESPONSABLE_INSCRIPTO": "IVA Responsable Inscripto",
    "MONOTRIBUTO": "Responsable Monotributo",
    "EXENTO": "IVA Sujeto Exento",
    "CONSUMIDOR_FINAL": "Consumidor Final",
}
_DOCUMENTOS = {tablas_arca.DOC_CUIT: "CUIT"}


def _pesos(centavos: int) -> str:
    return "$ " + centavos_a_texto_localizado(centavos)


def _fecha(iso: str | None) -> str:
    return "/".join(reversed(iso.split("-"))) if iso else ""


def _cuit(cuit: str) -> str:
    return f"{cuit[:2]}-{cuit[2:10]}-{cuit[10:]}"


@dataclass(frozen=True)
class Renglon:
    descripcion: str
    cantidad: int
    precio_unitario: str
    subtotal: str
    alicuota: str | None


@dataclass(frozen=True)
class DocumentoFiscal:
    letra: str
    codigo_tipo: str
    nombre_tipo: str
    numero: str
    fecha: str
    emisor: list[str]
    receptor: list[str]
    renglones: list[Renglon]
    totales: list[tuple[str, str]]
    leyendas: list[str]
    transparencia: list[tuple[str, str]]
    autorizacion: list[tuple[str, str]]
    asociado: str | None
    qr_url: str
    ambiente_prueba: bool


def armar(comprobante: ComprobanteFiscal, asociado: ComprobanteFiscal | None = None) -> DocumentoFiscal:
    """Raises: OperacionFiscalInvalidaError si el comprobante no está autorizado (no hay documento fiscal)."""
    if not comprobante.autorizado:
        raise OperacionFiscalInvalidaError(
            "El comprobante no está autorizado por ARCA: no existe documento fiscal para imprimir."
        )
    emisor = comprobante.emisor
    datos_emisor = [
        emisor.razon_social,
        f"Domicilio: {emisor.domicilio}",
        f"CUIT: {_cuit(emisor.cuit)}",
        f"Condición frente al IVA: {emisor.condicion_etiqueta}",
    ]
    if emisor.ingresos_brutos:
        datos_emisor.append(f"Ingresos Brutos: {emisor.ingresos_brutos}")
    if emisor.inicio_actividades:
        datos_emisor.append(f"Inicio de actividades: {_fecha(emisor.inicio_actividades)}")

    receptor = comprobante.receptor
    datos_receptor = [f"Condición frente al IVA: {_CONDICIONES_RECEPTOR.get(receptor.condicion_iva, receptor.condicion_iva)}"]
    if not receptor.es_anonimo:
        documento = _DOCUMENTOS.get(receptor.doc_tipo, f"Doc. {receptor.doc_tipo}")
        numero_doc = _cuit(receptor.doc_nro) if receptor.doc_tipo == tablas_arca.DOC_CUIT else receptor.doc_nro
        datos_receptor.insert(0, f"{documento}: {numero_doc}")
    datos_receptor.insert(0, f"Apellido y nombre / Razón social: {receptor.nombre}")
    if receptor.domicilio:
        datos_receptor.append(f"Domicilio: {receptor.domicilio}")

    discrimina = comprobante.clase == "A"
    renglones = [
        Renglon(
            descripcion=linea.descripcion,
            cantidad=linea.cantidad,
            precio_unitario=_pesos(linea.precio_unitario_centavos),
            subtotal=_pesos(linea.subtotal_centavos),
            alicuota=(linea.tratamiento.etiqueta if (discrimina and linea.tratamiento) else None),
        )
        for linea in comprobante.lineas
    ]
    importes = comprobante.importes
    totales: list[tuple[str, str]] = []
    if discrimina:
        totales.append(("Importe neto gravado", _pesos(importes.neto_centavos)))
        for alicuota in importes.alicuotas:
            totales.append((f"IVA {tablas_arca.etiqueta_alicuota(alicuota.alicuota_iva_id)}", _pesos(alicuota.importe_centavos)))
        if importes.exento_centavos:
            totales.append(("Importe exento", _pesos(importes.exento_centavos)))
        if importes.no_gravado_centavos:
            totales.append(("Importe no gravado", _pesos(importes.no_gravado_centavos)))
    totales.append(("Importe total", _pesos(importes.total_centavos)))

    leyendas = []
    transparencia: list[tuple[str, str]] = []
    if comprobante.clase == "B":
        transparencia.append(("IVA Contenido", _pesos(importes.iva_centavos)))
        # Del snapshot de las líneas, nunca de la clasificación actual del producto. Un comprobante anterior
        # a V1.16-H no registró ese dato: no se le agrega un valor que nadie confirmó al emitirlo.
        otros = otros_impuestos.importe_centavos(linea.otros_impuestos for linea in comprobante.lineas)
        if otros is not None:
            transparencia.append((otros_impuestos.ETIQUETA_DOCUMENTO, _pesos(otros)))
        if receptor.es_consumidor_final:
            leyendas.append("A CONSUMIDOR FINAL")

    codigo = "CAEA" if comprobante.modalidad == MODALIDAD_CAEA else "CAE"
    autorizacion = [
        (f"{codigo} N°", comprobante.codigo_autorizacion),
        (f"Vencimiento {codigo}" if codigo == "CAE" else "Vigencia CAEA hasta", _fecha(comprobante.vencimiento_autorizacion)),
    ]
    texto_asociado = None
    if asociado is not None:
        texto_asociado = (
            f"Comprobante asociado: {asociado.nombre_tipo} {asociado.numero_formateado} del {_fecha(asociado.fecha_comprobante)}"
        )
    return DocumentoFiscal(
        letra=comprobante.clase,
        codigo_tipo=f"COD. {comprobante.tipo_comprobante:03d}",
        nombre_tipo=comprobante.nombre_tipo,
        numero=comprobante.numero_formateado,
        fecha=_fecha(comprobante.fecha_comprobante),
        emisor=datos_emisor,
        receptor=datos_receptor,
        renglones=renglones,
        totales=totales,
        leyendas=leyendas,
        transparencia=transparencia,
        autorizacion=autorizacion,
        asociado=texto_asociado,
        qr_url=url_qr(comprobante),
        ambiente_prueba=comprobante.ambiente != "PRODUCCION",
    )
