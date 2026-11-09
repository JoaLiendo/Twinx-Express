"""Custodia de la historia fiscal fuera de la base (cierre operativo, E5).

`servicio_restore.verificar_historia_fiscal` impide que un restore o rollback de ESTA versión reemplace la
base por una copia sin los comprobantes ya informados a ARCA. Pero la base también puede reemplazarse por
fuera de ese chequeo: con el ejecutable archivado de una versión anterior (`versiones\\1.15.0\\KioscoApp.exe
--restore`), con una copia manual de `data\\` o con un restore hecho con la app cerrada en otra máquina.
En todos esos casos ARCA sigue teniendo los comprobantes y Twinx arrancaría numerando como si no
existieran: pérdida silenciosa.

Por eso se guarda, fuera de `data/` (hermano, junto al material fiscal, nunca dentro de un backup), un
registro de la historia fiscal conocida por esta instalación. Al arrancar se compara con la base: si a la
base le falta algo que la custodia conoce, la aplicación NO inicia y explica cómo recuperar la base o,
si el técnico confirma que la pérdida es deliberada, cómo aceptarla (`--aceptar-historia-fiscal`,
auditado). La custodia se actualiza al arrancar, al cerrar y tras cada cambio de estado fiscal; es una
cota inferior de lo conocido, nunca inventa hechos.
"""

import json
import logging
import os
from pathlib import Path

import config
from db.archivo_sqlite import leer_historia_fiscal
from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from domain.fiscal.historia import (
    CaeaOtorgado,
    ComprobanteConocido,
    HistoriaFiscal,
    InformeSinMovimiento,
    describir,
    no_cubierta_por,
)
from excepciones import ErrorBaseDatos, HistoriaFiscalPerdidaError

logger = logging.getLogger(__name__)

NOMBRE_ARCHIVO = "historia_fiscal_custodiada.json"
ACCION_HISTORIA_ACEPTADA = "HISTORIA_FISCAL_ACEPTADA"
VERSION_FORMATO = 1

_PROCEDIMIENTO = (
    "Para recuperar la base, restaurá el backup más reciente de tipo SEGURIDAD (o la carpeta "
    "data_reemplazada_*) con KioscoApp.exe --restore. Si el técnico confirma que la pérdida es deliberada "
    "(por ejemplo, se descartó una base de pruebas), ejecutá KioscoApp.exe --aceptar-historia-fiscal con la "
    "aplicación cerrada: queda registrado en la auditoría."
)


def ruta_custodia() -> Path:
    """Junto al material fiscal, hermano de `data/`: no viaja en backups ni lo toca un restore."""
    return Path(config.DIRECTORIO_SECRETOS_FISCALES) / NOMBRE_ARCHIVO


def _ruta_base() -> Path:
    import db.conexion as modulo_conexion

    return Path(modulo_conexion.RUTA_BASE_DATOS)


def _serializar(historia: HistoriaFiscal) -> dict:
    return {
        "version": VERSION_FORMATO,
        "comprobantes": [c.__dict__ for c in historia.comprobantes],
        "caeas": [c.__dict__ for c in sorted(historia.caeas, key=lambda c: (c.ambiente, c.codigo))],
        "informes": [i.__dict__ for i in sorted(historia.informes, key=lambda i: (i.ambiente, i.codigo_caea, i.punto_venta))],
    }


def _deserializar(datos: dict) -> HistoriaFiscal:
    return HistoriaFiscal(
        comprobantes=tuple(ComprobanteConocido(**c) for c in datos.get("comprobantes", [])),
        caeas=frozenset(CaeaOtorgado(**c) for c in datos.get("caeas", [])),
        informes=frozenset(InformeSinMovimiento(**i) for i in datos.get("informes", [])),
    )


def leer_custodia() -> HistoriaFiscal | None:
    """Historia custodiada, o `None` si nunca se registró. Un archivo ilegible cuenta como ausente pero se
    registra en el log (nunca se pierde el arranque por un JSON roto)."""
    ruta = ruta_custodia()
    if not ruta.is_file():
        return None
    try:
        return _deserializar(json.loads(ruta.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError) as error:
        logger.error("La custodia de la historia fiscal no se pudo leer (%s): se ignora", error)
        return None


def _escribir(historia: HistoriaFiscal) -> None:
    ruta = ruta_custodia()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix(".tmp")
    temporal.write_text(json.dumps(_serializar(historia), ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporal, ruta)


def registrar_historia_actual() -> HistoriaFiscal | None:
    """Guarda la historia fiscal de la base actual como custodiada. No escribe nada si la base no tiene
    historia y tampoco había custodia (una instalación sin facturación no deja archivos).

    Raises:
        ErrorBaseDatos: si la base no se puede leer.
    """
    actual = leer_historia_fiscal(_ruta_base())
    if actual.vacia and leer_custodia() is None:
        return None
    _escribir(actual)
    return actual


def actualizar_sin_fallar() -> None:
    """Tras un cambio de estado fiscal: refresca la custodia; un fallo se registra y nunca interrumpe la
    operación fiscal que acaba de confirmarse."""
    try:
        registrar_historia_actual()
    except (ErrorBaseDatos, OSError) as error:
        logger.error("No se pudo actualizar la custodia de la historia fiscal: %s", error)


def verificar_al_iniciar() -> None:
    """Compara la custodia con la base antes de arrancar.

    Raises:
        HistoriaFiscalPerdidaError: la base actual no contiene hechos fiscales que esta instalación ya
            conocía (base reemplazada por fuera del procedimiento seguro). No se arranca.
    """
    custodiada = leer_custodia()
    if custodiada is None or custodiada.vacia:
        return
    try:
        actual = leer_historia_fiscal(_ruta_base())
    except ErrorBaseDatos as error:
        raise HistoriaFiscalPerdidaError(
            "No se pudo leer la historia fiscal de la base actual y esta instalación tiene comprobantes "
            f"informados a ARCA en custodia ({describir(custodiada)}). {_PROCEDIMIENTO}"
        ) from error
    faltante = no_cubierta_por(custodiada, actual)
    if faltante.vacia:
        return
    logger.critical("Arranque bloqueado: la base actual perdió historia fiscal conocida: %s", describir(faltante))
    raise HistoriaFiscalPerdidaError(
        "La base de datos actual NO contiene comprobantes fiscales que esta instalación ya había informado a "
        f"ARCA ({describir(faltante)}). Probablemente se reemplazó la base por una copia anterior fuera del "
        "procedimiento seguro (restore con una versión vieja del programa o copia manual de la carpeta data). "
        f"Twinx Express no inicia para no volver a numerar sobre una secuencia perdida. {_PROCEDIMIENTO}"
    )


def aceptar_historia_actual(motivo: str = "Aceptada por el técnico desde la consola") -> str:
    """`KioscoApp.exe --aceptar-historia-fiscal`: reemplaza la custodia por la historia de la base actual y
    deja auditado qué se dio por perdido. Solo con la aplicación cerrada (lo garantiza el lanzador)."""
    custodiada = leer_custodia()
    actual = leer_historia_fiscal(_ruta_base())
    faltante = no_cubierta_por(custodiada, actual) if custodiada is not None else HistoriaFiscal()
    detalle = describir(faltante) if not faltante.vacia else "sin diferencias"
    with obtener_conexion() as conexion:
        repositorio_auditoria.registrar_en_conexion(
            conexion, None, ACCION_HISTORIA_ACEPTADA, "FISCAL", None, f"{motivo}. Historia dada por perdida: {detalle}"
        )
    _escribir(actual)
    logger.warning("Historia fiscal aceptada por el técnico; dada por perdida: %s", detalle)
    return (
        f"Custodia actualizada con la historia de la base actual. Dado por perdido: {detalle}. "
        "Quedó registrado en la auditoría."
    )
