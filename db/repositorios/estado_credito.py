"""Repositorio de episodios de crédito (cierres, strikes, reaperturas) y autorizaciones temporales
(migración 040, V1.14-E).

Solo SQL parametrizado. Las transiciones de un episodio son `UPDATE ... WHERE <columna> IS NULL`: dos
evaluaciones o dos reaperturas simultáneas (serializadas por el `BEGIN IMMEDIATE` del servicio) nunca
registran dos veces lo mismo, y los triggers rechazan cualquier otra modificación.
"""

import sqlite3
from datetime import date

from db.conexion import obtener_conexion
from domain.credito import AutorizacionCredito, EpisodioCredito

_SELECT_EPISODIO = """
    SELECT e.id, e.cliente_id, e.fecha_inicio, e.fecha_registro, e.fecha_strike, e.fecha_reapertura,
           u.nombre_completo AS reabierto_por, e.observacion_reapertura
    FROM episodios_credito e LEFT JOIN usuarios u ON u.id = e.reabierto_por_usuario_id
"""
_SELECT_AUTORIZACION = """
    SELECT a.id, a.cliente_id, a.compromiso, a.fecha_limite, u.nombre_completo AS usuario, a.fecha_creacion
    FROM autorizaciones_credito a JOIN usuarios u ON u.id = a.usuario_id
"""


def _fila_a_episodio(fila: sqlite3.Row) -> EpisodioCredito:
    return EpisodioCredito(
        id=fila["id"],
        cliente_id=fila["cliente_id"],
        fecha_inicio=fila["fecha_inicio"],
        fecha_registro=fila["fecha_registro"],
        fecha_strike=fila["fecha_strike"],
        fecha_reapertura=fila["fecha_reapertura"],
        reabierto_por=fila["reabierto_por"],
        observacion_reapertura=fila["observacion_reapertura"],
    )


def _fila_a_autorizacion(fila: sqlite3.Row) -> AutorizacionCredito:
    return AutorizacionCredito(
        id=fila["id"],
        cliente_id=fila["cliente_id"],
        compromiso=fila["compromiso"],
        fecha_limite=fila["fecha_limite"],
        usuario=fila["usuario"],
        fecha_creacion=fila["fecha_creacion"],
    )


def obtener_episodio_abierto_en_conexion(conexion: sqlite3.Connection, cliente_id: int) -> EpisodioCredito | None:
    fila = conexion.execute(
        f"{_SELECT_EPISODIO} WHERE e.cliente_id = ? AND e.fecha_reapertura IS NULL", (cliente_id,)
    ).fetchone()
    return _fila_a_episodio(fila) if fila is not None else None


def contar_strikes_en_conexion(conexion: sqlite3.Connection, cliente_id: int) -> int:
    return conexion.execute(
        "SELECT COUNT(*) FROM episodios_credito WHERE cliente_id = ? AND fecha_strike IS NOT NULL", (cliente_id,)
    ).fetchone()[0]


def abrir_episodio_en_conexion(
    conexion: sqlite3.Connection, cliente_id: int, obligacion_id: int, fecha_inicio: date
) -> None:
    """Abre el cierre del crédito. El índice único de episodios abiertos impide un segundo."""
    conexion.execute(
        "INSERT INTO episodios_credito (cliente_id, obligacion_id, fecha_inicio) VALUES (?, ?, ?)",
        (cliente_id, obligacion_id, fecha_inicio.isoformat()),
    )


def registrar_strike_en_conexion(conexion: sqlite3.Connection, episodio_id: int, fecha_strike: date) -> bool:
    """Registra el strike del episodio si todavía no lo tenía; `True` si lo registró ahora."""
    cursor = conexion.execute(
        """
        UPDATE episodios_credito SET fecha_strike = ?, strike_registrado_en = datetime('now', 'localtime')
        WHERE id = ? AND fecha_strike IS NULL AND fecha_reapertura IS NULL
        """,
        (fecha_strike.isoformat(), episodio_id),
    )
    return cursor.rowcount == 1


def reabrir_en_conexion(conexion: sqlite3.Connection, episodio_id: int, usuario_id: int, observacion: str) -> bool:
    """Cierra el episodio con la reapertura del OWNER; `True` si lo reabrió ahora (y no otro antes)."""
    cursor = conexion.execute(
        """
        UPDATE episodios_credito
        SET fecha_reapertura = datetime('now', 'localtime'), reabierto_por_usuario_id = ?, observacion_reapertura = ?
        WHERE id = ? AND fecha_reapertura IS NULL
        """,
        (usuario_id, observacion, episodio_id),
    )
    return cursor.rowcount == 1


def listar_episodios(cliente_id: int) -> list[EpisodioCredito]:
    """Historial de cierres del cliente, el más reciente primero."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(f"{_SELECT_EPISODIO} WHERE e.cliente_id = ? ORDER BY e.id DESC", (cliente_id,)).fetchall()
    return [_fila_a_episodio(fila) for fila in filas]


def obtener_ultima_autorizacion_en_conexion(conexion: sqlite3.Connection, cliente_id: int) -> AutorizacionCredito | None:
    fila = conexion.execute(
        f"{_SELECT_AUTORIZACION} WHERE a.cliente_id = ? ORDER BY a.id DESC LIMIT 1", (cliente_id,)
    ).fetchone()
    return _fila_a_autorizacion(fila) if fila is not None else None


def registrar_autorizacion_en_conexion(
    conexion: sqlite3.Connection, cliente_id: int, compromiso: str, fecha_limite: date, usuario_id: int
) -> AutorizacionCredito:
    nuevo_id = conexion.execute(
        "INSERT INTO autorizaciones_credito (cliente_id, compromiso, fecha_limite, usuario_id) VALUES (?, ?, ?, ?) RETURNING id",
        (cliente_id, compromiso, fecha_limite.isoformat(), usuario_id),
    ).fetchone()["id"]
    return _fila_a_autorizacion(conexion.execute(f"{_SELECT_AUTORIZACION} WHERE a.id = ?", (nuevo_id,)).fetchone())


def listar_autorizaciones(cliente_id: int) -> list[AutorizacionCredito]:
    """Autorizaciones temporales otorgadas al cliente, la más reciente primero."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(f"{_SELECT_AUTORIZACION} WHERE a.cliente_id = ? ORDER BY a.id DESC", (cliente_id,)).fetchall()
    return [_fila_a_autorizacion(fila) for fila in filas]
