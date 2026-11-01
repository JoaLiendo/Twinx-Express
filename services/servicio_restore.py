"""Restauración segura de `kiosco.db` + `imagenes_productos/` desde un
ZIP de backup.

Solo se invoca con la aplicación cerrada (ver `KioscoApp.exe
--restore`, en `lanzador.py`): nunca corre dentro del servidor web en
ejecución. El ZIP se valida por completo con el mismo criterio que usa
la creación de backups (`services.formato_backup.validar_backup`) antes
de tocar la instalación actual, y cualquier fallo durante el reemplazo
revierte a la instalación anterior desde un rollback temporal. Solo
librería estándar.
"""

import secrets
import shutil
import tempfile
from pathlib import Path

from config import DIRECTORIO_DATA
from excepciones import BackupInvalidoError, ErrorRestore
from services.formato_backup import CARPETA_IMAGENES, NOMBRE_DB, validar_backup


def validar_y_extraer_backup(ruta_zip, directorio_extraccion: Path) -> Path:
    """Valida un ZIP de backup y lo extrae dentro de `directorio_extraccion`,
    sin tocar la instalación actual. Devuelve la ruta al subdirectorio
    `data/` ya extraído y validado."""
    try:
        validar_backup(ruta_zip, directorio_extraccion)
    except BackupInvalidoError as error:
        raise ErrorRestore(str(error)) from error
    datos_extraidos = directorio_extraccion / "data"
    (datos_extraidos / CARPETA_IMAGENES).mkdir(exist_ok=True)
    return datos_extraidos


def restaurar_desde_backup(ruta_zip) -> None:
    """Restaura `DIRECTORIO_DATA` completo a partir de un ZIP de backup.

    Nunca modifica la instalación actual hasta que el ZIP pasó todas
    las validaciones de `validar_y_extraer_backup`. Si algo falla
    durante el reemplazo, la instalación anterior se restaura desde un
    rollback temporal (nunca se hace restore sobre una instalación
    parcialmente modificada).
    """
    directorio_data = DIRECTORIO_DATA
    directorio_padre = directorio_data.parent
    directorio_padre.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=directorio_padre, prefix=".kioscoapp_restore_tmp_") as tmp:
        datos_extraidos = validar_y_extraer_backup(ruta_zip, Path(tmp))

        ruta_rollback = directorio_padre / f".kioscoapp_rollback_{secrets.token_hex(8)}"
        habia_data_previa = directorio_data.exists()
        if habia_data_previa:
            directorio_data.rename(ruta_rollback)

        try:
            datos_extraidos.rename(directorio_data)
            if not (directorio_data / NOMBRE_DB).is_file():
                raise ErrorRestore("La instalación restaurada no pasó la validación final.")
        except Exception:
            if directorio_data.exists():
                shutil.rmtree(directorio_data, ignore_errors=True)
            if habia_data_previa:
                ruta_rollback.rename(directorio_data)
            raise

    if habia_data_previa and ruta_rollback.exists():
        shutil.rmtree(ruta_rollback, ignore_errors=True)
