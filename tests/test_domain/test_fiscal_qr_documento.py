"""V1.16-E: QR oficial (vector de la especificación de ARCA) y documento imprimible."""

import base64
import json

import pytest

from domain.fiscal import qr
from domain.fiscal.comprobante import ComprobanteFiscal, SnapshotEmisor, SnapshotReceptor
from domain.fiscal.documento import armar
from domain.fiscal.iva import AlicuotaFiscal, ImportesFiscales, LineaFiscal, TratamientoIva
from excepciones import OperacionFiscalInvalidaError

# Vector oficial: «Especificaciones del QR incluido en las facturas electrónicas» (ARCA).
JSON_OFICIAL = (
    '{"ver":1,"fecha":"2020-10-13","cuit":30000000007,"ptoVta":10,"tipoCmp":1,"nroCmp":94,"importe":12100,'
    '"moneda":"DOL","ctz":65,"tipoDocRec":80,"nroDocRec":20000000001,"tipoCodAut":"E","codAut":70417054367476}'
)
BASE64_OFICIAL = (
    "eyJ2ZXIiOjEsImZlY2hhIjoiMjAyMC0xMC0xMyIsImN1aXQiOjMwMDAwMDAwMDA3LCJwdG9WdGEiOjEwLCJ0aXBvQ21wIjoxLCJucm9DbXAiO"
    "jk0LCJpbXBvcnRlIjoxMjEwMCwibW9uZWRhIjoiRE9MIiwiY3R6Ijo2NSwidGlwb0RvY1JlYyI6ODAsIm5yb0RvY1JlYyI6MjAwMDAwMDAwMDEsInRpcG9Db2RBdX"
    "QiOiJFIiwiY29kQXV0Ijo3MDQxNzA1NDM2NzQ3Nn0="
)


def _comprobante(estado="AUTORIZADO", clase="B", modalidad="CAE", doc_tipo=99, doc_nro="0", total=12100, cae="70417054367476", **extra):
    alicuotas = (AlicuotaFiscal(5, 10000, 2100),) if clase != "C" and total == 12100 else ()
    neto, iva = (10000, 2100) if alicuotas else (total, 0)
    datos = dict(
        id=1, venta_id=1, rol="PRINCIPAL", comprobante_asociado_id=None, ambiente="HOMOLOGACION", modalidad=modalidad,
        caea_id=1 if modalidad == "CAEA" else None, punto_venta=10, tipo_comprobante=6, clase=clase, numero=94,
        fecha_comprobante="2020-10-13", fecha_hora_generacion=None, concepto=1, moneda="PES", cotizacion="1",
        emisor=SnapshotEmisor("30000000007", "Emisor SA", "RESPONSABLE_INSCRIPTO", "Calle 1", None, None, 10),
        receptor=SnapshotReceptor("CONSUMIDOR_FINAL", 5, doc_tipo, doc_nro, "Consumidor final"),
        importes=ImportesFiscales(total, 0, neto, 0, 0, iva, alicuotas),
        estado=estado, resultado="A" if estado == "AUTORIZADO" else None,
        codigo_autorizacion=cae if estado == "AUTORIZADO" else None,
        vencimiento_autorizacion="2020-10-23" if estado == "AUTORIZADO" else None, fecha_proceso=None,
        observaciones=[], errores=[], contenido_hash="0" * 64, fecha_creacion="", fecha_actualizacion="",
        lineas=(LineaFiscal(1, "779", "Alfajor", 1, total, total, TratamientoIva("GRAVADO", 5)),),
    )
    datos.update(extra)
    return ComprobanteFiscal(**datos)


class TestQR:
    def test_vector_oficial(self):
        datos = json.loads(JSON_OFICIAL)
        assert qr.json_qr(datos) == JSON_OFICIAL
        assert qr.url_qr_desde_datos(datos) == "https://www.arca.gob.ar/fe/qr/?p=" + BASE64_OFICIAL

    def test_datos_de_un_autorizado(self):
        datos = qr.datos_qr(_comprobante())
        assert list(datos) == ["ver", "fecha", "cuit", "ptoVta", "tipoCmp", "nroCmp", "importe", "moneda", "ctz", "tipoCodAut", "codAut"]
        assert (datos["importe"], datos["ctz"], datos["tipoCodAut"], datos["codAut"]) == (121, 1, "E", 70417054367476)

    def test_receptor_identificado_lleva_documento(self):
        datos = qr.datos_qr(_comprobante(doc_tipo=80, doc_nro="20000000001"))
        assert (datos["tipoDocRec"], datos["nroDocRec"]) == (80, 20000000001)

    def test_importe_con_centavos(self):
        texto = qr.json_qr(qr.datos_qr(_comprobante(clase="C", total=12150)))
        assert '"importe":121.50' in texto

    def test_caea_usa_tipo_a(self):
        assert qr.datos_qr(_comprobante(modalidad="CAEA", estado="CAEA_PENDIENTE_INFORMAR", codigo_autorizacion="31234567890123",
                                        vencimiento_autorizacion="2020-10-15"))["tipoCodAut"] == "A"

    @pytest.mark.parametrize("estado", ["PENDIENTE", "RECHAZADO", "INCIERTO", "ENVIANDO", "DESCARTADO"])
    def test_sin_autorizacion_no_hay_qr(self, estado):
        with pytest.raises(OperacionFiscalInvalidaError):
            qr.url_qr(_comprobante(estado=estado))

    def test_base64_decodifica_al_json(self):
        url = qr.url_qr(_comprobante())
        assert json.loads(base64.b64decode(url.split("?p=")[1]))["nroCmp"] == 94


class TestDocumento:
    def test_factura_b_con_transparencia_fiscal(self):
        documento = armar(_comprobante())
        assert documento.letra == "B" and documento.codigo_tipo == "COD. 006"
        assert ("IVA Contenido", "$ 21,00") in documento.transparencia
        assert "A CONSUMIDOR FINAL" in documento.leyendas
        assert documento.numero == "00010-00000094" and documento.ambiente_prueba

    def test_factura_a_discrimina_iva(self):
        documento = armar(_comprobante(clase="A"))
        assert ("Importe neto gravado", "$ 100,00") in documento.totales
        assert ("IVA 21%", "$ 21,00") in documento.totales
        assert documento.transparencia == []

    def test_no_autorizado_no_tiene_documento(self):
        with pytest.raises(OperacionFiscalInvalidaError):
            armar(_comprobante(estado="PENDIENTE"))
