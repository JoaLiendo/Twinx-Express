"""Repositorio de acceso a datos de movimientos de caja.

Contiene únicamente consultas SQL parametrizadas sobre
`caja_movimientos`. No contiene reglas de negocio: la validación del
monto/tipo/descripción ocurre en `domain.caja.MovimientoCaja`, y las
reglas de secuencia (no abrir una caja ya abierta, no cerrar una que
no está abierta) en `services.servicio_caja`.
"""

import sqlite3

from db.conexion import obtener_conexion
from domain.caja import MovimientoCaja

_COLUMNAS = "id, fecha, tipo, monto_centavos, descripcion"


def _fila_a_movimiento(fila: sqlite3.Row) -> MovimientoCaja:
    """Mapea una fila de la tabla `caja_movimientos` a la entidad de dominio."""
    return MovimientoCaja(
        id=fila["id"],
        fecha=fila["fecha"],
        tipo=fila["tipo"],
        monto_centavos=fila["monto_centavos"],
        descripcion=fila["descripcion"],
    )


def registrar_movimiento(movimiento: MovimientoCaja) -> MovimientoCaja:
    """Inserta un nuevo movimiento de caja y devuelve la entidad persistida."""
    consulta = f"""
        INSERT INTO caja_movimientos (tipo, monto_centavos, descripcion)
        VALUES (?, ?, ?)
        RETURNING {_COLUMNAS}
    """
    parametros = (movimiento.tipo, movimiento.monto_centavos, movimiento.descripcion)
    with obtener_conexion() as conexion:
        fila = conexion.execute(consulta, parametros).fetchone()
    return _fila_a_movimiento(fila)


def listar_movimientos() -> list[MovimientoCaja]:
    """Devuelve todos los movimientos de caja, ordenados cronológicamente."""
    consulta = f"SELECT {_COLUMNAS} FROM caja_movimientos ORDER BY id"
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta).fetchall()
    return [_fila_a_movimiento(fila) for fila in filas]


def listar_movimientos_del_dia() -> list[MovimientoCaja]:
    """Devuelve los movimientos de caja de hoy (hora local), en orden cronológico.

    Usada por el arqueo de caja (ver `services.servicio_caja.calcular_arqueo_del_dia`).
    """
    consulta = f"""
        SELECT {_COLUMNAS} FROM caja_movimientos
        WHERE date(fecha) = date('now', 'localtime')
        ORDER BY id
    """
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta).fetchall()
    return [_fila_a_movimiento(fila) for fila in filas]


def obtener_ultimo_movimiento() -> MovimientoCaja | None:
    """Devuelve el movimiento de caja más reciente, o `None` si no hay ninguno.

    Es la consulta que determina el estado actual de la caja: si el
    último movimiento no es un CIERRE, la caja sigue abierta (ver
    `services.servicio_caja._caja_esta_abierta`).
    """
    consulta = f"SELECT {_COLUMNAS} FROM caja_movimientos ORDER BY id DESC LIMIT 1"
    with obtener_conexion() as conexion:
        fila = conexion.execute(consulta).fetchone()
    return _fila_a_movimiento(fila) if fila is not None else None
