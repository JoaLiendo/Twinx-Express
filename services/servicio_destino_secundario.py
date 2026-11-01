"""Segundo destino opcional para COPIAS de backups (V1.15-D).

Es solo una carpeta (disco externo, otra unidad, carpeta sincronizada por un
servicio cloud del usuario) donde se copia cada backup ya creado y validado en
la carpeta primaria. Nunca es la ubicación de la base activa, que no es
configurable. Una copia fallida nunca toca el backup primario.
"""

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import config
from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import configuracion as repositorio_configuracion
from domain.auditoria import ACCION_CONFIGURACION_BACKUP_CAMBIADA
from excepciones import DatosInvalidosError, ErrorBaseDatos
from services.formato_backup import hash_archivo

logger = logging.getLogger(__name__)

CLAVE_DESTINO_SECUNDARIO = "backup.destino_secundario"
_LONGITUD_MAXIMA_RUTA = 400
_MARGEN_ESPACIO = 1.1


@dataclass(frozen=True)
class ResultadoCopia:
    ruta: Path | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.ruta is not None


def obtener_destino_secundario() -> Path | None:
    """Carpeta secundaria configurada, o `None` si no hay (o si la base todavía
    no tiene la tabla de configuración, como antes de migrar una instalación vieja)."""
    try:
        valor = repositorio_configuracion.obtener_todas().get(CLAVE_DESTINO_SECUNDARIO, "")
    except ErrorBaseDatos as error:
        logger.warning("No se pudo leer el segundo destino de backups: %s", error)
        return None
    return Path(valor) if valor else None


def _dentro_de(ruta: Path, base: Path) -> bool:
    return ruta == base or ruta.is_relative_to(base)


def validar_destino(texto: str) -> Path | None:
    """Valida la carpeta elegida por el OWNER. Vacío = sin segundo destino.

    Raises:
        DatosInvalidosError: ruta relativa, inexistente, sin permiso de
            escritura, o dentro de los datos activos o de los backups primarios.
    """
    texto = texto.strip().strip('"')
    if not texto:
        return None
    if len(texto) > _LONGITUD_MAXIMA_RUTA:
        raise DatosInvalidosError("La ruta del segundo destino es demasiado larga.")
    ruta = Path(texto)
    if not ruta.is_absolute():
        raise DatosInvalidosError("Indicá la ruta completa de la carpeta (por ejemplo E:\\Backups Twinx).")
    if not ruta.is_dir():
        raise DatosInvalidosError("La carpeta del segundo destino no existe o no está conectada.")
    # Import diferido: `servicio_backup` importa este módulo.
    from services.servicio_backup import DIRECTORIO_BACKUPS

    resuelta = ruta.resolve()
    if _dentro_de(resuelta, config.DIRECTORIO_DATA.resolve()):
        raise DatosInvalidosError("El segundo destino no puede estar dentro de la carpeta de datos de Twinx.")
    primario = DIRECTORIO_BACKUPS.resolve()
    if _dentro_de(resuelta, primario) or _dentro_de(primario, resuelta):
        raise DatosInvalidosError("El segundo destino tiene que ser una carpeta distinta de la de backups principal.")
    prueba = ruta / ".twinx_prueba_escritura"
    try:
        prueba.write_bytes(b"ok")
        prueba.unlink()
    except OSError as error:
        raise DatosInvalidosError(f"No se puede escribir en el segundo destino: {error.strerror or error}") from error
    return ruta


def guardar_destino_secundario(texto: str, usuario_id: int) -> Path | None:
    """Guarda (o quita, con texto vacío) el segundo destino y lo audita si cambió."""
    ruta = validar_destino(texto)
    valor = str(ruta) if ruta else ""
    with obtener_conexion(inmediata=True) as conexion:
        actual = repositorio_configuracion.obtener_todas_en_conexion(conexion).get(CLAVE_DESTINO_SECUNDARIO, "")
        if actual == valor:
            return ruta
        repositorio_configuracion.guardar_en_conexion(conexion, CLAVE_DESTINO_SECUNDARIO, valor)
        repositorio_auditoria.registrar_en_conexion(
            conexion,
            usuario_id,
            ACCION_CONFIGURACION_BACKUP_CAMBIADA,
            "CONFIGURACION",
            None,
            f"Segundo destino de backups: {valor or 'sin configurar'}",
        )
    logger.info("Segundo destino de backups: %s", valor or "sin configurar")
    return ruta


def copiar_backup(ruta_backup: Path, destino: Path) -> ResultadoCopia:
    """Copia un backup ya validado a `destino` y verifica la copia (tamaño y
    SHA-256) antes de publicarla con su nombre final. Nunca lanza: devuelve
    el error, para que el llamador lo registre. El primario no se toca."""
    temporal = destino / f".{ruta_backup.name}.copiando"
    try:
        if not destino.is_dir():
            return ResultadoCopia(None, f"la carpeta {destino} no está disponible (¿disco desconectado?).")
        tamano = ruta_backup.stat().st_size
        if shutil.disk_usage(destino).free < tamano * _MARGEN_ESPACIO:
            return ResultadoCopia(None, f"no hay espacio suficiente en {destino}.")
        shutil.copyfile(ruta_backup, temporal)
        if temporal.stat().st_size != tamano or hash_archivo(temporal) != hash_archivo(ruta_backup):
            return ResultadoCopia(None, "la copia no coincide con el backup original.")
        final = destino / ruta_backup.name
        contador = 2
        while final.exists():
            if hash_archivo(final) == hash_archivo(ruta_backup):
                return ResultadoCopia(final)
            final = destino / f"{ruta_backup.stem}_{contador}{ruta_backup.suffix}"
            contador += 1
        os.replace(temporal, final)
        return ResultadoCopia(final)
    except OSError as error:
        return ResultadoCopia(None, f"no se pudo copiar a {destino}: {error.strerror or error}")
    finally:
        try:
            temporal.unlink(missing_ok=True)
        except OSError as error:
            logger.warning("No se pudo borrar la copia temporal %s: %s", temporal, error)
