"""Actualización controlada y rollback coherente programa + base (V1.15-F).

Twinx Express no se actualiza solo desde Internet: estos comandos los corre
un técnico, con la aplicación cerrada, desde una copia del programa que NO
es la carpeta instalada (Windows no deja reemplazar la carpeta de un
ejecutable en uso):

- `actualizar(instalacion, programa_nuevo)`: lo corre la versión nueva.
  Backup `PRE_UPDATE` validado (si falla, no sigue) -> archiva la versión
  instalada y la nueva -> reemplaza el programa -> migra la base. Si la
  migración falla, vuelve solo al programa y a la base anteriores.
- `rollback(version)`: vuelve a una versión conservada usando el backup
  `PRE_UPDATE` tomado al dejarla. Nunca cambia solo el ejecutable: valida
  el backup, comprueba que la versión anterior conoce su esquema, protege
  el estado actual con un backup de seguridad (lo hace el restore), restaura
  la base, recién entonces pone el programa anterior, y verifica.
"""

import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import config
from db import instancia_unica
from db.conexion import inicializar_base_datos, migraciones_desconocidas
from domain.auditoria import ACCION_ROLLBACK
from domain.backup import TIPO_PRE_UPDATE
from excepciones import BackupInvalidoError, ErrorActualizacion, ErrorBackup, ErrorBaseDatos, ErrorRestore
from services import registro_versiones, servicio_backup, servicio_restore
from services.control_escrituras import ControlEscrituras
from services.formato_backup import validar_backup
from services.registro_versiones import Registro, VersionConservada
from version import VERSION

logger = logging.getLogger(__name__)

NOMBRE_EJECUTABLE = "KioscoApp.exe"
_PATRON_VERSION_LEEME = re.compile(r"^Twinx Express (\d+\.\d+\.\d+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class ResultadoActualizacion:
    version_anterior: str
    version_nueva: str
    backup_pre_update: Path


@dataclass(frozen=True)
class ResultadoRollback:
    version: str
    backup_restaurado: Path
    backup_seguridad: Path | None
    datos_reemplazados: Path | None


def version_de_programa(carpeta: Path) -> str:
    """Versión de una carpeta de programa, leída de su `LEEME.txt` (lo trae
    todo entregable; un test garantiza que coincide con `version.py`)."""
    leeme = carpeta / "LEEME.txt"
    try:
        coincidencia = _PATRON_VERSION_LEEME.search(leeme.read_text(encoding="utf-8", errors="replace"))
    except OSError as error:
        raise ErrorActualizacion(f"No se pudo leer la versión de {carpeta}: {error}") from error
    if coincidencia is None:
        raise ErrorActualizacion(f"No se reconoce la versión del programa en {carpeta}.")
    return coincidencia.group(1)


def migraciones_de_programa(carpeta: Path) -> tuple[str, ...]:
    """Migraciones que trae una carpeta de programa (entregable de PyInstaller)."""
    directorio = carpeta / "_internal" / "db" / "migraciones"
    nombres = tuple(sorted(r.name for r in directorio.glob("*.sql")))
    if not nombres:
        raise ErrorActualizacion(f"La carpeta {carpeta} no parece un programa Twinx Express completo.")
    return nombres


def _validar_programa(carpeta: Path) -> None:
    if not (carpeta / NOMBRE_EJECUTABLE).is_file():
        raise ErrorActualizacion(f"No se encontró {NOMBRE_EJECUTABLE} en {carpeta}.")


def _dentro_de(ruta: Path, base: Path) -> bool:
    ruta, base = ruta.resolve(), base.resolve()
    return ruta == base or ruta.is_relative_to(base)


def _archivar(programa: Path, version: str) -> Path:
    """Copia la carpeta del programa a `versiones/<version>` (si ya está, la deja)."""
    destino = config.DIRECTORIO_VERSIONES / version
    if (destino / NOMBRE_EJECUTABLE).is_file():
        return destino
    temporal = config.DIRECTORIO_VERSIONES / f".{version}.copiando"
    shutil.rmtree(temporal, ignore_errors=True)
    shutil.copytree(programa, temporal)
    _validar_programa(temporal)
    temporal.rename(destino)
    return destino


def _reemplazar_programa(instalacion: Path, programa: Path) -> None:
    """Deja en `instalacion` una copia de `programa`: primero copia a una
    carpeta hermana y recién después intercambia con dos renombrados, así un
    fallo a mitad de la copia nunca deja la instalación a medias."""
    nueva = instalacion.with_name(f".{instalacion.name}.nueva")
    anterior = instalacion.with_name(f".{instalacion.name}.anterior")
    shutil.rmtree(nueva, ignore_errors=True)
    shutil.copytree(programa, nueva)
    _validar_programa(nueva)
    instalacion.rename(anterior)
    try:
        nueva.rename(instalacion)
    except OSError:
        anterior.rename(instalacion)
        raise
    shutil.rmtree(anterior, ignore_errors=True)


def _podar_versiones(registro: Registro) -> None:
    """Conserva la versión activa y las `VERSIONES_ANTERIORES_A_CONSERVAR`
    anteriores más recientes; borra la carpeta archivada de las demás (sus
    backups pre-update dejan de estar protegidos y siguen la retención normal)."""
    anteriores = sorted(
        (v for v in registro.versiones if v.version != registro.activa), key=lambda v: v.fecha, reverse=True
    )
    for sobrante in anteriores[config.VERSIONES_ANTERIORES_A_CONSERVAR :]:
        carpeta = Path(sobrante.carpeta)
        if _dentro_de(carpeta, config.DIRECTORIO_VERSIONES):
            shutil.rmtree(carpeta, ignore_errors=True)
        registro.versiones.remove(sobrante)
        logger.info("Versión archivada descartada: %s", sobrante.version)


@instancia_unica.exclusiva()
def actualizar(instalacion: Path, programa_nuevo: Path, aplicacion_en_uso: bool) -> ResultadoActualizacion:
    """Actualiza la instalación a la versión de `programa_nuevo` (la que corre).

    Raises:
        InstanciaEnUsoError: hay una instancia viva usando la base; no se toca nada.
        ErrorActualizacion: con el estado en que quedó todo. Antes del backup
            pre-update validado no se modifica nada.
    """
    if aplicacion_en_uso:
        raise ErrorActualizacion("Twinx Express está abierto. Cerralo antes de actualizar.")
    _validar_programa(instalacion)
    _validar_programa(programa_nuevo)
    if _dentro_de(programa_nuevo, instalacion) or _dentro_de(instalacion, programa_nuevo):
        raise ErrorActualizacion("Ejecutá la actualización desde la carpeta de la versión nueva, no desde la instalada.")
    version_anterior = version_de_programa(instalacion)
    version_nueva = version_de_programa(programa_nuevo)
    if version_nueva != VERSION:
        raise ErrorActualizacion(f"La carpeta nueva dice ser la {version_nueva}, pero este programa es la {VERSION}.")
    if version_anterior == version_nueva:
        raise ErrorActualizacion(f"La versión {version_nueva} ya es la instalada.")
    if migraciones_desconocidas():
        raise ErrorActualizacion("Los datos son de una versión más nueva que la que se quiere instalar. No se cambió nada.")
    registro = registro_versiones.leer()

    try:
        pre_update = servicio_backup.crear_backup_pre_update(
            servicio_backup.DIRECTORIO_BACKUPS, ControlEscrituras(), version_destino=version_nueva
        )
    except ErrorBackup as error:
        raise ErrorActualizacion(f"No se pudo crear el backup previo; la actualización NO se hizo: {error}") from error

    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        archivo_anterior = _archivar(instalacion, version_anterior)
        archivo_nuevo = _archivar(programa_nuevo, version_nueva)
    except OSError as error:
        raise ErrorActualizacion(f"No se pudo archivar el programa; la actualización NO se hizo: {error}") from error
    registro.reemplazar(
        VersionConservada(version_anterior, str(archivo_anterior), migraciones_de_programa(instalacion), ahora, pre_update.name)
    )
    registro.reemplazar(VersionConservada(version_nueva, str(archivo_nuevo), migraciones_de_programa(programa_nuevo), ahora))
    registro.instalacion = str(instalacion.resolve())
    activa_previa, registro.activa = registro.activa, version_nueva
    registro_versiones.guardar(registro)

    try:
        _reemplazar_programa(instalacion, programa_nuevo)
    except OSError as error:
        registro.activa = activa_previa
        registro_versiones.guardar(registro)
        raise ErrorActualizacion(
            f"No se pudo reemplazar el programa (sigue instalada la {version_anterior}, datos sin cambios): {error}"
        ) from error

    try:
        inicializar_base_datos()
        servicio_backup.registrar_version_en_la_base()
    except ErrorBaseDatos as error:
        logger.critical("Falló la migración de la actualización: %s", error)
        try:
            _volver_atras_tras_migracion_fallida(instalacion, archivo_anterior, pre_update, version_anterior, registro)
        except (ErrorRestore, OSError) as error_vuelta:
            raise ErrorActualizacion(
                f"Falló la migración de datos ({error}) y no se pudo volver atrás automáticamente ({error_vuelta}). "
                f"Para recuperar: KioscoApp.exe --rollback {version_anterior} desde {archivo_nuevo}. "
                f"Backup previo: {pre_update}."
            ) from error_vuelta
        raise ErrorActualizacion(
            f"Falló la migración de datos ({error}). Se volvió automáticamente a la versión {version_anterior} "
            f"y a los datos previos ({pre_update.name})."
        ) from error

    _podar_versiones(registro)
    registro_versiones.guardar(registro)
    logger.info("Actualización %s -> %s completa (pre-update %s)", version_anterior, version_nueva, pre_update.name)
    return ResultadoActualizacion(version_anterior, version_nueva, pre_update)


def _volver_atras_tras_migracion_fallida(
    instalacion: Path, archivo_anterior: Path, pre_update: Path, version_anterior: str, registro: Registro
) -> None:
    servicio_restore.restaurar_desde_backup(
        pre_update,
        migraciones_compatibles=migraciones_de_programa(archivo_anterior),
        accion_auditoria=ACCION_ROLLBACK,
    )
    _reemplazar_programa(instalacion, archivo_anterior)
    registro.activa = version_anterior
    registro_versiones.guardar(registro)


@instancia_unica.exclusiva()
def rollback(version: str, programa_actual: Path, aplicacion_en_uso: bool) -> ResultadoRollback:
    """Vuelve a `version` (programa archivado + su backup pre-update).

    Raises:
        InstanciaEnUsoError: hay una instancia viva usando la base; no se toca nada.
        ErrorActualizacion: si no se puede hacer con seguridad (no se toca
            nada) o, si el fallo es después de restaurar la base, con las
            instrucciones para terminar a mano.
    """
    if aplicacion_en_uso:
        raise ErrorActualizacion("Twinx Express está abierto. Cerralo antes de volver a una versión anterior.")
    registro = registro_versiones.leer()
    conservada = registro.buscar(version)
    if conservada is None or registro.instalacion is None:
        disponibles = ", ".join(v.version for v in registro.versiones if v.backup_pre_update) or "ninguna"
        raise ErrorActualizacion(f"No hay rollback disponible a la versión {version}. Disponibles: {disponibles}.")
    if conservada.backup_pre_update is None:
        raise ErrorActualizacion(f"La versión {version} no tiene un backup previo asociado: no se puede volver a ella.")
    instalacion = Path(registro.instalacion)
    carpeta = Path(conservada.carpeta)
    _validar_programa(carpeta)
    if _dentro_de(programa_actual, instalacion):
        raise ErrorActualizacion(
            "Ejecutá el rollback desde la copia archivada de la versión actual "
            f"({config.DIRECTORIO_VERSIONES / str(registro.activa)}), no desde la carpeta instalada."
        )

    backup = servicio_backup.DIRECTORIO_BACKUPS / conservada.backup_pre_update
    try:
        info = validar_backup(backup)
    except BackupInvalidoError as error:
        raise ErrorActualizacion(f"El backup previo de la versión {version} no es válido; no se cambió nada: {error}") from error
    if info.tipo != TIPO_PRE_UPDATE:
        raise ErrorActualizacion(f"{backup.name} no es un backup previo a una actualización; no se cambió nada.")

    try:
        restaurado = servicio_restore.restaurar_desde_backup(
            backup, migraciones_compatibles=conservada.migraciones, accion_auditoria=ACCION_ROLLBACK
        )
    except ErrorRestore as error:
        raise ErrorActualizacion(f"No se pudo restaurar la base de la versión {version}; no se cambió nada: {error}") from error

    try:
        _reemplazar_programa(instalacion, carpeta)
    except OSError as error:
        raise ErrorActualizacion(
            f"La base ya se restauró a la de la versión {version}, pero no se pudo reemplazar el programa ({error}). "
            f"Abrí la versión {version} desde {carpeta / NOMBRE_EJECUTABLE}. "
            f"El estado anterior quedó en {restaurado.backup_seguridad or restaurado.datos_reemplazados}."
        ) from error
    registro.activa = version
    registro_versiones.guardar(registro)
    logger.info("Rollback a %s completo (base de %s)", version, backup.name)
    return ResultadoRollback(version, backup, restaurado.backup_seguridad, restaurado.datos_reemplazados)


def versiones_conservadas() -> Registro:
    return registro_versiones.leer()
