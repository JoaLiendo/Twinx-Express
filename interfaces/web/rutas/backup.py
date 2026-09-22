"""Ruta de backup manual, exclusiva para OWNER (ver auditoría de
distribución/backup).

El restore NO tiene UI web: por diseño, solo corre con la aplicación
cerrada, vía `KioscoApp.exe --restore <zip>` (ver `lanzador.py`) --
nunca como una ruta de este mismo servidor en ejecución.
"""

from fastapi import APIRouter, Depends, Request

from config import DIRECTORIO_BACKUPS
from interfaces.web.auth import requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base, redireccionar_con_mensaje
from services import servicio_backup
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
    return templates.TemplateResponse(request, "backup.html", contexto_base(request))


@router.post(RUTA_CREAR_BACKUP)
def accion_crear_backup(request: Request):
    """Un `ErrorBackup` (ver `excepciones.py`) no se atrapa acá: el
    manejador global de `ErrorAplicacion` en `interfaces/web/app.py`
    ya lo traduce a un toast de error sobre la página de origen."""
    ruta_zip = servicio_backup.crear_backup(DIRECTORIO_BACKUPS, control_escrituras)
    return redireccionar_con_mensaje("/backup", "success", f"Backup creado: {ruta_zip.name}")
