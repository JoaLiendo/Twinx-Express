"""Repositorio de configuración clave/valor (migración 018). Solo SQL parametrizado."""

import sqlite3

from db.conexion import obtener_conexion


def obtener_todas() -> dict[str, str]:
    with obtener_conexion() as conexion:
        return obtener_todas_en_conexion(conexion)


def obtener_todas_en_conexion(conexion: sqlite3.Connection) -> dict[str, str]:
    return {fila["clave"]: fila["valor"] for fila in conexion.execute("SELECT clave, valor FROM configuracion")}


def guardar_en_conexion(conexion: sqlite3.Connection, clave: str, valor: str) -> None:
    """Inserta o reemplaza una clave dentro de la transacción recibida."""
    conexion.execute(
        """
        INSERT INTO configuracion (clave, valor) VALUES (?, ?)
        ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor,
                                         fecha_actualizacion = datetime('now', 'localtime')
        """,
        (clave, valor),
    )
