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

_COLUMNAS = "id, fecha, tipo, monto_centavos, descripcion, diferencia_centavos"


def _fila_a_movimiento(fila: sqlite3.Row) -> MovimientoCaja:
    """Mapea una fila de la tabla `caja_movimientos` a la entidad de dominio."""
    return MovimientoCaja(
        id=fila["id"],
        fecha=fila["fecha"],
        tipo=fila["tipo"],
        monto_centavos=fila["monto_centavos"],
        descripcion=fila["descripcion"],
        diferencia_centavos=fila["diferencia_centavos"],
    )


def registrar_movimiento(movimiento: MovimientoCaja, usuario_id: int | None = None) -> MovimientoCaja:
    """Inserta un nuevo movimiento de caja y devuelve la entidad persistida.

    `usuario_id` (migración 010) es opcional, igual criterio que
    `ventas.usuario_id`: el CLI no autentica a nadie, así que sus
    movimientos quedan con `usuario_id = NULL`, nunca con un usuario
    inventado. No se expone en `MovimientoCaja` (el dominio no cambia en
    este bloque): quien necesite ese dato hoy lo consulta directo contra
    `caja_movimientos`, igual que ya ocurre con otras columnas de
    trazabilidad que tampoco están en el dominio.

    `movimiento.diferencia_centavos` (migración 011, faltante/sobrante
    del cierre) sí viaja en el propio `MovimientoCaja` -- a diferencia de
    `usuario_id`, es un hecho financiero permanente sobre el movimiento
    mismo, no un dato de sesión. Este repositorio no lo calcula: lo
    persiste tal cual viene, ya resuelto por
    `services.servicio_caja.cerrar_caja`.
    """
    consulta = f"""
        INSERT INTO caja_movimientos (tipo, monto_centavos, descripcion, usuario_id, diferencia_centavos)
        VALUES (?, ?, ?, ?, ?)
        RETURNING {_COLUMNAS}
    """
    parametros = (
        movimiento.tipo,
        movimiento.monto_centavos,
        movimiento.descripcion,
        usuario_id,
        movimiento.diferencia_centavos,
    )
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


def obtener_fecha_ultima_apertura_en_conexion(conexion: sqlite3.Connection) -> str | None:
    """Fecha de la APERTURA que inicia la sesión de caja actualmente
    vigente, o `None` si no hay ninguna caja abierta en este momento.

    El modelo no tiene (ni necesita) una relación `caja_id` en `ventas`:
    esta consulta reconstruye la sesión vigente solo con lo que ya
    existe en `caja_movimientos`. `services.servicio_caja.abrir_caja`
    nunca permite dos APERTURA consecutivas sin un CIERRE entre medio
    (rechaza abrir una caja ya abierta), así que mientras el último
    movimiento no sea un CIERRE, la APERTURA más reciente es, sin
    ambigüedad, el inicio de esa sesión.

    Pensada para componerse dentro de la transacción de
    `services.servicio_ventas.anular_venta` (ver `db.repositorios.ventas`),
    con la misma conexión que ya restaura stock y marca la venta como
    ANULADA -- nunca abre una conexión propia, para no romper la
    atomicidad de esa operación.
    """
    ultimo = conexion.execute(f"SELECT {_COLUMNAS} FROM caja_movimientos ORDER BY id DESC LIMIT 1").fetchone()
    if ultimo is None or ultimo["tipo"] == "CIERRE":
        return None
    if ultimo["tipo"] == "APERTURA":
        return ultimo["fecha"]
    # INGRESO/EGRESO: la caja sigue abierta, pero la APERTURA vigente es
    # una fila anterior -- no puede haber más de una entre el último
    # CIERRE (si existe) y ahora.
    apertura = conexion.execute(
        "SELECT fecha FROM caja_movimientos WHERE tipo = 'APERTURA' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return apertura["fecha"] if apertura is not None else None
