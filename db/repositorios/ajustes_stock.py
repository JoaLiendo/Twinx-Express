"""Repositorio de acceso a datos de ajustes manuales de stock.

Contiene únicamente consultas SQL parametrizadas sobre `ajustes_stock`.
No contiene reglas de negocio: la validación de motivo/delta/consistencia
de stock ocurre en `domain.ajuste_stock.AjusteStock`, y la orquestación
con el descuento/incremento real de `productos.stock_actual` en
`services.servicio_stock.ajustar_stock`.
"""

import sqlite3

from db.conexion import obtener_conexion
from domain.ajuste_stock import AjusteStock, AjusteStockConUsuario

_COLUMNAS = (
    "id, producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones, fecha, "
    "inventario_id, costo_unitario_centavos, reversa_de_id"
)


def _fila_a_ajuste(fila: sqlite3.Row) -> AjusteStock:
    return AjusteStock(
        id=fila["id"],
        producto_id=fila["producto_id"],
        usuario_id=fila["usuario_id"],
        motivo=fila["motivo"],
        delta=fila["delta"],
        stock_anterior=fila["stock_anterior"],
        stock_resultante=fila["stock_resultante"],
        observaciones=fila["observaciones"],
        fecha=fila["fecha"],
        inventario_id=fila["inventario_id"],
        costo_unitario_centavos=fila["costo_unitario_centavos"],
        reversa_de_id=fila["reversa_de_id"],
    )


def obtener_por_clave_idempotencia_en_conexion(
    conexion: sqlite3.Connection, clave_idempotencia: str
) -> AjusteStock | None:
    """Ajuste ya registrado con esa clave de idempotencia (migración 014), o `None`."""
    fila = conexion.execute(
        f"SELECT {_COLUMNAS} FROM ajustes_stock WHERE clave_idempotencia = ?", (clave_idempotencia,)
    ).fetchone()
    return _fila_a_ajuste(fila) if fila is not None else None


def obtener_por_id_en_conexion(conexion: sqlite3.Connection, ajuste_id: int) -> AjusteStock | None:
    """El ajuste con ese id, o `None` (ver `services.servicio_stock.revertir_ajuste`)."""
    fila = conexion.execute(f"SELECT {_COLUMNAS} FROM ajustes_stock WHERE id = ?", (ajuste_id,)).fetchone()
    return _fila_a_ajuste(fila) if fila is not None else None


def obtener_por_id(ajuste_id: int) -> AjusteStock | None:
    with obtener_conexion() as conexion:
        return obtener_por_id_en_conexion(conexion, ajuste_id)


def obtener_reversa_de_en_conexion(conexion: sqlite3.Connection, ajuste_id: int) -> AjusteStock | None:
    """La reversa de ese ajuste, si ya tiene una (a lo sumo una, ver migración 028), o `None`."""
    fila = conexion.execute(
        f"SELECT {_COLUMNAS} FROM ajustes_stock WHERE reversa_de_id = ?", (ajuste_id,)
    ).fetchone()
    return _fila_a_ajuste(fila) if fila is not None else None


def registrar_ajuste_en_conexion(
    conexion: sqlite3.Connection, ajuste: AjusteStock, clave_idempotencia: str | None = None
) -> AjusteStock:
    """Inserta un nuevo ajuste de stock usando la conexión recibida y
    devuelve la entidad persistida.

    No calcula nada (ni delta, ni stock anterior/resultante, ni motivo):
    persiste exactamente lo que ya viene resuelto y validado en
    `ajuste` -- esa resolución es responsabilidad de
    `services.servicio_stock.ajustar_stock`.

    No hace *commit* ni *rollback*: eso lo controla el
    `with obtener_conexion(inmediata=True)` de quien invoca esta función,
    para que el ajuste y el cambio real de `productos.stock_actual`
    queden dentro de una única transacción atómica.
    """
    consulta = f"""
        INSERT INTO ajustes_stock
            (producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones,
             clave_idempotencia, inventario_id, costo_unitario_centavos, reversa_de_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING {_COLUMNAS}
    """
    parametros = (
        ajuste.producto_id,
        ajuste.usuario_id,
        ajuste.motivo,
        ajuste.delta,
        ajuste.stock_anterior,
        ajuste.stock_resultante,
        ajuste.observaciones,
        clave_idempotencia,
        ajuste.inventario_id,
        ajuste.costo_unitario_centavos,
        ajuste.reversa_de_id,
    )
    fila = conexion.execute(consulta, parametros).fetchone()
    return _fila_a_ajuste(fila)


def listar_por_producto(producto_id: int) -> list[AjusteStockConUsuario]:
    """Historial de ajustes de un producto, más reciente primero, con el
    nombre completo del usuario ya resuelto en una sola consulta (sin
    N+1).

    `JOIN usuarios` (no `LEFT JOIN`): `ajustes_stock.usuario_id` es
    `NOT NULL` (a diferencia de `ventas`/`caja_movimientos`, este bloque
    no tiene ninguna ruta sin autenticar) -- no filtra por
    `usuarios.activo`: un usuario desactivado después de ajustar stock
    sigue apareciendo con su nombre, igual criterio que en ventas/caja.

    `LEFT JOIN ajustes_stock r` (V1.11-B): la reversa de esta fila, si ya tiene una (a lo sumo una,
    ver migración 028) -- permite mostrar "revertido por X el Y" sin una consulta aparte por fila.
    """
    consulta = """
        SELECT
            a.id, a.producto_id, a.usuario_id, u.nombre_completo AS usuario_nombre_completo,
            a.motivo, a.delta, a.stock_anterior, a.stock_resultante, a.observaciones, a.fecha,
            a.reversa_de_id, r.id AS reversa_id, r.fecha AS reversa_fecha,
            ru.nombre_completo AS reversa_usuario_nombre_completo
        FROM ajustes_stock a
        JOIN usuarios u ON u.id = a.usuario_id
        LEFT JOIN ajustes_stock r ON r.reversa_de_id = a.id
        LEFT JOIN usuarios ru ON ru.id = r.usuario_id
        WHERE a.producto_id = ?
        ORDER BY a.id DESC
    """
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta, (producto_id,)).fetchall()
    return [
        AjusteStockConUsuario(
            id=fila["id"],
            producto_id=fila["producto_id"],
            usuario_id=fila["usuario_id"],
            usuario_nombre_completo=fila["usuario_nombre_completo"],
            motivo=fila["motivo"],
            delta=fila["delta"],
            stock_anterior=fila["stock_anterior"],
            stock_resultante=fila["stock_resultante"],
            observaciones=fila["observaciones"],
            fecha=fila["fecha"],
            reversa_de_id=fila["reversa_de_id"],
            reversa_id=fila["reversa_id"],
            reversa_fecha=fila["reversa_fecha"],
            reversa_usuario_nombre_completo=fila["reversa_usuario_nombre_completo"],
        )
        for fila in filas
    ]
