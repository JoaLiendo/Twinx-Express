"""Repositorio de la relación producto-proveedor (migración 021).

Solo SQL parametrizado. Las funciones `*_en_conexion` se componen dentro de la
transacción del servicio (compra, alta/baja de vínculo, cambio de principal), de modo
que el vínculo, la compra y la auditoría se confirman o se revierten juntos. El
esquema (UNIQUE, índice parcial de principal, FK `RESTRICT`) es la última defensa.
"""

import sqlite3

from db.conexion import obtener_conexion
from domain.producto_proveedor import ProductoDeProveedor, ProveedorPrincipal, VinculoProveedor

_COLUMNAS = "id, producto_id, proveedor_id, codigo_proveedor, es_principal, fecha_creacion"


def _fila_a_vinculo(fila: sqlite3.Row) -> VinculoProveedor:
    return VinculoProveedor(
        id=fila["id"],
        producto_id=fila["producto_id"],
        proveedor_id=fila["proveedor_id"],
        codigo_proveedor=fila["codigo_proveedor"],
        es_principal=bool(fila["es_principal"]),
        fecha_creacion=fila["fecha_creacion"],
    )


def asegurar_vinculo_en_conexion(conexion: sqlite3.Connection, producto_id: int, proveedor_id: int) -> None:
    """Crea el vínculo si no existe (lo usa el registro de una compra); si ya existe no hace nada.

    El vínculo nuevo queda como principal solo si el producto todavía no tiene uno: jamás
    reemplaza a un principal existente. Es una única sentencia, así que dentro de la
    transacción de la compra no hay carrera entre "no hay principal" y el alta.
    """
    conexion.execute(
        """
        INSERT INTO producto_proveedor (producto_id, proveedor_id, es_principal)
        SELECT ?, ?, NOT EXISTS (
            SELECT 1 FROM producto_proveedor WHERE producto_id = ? AND es_principal = 1
        )
        WHERE true
        ON CONFLICT (producto_id, proveedor_id) DO NOTHING
        """,
        (producto_id, proveedor_id, producto_id),
    )


def obtener_vinculo_en_conexion(
    conexion: sqlite3.Connection, producto_id: int, proveedor_id: int
) -> VinculoProveedor | None:
    fila = conexion.execute(
        f"SELECT {_COLUMNAS} FROM producto_proveedor WHERE producto_id = ? AND proveedor_id = ?",
        (producto_id, proveedor_id),
    ).fetchone()
    return _fila_a_vinculo(fila) if fila is not None else None


def crear_vinculo_en_conexion(
    conexion: sqlite3.Connection, producto_id: int, proveedor_id: int, codigo_proveedor: str | None
) -> VinculoProveedor:
    """Alta explícita del vínculo (el servicio ya verificó que no existe). Queda principal
    solo si el producto no tenía principal."""
    fila = conexion.execute(
        f"""
        INSERT INTO producto_proveedor (producto_id, proveedor_id, codigo_proveedor, es_principal)
        SELECT ?, ?, ?, NOT EXISTS (
            SELECT 1 FROM producto_proveedor WHERE producto_id = ? AND es_principal = 1
        )
        RETURNING {_COLUMNAS}
        """,
        (producto_id, proveedor_id, codigo_proveedor, producto_id),
    ).fetchone()
    return _fila_a_vinculo(fila)


def quitar_vinculo_en_conexion(conexion: sqlite3.Connection, producto_id: int, proveedor_id: int) -> bool:
    """Elimina el vínculo. Devuelve `False` si no existía. No promueve a otro principal."""
    cursor = conexion.execute(
        "DELETE FROM producto_proveedor WHERE producto_id = ? AND proveedor_id = ?",
        (producto_id, proveedor_id),
    )
    return cursor.rowcount > 0


def establecer_principal_en_conexion(conexion: sqlite3.Connection, producto_id: int, proveedor_id: int) -> None:
    """Deja como único principal del producto al proveedor indicado (el vínculo ya existe).

    Primero baja al principal anterior: el índice único parcial no admite dos a la vez.
    """
    conexion.execute(
        "UPDATE producto_proveedor SET es_principal = 0 WHERE producto_id = ? AND es_principal = 1",
        (producto_id,),
    )
    conexion.execute(
        "UPDATE producto_proveedor SET es_principal = 1 WHERE producto_id = ? AND proveedor_id = ?",
        (producto_id, proveedor_id),
    )


def listar_por_proveedor(proveedor_id: int) -> list[ProductoDeProveedor]:
    """Productos vinculados a un proveedor (activos e inactivos), por nombre."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            """
            SELECT pp.id, pp.producto_id, pp.proveedor_id, pp.codigo_proveedor, pp.es_principal,
                   pp.fecha_creacion,
                   p.codigo_barras AS producto_codigo_barras, p.nombre AS producto_nombre,
                   p.activo AS producto_activo
            FROM producto_proveedor pp
            JOIN productos p ON p.id = pp.producto_id
            WHERE pp.proveedor_id = ?
            ORDER BY p.nombre, p.id
            """,
            (proveedor_id,),
        ).fetchall()
    return [
        ProductoDeProveedor(
            vinculo=_fila_a_vinculo(fila),
            producto_codigo_barras=fila["producto_codigo_barras"],
            producto_nombre=fila["producto_nombre"],
            producto_activo=bool(fila["producto_activo"]),
        )
        for fila in filas
    ]


def listar_principales_con_costo() -> dict[int, ProveedorPrincipal]:
    """Proveedor principal de cada producto que lo tiene, con el último costo de compra a él.

    El último costo es el de la línea de mayor `detalle_compra.id` de ese producto con ese
    proveedor. Lo usa la reposición para sugerir a quién comprar y a qué costo.
    """
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            """
            SELECT pp.producto_id, pr.id AS proveedor_id, pr.nombre AS proveedor_nombre,
                   pr.activo AS proveedor_activo,
                   (SELECT d.costo_unitario_centavos
                      FROM detalle_compra d
                      JOIN compras c ON c.id = d.compra_id
                     WHERE d.producto_id = pp.producto_id AND c.proveedor_id = pp.proveedor_id
                     ORDER BY d.id DESC
                     LIMIT 1) AS ultimo_costo_centavos
            FROM producto_proveedor pp
            JOIN proveedores pr ON pr.id = pp.proveedor_id
            WHERE pp.es_principal = 1
            """
        ).fetchall()
    return {
        fila["producto_id"]: ProveedorPrincipal(
            producto_id=fila["producto_id"],
            proveedor_id=fila["proveedor_id"],
            proveedor_nombre=fila["proveedor_nombre"],
            proveedor_activo=bool(fila["proveedor_activo"]),
            ultimo_costo_centavos=fila["ultimo_costo_centavos"],
        )
        for fila in filas
    }
