"""Metadata de un backup (V1.15): el manifiesto que viaja dentro de cada ZIP.

El manifiesto describe el backup pero nunca se le cree a ciegas: quien valida
un backup (`services.formato_backup`) contrasta cada dato verificable contra
el contenido real del ZIP (hash de cada archivo, migraciones de la base). No
lleva secretos: solo versión, tipo, fecha, esquema y la lista de archivos.
"""

from dataclasses import dataclass, field
from datetime import datetime

from excepciones import BackupInvalidoError

FORMATO_MANIFIESTO = 2
"""Versión del formato del paquete. Los backups anteriores a V1.15 no tienen
manifiesto (formato 1) y se siguen aceptando para restaurar."""

APLICACION = "Twinx Express"

TIPO_MANUAL = "MANUAL"
TIPO_AUTOMATICO = "AUTOMATICO"
TIPO_CIERRE_CAJA = "CIERRE_CAJA"
TIPO_PRE_UPDATE = "PRE_UPDATE"
TIPO_SEGURIDAD = "SEGURIDAD"
"""Copia del estado que se va a reemplazar, tomada antes de un restore o rollback."""

TIPOS_BACKUP = frozenset({TIPO_MANUAL, TIPO_AUTOMATICO, TIPO_CIERRE_CAJA, TIPO_PRE_UPDATE, TIPO_SEGURIDAD})

_FORMATO_FECHA = "%Y-%m-%dT%H:%M:%S"


@dataclass(frozen=True)
class ArchivoIncluido:
    tamano: int
    sha256: str


@dataclass(frozen=True)
class Manifiesto:
    version: str
    tipo: str
    id: str
    fecha: datetime
    migraciones: tuple[str, ...]
    integridad: str
    archivos: dict[str, ArchivoIncluido] = field(default_factory=dict)
    sesion_caja_id: int | None = None
    version_destino: str | None = None
    version_origen: str | None = None

    def a_dict(self) -> dict:
        datos = {
            "formato": FORMATO_MANIFIESTO,
            "aplicacion": APLICACION,
            "version": self.version,
            "tipo": self.tipo,
            "id": self.id,
            "fecha": self.fecha.strftime(_FORMATO_FECHA),
            "migraciones": list(self.migraciones),
            "integridad": self.integridad,
            "archivos": {
                nombre: {"tamano": archivo.tamano, "sha256": archivo.sha256}
                for nombre, archivo in sorted(self.archivos.items())
            },
        }
        if self.sesion_caja_id is not None:
            datos["sesion_caja_id"] = self.sesion_caja_id
        if self.version_destino is not None:
            datos["version_destino"] = self.version_destino
        if self.version_origen is not None:
            datos["version_origen"] = self.version_origen
        return datos


def _texto(datos: dict, clave: str) -> str:
    valor = datos.get(clave)
    if not isinstance(valor, str) or not valor:
        raise BackupInvalidoError(f"El manifiesto del backup no tiene un '{clave}' válido.")
    return valor


def _entero_opcional(datos: dict, clave: str) -> int | None:
    valor = datos.get(clave)
    if valor is None:
        return None
    if not isinstance(valor, int) or isinstance(valor, bool):
        raise BackupInvalidoError(f"El manifiesto del backup no tiene un '{clave}' válido.")
    return valor


def _archivos(datos: dict) -> dict[str, ArchivoIncluido]:
    crudo = datos.get("archivos")
    if not isinstance(crudo, dict):
        raise BackupInvalidoError("El manifiesto del backup no lista sus archivos.")
    archivos = {}
    for nombre, descripcion in crudo.items():
        if not isinstance(descripcion, dict):
            raise BackupInvalidoError("El manifiesto del backup tiene una entrada de archivo inválida.")
        tamano = descripcion.get("tamano")
        sha256 = descripcion.get("sha256")
        if not isinstance(tamano, int) or isinstance(tamano, bool) or tamano < 0:
            raise BackupInvalidoError("El manifiesto del backup tiene un tamaño de archivo inválido.")
        if not isinstance(sha256, str) or len(sha256) != 64:
            raise BackupInvalidoError("El manifiesto del backup tiene un hash de archivo inválido.")
        archivos[nombre] = ArchivoIncluido(tamano, sha256)
    return archivos


def manifiesto_desde_dict(datos: object) -> Manifiesto:
    """Interpreta el JSON del manifiesto, rechazando cualquier forma
    inesperada: un manifiesto que no se entiende hace inválido al backup."""
    if not isinstance(datos, dict):
        raise BackupInvalidoError("El manifiesto del backup no tiene un formato válido.")
    formato = datos.get("formato")
    if formato != FORMATO_MANIFIESTO:
        if isinstance(formato, int) and formato > FORMATO_MANIFIESTO:
            raise BackupInvalidoError("El backup fue generado por una versión más nueva de Twinx Express.")
        raise BackupInvalidoError("El manifiesto del backup tiene un formato desconocido.")
    if datos.get("aplicacion") != APLICACION:
        raise BackupInvalidoError("El backup no fue generado por Twinx Express.")
    tipo = _texto(datos, "tipo")
    if tipo not in TIPOS_BACKUP:
        raise BackupInvalidoError(f"El backup declara un tipo desconocido: '{tipo}'.")
    try:
        fecha = datetime.strptime(_texto(datos, "fecha"), _FORMATO_FECHA)
    except ValueError as error:
        raise BackupInvalidoError("El manifiesto del backup tiene una fecha inválida.") from error
    migraciones = datos.get("migraciones")
    if not isinstance(migraciones, list) or not all(isinstance(m, str) for m in migraciones):
        raise BackupInvalidoError("El manifiesto del backup no tiene una lista de migraciones válida.")
    version_destino = datos.get("version_destino")
    version_origen = datos.get("version_origen")
    if version_origen is not None and not isinstance(version_origen, str):
        raise BackupInvalidoError("El manifiesto del backup tiene una versión de origen inválida.")
    if version_destino is not None and not isinstance(version_destino, str):
        raise BackupInvalidoError("El manifiesto del backup tiene una versión de destino inválida.")
    return Manifiesto(
        version=_texto(datos, "version"),
        tipo=tipo,
        id=_texto(datos, "id"),
        fecha=fecha,
        migraciones=tuple(migraciones),
        integridad=_texto(datos, "integridad"),
        archivos=_archivos(datos),
        sesion_caja_id=_entero_opcional(datos, "sesion_caja_id"),
        version_destino=version_destino,
        version_origen=version_origen,
    )
