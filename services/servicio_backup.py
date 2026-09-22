"""Backup consistente de `kiosco.db` + `imagenes_productos/` en un
único ZIP.

El bloqueo (`ControlEscrituras`) se mantiene únicamente mientras se
toma el snapshot de la DB y se copian las imágenes a un directorio
temporal: una vez que esas dos copias están aisladas, ya no dependen
de lo que siga escribiendo la aplicación, así que el resto del proceso
(construir el ZIP, validarlo, publicarlo) corre sin el lock. Solo
librería estándar.
"""

import logging
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from config import (
    ANTIGUEDAD_MINIMA_BACKUP_AUTOMATICO_HORAS,
    DIRECTORIO_IMAGENES_PRODUCTOS,
    RUTA_BASE_DATOS,
)
from excepciones import ErrorBackup

logger = logging.getLogger(__name__)

_NOMBRE_DB_EN_ZIP = "kiosco.db"
_NOMBRE_CARPETA_IMAGENES_EN_ZIP = "imagenes_productos"
_PATRON_NOMBRE_BACKUP = re.compile(r"^KioscoApp_backup_(\d{4}-\d{2}-\d{2}_\d{4})\.zip$")
_FORMATO_FECHA_EN_NOMBRE = "%Y-%m-%d_%H%M"


def _snapshot_base_datos(ruta_origen: Path, ruta_destino: Path) -> None:
    """Copia consistente de la DB usando la Online Backup API de
    SQLite (`sqlite3.Connection.backup`), no una copia de archivo a
    nivel de sistema operativo."""
    conexion_origen = sqlite3.connect(ruta_origen)
    try:
        conexion_destino = sqlite3.connect(ruta_destino)
        try:
            conexion_origen.backup(conexion_destino)
        finally:
            conexion_destino.close()
    finally:
        conexion_origen.close()


def _construir_zip(directorio_datos: Path, ruta_zip: Path) -> None:
    ruta_db = directorio_datos / _NOMBRE_DB_EN_ZIP
    dir_imagenes = directorio_datos / _NOMBRE_CARPETA_IMAGENES_EN_ZIP

    with zipfile.ZipFile(ruta_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(ruta_db, _NOMBRE_DB_EN_ZIP)
        archivos = sorted(p for p in dir_imagenes.rglob("*") if p.is_file())
        for archivo in archivos:
            nombre_en_zip = f"{_NOMBRE_CARPETA_IMAGENES_EN_ZIP}/{archivo.relative_to(dir_imagenes).as_posix()}"
            zf.write(archivo, nombre_en_zip)
        if not archivos:
            # conservar la carpeta aunque no tenga imágenes todavía.
            zf.writestr(f"{_NOMBRE_CARPETA_IMAGENES_EN_ZIP}/", "")


def crear_backup(directorio_destino: Path, control_escrituras) -> Path:
    """Genera `KioscoApp_backup_<fecha>_<hora>.zip` dentro de
    `directorio_destino` y devuelve su ruta.

    `control_escrituras.iniciar_backup()` es la única sección crítica
    que decide, de forma atómica, si este backup puede arrancar (nunca
    dos a la vez) y espera a que las escrituras de negocio ya en curso
    terminen -- nunca cuenta esta misma operación como una escritura de
    negocio, así que no puede quedar esperándose a sí misma. El turno
    se libera pase lo que pase (`try/finally`), incluso si el snapshot
    o la copia fallan, y solo se mantiene mientras se copian la DB y
    las imágenes a un directorio temporal (ver docstring del módulo).
    """
    directorio_destino.mkdir(parents=True, exist_ok=True)
    nombre_final = f"KioscoApp_backup_{datetime.now().strftime('%Y-%m-%d_%H%M')}.zip"
    ruta_final = directorio_destino / nombre_final

    with tempfile.TemporaryDirectory(dir=directorio_destino, prefix=".kioscoapp_backup_tmp_") as tmp:
        tmp_path = Path(tmp)
        datos_tmp = tmp_path / "data"
        datos_tmp.mkdir()

        if not control_escrituras.iniciar_backup(timeout=30):
            raise ErrorBackup("Ya hay un backup en curso. Esperá a que termine e intentá de nuevo.")
        try:
            _snapshot_base_datos(RUTA_BASE_DATOS, datos_tmp / _NOMBRE_DB_EN_ZIP)
            shutil.copytree(DIRECTORIO_IMAGENES_PRODUCTOS, datos_tmp / _NOMBRE_CARPETA_IMAGENES_EN_ZIP)
        finally:
            control_escrituras.finalizar_backup()

        zip_temporal = tmp_path / "backup.zip.tmp"
        _construir_zip(datos_tmp, zip_temporal)

        with zipfile.ZipFile(zip_temporal) as zf:
            if zf.testzip() is not None:
                raise ErrorBackup("El backup generado quedó corrupto (falló la verificación interna).")

        zip_temporal.replace(ruta_final)

    return ruta_final


def _fecha_ultimo_backup(directorio_backups: Path) -> datetime | None:
    """Última fecha entre los backups reconocibles de `directorio_backups`,
    o `None` si no hay ninguno (carpeta inexistente, vacía, o con archivos
    que no siguen el nombre exacto que produce `crear_backup`).

    La fecha se toma del propio nombre del archivo (ya la lleva embebida),
    no de su `mtime`: así no depende de que el archivo no haya sido tocado
    (copiado, restaurado) después de creado.
    """
    fechas = []
    for ruta in directorio_backups.glob("KioscoApp_backup_*.zip"):
        coincidencia = _PATRON_NOMBRE_BACKUP.match(ruta.name)
        if coincidencia is None:
            continue
        fechas.append(datetime.strptime(coincidencia.group(1), _FORMATO_FECHA_EN_NOMBRE))
    return max(fechas) if fechas else None


def ejecutar_backup_automatico_si_corresponde(
    directorio_destino: Path,
    control_escrituras,
    antiguedad_minima_horas: float = ANTIGUEDAD_MINIMA_BACKUP_AUTOMATICO_HORAS,
) -> Path | None:
    """Genera un backup nuevo solo si el más reciente ya existente supera
    `antiguedad_minima_horas` (o no existe ninguno todavía). Pensado para
    llamarse una única vez al iniciar la aplicación (ver `lifespan` en
    `interfaces/web/app.py`), en un hilo aparte que no bloquee el arranque.

    Nunca propaga una excepción: es el límite exterior de un flujo
    "dispar-y-olvidar" sin ningún llamador esperando el resultado, así que
    dejar escapar el error no se lo comunicaría a nadie -- solo perdería el
    diagnóstico. Se registra por `logging` y el próximo arranque (o el
    próximo backup manual) vuelve a intentarlo con normalidad, sin
    reintentos inmediatos que puedan disparar backups duplicados.

    Reutiliza `crear_backup` y `control_escrituras` tal cual: un backup
    manual ya en curso en este mismo proceso hace que `crear_backup` levante
    `ErrorBackup` (ver `ControlEscrituras.iniciar_backup`), que acá se trata
    igual que cualquier otro fallo -- se registra y no se reintenta, porque
    el backup manual que sí está en curso ya cubre el objetivo.
    """
    fecha_ultimo = _fecha_ultimo_backup(directorio_destino) if directorio_destino.exists() else None
    if fecha_ultimo is not None:
        limite = fecha_ultimo + timedelta(hours=antiguedad_minima_horas)
        if datetime.now() < limite:
            logger.info(
                "Backup automático omitido: el último backup (%s) todavía no supera "
                "la antigüedad mínima de %s horas.",
                fecha_ultimo,
                antiguedad_minima_horas,
            )
            return None

    try:
        ruta_zip = crear_backup(directorio_destino, control_escrituras)
    except ErrorBackup as error:
        logger.warning("Backup automático omitido: %s", error)
        return None
    except Exception:
        logger.exception("Backup automático falló de forma inesperada.")
        return None

    logger.info("Backup automático creado: %s", ruta_zip.name)
    return ruta_zip
