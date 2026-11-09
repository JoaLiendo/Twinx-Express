"""Backup diario a una hora elegida, solo mientras la aplicación está abierta (cierre operativo, F4).

Complementa (no reemplaza) los backups existentes: el automático al arrancar (si el último tiene más de
24 h), el de cierre de caja y el manual. Es un hilo liviano del propio proceso: nada de servicios de
Windows ni tareas programadas. Si la computadora está apagada a esa hora, NO hay backup (la pantalla lo
dice tal cual); al día siguiente, o al abrir la aplicación, vuelve a corresponder. Reutiliza `crear_backup`
con el tipo AUTOMATICO, así la retención (14 diarios / 8 semanales / 12 mensuales) lo trata como un
backup diario más.
"""

import logging
import re
import threading
from datetime import datetime, time
from pathlib import Path

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import configuracion as repositorio_configuracion
from domain.auditoria import ACCION_BACKUP_AUTOMATICO_OK, ACCION_BACKUP_ERROR, ACCION_CONFIGURACION_BACKUP_CAMBIADA
from excepciones import DatosInvalidosError, ErrorBackup
from services import servicio_backup

logger = logging.getLogger(__name__)

CLAVE_HORA_PROGRAMADA = "backup.hora_programada"
INTERVALO_SONDEO_SEGUNDOS = 60
_HORA = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def validar_hora(texto: str | None) -> time | None:
    """`HH:MM` (24 h) o `None` si está vacío (desactivado).

    Raises:
        DatosInvalidosError: si el texto no es una hora válida.
    """
    limpio = (texto or "").strip()
    if not limpio:
        return None
    coincidencia = _HORA.match(limpio)
    if coincidencia is None:
        raise DatosInvalidosError("La hora del backup programado debe tener el formato HH:MM (por ejemplo 21:30).")
    return time(int(coincidencia.group(1)), int(coincidencia.group(2)))


def obtener_hora_programada() -> time | None:
    """Hora configurada, o `None` si el backup programado está desactivado (por defecto)."""
    try:
        return validar_hora(repositorio_configuracion.obtener_todas().get(CLAVE_HORA_PROGRAMADA))
    except DatosInvalidosError:
        logger.error("La hora del backup programado guardada no es válida: se ignora")
        return None


def guardar_hora_programada(texto: str, usuario_id: int) -> time | None:
    """Guarda (o quita, con texto vacío) la hora del backup diario y lo audita si cambió (solo OWNER: lo
    exige la ruta)."""
    hora = validar_hora(texto)
    valor = hora.strftime("%H:%M") if hora else ""
    with obtener_conexion(inmediata=True) as conexion:
        actual = repositorio_configuracion.obtener_todas_en_conexion(conexion).get(CLAVE_HORA_PROGRAMADA, "")
        if actual == valor:
            return hora
        repositorio_configuracion.guardar_en_conexion(conexion, CLAVE_HORA_PROGRAMADA, valor)
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, ACCION_CONFIGURACION_BACKUP_CAMBIADA, "CONFIGURACION", None,
            f"Backup diario programado: {valor or 'desactivado'}",
        )
    logger.info("Backup diario programado: %s", valor or "desactivado")
    return hora


def corresponde(hora: time | None, ahora: datetime, ultimo_backup_regular: datetime | None) -> bool:
    """Regla pura: ya pasó la hora de hoy y todavía no hay un backup regular (manual, automático o de cierre)
    posterior a esa hora de hoy. Un backup hecho a mano después de la hora también la cubre."""
    if hora is None or ahora.time() < hora:
        return False
    momento_programado = datetime.combine(ahora.date(), hora)
    return ultimo_backup_regular is None or ultimo_backup_regular < momento_programado


def ejecutar_si_corresponde(directorio_destino: Path, control_escrituras, ahora: datetime | None = None) -> Path | None:
    """Un backup AUTOMATICO si corresponde según la hora configurada. Nunca propaga errores (hilo en
    segundo plano): un fallo queda en el log y en la auditoría de `crear_backup`, y se reintenta en el
    próximo sondeo."""
    ahora = ahora or datetime.now()
    hora = obtener_hora_programada()
    ultimo = servicio_backup._fecha_ultimo_backup(directorio_destino) if directorio_destino.exists() else None
    if not corresponde(hora, ahora, ultimo):
        return None
    try:
        ruta = servicio_backup.crear_backup(directorio_destino, control_escrituras, tipo=servicio_backup.TIPO_AUTOMATICO)
    except ErrorBackup as error:
        logger.error("Backup programado de las %s no realizado: %s", hora.strftime("%H:%M"), error)
        servicio_backup._auditar(None, ACCION_BACKUP_ERROR, f"{servicio_backup.TIPO_AUTOMATICO} (programado {hora:%H:%M}): {error}")
        return None
    logger.info("Backup programado de las %s realizado: %s", hora.strftime("%H:%M"), ruta.name)
    servicio_backup._auditar(None, ACCION_BACKUP_AUTOMATICO_OK, f"{ruta.name} (programado {hora:%H:%M})")
    servicio_backup.copiar_a_destino_secundario(ruta)
    return ruta


def bucle(directorio_destino: Path, control_escrituras, detener: threading.Event, intervalo: float = INTERVALO_SONDEO_SEGUNDOS) -> None:
    """Hilo daemon del proceso: sondea cada `intervalo` segundos hasta que `detener` se active. Cualquier
    excepción inesperada se registra y el bucle sigue (nunca tumba la aplicación)."""
    while not detener.is_set():
        try:
            ejecutar_si_corresponde(directorio_destino, control_escrituras)
        except Exception:  # noqa: BLE001 - límite exterior de un hilo sin llamador: solo se registra
            logger.exception("Error inesperado en el backup programado")
        detener.wait(intervalo)


def iniciar(directorio_destino: Path, control_escrituras) -> threading.Event:
    """Arranca el hilo y devuelve el evento para detenerlo al cerrar la aplicación."""
    detener = threading.Event()
    threading.Thread(target=bucle, args=(directorio_destino, control_escrituras, detener), daemon=True, name="backup-programado").start()
    return detener
