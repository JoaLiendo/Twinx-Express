"""Una sola instancia de Twinx Express por base de datos, forzada por el sistema operativo.

Twinx Express es 1 PC, 1 caja y 1 base. Los candados fiscales (`services.fiscal`) son por proceso y
`recuperar_al_iniciar` da por muerto todo ENVIANDO: un segundo proceso sobre la misma base rompería
ambas cosas, así que no se lo deja llegar hasta ahí. Ni el puerto ni el navegador sirven para esto:
uvicorn corre el lifespan antes del bind, y otro puerto no choca con nada.

El lock es un bloqueo del sistema sobre un archivo, no el archivo: existir no significa nada, por viejo
que sea. Si el proceso muere, el sistema lo libera sin depender de ninguna limpieza. Por eso no se
guarda PID ni ningún otro dato (el archivo queda vacío) y nunca se borra: borrarlo abriría una carrera
con quien lo esté bloqueando.

El archivo vive en `locks/`, hermana de la carpeta de la base. Fuera de `data/` porque esa carpeta es
la que viaja en los backups y la que el restore renombra (Windows no renombra una carpeta con un handle
abierto adentro). Y depende solo de la ruta de la base, no de `%LOCALAPPDATA%` ni de otra variable del
entorno: dos procesos sobre la misma base tienen que dar siempre con el mismo archivo.
"""

import hashlib
import logging
import os
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import db.conexion as modulo_conexion
from excepciones import ErrorBaseDatos, InstanciaEnUsoError

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

logger = logging.getLogger(__name__)

NOMBRE_DIRECTORIO_LOCKS = "locks"
MENSAJE_INSTANCIA_EN_USO = (
    "Twinx Express ya está abierto usando esta base de datos. Cerrá la otra ventana antes de continuar."
)
_MENSAJE_SIN_LOCK = "No se pudo comprobar si Twinx Express ya está abierto, así que no se abrieron los datos."


@dataclass
class _Tenencia:
    descriptor: int
    usos: int


_tenencias: dict[Path, _Tenencia] = {}
_candado = threading.Lock()


def _bloquear(descriptor: int) -> None:
    """Sin espera: si otro lo tiene, falla enseguida con `PermissionError` o `BlockingIOError`."""
    if sys.platform == "win32":
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _desbloquear(descriptor: int) -> None:
    if sys.platform == "win32":
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


def ruta_lock(ruta_db: Path | str) -> Path:
    """Archivo de lock de una base. Rutas equivalentes (relativas, con `..`, por un enlace o, en Windows,
    con otras mayúsculas) dan el mismo archivo. El nombre es un hash: no expone la ruta."""
    canonica = Path(ruta_db).resolve()
    identidad = os.path.normcase(str(canonica)).encode("utf-8", "surrogatepass")
    return canonica.parent.parent / NOMBRE_DIRECTORIO_LOCKS / f"{hashlib.sha256(identidad).hexdigest()}.lock"


def _sin_lock(error: OSError) -> ErrorBaseDatos:
    """Sin ruta en el texto: lo lee el operador. El tipo y la causa del sistema alcanzan para soporte."""
    return ErrorBaseDatos(f"{_MENSAJE_SIN_LOCK} ({type(error).__name__}: {error.strerror})")


def _abrir(ruta: Path) -> int:
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        return os.open(ruta, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as error:
        raise _sin_lock(error) from error


def _tomar(ruta: Path) -> None:
    with _candado:
        tenencia = _tenencias.get(ruta)
        if tenencia is not None:
            tenencia.usos += 1
            return
        descriptor = _abrir(ruta)
        try:
            _bloquear(descriptor)
        except (PermissionError, BlockingIOError) as error:
            os.close(descriptor)
            raise InstanciaEnUsoError(MENSAJE_INSTANCIA_EN_USO) from error
        except OSError as error:
            os.close(descriptor)
            raise _sin_lock(error) from error
        _tenencias[ruta] = _Tenencia(descriptor, 1)


def _soltar(ruta: Path) -> None:
    with _candado:
        tenencia = _tenencias[ruta]
        tenencia.usos -= 1
        if tenencia.usos > 0:
            return
        del _tenencias[ruta]
        try:
            _desbloquear(tenencia.descriptor)
        except OSError as error:
            # Cerrar el descriptor (abajo) lo libera igual: esto solo deja constancia.
            logger.warning("No se pudo desbloquear la instancia de forma ordenada: %s", error)
        finally:
            os.close(tenencia.descriptor)


@contextmanager
def exclusiva(ruta_db: Path | None = None) -> Iterator[None]:
    """Hace a este proceso el único dueño de la base (por defecto, la activa) mientras dure el bloque.

    Dentro de un mismo proceso se puede anidar, porque quien ya es dueño sigue siéndolo: el lanzador la
    toma antes de importar la aplicación y el lifespan la vuelve a pedir para cubrir un arranque sin
    lanzador, y el rollback llama al restore. La base se libera recién al salir del bloque más externo.

    También sirve de decorador; la base se resuelve en cada llamada.

    Raises:
        InstanciaEnUsoError: otro proceso es el dueño. Nunca se fuerza ni se espera.
        ErrorBaseDatos: no se pudo preparar el lock. Sin él no hay garantía de exclusión: no se sigue.
    """
    ruta = ruta_lock(ruta_db if ruta_db is not None else modulo_conexion.RUTA_BASE_DATOS)
    _tomar(ruta)
    try:
        yield
    finally:
        _soltar(ruta)
