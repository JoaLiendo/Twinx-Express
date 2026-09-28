"""Repositorio del libro de cuenta de proveedor (migración 025, V1.9-A/B/C).

Solo SQL parametrizado. Las funciones `*_en_conexion` se componen dentro de la transacción
`BEGIN IMMEDIATE` del servicio (compra a crédito, anulación, pago), de modo que compra/pago,
cargo/reversa/PAGO y auditoría se confirman o se revierten juntos. Las reglas comerciales las
valida el servicio; el esquema (CHECK, índices únicos parciales, triggers) las garantiza como
última defensa -- mismo criterio que `db.repositorios.clientes` con `movimientos_cuenta`.
"""

import sqlite3

from db.conexion import obtener_conexion
from domain.proveedor import MovimientoProveedor

_SALDO = (
    "COALESCE(SUM(CASE tipo"
    " WHEN 'CARGO_COMPRA' THEN monto_centavos"
    " WHEN 'PAGO' THEN -monto_centavos"
    " WHEN 'REVERSA_COMPRA' THEN -monto_centavos END), 0)"
)

_COLUMNAS_MOVIMIENTO = (
    "id, fecha, proveedor_id, tipo, monto_centavos, compra_id, caja_movimiento_id, medio_pago,"
    " observacion, usuario_id"
)


def _fila_a_movimiento(fila: sqlite3.Row) -> MovimientoProveedor:
    return MovimientoProveedor(
        id=fila["id"],
        fecha=fila["fecha"],
        proveedor_id=fila["proveedor_id"],
        tipo=fila["tipo"],
        monto_centavos=fila["monto_centavos"],
        compra_id=fila["compra_id"],
        caja_movimiento_id=fila["caja_movimiento_id"],
        medio_pago=fila["medio_pago"],
        observacion=fila["observacion"],
        usuario_id=fila["usuario_id"],
    )


def obtener_saldo_en_conexion(conexion: sqlite3.Connection, proveedor_id: int) -> int:
    """Saldo pendiente del proveedor en centavos: `SUM(CARGO_COMPRA) - SUM(PAGO) - SUM(REVERSA_COMPRA)`.
    `0` si no tiene movimientos."""
    return conexion.execute(
        f"SELECT {_SALDO} FROM movimientos_proveedor WHERE proveedor_id = ?", (proveedor_id,)
    ).fetchone()[0]


def obtener_saldo(proveedor_id: int) -> int:
    """Ver `obtener_saldo_en_conexion`."""
    with obtener_conexion() as conexion:
        return obtener_saldo_en_conexion(conexion, proveedor_id)


def registrar_cargo_en_conexion(
    conexion: sqlite3.Connection, proveedor_id: int, compra_id: int, monto_centavos: int, usuario_id: int
) -> None:
    """Inserta el CARGO_COMPRA de una compra a crédito. `trg_movimientos_proveedor_cargo_valido`
    exige que la compra sea del mismo proveedor, esté ACTIVA, sea CREDITO y por el mismo monto; el
    índice único `idx_movimientos_proveedor_un_cargo_por_compra` impide un segundo cargo."""
    conexion.execute(
        "INSERT INTO movimientos_proveedor (proveedor_id, tipo, monto_centavos, compra_id, usuario_id)"
        " VALUES (?, 'CARGO_COMPRA', ?, ?, ?)",
        (proveedor_id, monto_centavos, compra_id, usuario_id),
    )


def registrar_reversa_en_conexion(
    conexion: sqlite3.Connection, proveedor_id: int, compra_id: int, monto_centavos: int, usuario_id: int
) -> None:
    """Inserta la REVERSA_COMPRA al anular una compra a crédito. `trg_movimientos_proveedor_reversa_valida`
    exige que exista la compra y su cargo previo, mismo proveedor y monto; el índice único
    `idx_movimientos_proveedor_una_reversa_por_compra` impide una segunda reversa."""
    conexion.execute(
        "INSERT INTO movimientos_proveedor (proveedor_id, tipo, monto_centavos, compra_id, usuario_id)"
        " VALUES (?, 'REVERSA_COMPRA', ?, ?, ?)",
        (proveedor_id, monto_centavos, compra_id, usuario_id),
    )


def obtener_id_cargo_de_compra_en_conexion(conexion: sqlite3.Connection, compra_id: int) -> int | None:
    """Id del movimiento CARGO_COMPRA de la compra, o `None` si no tiene (compra CONTADO, o
    histórica de antes de V1.9-B)."""
    fila = conexion.execute(
        "SELECT id FROM movimientos_proveedor WHERE compra_id = ? AND tipo = 'CARGO_COMPRA'", (compra_id,)
    ).fetchone()
    return fila[0] if fila is not None else None


def existe_pago_posterior_en_conexion(conexion: sqlite3.Connection, proveedor_id: int, cargo_id: int) -> bool:
    """True si el proveedor tiene algún PAGO registrado después del cargo (por `id`, no por fecha:
    mismo criterio de orden que `existe_compra_activa_posterior_en_conexion`). Regla conservadora de
    V1.9-B: no importa si ese pago "correspondía" a esta compra u otra, alcanza con que exista."""
    fila = conexion.execute(
        "SELECT 1 FROM movimientos_proveedor WHERE proveedor_id = ? AND tipo = 'PAGO' AND id > ? LIMIT 1",
        (proveedor_id, cargo_id),
    ).fetchone()
    return fila is not None


def registrar_pago_en_conexion(
    conexion: sqlite3.Connection,
    proveedor_id: int,
    monto_centavos: int,
    medio_pago: str,
    caja_movimiento_id: int | None,
    observacion: str | None,
    usuario_id: int | None,
    clave_idempotencia: str | None,
    contenido_hash: str | None,
) -> MovimientoProveedor:
    """Inserta el PAGO (V1.9-C). En efectivo, `trg_movimientos_proveedor_pago_efectivo_valido` exige
    que `caja_movimiento_id` sea un EGRESO real de origen `PAGO_PROVEEDOR`, de la sesión abierta y
    por el mismo monto; por transferencia, el CHECK de la tabla exige `caja_movimiento_id IS NULL`.
    Nunca referencia una compra puntual (`compra_id` siempre `NULL`, ver auditoría V1.9)."""
    fila = conexion.execute(
        f"""
        INSERT INTO movimientos_proveedor
            (proveedor_id, tipo, monto_centavos, medio_pago, caja_movimiento_id, observacion, usuario_id,
             clave_idempotencia, contenido_hash)
        VALUES (?, 'PAGO', ?, ?, ?, ?, ?, ?, ?)
        RETURNING {_COLUMNAS_MOVIMIENTO}
        """,
        (
            proveedor_id, monto_centavos, medio_pago, caja_movimiento_id, observacion, usuario_id,
            clave_idempotencia, contenido_hash,
        ),
    ).fetchone()
    return _fila_a_movimiento(fila)


def obtener_pago_por_clave_en_conexion(
    conexion: sqlite3.Connection, clave_idempotencia: str
) -> tuple[MovimientoProveedor, str | None] | None:
    """Pago ya registrado con esa clave de idempotencia: `(movimiento, contenido_hash)` o `None`."""
    fila = conexion.execute(
        f"SELECT {_COLUMNAS_MOVIMIENTO}, contenido_hash FROM movimientos_proveedor WHERE clave_idempotencia = ?",
        (clave_idempotencia,),
    ).fetchone()
    if fila is None:
        return None
    return _fila_a_movimiento(fila), fila["contenido_hash"]


def obtener_pago_por_clave(clave_idempotencia: str) -> tuple[MovimientoProveedor, str | None] | None:
    """Ver `obtener_pago_por_clave_en_conexion`."""
    with obtener_conexion() as conexion:
        return obtener_pago_por_clave_en_conexion(conexion, clave_idempotencia)


def listar_movimientos(proveedor_id: int) -> list[MovimientoProveedor]:
    """Libro del proveedor, del movimiento más reciente al más antiguo (ficha, V1.9-C)."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            f"SELECT {_COLUMNAS_MOVIMIENTO} FROM movimientos_proveedor WHERE proveedor_id = ? ORDER BY id DESC",
            (proveedor_id,),
        ).fetchall()
    return [_fila_a_movimiento(fila) for fila in filas]
