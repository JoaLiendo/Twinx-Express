"""Códigos de las tablas del sistema de ARCA que Twinx necesita nombrar (único lugar del proyecto).

ARCA publica estas tablas en los métodos `FEParamGet*` de WSFEv1; la tabla completa NO se copia acá:
la caché de parámetros (`services.fiscal.servicio_parametros`) es la fuente para validar que un código
siga vigente. Solo se nombran los códigos que una regla de Twinx usa, para no dispersar números mágicos.

Fuente: tablas del sistema WSFEv1 (FEParamGetTiposCbte, FEParamGetTiposDoc, FEParamGetTiposIva,
FEParamGetTiposConcepto, FEParamGetTiposMonedas, FEParamGetCondicionIvaReceptor), contrastadas con el
WSDL vigente de homologación (https://wswhomo.afip.gov.ar/wsfev1/service.asmx?WSDL) el 01/11/2026.
"""

from decimal import Decimal

# Tipos de comprobante (FEParamGetTiposCbte).
FACTURA_A = 1
NOTA_DEBITO_A = 2
NOTA_CREDITO_A = 3
FACTURA_B = 6
NOTA_DEBITO_B = 7
NOTA_CREDITO_B = 8
FACTURA_C = 11
NOTA_DEBITO_C = 12
NOTA_CREDITO_C = 13

ROL_PRINCIPAL = "PRINCIPAL"
ROL_NOTA_CREDITO = "NOTA_CREDITO"
ROL_NOTA_DEBITO = "NOTA_DEBITO"

TIPO_POR_CLASE_Y_ROL = {
    ("A", ROL_PRINCIPAL): FACTURA_A,
    ("A", ROL_NOTA_DEBITO): NOTA_DEBITO_A,
    ("A", ROL_NOTA_CREDITO): NOTA_CREDITO_A,
    ("B", ROL_PRINCIPAL): FACTURA_B,
    ("B", ROL_NOTA_DEBITO): NOTA_DEBITO_B,
    ("B", ROL_NOTA_CREDITO): NOTA_CREDITO_B,
    ("C", ROL_PRINCIPAL): FACTURA_C,
    ("C", ROL_NOTA_DEBITO): NOTA_DEBITO_C,
    ("C", ROL_NOTA_CREDITO): NOTA_CREDITO_C,
}

NOMBRE_TIPO_COMPROBANTE = {
    FACTURA_A: "Factura A",
    NOTA_DEBITO_A: "Nota de Débito A",
    NOTA_CREDITO_A: "Nota de Crédito A",
    FACTURA_B: "Factura B",
    NOTA_DEBITO_B: "Nota de Débito B",
    NOTA_CREDITO_B: "Nota de Crédito B",
    FACTURA_C: "Factura C",
    NOTA_DEBITO_C: "Nota de Débito C",
    NOTA_CREDITO_C: "Nota de Crédito C",
}

# Tipos de documento del receptor (FEParamGetTiposDoc).
DOC_CUIT = 80
DOC_SIN_IDENTIFICAR = 99
"""Consumidor final anónimo: DocTipo 99 con DocNro 0."""

# Concepto (FEParamGetTiposConcepto): un kiosco vende productos.
CONCEPTO_PRODUCTOS = 1

# Consecutividad: el número o la fecha no corresponden al próximo a autorizar.
ERROR_NUMERO_NO_CONSECUTIVO = 10016

# Moneda (FEParamGetTiposMonedas): Twinx factura solo en pesos, cotización 1.
MONEDA_PESOS = "PES"
COTIZACION_PESOS = Decimal("1")

# Alícuotas de IVA (FEParamGetTiposIva): id -> tasa. Al usarse se validan contra la caché de ARCA
# (id presente y descripción con la misma tasa); si ARCA dejara de publicarla, se bloquea.
ALICUOTAS_IVA = {
    3: Decimal("0"),
    9: Decimal("0.025"),
    8: Decimal("0.05"),
    4: Decimal("0.105"),
    5: Decimal("0.21"),
    6: Decimal("0.27"),
}

# Condición frente al IVA del receptor (FEParamGetCondicionIvaReceptor, campo obligatorio
# `CondicionIVAReceptorId`). Mapea la condición fiscal interna de V1.14 (`domain.cliente`); esta es la
# única capa que lo traduce. La caché valida que el id siga habilitado para la clase del comprobante.
CONDICION_IVA_RECEPTOR = {
    "RESPONSABLE_INSCRIPTO": 1,
    "EXENTO": 4,
    "CONSUMIDOR_FINAL": 5,
    "MONOTRIBUTO": 6,
}

# EmisionTipo de FEParamGetPtosVenta: ARCA informa la modalidad de cada punto de venta.
EMISION_CAE = "CAE"
EMISION_CAEA = "CAEA"


def etiqueta_alicuota(alicuota_iva_id: int) -> str:
    """'21%', '10,5%', '0%'... para pantalla."""
    tasa = ALICUOTAS_IVA[alicuota_iva_id] * 100
    texto = format(tasa.normalize(), "f")
    return texto.replace(".", ",") + "%"
