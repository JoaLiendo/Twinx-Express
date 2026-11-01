"""Formato del paquete de backup (ZIP) y su validación completa (V1.15).

Un paquete contiene `kiosco.db`, `imagenes_productos/` y `manifiesto.json`
(ver `domain.backup`). Los backups anteriores a V1.15 no tienen manifiesto y
siguen siendo válidos para restaurar.

`validar_backup` es el único criterio de "backup correcto" de la aplicación:
lo usan la creación (un backup recién hecho no se publica sin pasarla), el
restore, el rollback y el listado de backups recuperables. Nunca le cree al
nombre del archivo ni al manifiesto: recalcula hashes, abre la base en solo
lectura y corre `integrity_check` y `foreign_key_check`.
"""

import hashlib
import json
import logging
import re
import tempfile
import zipfile
import zlib
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from db.archivo_sqlite import verificar_archivo
from domain.backup import ArchivoIncluido, Manifiesto, manifiesto_desde_dict
from excepciones import BackupInvalidoError, ErrorBaseDatos

logger = logging.getLogger(__name__)

NOMBRE_DB = "kiosco.db"
CARPETA_IMAGENES = "imagenes_productos"
NOMBRE_MANIFIESTO = "manifiesto.json"

_LIMITE_TAMANO_DESCOMPRIMIDO = 500 * 1024 * 1024  # generoso: 2MB por imagen como máximo
_LIMITE_ENTRADAS = 50_000
_LIMITE_TAMANO_MANIFIESTO = 2 * 1024 * 1024
_TAMANO_BLOQUE = 1024 * 1024
_PATRON_COMPONENTE_SEGURO = re.compile(r"^[\w\-. ()]+$")
_NOMBRES_RESERVADOS_WINDOWS = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
)


@dataclass(frozen=True)
class InfoBackup:
    """Un backup que pasó la validación completa."""

    ruta: Path
    manifiesto: Manifiesto | None
    migraciones: tuple[str, ...]
    tamano_bytes: int

    @property
    def tipo(self) -> str | None:
        return self.manifiesto.tipo if self.manifiesto else None

    @property
    def fecha(self) -> datetime | None:
        return self.manifiesto.fecha if self.manifiesto else None


def hash_archivo(ruta: Path) -> str:
    hasher = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(_TAMANO_BLOQUE), b""):
            hasher.update(bloque)
    return hasher.hexdigest()


def _archivos_de_datos(directorio_datos: Path) -> list[tuple[str, Path]]:
    """(nombre dentro del ZIP, ruta en disco) de cada archivo del paquete.

    Un archivo ajeno con un nombre que el restore rechazaría (dejado a mano en
    la carpeta de imágenes) se omite con una advertencia: no puede dejar a la
    instalación sin backups. Las imágenes que guarda la aplicación siempre
    tienen nombres seguros."""
    archivos = [(NOMBRE_DB, directorio_datos / NOMBRE_DB)]
    dir_imagenes = directorio_datos / CARPETA_IMAGENES
    for ruta in sorted(p for p in dir_imagenes.rglob("*") if p.is_file() and not p.is_symlink()):
        nombre = f"{CARPETA_IMAGENES}/{ruta.relative_to(dir_imagenes).as_posix()}"
        try:
            _validar_nombre(nombre)
        except BackupInvalidoError:
            logger.warning("Archivo omitido del backup por tener un nombre no admitido: %s", ruta.name)
            continue
        archivos.append((nombre, ruta))
    return archivos


def describir_archivos(directorio_datos: Path) -> dict[str, ArchivoIncluido]:
    """Tamaño y SHA-256 de cada archivo que irá en el paquete."""
    return {
        nombre: ArchivoIncluido(ruta.stat().st_size, hash_archivo(ruta))
        for nombre, ruta in _archivos_de_datos(directorio_datos)
    }


def construir_paquete(directorio_datos: Path, manifiesto: Manifiesto, ruta_zip: Path) -> None:
    """Escribe el ZIP con la base, las imágenes y el manifiesto."""
    archivos = _archivos_de_datos(directorio_datos)
    with zipfile.ZipFile(ruta_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for nombre, ruta in archivos:
            zf.write(ruta, nombre)
        if len(archivos) == 1:
            # conservar la carpeta aunque no tenga imágenes todavía.
            zf.writestr(f"{CARPETA_IMAGENES}/", "")
        zf.writestr(NOMBRE_MANIFIESTO, json.dumps(manifiesto.a_dict(), ensure_ascii=False, indent=2))


def _validar_nombre(nombre: str) -> None:
    """Solo nombres relativos simples: sin rutas absolutas, unidades, `..`,
    barras invertidas ni nombres de dispositivo de Windows."""
    inseguro = BackupInvalidoError(f"El backup contiene un nombre de archivo no seguro: '{nombre[:120]}'.")
    if not nombre or "\\" in nombre or "\x00" in nombre or nombre.startswith("/") or ":" in nombre:
        raise inseguro
    # `split` y no `PurePosixPath.parts`: este último normaliza `./` y `//`.
    for parte in nombre.removesuffix("/").split("/"):
        if (
            parte in ("", ".", "..")
            or not _PATRON_COMPONENTE_SEGURO.match(parte)
            or parte.endswith((".", " "))
            or parte.split(".")[0].lower() in _NOMBRES_RESERVADOS_WINDOWS
        ):
            raise inseguro


def _es_enlace_simbolico(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _entradas_validadas(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Revisa el índice del ZIP antes de leer un solo byte de contenido."""
    entradas = zf.infolist()
    if len(entradas) > _LIMITE_ENTRADAS:
        raise BackupInvalidoError("El backup contiene una cantidad de archivos excesiva.")
    vistos: set[str] = set()
    tamano_total = 0
    tiene_carpeta_imagenes = False
    for info in entradas:
        nombre = info.filename
        _validar_nombre(nombre)
        if nombre in vistos:
            raise BackupInvalidoError(f"El backup contiene un archivo duplicado: '{nombre}'.")
        vistos.add(nombre)
        if _es_enlace_simbolico(info):
            raise BackupInvalidoError(f"El backup contiene un enlace simbólico: '{nombre}'.")
        if info.flag_bits & 0x1:
            raise BackupInvalidoError("El backup contiene archivos cifrados.")
        if nombre.startswith(f"{CARPETA_IMAGENES}/"):
            tiene_carpeta_imagenes = True
        elif nombre not in (NOMBRE_DB, NOMBRE_MANIFIESTO):
            raise BackupInvalidoError(f"El backup contiene un archivo inesperado: '{nombre}'.")
        tamano_total += info.file_size
        if tamano_total > _LIMITE_TAMANO_DESCOMPRIMIDO:
            raise BackupInvalidoError("El backup declara un tamaño descomprimido excesivo.")
    if NOMBRE_DB not in vistos:
        raise BackupInvalidoError(f"El backup no contiene '{NOMBRE_DB}'.")
    if not tiene_carpeta_imagenes:
        raise BackupInvalidoError(f"El backup no contiene la carpeta '{CARPETA_IMAGENES}'.")
    if NOMBRE_MANIFIESTO in vistos and zf.getinfo(NOMBRE_MANIFIESTO).file_size > _LIMITE_TAMANO_MANIFIESTO:
        raise BackupInvalidoError("El manifiesto del backup es demasiado grande.")
    return entradas


def _ruta_dentro(base: Path, nombre: str) -> Path:
    ruta = (base / nombre).resolve()
    if not ruta.is_relative_to(base.resolve()):
        raise BackupInvalidoError(f"Ruta insegura dentro del backup: '{nombre[:120]}'.")
    return ruta


def _leer_entradas(
    zf: zipfile.ZipFile, entradas: list[zipfile.ZipInfo], destino: Path, extraer_todo: bool
) -> tuple[dict[str, ArchivoIncluido], bytes | None]:
    """Lee cada archivo del ZIP una sola vez: calcula su hash (y con eso
    verifica el CRC) y lo escribe en `destino` si corresponde. La base siempre
    se escribe, porque hay que abrirla para verificarla."""
    hashes: dict[str, ArchivoIncluido] = {}
    manifiesto_crudo = None
    for info in entradas:
        nombre = info.filename
        if info.is_dir():
            if extraer_todo:
                _ruta_dentro(destino, nombre).mkdir(parents=True, exist_ok=True)
            continue
        if nombre == NOMBRE_MANIFIESTO:
            manifiesto_crudo = zf.read(info)
            continue
        ruta_destino = _ruta_dentro(destino, nombre) if extraer_todo or nombre == NOMBRE_DB else None
        if ruta_destino is not None:
            ruta_destino.parent.mkdir(parents=True, exist_ok=True)
        hasher = hashlib.sha256()
        with zf.open(info) as origen, (ruta_destino.open("wb") if ruta_destino else nullcontext()) as salida:
            for bloque in iter(lambda: origen.read(_TAMANO_BLOQUE), b""):
                hasher.update(bloque)
                if salida is not None:
                    salida.write(bloque)
        hashes[nombre] = ArchivoIncluido(info.file_size, hasher.hexdigest())
    return hashes, manifiesto_crudo


def _validar_manifiesto(crudo: bytes, hashes: dict[str, ArchivoIncluido], migraciones: tuple[str, ...]) -> Manifiesto:
    try:
        manifiesto = manifiesto_desde_dict(json.loads(crudo.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BackupInvalidoError("El manifiesto del backup no se puede leer.") from error
    if set(manifiesto.archivos) != set(hashes):
        raise BackupInvalidoError("El manifiesto del backup no coincide con los archivos que contiene.")
    for nombre, real in hashes.items():
        if manifiesto.archivos[nombre] != real:
            raise BackupInvalidoError(f"El archivo '{nombre}' del backup no coincide con su manifiesto.")
    if manifiesto.migraciones != migraciones:
        raise BackupInvalidoError("El manifiesto del backup no coincide con el esquema de su base de datos.")
    if manifiesto.integridad != "ok":
        raise BackupInvalidoError("El manifiesto del backup no declara una base íntegra.")
    return manifiesto


def leer_manifiesto(ruta_zip: Path) -> Manifiesto | None:
    """Lectura liviana del manifiesto, sin validar el contenido: sirve para
    clasificar y listar backups. `None` si no hay manifiesto legible (backup
    anterior a V1.15, archivo dañado o ajeno). Nunca habilita por sí sola una
    recuperación: para eso está `validar_backup`."""
    try:
        with zipfile.ZipFile(ruta_zip) as zf:
            info = zf.getinfo(NOMBRE_MANIFIESTO)
            if info.file_size > _LIMITE_TAMANO_MANIFIESTO:
                return None
            return manifiesto_desde_dict(json.loads(zf.read(info).decode("utf-8")))
    except (KeyError, OSError, zipfile.BadZipFile, zlib.error, EOFError, UnicodeDecodeError, ValueError, BackupInvalidoError):
        return None


def validar_backup(ruta_zip: Path | str, directorio_extraccion: Path | None = None) -> InfoBackup:
    """Valida un backup por completo y devuelve su información.

    Con `directorio_extraccion`, además deja el contenido validado en
    `directorio_extraccion / "data"` (lo usa el restore); sin él, todo lo
    extraído para verificar se descarta.

    Raises:
        BackupInvalidoError: si el backup no existe, el ZIP está dañado, su
            contenido es inesperado o inseguro, la base no pasa
            `integrity_check`/`foreign_key_check` o el manifiesto no
            coincide con el contenido.
    """
    ruta = Path(ruta_zip)
    if not ruta.is_file():
        raise BackupInvalidoError(f"No se encontró el archivo de backup: {ruta.name}")
    try:
        zf = zipfile.ZipFile(ruta)
    except (zipfile.BadZipFile, OSError) as error:
        raise BackupInvalidoError(f"El archivo de backup está dañado o no es un ZIP válido: {error}") from error

    contexto_tmp = (
        nullcontext(str(directorio_extraccion))
        if directorio_extraccion is not None
        else tempfile.TemporaryDirectory(prefix="kioscoapp_validacion_")
    )
    with zf, contexto_tmp as tmp:
        entradas = _entradas_validadas(zf)
        destino = Path(tmp) / "data"
        destino.mkdir(parents=True, exist_ok=directorio_extraccion is None)
        try:
            hashes, manifiesto_crudo = _leer_entradas(zf, entradas, destino, directorio_extraccion is not None)
        except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError) as error:
            raise BackupInvalidoError(f"El archivo de backup está dañado: {error}") from error

        try:
            verificacion = verificar_archivo(destino / NOMBRE_DB)
        except ErrorBaseDatos as error:
            raise BackupInvalidoError(f"La base de datos del backup está dañada: {error}") from error
        if verificacion.integridad != "ok":
            raise BackupInvalidoError(
                f"La base de datos del backup no pasó la verificación de integridad: {verificacion.integridad[:200]}"
            )
        if verificacion.errores_claves_foraneas:
            raise BackupInvalidoError(
                f"La base de datos del backup tiene {verificacion.errores_claves_foraneas} referencia(s) rota(s)."
            )

        manifiesto = None
        if manifiesto_crudo is not None:
            manifiesto = _validar_manifiesto(manifiesto_crudo, hashes, verificacion.migraciones)

    return InfoBackup(ruta, manifiesto, verificacion.migraciones, ruta.stat().st_size)
