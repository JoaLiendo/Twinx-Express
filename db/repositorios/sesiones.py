"""Repositorio de acceso a datos de sesiones (Fase 2B: solo persistencia).

Contiene únicamente consultas SQL parametrizadas sobre `sesiones`. La
lógica de cuándo crear una sesión, verificar su expiración o
invalidarla (login/logout) se implementa en `services.servicio_auth`
en una fase posterior: acá no hay ninguna regla de negocio de
autenticación.
"""

import sqlite3

from db.conexion import obtener_conexion
from domain.sesion import Sesion

_COLUMNAS = "token, usuario_id, fecha_creacion, fecha_expiracion"


def _fila_a_sesion(fila: sqlite3.Row) -> Sesion:
    """Mapea una fila de la tabla `sesiones` a la entidad de dominio."""
    return Sesion(
        token=fila["token"],
        usuario_id=fila["usuario_id"],
        fecha_creacion=fila["fecha_creacion"],
        fecha_expiracion=fila["fecha_expiracion"],
    )


def crear_sesion(sesion: Sesion) -> Sesion:
    """Inserta una nueva sesión y devuelve la entidad persistida.

    Falla (`ErrorBaseDatos`, traducido desde la FK de SQLite) si
    `usuario_id` no corresponde a un usuario existente.
    """
    consulta = f"""
        INSERT INTO sesiones (token, usuario_id, fecha_expiracion)
        VALUES (?, ?, ?)
        RETURNING {_COLUMNAS}
    """
    parametros = (sesion.token, sesion.usuario_id, sesion.fecha_expiracion)
    with obtener_conexion() as conexion:
        fila = conexion.execute(consulta, parametros).fetchone()
    return _fila_a_sesion(fila)


def obtener_por_token(token: str) -> Sesion | None:
    """Busca una sesión por su token, o `None` si no existe."""
    consulta = f"SELECT {_COLUMNAS} FROM sesiones WHERE token = ?"
    with obtener_conexion() as conexion:
        fila = conexion.execute(consulta, (token,)).fetchone()
    return _fila_a_sesion(fila) if fila is not None else None


def eliminar_por_token(token: str) -> None:
    """Borra una sesión por su token. No falla si el token no existe
    (logout repetido, o limpieza de una sesión ya vencida, deben ser
    idempotentes: ver `services.servicio_auth`)."""
    with obtener_conexion() as conexion:
        conexion.execute("DELETE FROM sesiones WHERE token = ?", (token,))
