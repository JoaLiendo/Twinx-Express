"""Repositorio de políticas de crédito por capas (migración 038, V1.14-B).

Solo SQL parametrizado. Una capa sin fila se devuelve como `HEREDAR` con `version` 0, así quien edita
siempre tiene una versión que comparar. Las escrituras corren dentro del `BEGIN IMMEDIATE` del servicio.
"""

import sqlite3

from db.conexion import obtener_conexion
from domain.credito import AMBITO_CLIENTE, AMBITOS_GLOBALES, MODO_HEREDAR, PoliticaCredito

_SELECT = """
    SELECT p.ambito, p.cliente_id, p.modo, p.limite_centavos, p.version, p.fecha_actualizacion,
           u.nombre_completo AS usuario
    FROM politicas_credito p JOIN usuarios u ON u.id = p.usuario_id
"""


def _fila_a_politica(fila: sqlite3.Row) -> PoliticaCredito:
    return PoliticaCredito(
        ambito=fila["ambito"],
        modo=fila["modo"],
        limite_centavos=fila["limite_centavos"],
        cliente_id=fila["cliente_id"],
        version=fila["version"],
        fecha_actualizacion=fila["fecha_actualizacion"],
        usuario=fila["usuario"],
    )


def obtener_en_conexion(conexion: sqlite3.Connection, ambito: str, cliente_id: int | None = None) -> PoliticaCredito:
    """La capa pedida; si no tiene fila, `HEREDAR` con versión 0."""
    fila = conexion.execute(
        f"{_SELECT} WHERE p.ambito = ? AND p.cliente_id IS ?", (ambito, cliente_id)
    ).fetchone()
    if fila is None:
        return PoliticaCredito(ambito=ambito, modo=MODO_HEREDAR, cliente_id=cliente_id)
    return _fila_a_politica(fila)


def listar_globales() -> list[PoliticaCredito]:
    """Las capas GENERAL, PERSONA y EMPRESA, en ese orden (las no configuradas como `HEREDAR`)."""
    with obtener_conexion() as conexion:
        return [obtener_en_conexion(conexion, ambito) for ambito in AMBITOS_GLOBALES]


def obtener_individual(cliente_id: int) -> PoliticaCredito:
    with obtener_conexion() as conexion:
        return obtener_en_conexion(conexion, AMBITO_CLIENTE, cliente_id)


def guardar_en_conexion(
    conexion: sqlite3.Connection,
    ambito: str,
    modo: str,
    limite_centavos: int | None,
    cliente_id: int | None,
    usuario_id: int,
) -> PoliticaCredito:
    """Crea la capa (versión 1) o la actualiza avanzando su versión en uno."""
    actualizada = conexion.execute(
        """
        UPDATE politicas_credito
        SET modo = ?, limite_centavos = ?, version = version + 1, usuario_id = ?,
            fecha_actualizacion = datetime('now', 'localtime')
        WHERE ambito = ? AND cliente_id IS ?
        """,
        (modo, limite_centavos, usuario_id, ambito, cliente_id),
    ).rowcount
    if actualizada == 0:
        conexion.execute(
            "INSERT INTO politicas_credito (ambito, cliente_id, modo, limite_centavos, usuario_id) VALUES (?, ?, ?, ?, ?)",
            (ambito, cliente_id, modo, limite_centavos, usuario_id),
        )
    return obtener_en_conexion(conexion, ambito, cliente_id)
