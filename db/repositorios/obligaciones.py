"""Repositorio de obligaciones de clientes, imputaciones de cobros y compradores de ventas (migración 039).

Solo SQL parametrizado. Las obligaciones las crea un trigger al insertar cada CARGO; acá solo se leen.
Las imputaciones se insertan dentro del `BEGIN IMMEDIATE` del cobro, y un trigger descuenta el saldo de
la obligación: ninguna función de este módulo escribe un saldo a mano.
"""

import sqlite3

from db.conexion import obtener_conexion
from domain.cliente import Imputacion, Obligacion
from domain.venta import CompradorRegistrado, CompradorVenta

_COLUMNAS_OBLIGACION = (
    "id, cliente_id, venta_id, monto_original_centavos, saldo_pendiente_centavos, fecha_origen"
)


def _fila_a_obligacion(fila: sqlite3.Row) -> Obligacion:
    return Obligacion(
        id=fila["id"],
        cliente_id=fila["cliente_id"],
        venta_id=fila["venta_id"],
        monto_original_centavos=fila["monto_original_centavos"],
        saldo_pendiente_centavos=fila["saldo_pendiente_centavos"],
        fecha_origen=fila["fecha_origen"],
    )


def listar_pendientes_en_conexion(conexion: sqlite3.Connection, cliente_id: int) -> list[Obligacion]:
    """Obligaciones con saldo del cliente, la más antigua primero (orden del libro): el orden FIFO."""
    filas = conexion.execute(
        f"""
        SELECT {_COLUMNAS_OBLIGACION} FROM obligaciones_cliente
        WHERE cliente_id = ? AND saldo_pendiente_centavos > 0
        ORDER BY movimiento_cargo_id
        """,
        (cliente_id,),
    ).fetchall()
    return [_fila_a_obligacion(fila) for fila in filas]


def listar_por_cliente_en_conexion(conexion: sqlite3.Connection, cliente_id: int) -> list[Obligacion]:
    """Todas las obligaciones del cliente (pendientes y saldadas), la más reciente primero."""
    filas = conexion.execute(
        f"SELECT {_COLUMNAS_OBLIGACION} FROM obligaciones_cliente WHERE cliente_id = ? ORDER BY movimiento_cargo_id DESC",
        (cliente_id,),
    ).fetchall()
    return [_fila_a_obligacion(fila) for fila in filas]


def obtener_por_venta(venta_id: int) -> Obligacion | None:
    with obtener_conexion() as conexion:
        fila = conexion.execute(
            f"SELECT {_COLUMNAS_OBLIGACION} FROM obligaciones_cliente WHERE venta_id = ?", (venta_id,)
        ).fetchone()
    return _fila_a_obligacion(fila) if fila is not None else None


def registrar_imputacion_en_conexion(
    conexion: sqlite3.Connection,
    movimiento_cobro_id: int,
    obligacion_id: int,
    monto_centavos: int,
    tipo: str,
    usuario_id: int,
    observacion: str | None = None,
) -> None:
    """Aplica parte de un cobro a una obligación. Los triggers exigen mismo cliente, obligación anterior al
    cobro, monto dentro del saldo y del cobro, y descuentan el saldo."""
    conexion.execute(
        """
        INSERT INTO imputaciones_cobro (movimiento_cobro_id, obligacion_id, monto_centavos, tipo, observacion, usuario_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (movimiento_cobro_id, obligacion_id, monto_centavos, tipo, observacion, usuario_id),
    )


def listar_imputaciones_de_cliente_en_conexion(conexion: sqlite3.Connection, cliente_id: int) -> list[Imputacion]:
    """Imputaciones de todos los cobros del cliente, la más reciente primero."""
    filas = conexion.execute(
        """
        SELECT i.id, i.movimiento_cobro_id, i.obligacion_id, o.venta_id, i.monto_centavos, i.tipo, i.observacion,
               u.nombre_completo AS usuario, i.fecha
        FROM imputaciones_cobro i
        JOIN obligaciones_cliente o ON o.id = i.obligacion_id
        LEFT JOIN usuarios u ON u.id = i.usuario_id
        WHERE o.cliente_id = ?
        ORDER BY i.movimiento_cobro_id DESC, o.movimiento_cargo_id
        """,
        (cliente_id,),
    ).fetchall()
    return [
        Imputacion(
            id=fila["id"],
            movimiento_cobro_id=fila["movimiento_cobro_id"],
            obligacion_id=fila["obligacion_id"],
            venta_id=fila["venta_id"],
            monto_centavos=fila["monto_centavos"],
            tipo=fila["tipo"],
            observacion=fila["observacion"],
            usuario=fila["usuario"],
            fecha=fila["fecha"],
        )
        for fila in filas
    ]


def listar_imputaciones_de_cliente(cliente_id: int) -> list[Imputacion]:
    with obtener_conexion() as conexion:
        return listar_imputaciones_de_cliente_en_conexion(conexion, cliente_id)


def registrar_comprador_venta_en_conexion(
    conexion: sqlite3.Connection, venta_id: int, cliente_id: int, comprador: CompradorVenta, usuario_id: int
) -> None:
    """Registra quién compró en una venta a cuenta de una empresa (un trigger exige que el autorizado esté
    vigente para esa empresa). Va ANTES del CARGO: el libro rechaza el cargo de una empresa sin comprador."""
    conexion.execute(
        """
        INSERT INTO compradores_venta
            (venta_id, cliente_id, comprador_autorizado_id, nombre_excepcional, metodo_autorizacion,
             detalle_autorizacion, usuario_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            venta_id,
            cliente_id,
            comprador.comprador_autorizado_id,
            comprador.nombre_excepcional,
            comprador.metodo_autorizacion,
            comprador.detalle_autorizacion,
            usuario_id,
        ),
    )


def obtener_comprador_de_venta(venta_id: int) -> CompradorRegistrado | None:
    with obtener_conexion() as conexion:
        fila = conexion.execute(
            """
            SELECT COALESCE(a.nombre, cv.nombre_excepcional) AS nombre, cv.comprador_autorizado_id IS NULL AS excepcional,
                   cv.metodo_autorizacion, cv.detalle_autorizacion, u.nombre_completo AS usuario, cv.fecha
            FROM compradores_venta cv
            JOIN usuarios u ON u.id = cv.usuario_id
            LEFT JOIN compradores_autorizados a ON a.id = cv.comprador_autorizado_id
            WHERE cv.venta_id = ?
            """,
            (venta_id,),
        ).fetchone()
    if fila is None:
        return None
    return CompradorRegistrado(
        nombre=fila["nombre"],
        excepcional=bool(fila["excepcional"]),
        metodo_autorizacion=fila["metodo_autorizacion"],
        detalle_autorizacion=fila["detalle_autorizacion"],
        usuario=fila["usuario"],
        fecha=fila["fecha"],
    )
