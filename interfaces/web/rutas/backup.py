"""Ruta de backup manual, exclusiva para OWNER (ver auditoría de
distribución/backup).

El restore NO tiene UI web: por diseño, solo corre con la aplicación
cerrada, vía `KioscoApp.exe --restore <zip>` (ver `lanzador.py`) --
nunca como una ruta de este mismo servidor en ejecución.
"""

from fastapi import APIRouter, Depends, Form, Request

from domain.backup import TIPO_MANUAL
from domain.usuario import Usuario
from interfaces.web.auth import obtener_usuario_actual, requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base, redireccionar_con_mensaje
from services import servicio_backup, servicio_destino_secundario
from services.control_escrituras import control_escrituras

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER"))])

# Único lugar donde se escribe este path: el middleware de
# `interfaces/web/app.py` lo importa para excluir este request del
# conteo de "escrituras de negocio" -- es quien orquesta el propio
# bloqueo (ver `services.control_escrituras.iniciar_backup`), contarlo
# generaría una espera circular sobre sí mismo.
RUTA_CREAR_BACKUP = "/backup/crear"


@router.get("/backup")
def panel_backup(request: Request):
    estado = servicio_backup.estado_backups()
    contexto = {**contexto_base(request), "estado": estado, "alerta_backup": estado.alerta}
    return templates.TemplateResponse(request, "backup.html", contexto)


@router.post("/backup/destino-secundario")
def guardar_destino_secundario(
    destino: str = Form(""), usuario_actual: Usuario | None = Depends(obtener_usuario_actual)
):
    """Un `DatosInvalidosError` (carpeta inexistente, sin permisos, dentro de
    los datos activos) lo traduce a toast el manejador global."""
    ruta = servicio_destino_secundario.guardar_destino_secundario(destino, usuario_actual.id)
    mensaje = f"Segundo destino configurado: {ruta}" if ruta else "Segundo destino desactivado."
    return redireccionar_con_mensaje("/backup", "success", mensaje)


@router.post(RUTA_CREAR_BACKUP)
def accion_crear_backup(usuario_actual: Usuario | None = Depends(obtener_usuario_actual)):
    """Un `BackupEnCursoError` (ver `excepciones.py`) no se atrapa acá: el
    manejador global de `ErrorAplicacion` en `interfaces/web/app.py` ya lo
    traduce a un toast de error sobre la página de origen."""
    resultado = servicio_backup.ejecutar_backup(TIPO_MANUAL, control_escrituras, usuario_id=usuario_actual.id)
    if not resultado.ok:
        return redireccionar_con_mensaje("/backup", "error", f"No se pudo crear el backup: {resultado.error}")
    return redireccionar_con_mensaje("/backup", "success", f"Backup creado: {resultado.ruta.name}")
