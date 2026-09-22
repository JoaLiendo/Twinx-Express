"""Configuración centralizada del proyecto.

Punto único de verdad para rutas y constantes de configuración,
de forma que ningún otro módulo hardcodee rutas absolutas.
"""

import os
import shutil
import sys
from pathlib import Path

RAIZ_PROYECTO = Path(__file__).resolve().parent


class ErrorMigracionDatos(Exception):
    """Error fatal al resolver o migrar el directorio persistente de datos."""


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


def resolver_directorio_datos_frozen(*, dir_origen: Path, dir_destino: Path) -> Path:
    """Migra `data/` del bundle efímero de PyInstaller al destino persistente.

    `dir_origen` es el `data/` embebido en el build (dentro de
    `_internal/`, se pierde en cada rebuild); `dir_destino` es la
    ubicación persistente fuera del bundle. Nunca borra `dir_origen`.

    Casos:
      - Ninguno existe (primera instalación real): se crea `dir_destino`
        vacío.
      - Solo existe `dir_origen` (migración pendiente): se copia
        completo a `dir_destino`, sin tocar el origen.
      - Solo existe `dir_destino` (uso normal post-migración): se usa
        tal cual.
      - Existen ambos: conflicto que no se puede resolver
        automáticamente sin arriesgar pérdida de datos -> error fatal,
        nunca se sobrescribe ni se fusiona en silencio.
    """
    origen_existe = dir_origen.is_dir()
    destino_existe = dir_destino.is_dir()

    if origen_existe and destino_existe:
        raise ErrorMigracionDatos(
            f"Se encontraron datos tanto en el origen empaquetado ({dir_origen}) "
            f"como en el destino persistente ({dir_destino}). Para evitar "
            "pérdida de datos, la aplicación no decide automáticamente cuál "
            "usar: resolvé manualmente cuál carpeta 'data' es la válida y "
            "eliminá o renombrá la otra antes de volver a iniciar."
        )

    if origen_existe:
        shutil.copytree(dir_origen, dir_destino)
        return dir_destino

    dir_destino.mkdir(parents=True, exist_ok=True)
    return dir_destino


if getattr(sys, "frozen", False):
    DIRECTORIO_DATA = resolver_directorio_datos_frozen(
        dir_origen=RAIZ_PROYECTO / "data",
        dir_destino=ruta_datos_persistentes_frozen(),
    )
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
# subdirectorio de imágenes de producto. En el caso de migración (frozen,
# Caso 2) ya quedó creada por `shutil.copytree`; `exist_ok=True` la deja
# intacta.
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
