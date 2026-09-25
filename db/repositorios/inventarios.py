"""Repositorio del inventario físico (migración 022).

Solo SQL parametrizado. Todas las escrituras son `*_en_conexion`: se componen dentro de la
transacción `BEGIN IMMEDIATE` del servicio, de modo que conteo, ajustes RECUENTO, auditoría y
cierre se confirman o se revierten juntos. Las reglas las decide el servicio; los triggers del
esquema impiden estados imposibles por acceso directo.

`productos.version_stock` se lee acá con consultas explícitas: no forma parte de las columnas
de `db.repositorios.productos` ni de sus `UPDATE ... RETURNING`.
"""

import sqlite3
from dataclasses import dataclass

from db.conexion import obtener_conexion
from domain.inventario import (
    DetalleInventario,
    Inventario,
    LineaConteo,
    LineaInventario,
    ResumenInventario,
)
from excepciones import InventarioAbiertoExistenteError

_COLUMNAS = "id, estado, usuario_id, fecha_inicio, fecha_cierre, usuario_cierre_id, observaciones"


def _fila_a_inventario(fila: sqlite3.Row) -> Inventario:
    return Inventario(
        id=fila["id"],
        estado=fila["estado"],
        usuario_id=fila["usuario_id"],
        fecha_inicio=fila["fecha_inicio"],
        fecha_cierre=fila["fecha_cierre"],
        usuario_cierre_id=fila["usuario_cierre_id"],
        observaciones=fila["observaciones"],
    )


@dataclass(frozen=True)
class LineaContada:
    """Una línea contada con el estado ACTUAL de su producto, para validar al confirmar."""

    linea_id: int
    producto_id: int
    producto_nombre: str
    stock_esperado: int
    version_esperada: int
    cantidad_contada: int
    stock_actual: int
    version_actual: int

    @property
    def desactualizada(self) -> bool:
        return self.stock_actual != self.stock_esperado or self.version_actual != self.version_esperada


@dataclass(frozen=True)
class EstadoProducto:
    stock_actual: int
    version_stock: int
    precio_costo_centavos: int


# --- escrituras -----------------------------------------------------------------------------------


def crear_inventario_en_conexion(
    conexion: sqlite3.Connection, usuario_id: int, observaciones: str | None
) -> Inventario:
    """Abre un inventario. El índice único parcial garantiza un solo ABIERTO a la vez."""
    try:
        fila = conexion.execute(
            f"INSERT INTO inventarios (usuario_id, observaciones) VALUES (?, ?) RETURNING {_COLUMNAS}",
            (usuario_id, observaciones),
        ).fetchone()
    except sqlite3.IntegrityError as error:
        raise InventarioAbiertoExistenteError(
            "Ya hay un inventario abierto: confirmalo o cancelalo antes de iniciar otro."
        ) from error
    return _fila_a_inventario(fila)


def agregar_lineas_en_conexion(conexion: sqlite3.Connection, inventario_id: int, producto_ids: list[int]) -> None:
    conexion.executemany(
        "INSERT INTO inventario_lineas (inventario_id, producto_id) VALUES (?, ?)",
        [(inventario_id, producto_id) for producto_id in producto_ids],
    )


def registrar_conteo_en_conexion(
    conexion: sqlite3.Connection,
    inventario_id: int,
    producto_id: int,
    stock_esperado: int,
    version_esperada: int,
    cantidad_contada: int,
    costo_unitario_centavos: int,
    usuario_id: int,
) -> bool:
    """Guarda el conteo de una línea (esperado, versión, contado, costo, quién y cuándo, todo junto).

    Volver a contar reemplaza el conteo anterior. Devuelve `False` si el producto no es una línea
    del inventario.
    """
    cursor = conexion.execute(
        """
        UPDATE inventario_lineas
        SET stock_esperado = ?, version_esperada = ?, cantidad_contada = ?, costo_unitario_centavos = ?,
            usuario_conteo_id = ?, fecha_conteo = datetime('now', 'localtime')
        WHERE inventario_id = ? AND producto_id = ?
        """,
        (
            stock_esperado,
            version_esperada,
            cantidad_contada,
            costo_unitario_centavos,
            usuario_id,
            inventario_id,
            producto_id,
        ),
    )
    return cursor.rowcount > 0


def asignar_ajuste_en_conexion(conexion: sqlite3.Connection, linea_id: int, ajuste_id: int) -> None:
    conexion.execute("UPDATE inventario_lineas SET ajuste_id = ? WHERE id = ?", (ajuste_id, linea_id))


def cerrar_inventario_en_conexion(
    conexion: sqlite3.Connection,
    inventario_id: int,
    estado: str,
    usuario_cierre_id: int,
    clave_idempotencia: str | None = None,
) -> Inventario:
    """Pasa un inventario ABIERTO a CONFIRMADO o CANCELADO. Los triggers validan la transición."""
    fila = conexion.execute(
        f"""
        UPDATE inventarios
        SET estado = ?, usuario_cierre_id = ?, fecha_cierre = datetime('now', 'localtime'),
            clave_idempotencia = ?
        WHERE id = ?
        RETURNING {_COLUMNAS}
        """,
        (estado, usuario_cierre_id, clave_idempotencia, inventario_id),
    ).fetchone()
    return _fila_a_inventario(fila)


# --- lecturas dentro de la transacción --------------------------------------------------------------


def obtener_inventario_en_conexion(conexion: sqlite3.Connection, inventario_id: int) -> Inventario | None:
    fila = conexion.execute(f"SELECT {_COLUMNAS} FROM inventarios WHERE id = ?", (inventario_id,)).fetchone()
    return _fila_a_inventario(fila) if fila is not None else None


def obtener_por_clave_idempotencia_en_conexion(conexion: sqlite3.Connection, clave: str) -> Inventario | None:
    fila = conexion.execute(
        f"SELECT {_COLUMNAS} FROM inventarios WHERE clave_idempotencia = ?", (clave,)
    ).fetchone()
    return _fila_a_inventario(fila) if fila is not None else None


def obtener_estado_producto_en_conexion(conexion: sqlite3.Connection, producto_id: int) -> EstadoProducto | None:
    """Stock, versión y costo vigentes de un producto (activo o inactivo), leídos dentro de la transacción."""
    fila = conexion.execute(
        "SELECT stock_actual, version_stock, precio_costo_centavos FROM productos WHERE id = ?", (producto_id,)
    ).fetchone()
    if fila is None:
        return None
    return EstadoProducto(fila["stock_actual"], fila["version_stock"], fila["precio_costo_centavos"])


def listar_ids_productos_activos_en_conexion(conexion: sqlite3.Connection) -> list[int]:
    filas = conexion.execute("SELECT id FROM productos WHERE activo = 1 ORDER BY id").fetchall()
    return [fila["id"] for fila in filas]


def listar_lineas_contadas_en_conexion(conexion: sqlite3.Connection, inventario_id: int) -> list[LineaContada]:
    """Líneas contadas con el stock y la versión ACTUALES de su producto (mismo `BEGIN IMMEDIATE`)."""
    filas = conexion.execute(
        """
        SELECT l.id AS linea_id, l.producto_id, p.nombre AS producto_nombre,
               l.stock_esperado, l.version_esperada, l.cantidad_contada,
               p.stock_actual, p.version_stock
        FROM inventario_lineas l
        JOIN productos p ON p.id = l.producto_id
        WHERE l.inventario_id = ? AND l.cantidad_contada IS NOT NULL
        ORDER BY p.nombre, l.producto_id
        """,
        (inventario_id,),
    ).fetchall()
    return [
        LineaContada(
            linea_id=fila["linea_id"],
            producto_id=fila["producto_id"],
            producto_nombre=fila["producto_nombre"],
            stock_esperado=fila["stock_esperado"],
            version_esperada=fila["version_esperada"],
            cantidad_contada=fila["cantidad_contada"],
            stock_actual=fila["stock_actual"],
            version_actual=fila["version_stock"],
        )
        for fila in filas
    ]


def contar_lineas_en_conexion(conexion: sqlite3.Connection, inventario_id: int) -> tuple[int, int, int]:
    """`(total, contadas, con ajuste)` de las líneas de un inventario."""
    fila = conexion.execute(
        """
        SELECT COUNT(*) AS total, COUNT(cantidad_contada) AS contadas, COUNT(ajuste_id) AS con_ajuste
        FROM inventario_lineas WHERE inventario_id = ?
        """,
        (inventario_id,),
    ).fetchone()
    return fila["total"], fila["contadas"], fila["con_ajuste"]


# --- lecturas independientes --------------------------------------------------------------------------


def obtener_inventario(inventario_id: int) -> Inventario | None:
    with obtener_conexion() as conexion:
        return obtener_inventario_en_conexion(conexion, inventario_id)


def obtener_abierto() -> Inventario | None:
    with obtener_conexion() as conexion:
        fila = conexion.execute(f"SELECT {_COLUMNAS} FROM inventarios WHERE estado = 'ABIERTO'").fetchone()
    return _fila_a_inventario(fila) if fila is not None else None


def listar_resumen() -> list[ResumenInventario]:
    """Historial de inventarios, el más reciente primero, con quién lo inició y su avance."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            f"""
            SELECT i.id, i.estado, i.usuario_id, i.fecha_inicio, i.fecha_cierre, i.usuario_cierre_id,
                   i.observaciones, u.nombre_completo AS usuario_nombre_completo,
                   COUNT(l.id) AS cantidad_lineas, COUNT(l.cantidad_contada) AS cantidad_contadas,
                   COUNT(l.ajuste_id) AS cantidad_ajustes
            FROM inventarios i
            JOIN usuarios u ON u.id = i.usuario_id
            LEFT JOIN inventario_lineas l ON l.inventario_id = i.id
            GROUP BY i.id
            ORDER BY i.id DESC
            """
        ).fetchall()
    return [
        ResumenInventario(
            inventario=_fila_a_inventario(fila),
            usuario_nombre_completo=fila["usuario_nombre_completo"],
            cantidad_lineas=fila["cantidad_lineas"],
            cantidad_contadas=fila["cantidad_contadas"],
            cantidad_ajustes=fila["cantidad_ajustes"],
        )
        for fila in filas
    ]


def listar_lineas_para_conteo(inventario_id: int) -> list[LineaConteo]:
    """Líneas para quien cuenta. La consulta ni siquiera selecciona `stock_esperado` (conteo a ciegas)."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            """
            SELECT l.producto_id, p.nombre AS producto_nombre, p.codigo_barras AS producto_codigo_barras,
                   p.activo AS producto_activo, l.cantidad_contada,
                   u.nombre_completo AS usuario_conteo_nombre, l.fecha_conteo
            FROM inventario_lineas l
            JOIN productos p ON p.id = l.producto_id
            LEFT JOIN usuarios u ON u.id = l.usuario_conteo_id
            WHERE l.inventario_id = ?
            ORDER BY p.nombre, l.producto_id
            """,
            (inventario_id,),
        ).fetchall()
    return [
        LineaConteo(
            producto_id=fila["producto_id"],
            producto_nombre=fila["producto_nombre"],
            producto_codigo_barras=fila["producto_codigo_barras"],
            producto_activo=bool(fila["producto_activo"]),
            cantidad_contada=fila["cantidad_contada"],
            usuario_conteo_nombre=fila["usuario_conteo_nombre"],
            fecha_conteo=fila["fecha_conteo"],
        )
        for fila in filas
    ]


def obtener_detalle(inventario_id: int) -> DetalleInventario | None:
    """Inventario con todas sus líneas (esperado, contado, costo, ajuste). Solo para OWNER."""
    with obtener_conexion() as conexion:
        cabecera = conexion.execute(
            f"""
            SELECT i.id, i.estado, i.usuario_id, i.fecha_inicio, i.fecha_cierre, i.usuario_cierre_id,
                   i.observaciones, u.nombre_completo AS usuario_nombre_completo
            FROM inventarios i JOIN usuarios u ON u.id = i.usuario_id
            WHERE i.id = ?
            """,
            (inventario_id,),
        ).fetchone()
        if cabecera is None:
            return None
        filas = conexion.execute(
            """
            SELECT l.producto_id, p.nombre AS producto_nombre, p.codigo_barras AS producto_codigo_barras,
                   p.activo AS producto_activo, l.stock_esperado, l.version_esperada, l.cantidad_contada,
                   l.costo_unitario_centavos, l.ajuste_id, u.nombre_completo AS usuario_conteo_nombre,
                   l.fecha_conteo, p.stock_actual, p.version_stock
            FROM inventario_lineas l
            JOIN productos p ON p.id = l.producto_id
            LEFT JOIN usuarios u ON u.id = l.usuario_conteo_id
            WHERE l.inventario_id = ?
            ORDER BY p.nombre, l.producto_id
            """,
            (inventario_id,),
        ).fetchall()
    abierto = cabecera["estado"] == "ABIERTO"
    lineas = [
        LineaInventario(
            producto_id=fila["producto_id"],
            producto_nombre=fila["producto_nombre"],
            producto_codigo_barras=fila["producto_codigo_barras"],
            producto_activo=bool(fila["producto_activo"]),
            stock_esperado=fila["stock_esperado"],
            cantidad_contada=fila["cantidad_contada"],
            costo_unitario_centavos=fila["costo_unitario_centavos"],
            ajuste_id=fila["ajuste_id"],
            usuario_conteo_nombre=fila["usuario_conteo_nombre"],
            fecha_conteo=fila["fecha_conteo"],
            desactualizada=abierto
            and fila["cantidad_contada"] is not None
            and (
                fila["stock_actual"] != fila["stock_esperado"] or fila["version_stock"] != fila["version_esperada"]
            ),
        )
        for fila in filas
    ]
    return DetalleInventario(
        inventario=_fila_a_inventario(cabecera),
        usuario_nombre_completo=cabecera["usuario_nombre_completo"],
        lineas=lineas,
    )
