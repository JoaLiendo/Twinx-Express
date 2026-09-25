"""Gestor de conexiones seguras a la base de datos SQLite.

Centraliza la creación de conexiones (con `PRAGMA foreign_keys` activado)
y el manejo de transacciones mediante un context manager, y expone la
inicialización del esquema a partir del script de migración. Ningún otro
módulo del proyecto debe abrir conexiones sqlite3 por su cuenta.
"""

import logging
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from config import DIRECTORIO_MIGRACIONES, RUTA_BASE_DATOS
from excepciones import ErrorBaseDatos

logger = logging.getLogger(__name__)

_TABLA_MIGRACIONES = """
    CREATE TABLE IF NOT EXISTS schema_migraciones (
        nombre_archivo  TEXT NOT NULL PRIMARY KEY,
        fecha_aplicada  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    )
"""


_MENSAJE_BASE_DATOS_OCUPADA = "Hay otra operación en curso. Intentá nuevamente en unos segundos."


ConexionBD = sqlite3.Connection
"""Tipo de la conexión que entrega `obtener_conexion`: los servicios lo usan para anotar los
parámetros `conexion` de sus funciones auxiliares sin importar `sqlite3` (solo `db/` lo hace)."""


@contextmanager
def obtener_conexion(*, inmediata: bool = False) -> Generator[sqlite3.Connection, None, None]:
    """Provee una conexión SQLite dentro de una transacción segura.

    Activa las claves foráneas en cada conexión (SQLite las tiene
    desactivadas por defecto) y garantiza que, al salir del bloque
    `with`, se haga *commit* si no hubo errores o *rollback* si los
    hubo, cerrando siempre la conexión.

    Los errores de `sqlite3` se traducen a `ErrorBaseDatos` para que
    las capas superiores (services/, interfaces/) no dependan de
    `sqlite3` directamente.

    Uso:
        with obtener_conexion() as conexion:
            conexion.execute("INSERT INTO productos (...) VALUES (...)", parametros)

    Args:
        inmediata: si es `True`, toma el lock de escritura con `BEGIN
            IMMEDIATE` apenas se entra al bloque `with`, en vez de
            dejar que sqlite3 abra una transacción implícita recién
            antes de la primera escritura (su comportamiento por
            defecto, `isolation_level` no configurado). Sin esto, un
            `SELECT` de chequeo previo a una decisión de escritura (ej.
            "¿alcanza el stock?") corre sin ningún lock que lo proteja:
            dos transacciones concurrentes pueden leer el mismo valor y
            escribir las dos "de más" sin que SQLite lo detecte --
            comprobado empíricamente en la auditoría de concurrencia de
            Stage D (lost update silencioso, sin ningún error). Usar
            en toda transacción que primero lea un valor y decida, en
            base a ese valor, si escribe (ver
            `services.servicio_ventas.registrar_venta` y
            `services.servicio_compras.registrar_compra`); no se activa
            por defecto para no tomar un lock de escritura en
            operaciones puramente de lectura.
    """
    conexion = sqlite3.connect(RUTA_BASE_DATOS)
    conexion.execute("PRAGMA foreign_keys = ON;")
    # No garantiza la idempotencia (eso lo hace el UNIQUE de
    # ventas.clave_idempotencia, ver Fase 5A) -- evita que, ante una
    # carrera real de escritura entre dos transacciones, la que pierde
    # el lock falle de inmediato con "database is locked" en vez de
    # esperar un poco a que la otra termine.
    conexion.execute("PRAGMA busy_timeout = 5000;")
    conexion.row_factory = sqlite3.Row

    try:
        if inmediata:
            conexion.execute("BEGIN IMMEDIATE;")
        yield conexion
    except sqlite3.OperationalError as error:
        conexion.rollback()
        if "database is locked" in str(error):
            # Se agotó `busy_timeout` (5s) esperando el lock de
            # escritura de otra transacción: es una contención real,
            # no un error de la aplicación. Mensaje claro para el
            # usuario final -- nunca el texto técnico crudo de sqlite3
            # (ver auditoría de concurrencia, Stage D). Cualquier otro
            # `OperationalError` (sintaxis, tabla inexistente, etc.)
            # sigue el camino genérico de abajo, con el detalle técnico
            # intacto: esto no oculta ningún otro error de sqlite3.
            logger.warning("Contención real de escritura en la base de datos: %s", error)
            raise ErrorBaseDatos(_MENSAJE_BASE_DATOS_OCUPADA) from error
        logger.error("Error de base de datos, se revirtió la transacción: %s", error)
        raise ErrorBaseDatos(f"Error al operar sobre la base de datos: {error}") from error
    except sqlite3.Error as error:
        conexion.rollback()
        logger.error("Error de base de datos, se revirtió la transacción: %s", error)
        raise ErrorBaseDatos(f"Error al operar sobre la base de datos: {error}") from error
    except Exception:
        conexion.rollback()
        raise
    else:
        conexion.commit()
    finally:
        conexion.close()


def inicializar_base_datos(ruta_script: Path | None = None) -> None:
    """Crea o actualiza el esquema de la base de datos.

    Sin argumentos, aplica todos los scripts de `db/migraciones/` que
    todavía no se hayan aplicado (en orden alfabético de nombre de
    archivo), llevando el registro en la tabla `schema_migraciones`.
    Cada script se ejecuta una única vez en la vida de la base de
    datos, así que puede contener cambios no idempotentes (ej.
    `ALTER TABLE ... ADD COLUMN`) además de `CREATE TABLE IF NOT
    EXISTS`. Cada migración es atómica: se aplica completa junto con
    su registro o no deja ningún cambio (los scripts no deben incluir
    `BEGIN`/`COMMIT` propios). Es seguro llamar a esta función en cada
    arranque de la aplicación: los scripts ya aplicados se saltean.

    Args:
        ruta_script: ruta a un único script SQL a ejecutar en lugar
            del flujo normal de migraciones versionadas, sin dejar
            registro en `schema_migraciones`. Pensado para tests que
            necesitan un esquema puntual.
    """
    if ruta_script is not None:
        if not ruta_script.exists():
            raise ErrorBaseDatos(f"No se encontró el script de migración: {ruta_script}")
        with obtener_conexion() as conexion:
            conexion.executescript(ruta_script.read_text(encoding="utf-8"))
        logger.info("Base de datos inicializada correctamente en %s", RUTA_BASE_DATOS)
        return

    rutas_migraciones = sorted(DIRECTORIO_MIGRACIONES.glob("*.sql"))
    if not rutas_migraciones:
        raise ErrorBaseDatos(f"No se encontraron scripts de migración en: {DIRECTORIO_MIGRACIONES}")

    with obtener_conexion() as conexion:
        conexion.execute(_TABLA_MIGRACIONES)
        aplicadas = {
            fila["nombre_archivo"]
            for fila in conexion.execute("SELECT nombre_archivo FROM schema_migraciones").fetchall()
        }
        for ruta in rutas_migraciones:
            if ruta.name in aplicadas:
                continue
            # `executescript` confirma lo pendiente y luego corre el script en
            # autocommit: sin este `BEGIN` una migración que falla a mitad
            # deja cambios parciales persistidos. Con él, el script y su
            # registro forman una única transacción: se confirma entera acá
            # o, ante cualquier error, la hace revertir `obtener_conexion`
            # y la migración sigue pendiente.
            conexion.executescript("BEGIN IMMEDIATE;\n" + ruta.read_text(encoding="utf-8"))
            conexion.execute(
                "INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (ruta.name,)
            )
            conexion.commit()
            logger.info("Migración aplicada: %s", ruta.name)

    logger.info("Base de datos inicializada correctamente en %s", RUTA_BASE_DATOS)


def hay_migraciones_pendientes_en_base_existente() -> bool:
    """Indica si `kiosco.db` ya existe con esquema y le faltan migraciones.

    Sirve para decidir si conviene un backup preventivo antes de migrar
    (ver `services.servicio_backup.migrar_base_datos_con_backup_preventivo`).
    Es `False` para una instalación nueva -- el archivo no existe, o existe
    pero todavía no tiene ninguna tabla -- porque no hay nada que proteger.
    Solo lee: nunca crea el archivo ni aplica nada.
    """
    if not RUTA_BASE_DATOS.is_file():
        return False

    with obtener_conexion() as conexion:
        tablas = {
            fila["name"]
            for fila in conexion.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        if not tablas:
            return False
        aplicadas: set[str] = set()
        if "schema_migraciones" in tablas:
            aplicadas = {
                fila["nombre_archivo"]
                for fila in conexion.execute("SELECT nombre_archivo FROM schema_migraciones").fetchall()
            }

    return any(ruta.name not in aplicadas for ruta in DIRECTORIO_MIGRACIONES.glob("*.sql"))


if __name__ == "__main__":
    # Permite inicializar la base de datos manualmente:
    #   python -m db.conexion
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    inicializar_base_datos()
