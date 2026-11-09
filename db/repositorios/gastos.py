"""Acceso a datos de los gastos operativos (migración 048)."""

import sqlite3
from dataclasses import dataclass

from db.conexion import obtener_conexion
from domain.gasto import CambiosGasto, DatosGasto, Gasto

_COLUMNAS = """
    g.id, g.fecha, g.categoria, g.descripcion, g.importe_centavos, g.medio_pago, g.referencia, g.responsable,
    g.observaciones, g.usuario_id, u.nombre_completo AS usuario_nombre, g.creado_en, g.modificado_en, g.estado,
    g.anulado_en, g.anulado_por_usuario_id, a.nombre_completo AS anulado_por_nombre, g.motivo_anulacion,
    g.caja_movimiento_id, g.caja_movimiento_reintegro_id, g.clave_idempotencia
"""
_DESDE = """
    FROM gastos g
    JOIN usuarios u ON u.id = g.usuario_id
    LEFT JOIN usuarios a ON a.id = g.anulado_por_usuario_id
"""


def _fila_a_gasto(fila: sqlite3.Row) -> Gasto:
    return Gasto(**{clave: fila[clave] for clave in fila.keys()})


@dataclass(frozen=True)
class FiltrosGastos:
    """Filtros del listado; `inicio`/`fin_exclusivo` son fechas `AAAA-MM-DD` (o `None`)."""

    inicio: str | None = None
    fin_exclusivo: str | None = None
    categoria: str | None = None
    medio_pago: str | None = None
    estado: str | None = None
    texto: str | None = None


@dataclass(frozen=True)
class TotalCategoria:
    categoria: str
    cantidad: int
    importe_centavos: int


def _condiciones(filtros: FiltrosGastos) -> tuple[str, list[object]]:
    condiciones, parametros = [], []
    if filtros.inicio:
        condiciones.append("g.fecha >= ?")
        parametros.append(filtros.inicio)
    if filtros.fin_exclusivo:
        condiciones.append("g.fecha < ?")
        parametros.append(filtros.fin_exclusivo)
    if filtros.categoria:
        condiciones.append("g.categoria = ?")
        parametros.append(filtros.categoria)
    if filtros.medio_pago:
        condiciones.append("g.medio_pago = ?")
        parametros.append(filtros.medio_pago)
    if filtros.estado:
        condiciones.append("g.estado = ?")
        parametros.append(filtros.estado)
    if filtros.texto:
        patron = f"%{filtros.texto}%"
        condiciones.append(
            "(g.descripcion LIKE ? OR g.referencia LIKE ? OR g.responsable LIKE ? OR g.observaciones LIKE ?)"
        )
        parametros.extend([patron, patron, patron, patron])
    return (" WHERE " + " AND ".join(condiciones)) if condiciones else "", parametros


def obtener_por_clave_idempotencia_en_conexion(conexion: sqlite3.Connection, clave: str) -> Gasto | None:
    fila = conexion.execute(f"SELECT {_COLUMNAS} {_DESDE} WHERE g.clave_idempotencia = ?", (clave,)).fetchone()
    return _fila_a_gasto(fila) if fila is not None else None


def obtener_por_id_en_conexion(conexion: sqlite3.Connection, gasto_id: int) -> Gasto | None:
    fila = conexion.execute(f"SELECT {_COLUMNAS} {_DESDE} WHERE g.id = ?", (gasto_id,)).fetchone()
    return _fila_a_gasto(fila) if fila is not None else None


def obtener_por_id(gasto_id: int) -> Gasto | None:
    with obtener_conexion() as conexion:
        return obtener_por_id_en_conexion(conexion, gasto_id)


def registrar_en_conexion(
    conexion: sqlite3.Connection,
    datos: DatosGasto,
    usuario_id: int,
    caja_movimiento_id: int | None,
    clave_idempotencia: str | None,
) -> Gasto:
    """Inserta el gasto dentro de la transacción recibida (abierta con `BEGIN IMMEDIATE` por el servicio,
    que ya creó el egreso de caja si corresponde). Los triggers de la 048 validan la coherencia."""
    gasto_id = conexion.execute(
        """
        INSERT INTO gastos
            (fecha, categoria, descripcion, importe_centavos, medio_pago, referencia, responsable, observaciones,
             usuario_id, caja_movimiento_id, clave_idempotencia)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datos.fecha,
            datos.categoria,
            datos.descripcion,
            datos.importe_centavos,
            datos.medio_pago,
            datos.referencia,
            datos.responsable,
            datos.observaciones,
            usuario_id,
            caja_movimiento_id,
            clave_idempotencia,
        ),
    ).lastrowid
    gasto = obtener_por_id_en_conexion(conexion, gasto_id)
    assert gasto is not None
    return gasto


def modificar_en_conexion(conexion: sqlite3.Connection, gasto_id: int, cambios: CambiosGasto) -> None:
    conexion.execute(
        """
        UPDATE gastos
        SET categoria = ?, descripcion = ?, referencia = ?, responsable = ?, observaciones = ?,
            modificado_en = datetime('now', 'localtime')
        WHERE id = ? AND estado = 'VIGENTE'
        """,
        (cambios.categoria, cambios.descripcion, cambios.referencia, cambios.responsable, cambios.observaciones, gasto_id),
    )


def anular_en_conexion(
    conexion: sqlite3.Connection, gasto_id: int, usuario_id: int, motivo: str, caja_movimiento_reintegro_id: int | None
) -> None:
    conexion.execute(
        """
        UPDATE gastos
        SET estado = 'ANULADO', anulado_en = datetime('now', 'localtime'), anulado_por_usuario_id = ?,
            motivo_anulacion = ?, caja_movimiento_reintegro_id = ?
        WHERE id = ? AND estado = 'VIGENTE'
        """,
        (usuario_id, motivo, caja_movimiento_reintegro_id, gasto_id),
    )


def listar(filtros: FiltrosGastos, limite: int, desplazamiento: int) -> list[Gasto]:
    condiciones, parametros = _condiciones(filtros)
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            f"SELECT {_COLUMNAS} {_DESDE}{condiciones} ORDER BY g.fecha DESC, g.id DESC LIMIT ? OFFSET ?",
            [*parametros, limite, desplazamiento],
        ).fetchall()
    return [_fila_a_gasto(fila) for fila in filas]


def listar_todos(filtros: FiltrosGastos) -> list[Gasto]:
    """Sin paginar, para la exportación CSV (mismos filtros que el listado)."""
    condiciones, parametros = _condiciones(filtros)
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            f"SELECT {_COLUMNAS} {_DESDE}{condiciones} ORDER BY g.fecha DESC, g.id DESC", parametros
        ).fetchall()
    return [_fila_a_gasto(fila) for fila in filas]


def contar(filtros: FiltrosGastos) -> tuple[int, int]:
    """`(cantidad, importe total de los vigentes)` de los gastos que cumplen los filtros."""
    condiciones, parametros = _condiciones(filtros)
    with obtener_conexion() as conexion:
        fila = conexion.execute(
            f"""
            SELECT COUNT(*) AS cantidad,
                   COALESCE(SUM(CASE WHEN g.estado = 'VIGENTE' THEN g.importe_centavos END), 0) AS total
            {_DESDE}{condiciones}
            """,
            parametros,
        ).fetchone()
    return fila["cantidad"], fila["total"]


def totales_por_categoria(inicio: str, fin_exclusivo: str) -> list[TotalCategoria]:
    """Gastos vigentes del período por categoría, de mayor a menor importe."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            """
            SELECT categoria, COUNT(*) AS cantidad, SUM(importe_centavos) AS importe
            FROM gastos WHERE estado = 'VIGENTE' AND fecha >= ? AND fecha < ?
            GROUP BY categoria ORDER BY importe DESC, categoria
            """,
            (inicio, fin_exclusivo),
        ).fetchall()
    return [TotalCategoria(fila["categoria"], fila["cantidad"], fila["importe"]) for fila in filas]


def totales_por_dia(inicio: str, fin_exclusivo: str) -> list[tuple[str, int]]:
    """`(fecha, importe)` de los gastos vigentes por día, en orden cronológico."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            """
            SELECT fecha, SUM(importe_centavos) AS importe
            FROM gastos WHERE estado = 'VIGENTE' AND fecha >= ? AND fecha < ?
            GROUP BY fecha ORDER BY fecha
            """,
            (inicio, fin_exclusivo),
        ).fetchall()
    return [(fila["fecha"], fila["importe"]) for fila in filas]


def total_vigente(inicio: str, fin_exclusivo: str) -> int:
    with obtener_conexion() as conexion:
        fila = conexion.execute(
            "SELECT COALESCE(SUM(importe_centavos), 0) FROM gastos WHERE estado = 'VIGENTE' AND fecha >= ? AND fecha < ?",
            (inicio, fin_exclusivo),
        ).fetchone()
    return fila[0]
