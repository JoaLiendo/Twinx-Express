"""Acceso a datos de los pedidos de clientes (migración 051)."""

import sqlite3
from dataclasses import dataclass

from db.conexion import obtener_conexion
from domain.pedido import DatosPedido, LineaPedido, Pedido

_COLUMNAS = """
    p.id, p.fecha_creacion, p.usuario_id, u.nombre_completo AS usuario_nombre, p.cliente_id, c.nombre AS cliente_nombre,
    p.contacto_nombre, p.contacto_telefono, p.notas, p.fecha_entrega, p.estado, p.venta_id, p.convertido_en,
    p.cancelado_en, p.cancelado_por_usuario_id, a.nombre_completo AS cancelado_por_nombre, p.motivo_cancelacion,
    (SELECT COUNT(*) FROM pedido_lineas l WHERE l.pedido_id = p.id) AS cantidad_lineas,
    (SELECT COALESCE(SUM(l.cantidad * l.precio_unitario_centavos), 0) FROM pedido_lineas l WHERE l.pedido_id = p.id)
        AS total_al_pedir_centavos
"""
_DESDE = """
    FROM pedidos p
    JOIN usuarios u ON u.id = p.usuario_id
    LEFT JOIN clientes c ON c.id = p.cliente_id
    LEFT JOIN usuarios a ON a.id = p.cancelado_por_usuario_id
"""


def _fila_a_pedido(fila: sqlite3.Row) -> Pedido:
    return Pedido(**{clave: fila[clave] for clave in fila.keys()})


@dataclass(frozen=True)
class FiltrosPedidos:
    estado: str | None = None
    texto: str | None = None
    cliente_id: int | None = None


def _condiciones(filtros: FiltrosPedidos) -> tuple[str, list[object]]:
    condiciones, parametros = [], []
    if filtros.estado:
        condiciones.append("p.estado = ?")
        parametros.append(filtros.estado)
    if filtros.cliente_id is not None:
        condiciones.append("p.cliente_id = ?")
        parametros.append(filtros.cliente_id)
    if filtros.texto:
        patron = f"%{filtros.texto}%"
        condiciones.append("(c.nombre LIKE ? OR p.contacto_nombre LIKE ? OR p.notas LIKE ? OR CAST(p.id AS TEXT) = ?)")
        parametros.extend([patron, patron, patron, filtros.texto])
    return (" WHERE " + " AND ".join(condiciones)) if condiciones else "", parametros


def obtener_por_id_en_conexion(conexion: sqlite3.Connection, pedido_id: int) -> Pedido | None:
    fila = conexion.execute(f"SELECT {_COLUMNAS} {_DESDE} WHERE p.id = ?", (pedido_id,)).fetchone()
    return _fila_a_pedido(fila) if fila is not None else None


def obtener_por_id(pedido_id: int) -> Pedido | None:
    with obtener_conexion() as conexion:
        return obtener_por_id_en_conexion(conexion, pedido_id)


def obtener_por_clave_idempotencia_en_conexion(conexion: sqlite3.Connection, clave: str) -> Pedido | None:
    fila = conexion.execute(f"SELECT {_COLUMNAS} {_DESDE} WHERE p.clave_idempotencia = ?", (clave,)).fetchone()
    return _fila_a_pedido(fila) if fila is not None else None


def registrar_en_conexion(
    conexion: sqlite3.Connection,
    datos: DatosPedido,
    usuario_id: int,
    precios: dict[int, int],
    clave_idempotencia: str | None,
) -> Pedido:
    """Inserta el pedido y sus líneas (con el precio vigente de cada producto, ya resuelto por el servicio)."""
    pedido_id = conexion.execute(
        """
        INSERT INTO pedidos (usuario_id, cliente_id, contacto_nombre, contacto_telefono, notas, fecha_entrega, clave_idempotencia)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            usuario_id,
            datos.cliente_id,
            datos.contacto_nombre,
            datos.contacto_telefono,
            datos.notas,
            datos.fecha_entrega,
            clave_idempotencia,
        ),
    ).lastrowid
    conexion.executemany(
        "INSERT INTO pedido_lineas (pedido_id, producto_id, cantidad, precio_unitario_centavos) VALUES (?, ?, ?, ?)",
        [(pedido_id, item.producto_id, item.cantidad, precios[item.producto_id]) for item in datos.items],
    )
    pedido = obtener_por_id_en_conexion(conexion, pedido_id)
    assert pedido is not None
    return pedido


def listar_lineas_en_conexion(conexion: sqlite3.Connection, pedido_id: int) -> list[LineaPedido]:
    filas = conexion.execute(
        """
        SELECT l.id, l.producto_id, pr.nombre AS producto_nombre, pr.activo AS producto_activo, l.cantidad,
               l.precio_unitario_centavos, pr.precio_venta_centavos AS precio_vigente_centavos, pr.stock_actual
        FROM pedido_lineas l JOIN productos pr ON pr.id = l.producto_id
        WHERE l.pedido_id = ? ORDER BY l.id
        """,
        (pedido_id,),
    ).fetchall()
    return [
        LineaPedido(
            id=f["id"],
            producto_id=f["producto_id"],
            producto_nombre=f["producto_nombre"],
            producto_activo=bool(f["producto_activo"]),
            cantidad=f["cantidad"],
            precio_unitario_centavos=f["precio_unitario_centavos"],
            precio_vigente_centavos=f["precio_vigente_centavos"],
            stock_actual=f["stock_actual"],
        )
        for f in filas
    ]


def listar_lineas(pedido_id: int) -> list[LineaPedido]:
    with obtener_conexion() as conexion:
        return listar_lineas_en_conexion(conexion, pedido_id)


def marcar_convertido_en_conexion(conexion: sqlite3.Connection, pedido_id: int, venta_id: int) -> bool:
    """PENDIENTE → CONVERTIDO con su venta. `False` si el pedido ya no estaba pendiente."""
    cursor = conexion.execute(
        "UPDATE pedidos SET estado = 'CONVERTIDO', venta_id = ?, convertido_en = datetime('now', 'localtime')"
        " WHERE id = ? AND estado = 'PENDIENTE'",
        (venta_id, pedido_id),
    )
    return cursor.rowcount == 1


def cancelar_en_conexion(conexion: sqlite3.Connection, pedido_id: int, usuario_id: int, motivo: str) -> bool:
    cursor = conexion.execute(
        "UPDATE pedidos SET estado = 'CANCELADO', cancelado_en = datetime('now', 'localtime'),"
        " cancelado_por_usuario_id = ?, motivo_cancelacion = ? WHERE id = ? AND estado = 'PENDIENTE'",
        (usuario_id, motivo, pedido_id),
    )
    return cursor.rowcount == 1


def listar(filtros: FiltrosPedidos, limite: int, desplazamiento: int) -> list[Pedido]:
    condiciones, parametros = _condiciones(filtros)
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            f"""SELECT {_COLUMNAS} {_DESDE}{condiciones}
                ORDER BY CASE p.estado WHEN 'PENDIENTE' THEN 0 ELSE 1 END, p.id DESC LIMIT ? OFFSET ?""",
            [*parametros, limite, desplazamiento],
        ).fetchall()
    return [_fila_a_pedido(fila) for fila in filas]


def contar(filtros: FiltrosPedidos) -> int:
    condiciones, parametros = _condiciones(filtros)
    with obtener_conexion() as conexion:
        return conexion.execute(f"SELECT COUNT(*) {_DESDE}{condiciones}", parametros).fetchone()[0]


def contar_pendientes() -> int:
    with obtener_conexion() as conexion:
        return conexion.execute("SELECT COUNT(*) FROM pedidos WHERE estado = 'PENDIENTE'").fetchone()[0]


def listar_de_cliente(cliente_id: int, limite: int = 20) -> list[Pedido]:
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            f"SELECT {_COLUMNAS} {_DESDE} WHERE p.cliente_id = ? ORDER BY p.id DESC LIMIT ?", (cliente_id, limite)
        ).fetchall()
    return [_fila_a_pedido(fila) for fila in filas]
