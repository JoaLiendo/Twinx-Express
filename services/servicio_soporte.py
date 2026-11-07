"""Asistencia local (V1.18): información técnica sanitizada de esta instalación y un archivo de diagnóstico
para enviar al soporte. Todo es de solo lectura y local: no hay servicios externos.

Qué incluye: versión, entorno, ubicación de los datos, estado de la base (migraciones, integridad rápida,
contadores), estado de los backups y los últimos avisos del registro. Qué NO incluye nunca: la base de
datos, contraseñas, tokens, cookies, el material fiscal (certificados, claves, tickets de acceso) ni el
contenido de ventas, clientes o productos. El registro se copia con la misma máscara de datos sensibles
que usa `logging_config`.
"""

import io
import json
import logging
import platform
import sys
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import config
import db.conexion as modulo_conexion
from db import instancia_unica
from db.repositorios import soporte as repositorio_soporte
from excepciones import ErrorBaseDatos
from logging_config import NOMBRE_ARCHIVO_LOG, enmascarar_datos_sensibles
from services import servicio_backup
from version import NOMBRE_APLICACION, VERSION

logger = logging.getLogger(__name__)

LINEAS_DE_LOG_EN_PANTALLA = 20
LINEAS_DE_LOG_EN_DIAGNOSTICO = 2000
_NIVELES_RELEVANTES = ("[WARNING]", "[ERROR]", "[CRITICAL]")


@dataclass(frozen=True)
class EstadoBase:
    existe: bool
    tamano_bytes: int
    integridad: str
    migraciones_aplicadas: int
    ultima_migracion: str | None
    migraciones_pendientes: bool
    migraciones_desconocidas: list[str]
    version_registrada: str | None
    contadores: dict[str, int]
    error: str | None = None

    @property
    def integra(self) -> bool:
        return self.error is None and self.integridad == "ok"


@dataclass(frozen=True)
class InformacionSoporte:
    aplicacion: str
    version: str
    ejecutable: bool
    python: str
    sistema: str
    generado: str
    directorio_datos: str
    ruta_base: str
    directorio_backups: str
    directorio_logs: str
    directorio_versiones: str
    ruta_lock: str
    base: EstadoBase
    ultimo_backup: str | None
    fecha_ultimo_backup: str | None
    alerta_backup: str | None
    destino_secundario: str | None
    versiones_conservadas: list[str]
    ruta_log: str
    log_existe: bool
    log_tamano_bytes: int
    ultimos_avisos: list[str]


def _estado_base() -> EstadoBase:
    ruta = modulo_conexion.RUTA_BASE_DATOS
    # Sin archivo no se abre ninguna conexión: abrirla crearía una base vacía como efecto secundario.
    if not ruta.is_file():
        return EstadoBase(False, 0, "sin base", 0, None, False, [], None, {})
    tamano = ruta.stat().st_size
    try:
        aplicadas = modulo_conexion.migraciones_aplicadas()
        return EstadoBase(
            existe=True,
            tamano_bytes=tamano,
            integridad=modulo_conexion.verificar_integridad(),
            migraciones_aplicadas=len(aplicadas),
            ultima_migracion=aplicadas[-1] if aplicadas else None,
            migraciones_pendientes=modulo_conexion.hay_migraciones_pendientes_en_base_existente(),
            migraciones_desconocidas=modulo_conexion.migraciones_desconocidas(),
            version_registrada=servicio_backup.version_registrada_en_la_base(),
            contadores=repositorio_soporte.contadores(),
        )
    except ErrorBaseDatos as error:
        logger.error("No se pudo leer el estado de la base para soporte: %s", error)
        return EstadoBase(True, tamano, "error", 0, None, False, [], None, {}, error=str(error))


# El access log de uvicorn registra cada URL con su query string: ahí viajan búsquedas de clientes y
# productos, que son datos de negocio. El diagnóstico no lo incluye; los errores de la app ya quedan en
# los loggers propios.
_MARCA_ACCESS_LOG = "] uvicorn.access: "


def _lineas_del_log(ruta_log: Path, maximo: int) -> list[str]:
    """Últimas `maximo` líneas del registro sin el access log de uvicorn, enmascaradas. Un log ilegible
    devuelve una sola línea que lo dice."""
    if not ruta_log.is_file():
        return []
    try:
        with ruta_log.open("r", encoding="utf-8", errors="replace") as archivo:
            lineas = [linea for linea in archivo if _MARCA_ACCESS_LOG not in linea]
    except OSError as error:
        return [f"(no se pudo leer el registro: {error.strerror or error})"]
    return [enmascarar_datos_sensibles(linea.rstrip("\n")) for linea in lineas[-maximo:]]


def _ultimos_avisos(ruta_log: Path) -> list[str]:
    relevantes = [linea for linea in _lineas_del_log(ruta_log, LINEAS_DE_LOG_EN_DIAGNOSTICO) if any(n in linea for n in _NIVELES_RELEVANTES)]
    return relevantes[-LINEAS_DE_LOG_EN_PANTALLA:]


def obtener_informacion() -> InformacionSoporte:
    """Foto técnica de la instalación para mostrar en `/soporte`. Nunca falla por un detalle: cada parte que
    no se pueda leer se informa como tal."""
    base = _estado_base()  # antes que los backups: leer su estado abre la base (y la crearía si no existe)
    estado = servicio_backup.estado_backups()
    ruta_log = config.DIRECTORIO_LOGS / NOMBRE_ARCHIVO_LOG
    ultimo = estado.ultimo_backup
    return InformacionSoporte(
        aplicacion=NOMBRE_APLICACION,
        version=VERSION,
        ejecutable=bool(getattr(sys, "frozen", False)),
        python=platform.python_version(),
        sistema=f"{platform.system()} {platform.release()} ({platform.machine()})",
        generado=datetime.now().isoformat(timespec="seconds"),
        directorio_datos=str(config.DIRECTORIO_DATA),
        ruta_base=str(modulo_conexion.RUTA_BASE_DATOS),
        directorio_backups=str(servicio_backup.DIRECTORIO_BACKUPS),
        directorio_logs=str(config.DIRECTORIO_LOGS),
        directorio_versiones=str(config.DIRECTORIO_VERSIONES),
        ruta_lock=str(instancia_unica.ruta_lock(modulo_conexion.RUTA_BASE_DATOS)),
        base=base,
        ultimo_backup=ultimo.nombre if ultimo else None,
        fecha_ultimo_backup=ultimo.fecha.isoformat(timespec="seconds") if ultimo else None,
        alerta_backup=f"{estado.alerta.fecha}: {estado.alerta.detalle}" if estado.alerta else None,
        destino_secundario=str(estado.destino_secundario) if estado.destino_secundario else None,
        versiones_conservadas=list(estado.versiones_conservadas),
        ruta_log=str(ruta_log),
        log_existe=ruta_log.is_file(),
        log_tamano_bytes=ruta_log.stat().st_size if ruta_log.is_file() else 0,
        ultimos_avisos=_ultimos_avisos(ruta_log),
    )


def nombre_de_diagnostico(ahora: datetime | None = None) -> str:
    ahora = ahora or datetime.now()
    return f"diagnostico_twinx_{VERSION}_{ahora.strftime('%Y%m%d_%H%M%S')}.zip"


def exportar_diagnostico() -> bytes:
    """ZIP en memoria con `diagnostico.json` (la misma información de la pantalla), `migraciones.txt` y las
    últimas líneas del registro enmascaradas. Sin base de datos, sin secretos, sin datos de negocio."""
    informacion = obtener_informacion()
    ruta_log = Path(informacion.ruta_log)
    try:
        migraciones = modulo_conexion.migraciones_aplicadas()
    except ErrorBaseDatos as error:
        migraciones = [f"(no se pudieron leer: {error})"]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zip_salida:
        zip_salida.writestr("diagnostico.json", json.dumps(asdict(informacion), ensure_ascii=False, indent=2))
        zip_salida.writestr("migraciones.txt", "\n".join(migraciones) + "\n")
        zip_salida.writestr(
            "registro_enmascarado.log", "\n".join(_lineas_del_log(ruta_log, LINEAS_DE_LOG_EN_DIAGNOSTICO)) + "\n"
        )
    return buffer.getvalue()
