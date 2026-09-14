"""Backup consistente de `kiosco.db` + `imagenes_productos/` en un
único ZIP.

El bloqueo (`ControlEscrituras`) se mantiene únicamente mientras se
toma el snapshot de la DB y se copian las imágenes a un directorio
temporal: una vez que esas dos copias están aisladas, ya no dependen
de lo que siga escribiendo la aplicación, así que el resto del proceso
(construir el ZIP, validarlo, publicarlo) corre sin el lock. Solo
librería estándar.
"""

import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from config import DIRECTORIO_IMAGENES_PRODUCTOS, RUTA_BASE_DATOS
from excepciones import ErrorBackup

_NOMBRE_DB_EN_ZIP = "kiosco.db"
_NOMBRE_CARPETA_IMAGENES_EN_ZIP = "imagenes_productos"


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
