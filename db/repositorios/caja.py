"""Repositorio de acceso a datos de movimientos de caja.

Contiene únicamente consultas SQL parametrizadas sobre
`caja_movimientos`. No contiene reglas de negocio: la validación del
monto/tipo/descripción ocurre en `domain.caja.MovimientoCaja`, y las
reglas de secuencia (no abrir una caja ya abierta, no cerrar una que
no está abierta) en `services.servicio_caja`.
"""

import sqlite3

from db.conexion import obtener_conexion
from domain.caja import MovimientoCaja, SesionCaja

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


def registrar_movimiento(
    movimiento: MovimientoCaja, usuario_id: int | None = None, clave_idempotencia: str | None = None
) -> MovimientoCaja:
    """Inserta un nuevo movimiento de caja y devuelve la entidad persistida.

    `usuario_id` (migración 010) es opcional, igual criterio que
    `ventas.usuario_id`: el CLI no autentica a nadie, así que sus
    movimientos quedan con `usuario_id = NULL`, nunca con un usuario
    inventado. No se expone en `MovimientoCaja` (el dominio no cambia en
    este bloque): quien necesite ese dato hoy lo consulta directo contra
    `caja_movimientos`, igual que ya ocurre con otras columnas de
    trazabilidad que tampoco están en el dominio.

    `clave_idempotencia` (migración 014, V1.1) protege contra el doble
    envío: si ya existe un movimiento con esa clave se devuelve ese mismo
    movimiento sin insertar nada. La lectura y el `INSERT` corren bajo
    `BEGIN IMMEDIATE`, así dos requests simultáneos con la misma clave
    quedan serializados y el segundo ve la fila del primero.

    `movimiento.diferencia_centavos` (migración 011, faltante/sobrante
    del cierre) sí viaja en el propio `MovimientoCaja` -- a diferencia de
    `usuario_id`, es un hecho financiero permanente sobre el movimiento
    mismo, no un dato de sesión. Este repositorio no lo calcula: lo
    persiste tal cual viene, ya resuelto por
    `services.servicio_caja.cerrar_caja`.
    """
    consulta = f"""
        INSERT INTO caja_movimientos
            (tipo, monto_centavos, descripcion, usuario_id, diferencia_centavos, clave_idempotencia)
        VALUES (?, ?, ?, ?, ?, ?)
        RETURNING {_COLUMNAS}
    """
    parametros = (
        movimiento.tipo,
        movimiento.monto_centavos,
        movimiento.descripcion,
        usuario_id,
        movimiento.diferencia_centavos,
        clave_idempotencia,
    )
    with obtener_conexion(inmediata=clave_idempotencia is not None) as conexion:
        if clave_idempotencia is not None:
            existente = conexion.execute(
                f"SELECT {_COLUMNAS} FROM caja_movimientos WHERE clave_idempotencia = ?", (clave_idempotencia,)
            ).fetchone()
            if existente is not None:
                return _fila_a_movimiento(existente)
        fila = conexion.execute(consulta, parametros).fetchone()
    return _fila_a_movimiento(fila)


def obtener_por_clave_idempotencia(clave_idempotencia: str) -> MovimientoCaja | None:
    """Movimiento ya registrado con esa clave de idempotencia (migración 014), o `None`."""
    with obtener_conexion() as conexion:
        fila = conexion.execute(
            f"SELECT {_COLUMNAS} FROM caja_movimientos WHERE clave_idempotencia = ?", (clave_idempotencia,)
        ).fetchone()
    return _fila_a_movimiento(fila) if fila is not None else None


def listar_movimientos() -> list[MovimientoCaja]:
    """Devuelve todos los movimientos de caja, ordenados cronológicamente."""
    consulta = f"SELECT {_COLUMNAS} FROM caja_movimientos ORDER BY id"
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta).fetchall()
    return [_fila_a_movimiento(fila) for fila in filas]


def listar_movimientos_de_sesion(sesion: SesionCaja) -> list[MovimientoCaja]:
    """Devuelve los movimientos de una sesión de caja (de su APERTURA a su
    CIERRE, o hasta el último si sigue abierta), en orden cronológico.

    Usada por el arqueo de caja (ver `services.servicio_caja.calcular_arqueo_de_sesion`).
    Se delimita por `id` (siempre creciente), no por fecha: no depende
    del día calendario ni de la resolución del reloj.
    """
    limite_superior = sesion.cierre_id if sesion.cierre_id is not None else -1
    consulta = f"""
        SELECT {_COLUMNAS} FROM caja_movimientos
        WHERE id >= ? AND (? = -1 OR id <= ?)
        ORDER BY id
    """
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta, (sesion.apertura_id, limite_superior, limite_superior)).fetchall()
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


def obtener_ultima_sesion_en_conexion(conexion: sqlite3.Connection) -> SesionCaja | None:
    """Última sesión de caja (abierta o ya cerrada), o `None` si nunca se
    abrió una caja.

    El modelo no tiene (ni necesita) una relación `caja_id` en `ventas`:
    la sesión se reconstruye solo con `caja_movimientos`.
    `services.servicio_caja.abrir_caja` nunca permite dos APERTURA
    consecutivas sin un CIERRE entre medio, así que el último movimiento
    define sin ambigüedad la sesión: si es un CIERRE, la sesión es la
    que abrió la APERTURA inmediatamente anterior; si no, sigue abierta
    desde la APERTURA más reciente.

    Recibe la conexión para poder componerse dentro de otra transacción
    (ver `services.servicio_ventas.registrar_venta`/`anular_venta`) sin
    romper su atomicidad.
    """
    ultimo = conexion.execute(f"SELECT {_COLUMNAS} FROM caja_movimientos ORDER BY id DESC LIMIT 1").fetchone()
    if ultimo is None:
        return None
    if ultimo["tipo"] == "CIERRE":
        apertura = conexion.execute(
            "SELECT id, fecha FROM caja_movimientos WHERE tipo = 'APERTURA' AND id < ? ORDER BY id DESC LIMIT 1",
            (ultimo["id"],),
        ).fetchone()
        cierre_id, fecha_cierre = ultimo["id"], ultimo["fecha"]
    else:
        apertura = conexion.execute(
            "SELECT id, fecha FROM caja_movimientos WHERE tipo = 'APERTURA' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        cierre_id, fecha_cierre = None, None
    if apertura is None:
        return None
    return SesionCaja(
        apertura_id=apertura["id"],
        fecha_apertura=apertura["fecha"],
        cierre_id=cierre_id,
        fecha_cierre=fecha_cierre,
    )


def obtener_ultima_sesion() -> SesionCaja | None:
    """Ver `obtener_ultima_sesion_en_conexion`."""
    with obtener_conexion() as conexion:
        return obtener_ultima_sesion_en_conexion(conexion)


def obtener_sesion_abierta_en_conexion(conexion: sqlite3.Connection) -> SesionCaja | None:
    """La sesión de caja actualmente abierta, o `None` si no hay ninguna."""
    sesion = obtener_ultima_sesion_en_conexion(conexion)
    return sesion if sesion is not None and sesion.abierta else None
