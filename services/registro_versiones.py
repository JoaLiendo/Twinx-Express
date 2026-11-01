"""Registro de las versiones del programa conservadas para rollback (V1.15-F).

Vive en `%LOCALAPPDATA%\\KioscoApp\\versiones\\versiones.json`, junto a la
copia archivada de cada versión. Cada versión conservada se identifica por su
número, su carpeta archivada, las migraciones que conoce y el backup
`PRE_UPDATE` tomado al dejarla: el rollback real es ESE programa con ESE
backup, nunca el ejecutable solo.
"""

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import config
from excepciones import ErrorActualizacion

logger = logging.getLogger(__name__)

NOMBRE_REGISTRO = "versiones.json"


@dataclass(frozen=True)
class VersionConservada:
    version: str
    carpeta: str
    migraciones: tuple[str, ...]
    fecha: str
    backup_pre_update: str | None = None
    """Backup tomado al actualizar DESDE esta versión: con él se vuelve a ella."""


@dataclass
class Registro:
    instalacion: str | None = None
    activa: str | None = None
    versiones: list[VersionConservada] = field(default_factory=list)

    def buscar(self, version: str) -> VersionConservada | None:
        return next((v for v in self.versiones if v.version == version), None)

    def reemplazar(self, conservada: VersionConservada) -> None:
        self.versiones = [v for v in self.versiones if v.version != conservada.version] + [conservada]


def ruta_registro() -> Path:
    return config.DIRECTORIO_VERSIONES / NOMBRE_REGISTRO


def leer() -> Registro:
    """El registro, vacío si todavía no hubo ninguna actualización con V1.15+.

    Raises:
        ErrorActualizacion: si el archivo existe pero no se puede interpretar.
    """
    ruta = ruta_registro()
    if not ruta.is_file():
        return Registro()
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        return Registro(
            instalacion=datos.get("instalacion"),
            activa=datos.get("activa"),
            versiones=[
                VersionConservada(
                    version=v["version"],
                    carpeta=v["carpeta"],
                    migraciones=tuple(v["migraciones"]),
                    fecha=v["fecha"],
                    backup_pre_update=v.get("backup_pre_update"),
                )
                for v in datos.get("versiones", [])
            ],
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ErrorActualizacion(f"El registro de versiones está dañado ({ruta}): {error}") from error


def guardar(registro: Registro) -> None:
    """Escritura atómica: un corte a mitad de camino deja el registro anterior."""
    ruta = ruta_registro()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix(".json.tmp")
    datos = asdict(registro)
    temporal.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporal, ruta)


def backups_protegidos() -> frozenset[str] | None:
    """Nombres de los backups `PRE_UPDATE` que respaldan una versión conservada:
    la retención nunca los borra. `None` si el registro no se puede leer, para
    que la retención proteja todos los pre-update antes que arriesgar uno."""
    try:
        registro = leer()
    except ErrorActualizacion as error:
        logger.error("%s Se conservan todos los backups previos a actualizaciones.", error)
        return None
    return frozenset(v.backup_pre_update for v in registro.versiones if v.backup_pre_update)
