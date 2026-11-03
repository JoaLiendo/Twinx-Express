"""Operaciones sobre archivos SQLite completos (no sobre la base activa vía
`obtener_conexion`): copia consistente y verificación de un archivo de base
de datos aislado, como el snapshot de un backup o la base extraída de uno.

Único lugar, junto con `db.conexion`, que abre archivos SQLite directamente.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from domain.fiscal.historia import (
    ESTADOS_CONOCIDOS_POR_ARCA,
    CaeaOtorgado,
    ComprobanteConocido,
    HistoriaFiscal,
    InformeSinMovimiento,
)
from excepciones import ErrorBaseDatos


@dataclass(frozen=True)
class VerificacionSQLite:
    """Resultado de verificar un archivo SQLite aislado."""

    integridad: str
    errores_claves_foraneas: int
    migraciones: tuple[str, ...]

    @property
    def es_valida(self) -> bool:
        return self.integridad == "ok" and self.errores_claves_foraneas == 0


def copiar_base_consistente(origen: Path, destino: Path) -> None:
    """Copia consistente de `origen` en `destino` con la Online Backup API de
    SQLite: nunca una copia del archivo a nivel de sistema operativo, que
    podría capturar una escritura a mitad de camino. Exige que `origen`
    exista: `sqlite3.connect` crearía una base vacía en su lugar."""
    if not origen.is_file():
        raise ErrorBaseDatos(f"No existe la base de datos a copiar: {origen.name}")
    try:
        conexion_origen = sqlite3.connect(origen)
        try:
            conexion_destino = sqlite3.connect(destino)
            try:
                conexion_origen.backup(conexion_destino)
            finally:
                conexion_destino.close()
        finally:
            conexion_origen.close()
    except sqlite3.Error as error:
        raise ErrorBaseDatos(f"No se pudo copiar la base de datos: {error}") from error


def registrar_auditoria_en_archivo(ruta: Path, accion: str, entidad: str, resumen: str) -> None:
    """Deja una entrada de auditoría sin usuario en una base que la aplicación
    no está usando (ej. la recién restaurada por consola, con el programa
    cerrado). `accion` y `resumen` ya vienen normalizados por el dominio.

    Raises:
        ErrorBaseDatos: si la base no se puede escribir o no tiene la tabla.
    """
    try:
        conexion = sqlite3.connect(ruta)
        try:
            with conexion:
                conexion.execute(
                    "INSERT INTO auditoria (usuario_id, accion, entidad, entidad_id, resumen) VALUES (NULL, ?, ?, NULL, ?)",
                    (accion, entidad, resumen),
                )
        finally:
            conexion.close()
    except sqlite3.Error as error:
        raise ErrorBaseDatos(f"No se pudo registrar la auditoría: {error}") from error


def verificar_archivo(ruta: Path) -> VerificacionSQLite:
    """Abre `ruta` en solo lectura y corre `integrity_check` y
    `foreign_key_check`, más la lista de migraciones aplicadas (vacía si la
    base no tiene `schema_migraciones`).

    Raises:
        ErrorBaseDatos: si el archivo no existe o no es una base SQLite legible.
    """
    if not ruta.is_file():
        raise ErrorBaseDatos(f"No existe el archivo de base de datos: {ruta.name}")
    try:
        conexion = sqlite3.connect(f"{ruta.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as error:
        raise ErrorBaseDatos(f"No se pudo abrir la base de datos: {error}") from error
    try:
        filas_integridad = conexion.execute("PRAGMA integrity_check").fetchall()
        integridad = "; ".join(str(fila[0]) for fila in filas_integridad) or "sin resultado"
        errores_fk = len(conexion.execute("PRAGMA foreign_key_check").fetchall())
        tiene_registro = conexion.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migraciones'"
        ).fetchone()
        migraciones: tuple[str, ...] = ()
        if tiene_registro:
            migraciones = tuple(
                fila[0]
                for fila in conexion.execute("SELECT nombre_archivo FROM schema_migraciones ORDER BY nombre_archivo")
            )
    except sqlite3.DatabaseError as error:
        raise ErrorBaseDatos(f"El archivo de base de datos está dañado: {error}") from error
    finally:
        conexion.close()
    return VerificacionSQLite(integridad, errores_fk, migraciones)


def _tablas(conexion: sqlite3.Connection) -> set[str]:
    return {fila[0] for fila in conexion.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def leer_historia_fiscal(ruta: Path) -> HistoriaFiscal:
    """Historia fiscal conocida o potencialmente conocida por ARCA de un archivo SQLite aislado (solo
    lectura). Una base anterior a la facturación (sin las tablas de las migraciones 041/044) no tiene
    historia.

    Raises:
        ErrorBaseDatos: si el archivo no existe o no se puede leer.
    """
    if not ruta.is_file():
        raise ErrorBaseDatos(f"No existe el archivo de base de datos: {ruta.name}")
    marcadores = ", ".join("?" for _ in ESTADOS_CONOCIDOS_POR_ARCA)
    try:
        conexion = sqlite3.connect(f"{ruta.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as error:
        raise ErrorBaseDatos(f"No se pudo abrir la base de datos: {error}") from error
    try:
        tablas = _tablas(conexion)
        comprobantes: tuple[ComprobanteConocido, ...] = ()
        caeas: frozenset[CaeaOtorgado] = frozenset()
        informes: frozenset[InformeSinMovimiento] = frozenset()
        if "comprobantes_fiscales" in tablas:
            comprobantes = tuple(
                ComprobanteConocido(*fila)
                for fila in conexion.execute(
                    "SELECT ambiente, cuit_emisor, punto_venta, tipo_comprobante, numero, estado, codigo_autorizacion"
                    f" FROM comprobantes_fiscales WHERE estado IN ({marcadores}) ORDER BY id",
                    tuple(sorted(ESTADOS_CONOCIDOS_POR_ARCA)),
                )
            )
        if "caea_autorizaciones" in tablas:
            caeas = frozenset(
                CaeaOtorgado(*fila)
                for fila in conexion.execute("SELECT ambiente, cuit, codigo, periodo, orden FROM caea_autorizaciones")
            )
        if "caea_sin_movimiento" in tablas:
            informes = frozenset(
                InformeSinMovimiento(*fila)
                for fila in conexion.execute(
                    "SELECT c.ambiente, c.codigo, s.punto_venta FROM caea_sin_movimiento s"
                    " JOIN caea_autorizaciones c ON c.id = s.caea_id WHERE s.resultado = 'A'"
                )
            )
    except sqlite3.DatabaseError as error:
        raise ErrorBaseDatos(f"No se pudo leer la historia fiscal de la base: {error}") from error
    finally:
        conexion.close()
    return HistoriaFiscal(comprobantes, caeas, informes)
