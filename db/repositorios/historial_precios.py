"""Repositorio del historial de cambios de precio (migración 015).

Solo SQL parametrizado. La decisión de qué se registra ("solo si el valor
realmente cambió") vive en `registrar_cambio_en_conexion`, que es el único
punto de escritura: así ningún camino que cambie un precio puede dejar una
fila sin cambio real.
"""

import sqlite3

from db.conexion import obtener_conexion
from domain.historial_precio import CambioPrecio


def registrar_cambio_en_conexion(
    conexion: sqlite3.Connection,
    producto_id: int,
    campo: str,
    precio_anterior_centavos: int,
    precio_nuevo_centavos: int,
    usuario_id: int | None,
    origen: str,
    lote_id: int | None = None,
) -> bool:
    """Registra un cambio de precio dentro de la transacción recibida.

    Devuelve `False` (sin insertar nada) si el precio nuevo es igual al
    anterior. No hace *commit*: forma parte de la transacción de quien
    cambió el precio, así el precio y su historial se confirman o se
    revierten juntos.
    """
    if precio_anterior_centavos == precio_nuevo_centavos:
        return False
    conexion.execute(
        """
        INSERT INTO historial_precios
            (producto_id, usuario_id, campo, precio_anterior_centavos, precio_nuevo_centavos, origen, lote_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (producto_id, usuario_id, campo, precio_anterior_centavos, precio_nuevo_centavos, origen, lote_id),
    )
    return True


def listar_por_producto(producto_id: int) -> list[CambioPrecio]:
    """Cambios de precio de un producto, el más reciente primero.

    `LEFT JOIN usuarios`: un cambio sin usuario (CLI) sigue apareciendo.
    No filtra por `usuarios.activo`: un usuario desactivado después
    sigue figurando con su nombre, igual criterio que ventas y ajustes.
    """
    consulta = """
        SELECT h.id, h.producto_id, h.fecha, h.campo, h.precio_anterior_centavos,
               h.precio_nuevo_centavos, h.origen, u.nombre_completo AS usuario_nombre_completo
        FROM historial_precios h
        LEFT JOIN usuarios u ON u.id = h.usuario_id
        WHERE h.producto_id = ?
        ORDER BY h.id DESC
    """
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta, (producto_id,)).fetchall()
    return [
        CambioPrecio(
            id=fila["id"],
            producto_id=fila["producto_id"],
            fecha=fila["fecha"],
            campo=fila["campo"],
            precio_anterior_centavos=fila["precio_anterior_centavos"],
            precio_nuevo_centavos=fila["precio_nuevo_centavos"],
            origen=fila["origen"],
            usuario_nombre_completo=fila["usuario_nombre_completo"],
        )
        for fila in filas
    ]


def listar_productos_del_lote_en_conexion(conexion: sqlite3.Connection, lote_id: int) -> set[int]:
    """Ids de los productos cuyo precio cambió en un lote de actualización masiva."""
    filas = conexion.execute(
        "SELECT DISTINCT producto_id FROM historial_precios WHERE lote_id = ?", (lote_id,)
    ).fetchall()
    return {fila["producto_id"] for fila in filas}
