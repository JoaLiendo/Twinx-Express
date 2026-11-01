"""Restauración segura de `kiosco.db` + `imagenes_productos/` desde un
ZIP de backup.

Solo se invoca con la aplicación cerrada (ver `KioscoApp.exe --restore`
y `--rollback`, en `lanzador.py`): nunca corre dentro del servidor web en
ejecución. Orden, pensado para que siempre quede un camino de vuelta:

1. el ZIP se valida por completo (`formato_backup.validar_backup`) y su
   esquema tiene que ser compatible con la versión que va a abrir la base;
2. se protege el estado actual con un backup `SEGURIDAD` -- si la base
   actual está tan dañada que ni eso es posible, la carpeta `data/`
   reemplazada se conserva entera como evidencia en vez de borrarse;
3. se reemplaza `data/` con un renombrado (mismo disco) y se verifica la
   base restaurada; ante cualquier fallo vuelve la instalación anterior.
"""

import logging
import secrets
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import db.conexion as modulo_conexion
from config import DIRECTORIO_DATA
from db.archivo_sqlite import registrar_auditoria_en_archivo, verificar_archivo
from domain.auditoria import ACCION_RESTORE, normalizar
from domain.backup import TIPO_SEGURIDAD
from excepciones import BackupInvalidoError, ErrorBackup, ErrorBaseDatos, ErrorRestore
from services import servicio_backup
from services.control_escrituras import ControlEscrituras
from services.formato_backup import CARPETA_IMAGENES, NOMBRE_DB, InfoBackup, validar_backup

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResultadoRestore:
    info: InfoBackup
    backup_seguridad: Path | None
    """Copia del estado reemplazado, o `None` si no había datos o no se pudo."""
    datos_reemplazados: Path | None
    """Carpeta `data/` anterior conservada tal cual cuando no hubo backup de seguridad."""


def _migraciones_de_esta_version() -> set[str]:
    return {ruta.name for ruta in modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")}


def validar_y_extraer_backup(
    ruta_zip, directorio_extraccion: Path, migraciones_compatibles: Iterable[str] | None = None
) -> tuple[Path, InfoBackup]:
    """Valida un ZIP de backup (contenido y compatibilidad de esquema) y lo
    extrae dentro de `directorio_extraccion`, sin tocar la instalación actual.
    Devuelve la carpeta `data/` extraída y la información del backup."""
    try:
        info = validar_backup(ruta_zip, directorio_extraccion)
    except BackupInvalidoError as error:
        raise ErrorRestore(str(error)) from error
    conocidas = set(migraciones_compatibles) if migraciones_compatibles is not None else _migraciones_de_esta_version()
    futuras = sorted(set(info.migraciones) - conocidas)
    if futuras:
        raise ErrorRestore(
            "El backup es de una versión más nueva de Twinx Express que la que lo va a abrir "
            f"(cambio de estructura desconocido: {futuras[-1]}). No se restauró nada."
        )
    datos_extraidos = directorio_extraccion / "data"
    (datos_extraidos / CARPETA_IMAGENES).mkdir(exist_ok=True)
    return datos_extraidos, info


def _proteger_estado_actual(directorio_data: Path, directorio_backups: Path) -> Path | None:
    if not (directorio_data / NOMBRE_DB).is_file():
        return None
    try:
        ruta = servicio_backup.crear_backup(
            directorio_backups, ControlEscrituras(), tipo=TIPO_SEGURIDAD, origen_datos=directorio_data
        )
    except ErrorBackup as error:
        logger.warning("No se pudo crear el backup de seguridad del estado actual: %s", error)
        return None
    logger.info("Backup de seguridad del estado actual: %s", ruta.name)
    return ruta


def restaurar_desde_backup(
    ruta_zip,
    *,
    migraciones_compatibles: Iterable[str] | None = None,
    accion_auditoria: str = ACCION_RESTORE,
    directorio_backups: Path | None = None,
) -> ResultadoRestore:
    """Restaura `DIRECTORIO_DATA` completo a partir de un ZIP de backup.

    `migraciones_compatibles`: las que conoce la versión que va a abrir la
    base (por defecto, esta); el rollback pasa las de la versión anterior.

    Raises:
        ErrorRestore: backup inválido o incompatible, o la base restaurada no
            pasó la verificación final (en ambos casos la instalación queda
            como estaba).
    """
    directorio_data = DIRECTORIO_DATA
    directorio_padre = directorio_data.parent
    directorio_padre.mkdir(parents=True, exist_ok=True)
    directorio_backups = directorio_backups or servicio_backup.DIRECTORIO_BACKUPS

    with tempfile.TemporaryDirectory(dir=directorio_padre, prefix=".kioscoapp_restore_tmp_") as tmp:
        datos_extraidos, info = validar_y_extraer_backup(ruta_zip, Path(tmp), migraciones_compatibles)
        seguridad = _proteger_estado_actual(directorio_data, directorio_backups)

        ruta_rollback = directorio_padre / f".kioscoapp_rollback_{secrets.token_hex(8)}"
        habia_data_previa = directorio_data.exists()
        if habia_data_previa:
            directorio_data.rename(ruta_rollback)

        try:
            datos_extraidos.rename(directorio_data)
            try:
                verificacion = verificar_archivo(directorio_data / NOMBRE_DB)
            except ErrorBaseDatos as error:
                raise ErrorRestore(f"La instalación restaurada no pasó la validación final: {error}") from error
            if not verificacion.es_valida:
                raise ErrorRestore("La instalación restaurada no pasó la validación final de integridad.")
        except Exception:
            if directorio_data.exists():
                shutil.rmtree(directorio_data, ignore_errors=True)
            if habia_data_previa:
                ruta_rollback.rename(directorio_data)
            raise

    datos_reemplazados = None
    if habia_data_previa and ruta_rollback.exists():
        if seguridad is not None:
            shutil.rmtree(ruta_rollback, ignore_errors=True)
        else:
            # Sin backup de seguridad (base actual dañada): se conserva la carpeta tal cual.
            datos_reemplazados = directorio_padre / f"data_reemplazada_{datetime.now():%Y-%m-%d_%H%M%S}"
            ruta_rollback.rename(datos_reemplazados)
            logger.warning("Datos reemplazados conservados en %s", datos_reemplazados)

    _auditar_restore(directorio_data / NOMBRE_DB, accion_auditoria, Path(ruta_zip).name, seguridad)
    logger.info("Datos restaurados desde %s", Path(ruta_zip).name)
    return ResultadoRestore(info, seguridad, datos_reemplazados)


def _auditar_restore(ruta_db: Path, accion: str, nombre_backup: str, seguridad: Path | None) -> None:
    resumen = f"Restaurado desde {nombre_backup}" + (f"; estado anterior en {seguridad.name}" if seguridad else "")
    try:
        registrar_auditoria_en_archivo(ruta_db, accion, "BACKUP", normalizar(accion, resumen))
    except ErrorBaseDatos as error:
        logger.warning("No se pudo registrar el restore en la auditoría de la base restaurada: %s", error)
