"""Configuración centralizada del proyecto.

Punto único de verdad para rutas y constantes de configuración,
de forma que ningún otro módulo hardcodee rutas absolutas.
"""

import os
import sys
from pathlib import Path

RAIZ_PROYECTO = Path(__file__).resolve().parent


class ErrorMigracionDatos(Exception):
    """Error fatal al resolver el directorio persistente de datos."""


def ruta_datos_persistentes_frozen() -> Path:
    """Ubicación persistente de `data/` en modo PyInstaller frozen.

    Windows: `%LOCALAPPDATA%\\KioscoApp\\data`. Fuera del árbol que
    PyInstaller gestiona (`_internal/`), porque un rebuild de PyInstaller
    borra ese árbol completo -- exe incluido -- antes de reconstruirlo
    (comprobado empíricamente en la auditoría de distribución), así que
    cualquier dato mutable que viva ahí dentro se pierde en cada rebuild.
    """
    local_appdata = os.environ.get("LOCALAPPDATA")
    if not local_appdata:
        raise ErrorMigracionDatos(
            "La variable de entorno LOCALAPPDATA no está definida: no se "
            "puede ubicar el directorio de datos persistente."
        )
    return Path(local_appdata) / "KioscoApp" / "data"


# En frozen, los datos del cliente nacen siempre en el directorio persistente
# y nunca desde el bundle: si un build trajera un `data/` embebido dentro de
# `_internal/`, se ignora por completo (no se lee ni se copia). Un build así
# es inválido y `Construir_entregable.ps1` lo rechaza.
if getattr(sys, "frozen", False):
    DIRECTORIO_DATA = ruta_datos_persistentes_frozen()
else:
    DIRECTORIO_DATA = RAIZ_PROYECTO / "data"

RUTA_BASE_DATOS = DIRECTORIO_DATA / "kiosco.db"
DIRECTORIO_MIGRACIONES = RAIZ_PROYECTO / "db" / "migraciones"

# Imágenes de producto (Fase 3D): archivos en disco, no BLOB en SQLite (ver
# auditoría de Fase 3D). Vive bajo DIRECTORIO_DATA -- igual que kiosco.db --
# para que ambos viajen siempre juntos, sea cual sea su ubicación real.
DIRECTORIO_IMAGENES_PRODUCTOS = DIRECTORIO_DATA / "imagenes_productos"

# La carpeta de datos puede no existir todavía (checkout nuevo en modo dev,
# o primera ejecución en modo frozen): se crea automáticamente al importar
# este módulo, para que cualquier código que use RUTA_BASE_DATOS pueda
# asumir que el directorio contenedor ya existe. Lo mismo para el
# subdirectorio de imágenes de producto.
DIRECTORIO_DATA.mkdir(parents=True, exist_ok=True)
DIRECTORIO_IMAGENES_PRODUCTOS.mkdir(parents=True, exist_ok=True)

# Backups (manual y automático, ver `services/servicio_backup.py`): siempre
# hermano de `data/`, nunca dentro (mismo motivo que las imágenes: viaja
# junto a la instalación, sea cual sea `DIRECTORIO_DATA` -- dev o frozen).
DIRECTORIO_BACKUPS = DIRECTORIO_DATA.parent / "backups"

# Antigüedad mínima del último backup para que el arranque de la app dispare
# uno nuevo automáticamente (ver `services.servicio_backup.
# ejecutar_backup_automatico_si_corresponde`). Constante simple: no amerita
# configuración externa todavía para un kiosco de un solo equipo.
ANTIGUEDAD_MINIMA_BACKUP_AUTOMATICO_HORAS = 24
