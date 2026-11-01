"""Repositorio del registro de auditoría (migración 017). Solo SQL parametrizado."""

import sqlite3
from collections.abc import Sequence

from db.conexion import obtener_conexion
from domain.auditoria import EntradaAuditoria, normalizar


def registrar_en_conexion(
    conexion: sqlite3.Connection,
    usuario_id: int | None,
    accion: str,
    entidad: str,
    entidad_id: int | None,
    resumen: str,
) -> None:
    """Registra una operación auditada dentro de la transacción recibida: la
    operación y su rastro se confirman o se revierten juntos. No hace *commit*."""
    conexion.execute(
        "INSERT INTO auditoria (usuario_id, accion, entidad, entidad_id, resumen) VALUES (?, ?, ?, ?, ?)",
        (usuario_id, accion, entidad, entidad_id, normalizar(accion, resumen)),
    )


def listar(
    accion: str | None = None,
    usuario_id: int | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    limite: int = 200,
) -> list[EntradaAuditoria]:
    """Entradas de auditoría, la más reciente primero, con filtros opcionales.
    `fecha_desde`/`fecha_hasta` son "YYYY-MM-DD" inclusivos."""
    condiciones = ["1 = 1"]
    parametros: list[object] = []
    if accion:
        condiciones.append("a.accion = ?")
        parametros.append(accion)
    if usuario_id is not None:
        condiciones.append("a.usuario_id = ?")
        parametros.append(usuario_id)
    if fecha_desde:
        condiciones.append("date(a.fecha) >= date(?)")
        parametros.append(fecha_desde)
    if fecha_hasta:
        condiciones.append("date(a.fecha) <= date(?)")
        parametros.append(fecha_hasta)
    consulta = f"""
        SELECT a.id, a.fecha, a.usuario_id, u.nombre_completo AS usuario_nombre_completo,
               a.accion, a.entidad, a.entidad_id, a.resumen
        FROM auditoria a
        LEFT JOIN usuarios u ON u.id = a.usuario_id
        WHERE {" AND ".join(condiciones)}
        ORDER BY a.id DESC
        LIMIT ?
    """
    parametros.append(limite)
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta, parametros).fetchall()
    return [_entrada(f) for f in filas]


def registrar(usuario_id: int | None, accion: str, entidad: str, entidad_id: int | None, resumen: str) -> None:
    """Registra una operación auditada en su propia transacción: para eventos
    que no forman parte de ninguna escritura de negocio (ej. un backup)."""
    with obtener_conexion() as conexion:
        registrar_en_conexion(conexion, usuario_id, accion, entidad, entidad_id, resumen)


def ultima_de(acciones: Sequence[str]) -> EntradaAuditoria | None:
    """La entrada más reciente cuya acción esté en `acciones`, o `None`."""
    marcadores = ", ".join("?" for _ in acciones)
    consulta = f"""
        SELECT a.id, a.fecha, a.usuario_id, u.nombre_completo AS usuario_nombre_completo,
               a.accion, a.entidad, a.entidad_id, a.resumen
        FROM auditoria a
        LEFT JOIN usuarios u ON u.id = a.usuario_id
        WHERE a.accion IN ({marcadores})
        ORDER BY a.id DESC
        LIMIT 1
    """
    with obtener_conexion() as conexion:
        fila = conexion.execute(consulta, tuple(acciones)).fetchone()
    return _entrada(fila) if fila else None


def _entrada(f: sqlite3.Row) -> EntradaAuditoria:
    return EntradaAuditoria(
        id=f["id"],
        fecha=f["fecha"],
        usuario_id=f["usuario_id"],
        usuario_nombre_completo=f["usuario_nombre_completo"],
        accion=f["accion"],
        entidad=f["entidad"],
        entidad_id=f["entidad_id"],
        resumen=f["resumen"],
    )
