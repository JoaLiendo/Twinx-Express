"""Backup consistente de `kiosco.db` + `imagenes_productos/` en un
único ZIP con manifiesto (ver `services.formato_backup`).

El bloqueo (`ControlEscrituras`) se mantiene únicamente mientras se
toma el snapshot de la DB y se copian las imágenes a un directorio
temporal: una vez que esas dos copias están aisladas, ya no dependen
de lo que siga escribiendo la aplicación, así que el resto del proceso
(verificar la base, construir el ZIP, validarlo, publicarlo) corre sin
el lock. Un backup solo se publica después de pasar la validación
completa. Solo librería estándar.
"""

import errno
import logging
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import db.conexion as modulo_conexion
import services.servicio_imagenes as modulo_imagenes
from config import (
    ANTIGUEDAD_MINIMA_BACKUP_AUTOMATICO_HORAS,
    RETENCION_BACKUPS,
    DIRECTORIO_BACKUPS,
)
from db.archivo_sqlite import copiar_base_consistente, verificar_archivo
from db.conexion import hay_migraciones_pendientes_en_base_existente, inicializar_base_datos
from db.repositorios import auditoria as repositorio_auditoria
from domain.auditoria import (
    ACCION_BACKUP_AUTOMATICO_OK,
    ACCION_BACKUP_CIERRE_CAJA_OK,
    ACCION_BACKUP_ERROR,
    ACCION_BACKUP_MANUAL_OK,
    ACCION_PRE_UPDATE_BACKUP_OK,
    EntradaAuditoria,
)
from domain.backup import (
    TIPO_AUTOMATICO,
    TIPO_CIERRE_CAJA,
    TIPO_MANUAL,
    TIPO_PRE_UPDATE,
    TIPO_SEGURIDAD,
    Manifiesto,
)
from domain.retencion_backups import TIPOS_REGULARES, BackupClasificado, PoliticaRetencion, backups_a_conservar
from excepciones import BackupEnCursoError, BackupInvalidoError, ErrorBackup, ErrorBaseDatos
from services.formato_backup import (
    CARPETA_IMAGENES,
    NOMBRE_DB,
    construir_paquete,
    describir_archivos,
    leer_manifiesto,
    validar_backup,
)
from version import VERSION

logger = logging.getLogger(__name__)

POLITICA_RETENCION = PoliticaRetencion(**RETENCION_BACKUPS)

_PREFIJO_POR_TIPO = {
    TIPO_MANUAL: "KioscoApp_backup_",
    TIPO_AUTOMATICO: "KioscoApp_backup_",
    TIPO_CIERRE_CAJA: "KioscoApp_backup_cierre_caja_",
    TIPO_PRE_UPDATE: "KioscoApp_backup_pre_update_",
    TIPO_SEGURIDAD: "KioscoApp_backup_seguridad_",
}
# El nombre lleva segundos (V1.1): con minutos, un backup manual y uno automático
# del mismo minuto se pisaban. Los backups anteriores, con nombre de minutos, siguen
# siendo válidos y se reconocen igual. Ante una colisión de nombre se agrega `_2`, `_3`...
_PATRON_NOMBRE_BACKUP = re.compile(r"^KioscoApp_backup_(\d{4}-\d{2}-\d{2}_\d{4}(?:\d{2})?)(?:_(\d+))?\.zip$")
_PATRON_NOMBRE_PREVENTIVO = re.compile(r"^KioscoApp_backup_pre_migracion_(\d{4}-\d{2}-\d{2}_\d{6})(?:_(\d+))?\.zip$")
_FORMATO_FECHA_EN_NOMBRE = "%Y-%m-%d_%H%M%S"
_FORMATO_FECHA_EN_NOMBRE_ANTIGUO = "%Y-%m-%d_%H%M"


def _mensaje_error_de_disco(error: OSError) -> str:
    if error.errno == errno.ENOSPC:
        return "No hay espacio suficiente en el disco para guardar el backup."
    if isinstance(error, PermissionError):
        return "No hay permiso para escribir en la carpeta de backups."
    return f"No se pudo escribir el backup en disco: {error.strerror or error}"


def _ruta_sin_colision(directorio: Path, nombre: str) -> Path:
    """`directorio/nombre`, o con sufijo `_2`, `_3`... si ya existe: publicar un
    backup nunca debe pisar uno anterior."""
    ruta = directorio / nombre
    base = ruta.stem
    contador = 2
    while ruta.exists():
        ruta = directorio / f"{base}_{contador}{ruta.suffix}"
        contador += 1
    return ruta


def _publicar(zip_temporal: Path, directorio: Path, nombre: str) -> Path:
    """Mueve el ZIP ya validado a su nombre final sin pisar ningún backup: en
    Windows `os.rename` falla si el destino existe, así que dos backups que
    eligieron el mismo nombre en el mismo segundo terminan con `_2`, `_3`..."""
    while True:
        ruta_final = _ruta_sin_colision(directorio, nombre)
        try:
            os.rename(zip_temporal, ruta_final)
        except FileExistsError:
            continue
        return ruta_final


def nombre_de_backup(tipo: str, fecha: datetime) -> str:
    """Nombre de archivo para un backup nuevo. Es solo descriptivo: el tipo y
    la fecha que cuentan son los del manifiesto."""
    return f"{_PREFIJO_POR_TIPO[tipo]}{fecha.strftime(_FORMATO_FECHA_EN_NOMBRE)}.zip"


def crear_backup(
    directorio_destino: Path,
    control_escrituras,
    *,
    tipo: str = TIPO_MANUAL,
    nombre_archivo: str | None = None,
    sesion_caja_id: int | None = None,
    version_destino: str | None = None,
    ahora: datetime | None = None,
    esperar_turno: bool = False,
) -> Path:
    """Genera un backup validado dentro de `directorio_destino` y devuelve su
    ruta. Un backup que no pasa `formato_backup.validar_backup` nunca se
    publica: el ZIP se arma y se valida en un temporal y recién entonces se
    mueve a su nombre final.

    `control_escrituras.iniciar_backup()` es la única sección crítica
    que decide, de forma atómica, si este backup puede arrancar (nunca
    dos a la vez) y espera a que las escrituras de negocio ya en curso
    terminen -- nunca cuenta esta misma operación como una escritura de
    negocio, así que no puede quedar esperándose a sí misma. El turno
    se libera pase lo que pase (`try/finally`), incluso si el snapshot
    o la copia fallan, y solo se mantiene mientras se copian la DB y
    las imágenes a un directorio temporal (ver docstring del módulo).

    Raises:
        ErrorBackup: si no se pudo generar o validar (incluye disco lleno y
            falta de permisos, con un mensaje claro).
    """
    fecha = (ahora or datetime.now()).replace(microsecond=0)
    try:
        ruta_final = _crear_backup_validado(
            directorio_destino,
            control_escrituras,
            tipo=tipo,
            nombre=nombre_archivo or nombre_de_backup(tipo, fecha),
            fecha=fecha,
            sesion_caja_id=sesion_caja_id,
            version_destino=version_destino,
            esperar_turno=esperar_turno,
        )
    except OSError as error:
        logger.error("No se pudo escribir el backup: %s", error)
        raise ErrorBackup(_mensaje_error_de_disco(error)) from error
    logger.info("Backup %s creado y validado: %s", tipo, ruta_final.name)

    try:
        aplicar_retencion(directorio_destino, ruta_final)
    except OSError as error:
        # El backup ya está publicado y validado: un fallo de la limpieza no lo invalida.
        logger.error("No se pudo aplicar la retención de backups: %s", error)
    return ruta_final


def _crear_backup_validado(
    directorio_destino: Path,
    control_escrituras,
    *,
    tipo: str,
    nombre: str,
    fecha: datetime,
    sesion_caja_id: int | None,
    version_destino: str | None,
    esperar_turno: bool,
) -> Path:
    directorio_destino.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directorio_destino, prefix=".kioscoapp_backup_tmp_") as tmp:
        tmp_path = Path(tmp)
        datos_tmp = tmp_path / "data"
        datos_tmp.mkdir()

        if not control_escrituras.iniciar_backup(timeout=30, esperar_otro_backup=esperar_turno):
            raise BackupEnCursoError("Ya hay un backup en curso. Esperá a que termine e intentá de nuevo.")
        try:
            # Siempre la base y las imágenes que la aplicación está usando de verdad:
            # nunca un binding propio de `config` que pueda quedar desincronizado.
            copiar_base_consistente(modulo_conexion.RUTA_BASE_DATOS, datos_tmp / NOMBRE_DB)
            shutil.copytree(modulo_imagenes.DIRECTORIO_IMAGENES_PRODUCTOS, datos_tmp / CARPETA_IMAGENES)
        except ErrorBaseDatos as error:
            raise ErrorBackup(f"No se pudo copiar la base de datos: {error}") from error
        finally:
            control_escrituras.finalizar_backup()

        try:
            verificacion = verificar_archivo(datos_tmp / NOMBRE_DB)
        except ErrorBaseDatos as error:
            raise ErrorBackup(f"La base de datos actual no se pudo verificar: {error}") from error
        if not verificacion.es_valida:
            raise ErrorBackup(
                "La base de datos actual no pasó la verificación de integridad "
                f"({verificacion.integridad[:200]}; {verificacion.errores_claves_foraneas} referencia(s) rota(s)). "
                "No se generó el backup: contactá a soporte."
            )

        manifiesto = Manifiesto(
            version=VERSION,
            tipo=tipo,
            id=uuid.uuid4().hex,
            fecha=fecha,
            migraciones=verificacion.migraciones,
            integridad=verificacion.integridad,
            archivos=describir_archivos(datos_tmp),
            sesion_caja_id=sesion_caja_id,
            version_destino=version_destino,
        )
        zip_temporal = tmp_path / "backup.zip.tmp"
        construir_paquete(datos_tmp, manifiesto, zip_temporal)

        try:
            info = validar_backup(zip_temporal)
        except BackupInvalidoError as error:
            raise ErrorBackup(f"El backup generado no pasó la validación: {error}") from error
        if info.manifiesto is None or info.manifiesto.id != manifiesto.id:
            raise ErrorBackup("El backup generado no pasó la validación: manifiesto inesperado.")

        return _publicar(zip_temporal, directorio_destino, nombre)


def _clave_de_orden(patron: re.Pattern[str], nombre: str, formato: str) -> tuple[datetime, int] | None:
    coincidencia = patron.match(nombre)
    if coincidencia is None:
        return None
    return datetime.strptime(coincidencia.group(1), formato), int(coincidencia.group(2) or 1)


def _clave_de_backup_regular(nombre: str) -> tuple[datetime, int] | None:
    """Orden cronológico de un backup manual/automático, con nombre de segundos
    o con el nombre de minutos anterior a V1.1; `None` si no es uno de ellos."""
    coincidencia = _PATRON_NOMBRE_BACKUP.match(nombre)
    if coincidencia is None:
        return None
    formato = _FORMATO_FECHA_EN_NOMBRE if len(coincidencia.group(1)) == 17 else _FORMATO_FECHA_EN_NOMBRE_ANTIGUO
    return datetime.strptime(coincidencia.group(1), formato), int(coincidencia.group(2) or 1)


def clasificar_backup(ruta: Path) -> BackupClasificado | None:
    """Tipo y fecha de un archivo de la carpeta de backups, o `None` si no se
    reconoce como un backup propio.

    Se le cree al manifiesto (V1.15), no al nombre. Un archivo sin manifiesto
    solo se reconoce si tiene exactamente el nombre de un backup anterior a
    V1.15 (regular o `pre_migracion`), con la fecha que lleva en el nombre.
    """
    manifiesto = leer_manifiesto(ruta)
    if manifiesto is not None:
        return BackupClasificado(ruta.name, manifiesto.fecha, manifiesto.tipo)
    clave = _clave_de_backup_regular(ruta.name)
    if clave is not None:
        return BackupClasificado(ruta.name, clave[0], TIPO_AUTOMATICO)
    clave = _clave_de_orden(_PATRON_NOMBRE_PREVENTIVO, ruta.name, _FORMATO_FECHA_EN_NOMBRE)
    if clave is not None:
        return BackupClasificado(ruta.name, clave[0], TIPO_PRE_UPDATE)
    return None


def _backups_propios(directorio_backups: Path) -> list[tuple[Path, BackupClasificado]]:
    """Archivos de primer nivel con prefijo de backup que se reconocen como
    propios. Nunca enlaces simbólicos ni subcarpetas."""
    if not directorio_backups.is_dir():
        return []
    propios = []
    for ruta in sorted(directorio_backups.glob("KioscoApp_backup_*.zip")):
        if ruta.is_symlink() or not ruta.is_file():
            continue
        clasificado = clasificar_backup(ruta)
        if clasificado is not None:
            propios.append((ruta, clasificado))
    return propios


def aplicar_retencion(
    directorio_backups: Path,
    recien_creado: Path,
    politica: PoliticaRetencion | None = None,
    protegidos: frozenset[str] = frozenset(),
) -> list[Path]:
    """Borra los backups que la política de retención ya no conserva (ver
    `domain.retencion_backups`) y devuelve los que borró.

    Solo se llama después de publicar un backup validado. Nunca toca un
    archivo que no reconoce como propio, ni `recien_creado`, ni los
    `protegidos`; un backup sin manifiesto (anterior a V1.15) solo se borra si
    además abre como ZIP -- un archivo dañado con nombre de backup no se
    clasifica ni se borra. Un fallo al borrar se registra y no interrumpe nada.
    """
    candidatos = [
        (ruta, clasificado)
        for ruta, clasificado in _backups_propios(directorio_backups)
        if leer_manifiesto(ruta) is not None or zipfile.is_zipfile(ruta)
    ]
    politica = politica or POLITICA_RETENCION
    conservar = backups_a_conservar([c for _, c in candidatos], politica, protegidos) | {recien_creado.name}
    borrados: list[Path] = []
    for ruta, clasificado in candidatos:
        if clasificado.nombre in conservar:
            continue
        try:
            ruta.unlink()
        except OSError as error:
            logger.warning("No se pudo borrar el backup antiguo %s: %s", ruta.name, error)
            continue
        borrados.append(ruta)
        logger.info("Backup eliminado por retención (%s, %s): %s", clasificado.tipo, clasificado.fecha, ruta.name)
    return borrados


def _fecha_ultimo_backup(directorio_backups: Path) -> datetime | None:
    """Fecha del backup regular (manual, automático o de cierre) más reciente,
    o `None` si no hay ninguno. Los pre-update y de seguridad no cuentan: no
    reemplazan al backup diario."""
    fechas = [c.fecha for _, c in _backups_propios(directorio_backups) if c.tipo in TIPOS_REGULARES]
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
        ruta_zip = crear_backup(directorio_destino, control_escrituras, tipo=TIPO_AUTOMATICO)
    except BackupEnCursoError as error:
        logger.warning("Backup automático omitido: %s", error)
        return None
    except ErrorBackup as error:
        logger.error("Backup automático falló: %s", error)
        _auditar(None, ACCION_BACKUP_ERROR, f"{TIPO_AUTOMATICO}: {error}")
        return None
    except Exception:
        logger.exception("Backup automático falló de forma inesperada.")
        _auditar(None, ACCION_BACKUP_ERROR, f"{TIPO_AUTOMATICO}: error inesperado (ver registro)")
        return None

    logger.info("Backup automático creado: %s", ruta_zip.name)
    _auditar(None, ACCION_BACKUP_AUTOMATICO_OK, ruta_zip.name)
    return ruta_zip


_ENTIDAD_AUDITORIA = "BACKUP"
_ACCION_OK_POR_TIPO = {
    TIPO_MANUAL: ACCION_BACKUP_MANUAL_OK,
    TIPO_AUTOMATICO: ACCION_BACKUP_AUTOMATICO_OK,
    TIPO_CIERRE_CAJA: ACCION_BACKUP_CIERRE_CAJA_OK,
    TIPO_PRE_UPDATE: ACCION_PRE_UPDATE_BACKUP_OK,
}
_ACCIONES_DE_ESTADO = (*_ACCION_OK_POR_TIPO.values(), ACCION_BACKUP_ERROR)


@dataclass(frozen=True)
class ResultadoBackup:
    """Resultado de un backup pedido por la aplicación: o una ruta validada,
    o el motivo del fallo. Nunca ambas cosas."""

    ruta: Path | None
    error: str | None = None
    ya_existia: bool = False

    @property
    def ok(self) -> bool:
        return self.ruta is not None


@dataclass(frozen=True)
class AlertaBackup:
    fecha: str
    detalle: str


def _auditar(usuario_id: int | None, accion: str, resumen: str) -> None:
    """Registra el evento sin poner en riesgo el backup ya resuelto: si la
    auditoría no se puede escribir, queda en el log."""
    try:
        repositorio_auditoria.registrar(usuario_id, accion, _ENTIDAD_AUDITORIA, None, resumen)
    except ErrorBaseDatos as error:
        logger.error("No se pudo registrar en auditoría el evento %s (%s): %s", accion, resumen, error)


def ejecutar_backup(
    tipo: str,
    control_escrituras,
    *,
    usuario_id: int | None = None,
    sesion_caja_id: int | None = None,
    esperar_turno: bool = False,
) -> ResultadoBackup:
    """Crea un backup en `DIRECTORIO_BACKUPS` y registra el resultado en
    auditoría (OK por tipo, o `BACKUP_ERROR`).

    Raises:
        BackupEnCursoError: si otro backup ocupa el turno; no se audita porque
            no es un fallo (el llamador decide si avisa o reintenta).
    """
    try:
        ruta = crear_backup(
            DIRECTORIO_BACKUPS,
            control_escrituras,
            tipo=tipo,
            sesion_caja_id=sesion_caja_id,
            esperar_turno=esperar_turno,
        )
    except BackupEnCursoError:
        raise
    except ErrorBackup as error:
        logger.error("Falló el backup %s: %s", tipo, error)
        _auditar(usuario_id, ACCION_BACKUP_ERROR, f"{tipo}: {error}")
        return ResultadoBackup(None, str(error))
    accion = _ACCION_OK_POR_TIPO.get(tipo)
    if accion is not None:
        _auditar(usuario_id, accion, ruta.name)
    return ResultadoBackup(ruta)


def _backup_de_cierre_existente(sesion_caja_id: int) -> Path | None:
    """Un backup de cierre ya publicado y válido para esa sesión, si existe."""
    if not DIRECTORIO_BACKUPS.is_dir():
        return None
    for ruta in sorted(DIRECTORIO_BACKUPS.glob(f"{_PREFIJO_POR_TIPO[TIPO_CIERRE_CAJA]}*.zip")):
        manifiesto = leer_manifiesto(ruta)
        if manifiesto is None or manifiesto.tipo != TIPO_CIERRE_CAJA or manifiesto.sesion_caja_id != sesion_caja_id:
            continue
        try:
            validar_backup(ruta)
        except BackupInvalidoError as error:
            logger.warning("El backup de cierre %s no es válido: %s", ruta.name, error)
            continue
        return ruta
    return None


def backup_de_cierre_de_caja(sesion_caja_id: int, control_escrituras, usuario_id: int | None = None) -> ResultadoBackup:
    """Backup `CIERRE_CAJA` de una sesión ya cerrada.

    El cierre es la operación de negocio primaria y ya está confirmado
    cuando se llama a esto: el backup nunca lo revierte ni lo hace fallar.
    Por eso esta función no propaga ningún error -- lo devuelve en el
    resultado y lo deja auditado, y la alerta (`alerta_de_backup`) queda
    visible hasta que un backup posterior salga bien. Si ya existe un
    backup válido de esa sesión (reintento, doble envío), lo devuelve sin
    generar otro. Espera su turno si coincide con otro backup en curso.
    """
    existente = _backup_de_cierre_existente(sesion_caja_id)
    if existente is not None:
        return ResultadoBackup(existente, ya_existia=True)
    try:
        return ejecutar_backup(
            TIPO_CIERRE_CAJA,
            control_escrituras,
            usuario_id=usuario_id,
            sesion_caja_id=sesion_caja_id,
            esperar_turno=True,
        )
    except BackupEnCursoError:
        mensaje = "el sistema estuvo ocupado demasiado tiempo y no se pudo tomar la copia."
        logger.error("Backup del cierre de caja %s: %s", sesion_caja_id, mensaje)
        _auditar(usuario_id, ACCION_BACKUP_ERROR, f"{TIPO_CIERRE_CAJA}: {mensaje}")
        return ResultadoBackup(None, mensaje)
    except Exception:  # noqa: BLE001 -- el cierre ya está confirmado: no puede terminar en un error 500
        logger.exception("Fallo inesperado en el backup del cierre de caja %s.", sesion_caja_id)
        mensaje = "error inesperado al generar el backup (ver registro)."
        _auditar(usuario_id, ACCION_BACKUP_ERROR, f"{TIPO_CIERRE_CAJA}: {mensaje}")
        return ResultadoBackup(None, mensaje)


def alerta_de_backup() -> AlertaBackup | None:
    """Alerta vigente si el último evento de backup fue un error: se apaga
    recién con un backup correcto posterior, así que ni un reinicio ni la
    apertura de la caja siguiente la ocultan."""
    try:
        ultima: EntradaAuditoria | None = repositorio_auditoria.ultima_de(_ACCIONES_DE_ESTADO)
    except ErrorBaseDatos as error:
        logger.error("No se pudo consultar el estado de los backups: %s", error)
        return AlertaBackup("", "No se pudo consultar el estado de los backups.")
    if ultima is not None and ultima.accion == ACCION_BACKUP_ERROR:
        return AlertaBackup(ultima.fecha, ultima.resumen)
    return None


def migrar_base_datos_con_backup_preventivo(directorio_backups: Path, control_escrituras) -> Path | None:
    """Aplica las migraciones pendientes, con un backup preventivo antes
    si la base de datos ya existe.

    Solo se crea el backup (`PRE_UPDATE`, mismo `crear_backup` y misma
    validación que cualquier otro) cuando `kiosco.db` ya existe y tiene
    migraciones pendientes: una instalación nueva, o una base al día, no
    generan ninguno. Si el backup falla, la excepción se propaga y las
    migraciones NO se aplican; si la migración falla después, el backup ya
    creado queda disponible para restaurar.

    Devuelve la ruta del backup preventivo, o `None` si no hizo falta.
    """
    ruta_backup = None
    if hay_migraciones_pendientes_en_base_existente():
        ruta_backup = crear_backup(directorio_backups, control_escrituras, tipo=TIPO_PRE_UPDATE, version_destino=VERSION)
        logger.info("Backup preventivo previo a la migración creado: %s", ruta_backup.name)

    inicializar_base_datos()
    return ruta_backup
