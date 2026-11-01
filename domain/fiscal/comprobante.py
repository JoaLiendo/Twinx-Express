"""Comprobante fiscal: estados, snapshots inmutables de emisor y receptor, y el modelo de lectura.

La historia fiscal es el snapshot: un comprobante no vuelve a leer datos mutables del cliente, del
producto ni del perfil del emisor. Si mañana cambian, el comprobante histórico sigue igual.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date

from domain.cliente import Cliente
from domain.fiscal import reglas, tablas_arca
from domain.fiscal.iva import ImportesFiscales, LineaFiscal
from excepciones import FiscalizacionBloqueadaError

PENDIENTE = "PENDIENTE"
ENVIANDO = "ENVIANDO"
AUTORIZADO = "AUTORIZADO"
RECHAZADO = "RECHAZADO"
INCIERTO = "INCIERTO"
DESCARTADO = "DESCARTADO"
CAEA_PENDIENTE_INFORMAR = "CAEA_PENDIENTE_INFORMAR"
CAEA_INFORMADO = "CAEA_INFORMADO"
CAEA_INFORME_RECHAZADO = "CAEA_INFORME_RECHAZADO"

ESTADOS_AUTORIZADOS = frozenset({AUTORIZADO, CAEA_PENDIENTE_INFORMAR, CAEA_INFORMADO, CAEA_INFORME_RECHAZADO})
"""Tienen un código de autorización real de ARCA (CAE o CAEA): son documentos fiscales válidos."""

ESTADOS_TERMINALES_SIN_AUTORIZACION = frozenset({RECHAZADO, DESCARTADO})
"""No consumieron número ni tienen autorización: la venta puede volver a fiscalizarse."""

ESTADOS_EN_VUELO = frozenset({ENVIANDO, INCIERTO})
"""Bloquean la siguiente emisión de su secuencia (punto de venta + tipo) hasta conciliarse."""

ETIQUETAS_ESTADO = {
    PENDIENTE: "Fiscalización pendiente",
    ENVIANDO: "Enviando a ARCA",
    AUTORIZADO: "Autorizada",
    RECHAZADO: "Fiscalización rechazada",
    INCIERTO: "Resultado incierto",
    DESCARTADO: "Descartado",
    CAEA_PENDIENTE_INFORMAR: "Autorizada (CAEA) · pendiente de informar",
    CAEA_INFORMADO: "Autorizada (CAEA) · informada",
    CAEA_INFORME_RECHAZADO: "Autorizada (CAEA) · informe con error",
}

MODALIDAD_CAE = "CAE"
MODALIDAD_CAEA = "CAEA"

AMBIENTE_HOMOLOGACION = "HOMOLOGACION"
AMBIENTE_PRODUCCION = "PRODUCCION"
AMBIENTES = {AMBIENTE_HOMOLOGACION: "Homologación (pruebas)", AMBIENTE_PRODUCCION: "Producción"}


def _json_canonico(datos: dict) -> str:
    return json.dumps(datos, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class SnapshotEmisor:
    cuit: str
    razon_social: str
    condicion_iva: str
    domicilio: str
    ingresos_brutos: str | None
    inicio_actividades: str | None
    punto_venta: int

    def a_json(self) -> str:
        return _json_canonico(asdict(self))

    @classmethod
    def desde_json(cls, texto: str) -> "SnapshotEmisor":
        return cls(**json.loads(texto))

    @property
    def condicion_etiqueta(self) -> str:
        return reglas.CONDICIONES_IVA_EMISOR.get(self.condicion_iva, self.condicion_iva)


@dataclass(frozen=True)
class SnapshotReceptor:
    condicion_iva: str
    condicion_iva_receptor_id: int
    doc_tipo: int
    doc_nro: str
    nombre: str
    domicilio: str | None = None
    cliente_id: int | None = None

    def a_json(self) -> str:
        return _json_canonico(asdict(self))

    @classmethod
    def desde_json(cls, texto: str) -> "SnapshotReceptor":
        return cls(**json.loads(texto))

    @property
    def es_anonimo(self) -> bool:
        return self.doc_tipo == tablas_arca.DOC_SIN_IDENTIFICAR

    @property
    def es_consumidor_final(self) -> bool:
        return self.condicion_iva == "CONSUMIDOR_FINAL"


NOMBRE_CONSUMIDOR_FINAL = "Consumidor final"


def resolver_receptor(cliente: Cliente | None, total_centavos: int, fecha: date) -> SnapshotReceptor:
    """Receptor del comprobante a partir del cliente de la venta (V1.14), sin inventar datos.

    - Sin cliente: consumidor final anónimo, solo por debajo del umbral vigente de identificación.
    - Cliente sin condición fiscal (histórico sin clasificar): se bloquea; nunca se supone una.
    - Inscripto, monotributista o exento: exige CUIT.
    - Consumidor final identificado: con CUIT si lo tiene; sin CUIT solo por debajo del umbral.

    PERSONA/EMPRESA no interviene: la condición fiscal es un dato independiente (V1.14).

    Raises:
        FiscalizacionBloqueadaError: con el dato que hay que completar.
    """
    requiere_identificacion = reglas.consumidor_final_requiere_identificacion(total_centavos, fecha)
    if cliente is None:
        if requiere_identificacion:
            raise FiscalizacionBloqueadaError(
                "La operación alcanza el importe a partir del cual ARCA exige identificar al consumidor final:"
                " asociá a la venta un cliente con CUIT/CUIL."
            )
        return SnapshotReceptor(
            condicion_iva="CONSUMIDOR_FINAL",
            condicion_iva_receptor_id=tablas_arca.CONDICION_IVA_RECEPTOR["CONSUMIDOR_FINAL"],
            doc_tipo=tablas_arca.DOC_SIN_IDENTIFICAR,
            doc_nro="0",
            nombre=NOMBRE_CONSUMIDOR_FINAL,
        )
    if cliente.condicion_fiscal is None:
        raise FiscalizacionBloqueadaError(
            f"El cliente «{cliente.nombre}» no tiene condición fiscal: completala en su ficha antes de fiscalizar."
        )
    nombre = cliente.razon_social or cliente.nombre
    condicion_id = tablas_arca.CONDICION_IVA_RECEPTOR[cliente.condicion_fiscal]
    if cliente.cuit is not None:
        return SnapshotReceptor(
            condicion_iva=cliente.condicion_fiscal,
            condicion_iva_receptor_id=condicion_id,
            doc_tipo=tablas_arca.DOC_CUIT,
            doc_nro=cliente.cuit,
            nombre=nombre,
            domicilio=cliente.direccion,
            cliente_id=cliente.id,
        )
    if cliente.condicion_fiscal != "CONSUMIDOR_FINAL":
        raise FiscalizacionBloqueadaError(
            f"El cliente «{cliente.nombre}» es {cliente.condicion_fiscal.replace('_', ' ').lower()}: cargá su CUIT"
            " antes de fiscalizar."
        )
    if requiere_identificacion:
        raise FiscalizacionBloqueadaError(
            f"La operación alcanza el umbral de identificación de ARCA: cargá el CUIT/CUIL de «{cliente.nombre}»."
        )
    return SnapshotReceptor(
        condicion_iva="CONSUMIDOR_FINAL",
        condicion_iva_receptor_id=condicion_id,
        doc_tipo=tablas_arca.DOC_SIN_IDENTIFICAR,
        doc_nro="0",
        nombre=nombre,
        domicilio=cliente.direccion,
        cliente_id=cliente.id,
    )


def calcular_contenido_hash(
    rol: str,
    asociado_id: int | None,
    ambiente: str,
    emisor: SnapshotEmisor,
    tipo: int,
    receptor: SnapshotReceptor,
    importes: ImportesFiscales,
    lineas: list[LineaFiscal],
) -> str:
    """Huella del contenido fiscal (sin número ni fecha, que se asignan al enviar)."""
    contenido = {
        "rol": rol,
        "asociado": asociado_id,
        "ambiente": ambiente,
        "emisor": asdict(emisor),
        "tipo": tipo,
        "receptor": asdict(receptor),
        "importes": asdict(importes),
        "lineas": [asdict(linea) for linea in lineas],
    }
    return hashlib.sha256(_json_canonico(contenido).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ComprobanteFiscal:
    """Un comprobante fiscal persistido (modelo de lectura)."""

    id: int
    venta_id: int
    rol: str
    comprobante_asociado_id: int | None
    ambiente: str
    modalidad: str
    caea_id: int | None
    punto_venta: int
    tipo_comprobante: int
    clase: str
    numero: int | None
    fecha_comprobante: str | None
    fecha_hora_generacion: str | None
    concepto: int
    moneda: str
    cotizacion: str
    emisor: SnapshotEmisor
    receptor: SnapshotReceptor
    importes: ImportesFiscales
    estado: str
    resultado: str | None
    codigo_autorizacion: str | None
    vencimiento_autorizacion: str | None
    fecha_proceso: str | None
    observaciones: list[dict]
    errores: list[dict]
    contenido_hash: str
    fecha_creacion: str
    fecha_actualizacion: str
    lineas: tuple[LineaFiscal, ...] = field(default=())

    @property
    def autorizado(self) -> bool:
        return self.estado in ESTADOS_AUTORIZADOS

    @property
    def nombre_tipo(self) -> str:
        return tablas_arca.NOMBRE_TIPO_COMPROBANTE.get(self.tipo_comprobante, f"Comprobante {self.tipo_comprobante}")

    @property
    def numero_formateado(self) -> str:
        """`00001-00000023`; sin número todavía, `00001-········`."""
        numero = f"{self.numero:08d}" if self.numero is not None else "--------"
        return f"{self.punto_venta:05d}-{numero}"

    @property
    def etiqueta_estado(self) -> str:
        return ETIQUETAS_ESTADO.get(self.estado, self.estado)

    @property
    def es_nota_credito(self) -> bool:
        return self.rol == tablas_arca.ROL_NOTA_CREDITO
