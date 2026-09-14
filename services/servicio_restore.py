"""Restauración segura de `kiosco.db` + `imagenes_productos/` desde un
ZIP de backup.

Solo se invoca con la aplicación cerrada (ver `KioscoApp.exe
--restore`, en `lanzador.py`): nunca corre dentro del servidor web en
ejecución. El ZIP se valida por completo -- estructura, path
traversal, integridad de SQLite -- antes de tocar la instalación
actual, y cualquier fallo durante el reemplazo revierte a la
instalación anterior desde un rollback temporal. Solo librería
estándar.
"""

import secrets
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from config import DIRECTORIO_DATA
from excepciones import ErrorRestore

_NOMBRE_DB = "kiosco.db"
_PREFIJO_IMAGENES = "imagenes_productos/"
_NOMBRE_CARPETA_IMAGENES = "imagenes_productos"
_LIMITE_TAMANO_DESCOMPRIMIDO = 500 * 1024 * 1024  # 500MB: generoso dado el límite de 2MB por imagen.


def _validar_nombre_seguro(nombre: str) -> None:
    if nombre.startswith("/") or nombre.startswith("\\") or ":" in nombre:
        raise ErrorRestore(f"Nombre de archivo no seguro dentro del backup: '{nombre}'.")
    if ".." in Path(nombre).parts:
        raise ErrorRestore(f"Nombre de archivo no seguro dentro del backup: '{nombre}'.")


def _validar_estructura_y_seguridad(zf: zipfile.ZipFile) -> None:
    tiene_db = False
    tamano_total = 0
    for info in zf.infolist():
        nombre = info.filename
        _validar_nombre_seguro(nombre)

        if nombre == _NOMBRE_DB:
            tiene_db = True
        elif nombre == _PREFIJO_IMAGENES or nombre.startswith(_PREFIJO_IMAGENES):
            pass
        else:
            raise ErrorRestore(f"El backup contiene un archivo inesperado: '{nombre}'.")

        tamano_total += info.file_size
        if tamano_total > _LIMITE_TAMANO_DESCOMPRIMIDO:
            raise ErrorRestore("El backup declara un tamaño descomprimido excesivo.")

    if not tiene_db:
        raise ErrorRestore(f"El backup no contiene '{_NOMBRE_DB}'.")


def _extraer_de_forma_segura(zf: zipfile.ZipFile, destino: Path) -> None:
    destino_resuelto = destino.resolve()
    for info in zf.infolist():
        ruta_destino = (destino / info.filename).resolve()
        if ruta_destino != destino_resuelto and not ruta_destino.is_relative_to(destino_resuelto):
            raise ErrorRestore(f"Ruta insegura dentro del backup: '{info.filename}'.")
    zf.extractall(destino)


def _verificar_integridad_sqlite(ruta_db: Path) -> None:
    try:
        conexion = sqlite3.connect(ruta_db)
    except sqlite3.Error as error:
        raise ErrorRestore(f"No se pudo abrir la base de datos del backup: {error}") from error
    try:
        try:
            resultado = conexion.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.DatabaseError as error:
            raise ErrorRestore(f"El archivo de base de datos del backup está corrupto: {error}") from error
        if resultado is None or resultado[0] != "ok":
            raise ErrorRestore(f"La base de datos del backup no pasó la verificación de integridad: {resultado}")
    finally:
        conexion.close()


def validar_y_extraer_backup(ruta_zip, directorio_extraccion: Path) -> Path:
    """Valida un ZIP de backup y lo extrae dentro de `directorio_extraccion`,
    sin tocar la instalación actual. Devuelve la ruta al subdirectorio
    `data/` ya extraído y validado."""
    ruta_zip = Path(ruta_zip)
    if not ruta_zip.is_file():
        raise ErrorRestore(f"No se encontró el archivo de backup: {ruta_zip}")

    try:
        zf = zipfile.ZipFile(ruta_zip)
    except zipfile.BadZipFile as error:
        raise ErrorRestore(f"El archivo de backup está corrupto o no es un ZIP válido: {error}") from error

    with zf:
        if zf.testzip() is not None:
            raise ErrorRestore("El archivo de backup está corrupto (falló la verificación interna).")

        _validar_estructura_y_seguridad(zf)

        datos_extraidos = directorio_extraccion / "data"
        datos_extraidos.mkdir(parents=True)
        _extraer_de_forma_segura(zf, datos_extraidos)

    if not (datos_extraidos / _NOMBRE_DB).is_file():
        raise ErrorRestore(f"El backup extraído no contiene '{_NOMBRE_DB}'.")
    if not (datos_extraidos / _NOMBRE_CARPETA_IMAGENES).is_dir():
        raise ErrorRestore(f"El backup extraído no contiene la carpeta '{_NOMBRE_CARPETA_IMAGENES}'.")

    _verificar_integridad_sqlite(datos_extraidos / _NOMBRE_DB)

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
            if not (directorio_data / _NOMBRE_DB).is_file():
                raise ErrorRestore("La instalación restaurada no pasó la validación final.")
        except Exception:
            if directorio_data.exists():
                shutil.rmtree(directorio_data, ignore_errors=True)
            if habia_data_previa:
                ruta_rollback.rename(directorio_data)
            raise

    if habia_data_previa and ruta_rollback.exists():
        shutil.rmtree(ruta_rollback, ignore_errors=True)
