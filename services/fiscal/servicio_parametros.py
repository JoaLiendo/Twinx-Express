"""Tablas de ARCA en caché y validación de los códigos que va a usar un comprobante.

Se refrescan al validar el punto de venta y, de forma oportunista, cuando la copia supera
`DIAS_VIGENCIA_CACHE`. Si un refresco falla se usa la copia vigente; sin ninguna copia se bloquea.
"""

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from db.repositorios import parametros_arca as repositorio_parametros
from domain.fiscal import tablas_arca
from domain.fiscal.iva import ImportesFiscales
from excepciones import ErrorFiscal, FiscalizacionBloqueadaError
from services.fiscal.arca.wsfe import TABLAS_PARAMETROS
from services.fiscal.gateway import GatewayARCA

logger = logging.getLogger(__name__)

DIAS_VIGENCIA_CACHE = 7
CLASES = ("A", "B", "C")


def _tabla_condiciones(clase: str) -> str:
    return f"CondicionIvaReceptor_{clase}"


def refrescar(gateway: GatewayARCA) -> list[str]:
    """Pide todas las tablas y guarda las que llegaron. Devuelve las que no pudieron refrescarse."""
    fallidas = []
    fuente = f"WSFEv1 {gateway.ambiente.lower()} {datetime.now().isoformat(timespec='seconds')}"
    for tabla in TABLAS_PARAMETROS:
        try:
            repositorio_parametros.guardar(gateway.ambiente, tabla, gateway.parametros(tabla), fuente)
        except ErrorFiscal as error:
            logger.warning("No se pudo refrescar la tabla ARCA %s: %s", tabla, error)
            fallidas.append(tabla)
    for clase in CLASES:
        try:
            repositorio_parametros.guardar(
                gateway.ambiente, _tabla_condiciones(clase), gateway.condiciones_iva_receptor(clase), fuente
            )
        except ErrorFiscal as error:
            logger.warning("No se pudo refrescar la condición IVA receptor clase %s: %s", clase, error)
            fallidas.append(_tabla_condiciones(clase))
    return fallidas


def refrescar_si_vencida(gateway: GatewayARCA) -> None:
    """Refresco oportunista: nunca bloquea la operación si falla (queda la copia anterior)."""
    tabla = repositorio_parametros.obtener(gateway.ambiente, "TiposCbte")
    if tabla is not None:
        antiguedad = (date.today() - date.fromisoformat(tabla.fecha_actualizacion[:10])).days
        if antiguedad < DIAS_VIGENCIA_CACHE:
            return
    refrescar(gateway)


def _tasa_de_descripcion(descripcion: str) -> Decimal | None:
    try:
        return Decimal(descripcion.replace("%", "").replace(",", ".").strip()) / 100
    except InvalidOperation:
        return None


def validar_codigos(
    ambiente: str,
    clase: str,
    tipo: int,
    doc_tipo: int,
    condicion_iva_receptor_id: int,
    importes: ImportesFiscales,
    fecha: date,
) -> None:
    """Exige que cada código ARCA del comprobante figure vigente en la caché del ambiente.

    Raises:
        FiscalizacionBloqueadaError: tabla faltante o código no vigente (con el motivo).
    """
    aaaammdd = fecha.strftime("%Y%m%d")
    motivos = []

    def vigentes(nombre: str):
        tabla = repositorio_parametros.obtener(ambiente, nombre)
        if tabla is None:
            motivos.append(f"Falta la tabla de ARCA «{nombre}»: validá el punto de venta para descargarla.")
            return None
        return {fila.id: fila for fila in tabla.filas if fila.vigente_en(aaaammdd)}

    tipos = vigentes("TiposCbte")
    if tipos is not None and str(tipo) not in tipos:
        motivos.append(f"ARCA no publica vigente el tipo de comprobante {tipo}.")
    documentos = vigentes("TiposDoc")
    if documentos is not None and str(doc_tipo) not in documentos:
        motivos.append(f"ARCA no publica vigente el tipo de documento {doc_tipo}.")
    monedas = vigentes("TiposMonedas")
    if monedas is not None and tablas_arca.MONEDA_PESOS not in monedas:
        motivos.append("ARCA no publica vigente la moneda pesos.")
    condiciones = vigentes(_tabla_condiciones(clase))
    if condiciones is not None and str(condicion_iva_receptor_id) not in condiciones:
        motivos.append(f"La condición IVA del receptor ({condicion_iva_receptor_id}) no está habilitada por ARCA para la clase {clase}.")
    if importes.alicuotas:
        alicuotas = vigentes("TiposIva")
        for alicuota in importes.alicuotas if alicuotas is not None else ():
            fila = alicuotas.get(str(alicuota.alicuota_iva_id))
            if fila is None:
                motivos.append(f"ARCA no publica vigente la alícuota de IVA {alicuota.alicuota_iva_id}.")
            elif _tasa_de_descripcion(fila.descripcion) != tablas_arca.ALICUOTAS_IVA[alicuota.alicuota_iva_id]:
                motivos.append(f"La alícuota {alicuota.alicuota_iva_id} de ARCA («{fila.descripcion}») no coincide con la de Twinx.")
    if motivos:
        raise FiscalizacionBloqueadaError("Los parámetros de ARCA no permiten emitir este comprobante.", motivos)


def tablas(ambiente: str):
    return repositorio_parametros.listar(ambiente)
