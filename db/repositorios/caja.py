"""Repositorio de acceso a datos de sesiones y movimientos de caja.

Contiene únicamente consultas SQL parametrizadas sobre `sesiones_caja`
(migración 019) y `caja_movimientos`. La validación del monto/tipo/descripción
ocurre en `domain.caja.MovimientoCaja`; las reglas de secuencia (una sola
sesión abierta, sin operar sobre una sesión cerrada) se aplican dentro de la
propia transacción y además las garantiza el esquema (índice único parcial y
triggers de la migración 019).
"""

import sqlite3

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from domain.caja import MovimientoCaja, SesionCaja
from domain.dinero import centavos_a_texto
from excepciones import CajaError

_COLUMNAS = "id, fecha, tipo, monto_centavos, descripcion, diferencia_centavos, origen"
_COLUMNAS_SESION = (
    "id, estado, origen, fecha_apertura, fecha_cierre, fondo_centavos, contado_centavos, diferencia_centavos"
)


def _fila_a_movimiento(fila: sqlite3.Row) -> MovimientoCaja:
    """Mapea una fila de la tabla `caja_movimientos` a la entidad de dominio."""
    return MovimientoCaja(
        id=fila["id"],
        fecha=fila["fecha"],
        tipo=fila["tipo"],
        monto_centavos=fila["monto_centavos"],
        descripcion=fila["descripcion"],
        diferencia_centavos=fila["diferencia_centavos"],
        origen=fila["origen"],
    )


def _fila_a_sesion(fila: sqlite3.Row) -> SesionCaja:
    return SesionCaja(
        id=fila["id"],
        estado=fila["estado"],
        origen=fila["origen"],
        fecha_apertura=fila["fecha_apertura"],
        fecha_cierre=fila["fecha_cierre"],
        fondo_centavos=fila["fondo_centavos"],
        contado_centavos=fila["contado_centavos"],
        diferencia_centavos=fila["diferencia_centavos"],
    )


def _resumen_de_movimiento(movimiento: MovimientoCaja) -> str:
    monto = f"${centavos_a_texto(movimiento.monto_centavos)}"
    if movimiento.tipo == "APERTURA":
        return f"Apertura con {monto}"
    if movimiento.tipo == "CIERRE":
        diferencia = movimiento.diferencia_centavos or 0
        signo = "+" if diferencia > 0 else "-" if diferencia < 0 else ""
        return f"Cierre: contado {monto}, diferencia {signo}${centavos_a_texto(abs(diferencia))}"
    return f"{monto}: {movimiento.descripcion}"


def obtener_sesion_abierta_en_conexion(conexion: sqlite3.Connection) -> SesionCaja | None:
    """La sesión de caja actualmente abierta (`sesiones_caja.estado = 'ABIERTA'`),
    o `None` si no hay ninguna. El esquema garantiza que hay como máximo una.

    Recibe la conexión para poder componerse dentro de otra transacción
    (ver `services.servicio_ventas.registrar_venta`/`anular_venta`) sin
    romper su atomicidad.
    """
    fila = conexion.execute(f"SELECT {_COLUMNAS_SESION} FROM sesiones_caja WHERE estado = 'ABIERTA'").fetchone()
    return _fila_a_sesion(fila) if fila is not None else None


def obtener_sesion_abierta() -> SesionCaja | None:
    """Ver `obtener_sesion_abierta_en_conexion`."""
    with obtener_conexion() as conexion:
        return obtener_sesion_abierta_en_conexion(conexion)


def obtener_ultima_sesion() -> SesionCaja | None:
    """La sesión abierta o, si no hay ninguna, la más reciente ya cerrada;
    `None` si nunca hubo una caja. Excluye la sesión `LEGADO`: no es una caja
    real (ver `domain.caja.SesionCaja`)."""
    with obtener_conexion() as conexion:
        fila = conexion.execute(
            f"SELECT {_COLUMNAS_SESION} FROM sesiones_caja WHERE origen <> 'LEGADO' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return _fila_a_sesion(fila) if fila is not None else None


def registrar_movimiento_en_conexion(
    conexion: sqlite3.Connection,
    movimiento: MovimientoCaja,
    usuario_id: int | None = None,
    clave_idempotencia: str | None = None,
) -> MovimientoCaja:
    """Registra un movimiento de caja dentro de la transacción recibida (que
    debe haberse abierto con `BEGIN IMMEDIATE`) y devuelve la entidad persistida.

    Es el único punto donde se crean y cierran sesiones de caja:
      * `APERTURA` crea una sesión `NORMAL`/`ABIERTA` (con el monto como fondo)
        y el movimiento asociado; falla si ya hay una sesión abierta.
      * `INGRESO`/`EGRESO` se asocian a la sesión abierta; fallan si no hay.
      * `CIERRE` se asocia a la sesión abierta y la pasa a `CERRADA` guardando
        el monto contado y la diferencia. El movimiento se inserta con la
        sesión todavía abierta y recién después se la cierra: es el único
        orden compatible con los triggers de la migración 019.

    `usuario_id` (migración 010) es opcional, igual criterio que
    `ventas.usuario_id`: el CLI no autentica a nadie, así que sus movimientos
    quedan con `usuario_id = NULL`, nunca con un usuario inventado.

    `clave_idempotencia` (migración 014, V1.1): si ya existe un movimiento con
    esa clave se devuelve ese mismo movimiento sin escribir nada. Como toda la
    operación corre bajo `BEGIN IMMEDIATE`, dos requests simultáneos con la
    misma clave quedan serializados y el segundo ve la fila del primero.

    `movimiento.origen` (migración 020) se persiste tal cual y ya no cambia.

    `movimiento.diferencia_centavos` (migración 011) viaja en el propio
    `MovimientoCaja`: este repositorio no lo calcula, lo persiste tal cual
    viene ya resuelto por `services.servicio_caja.cerrar_caja`.

    Raises:
        CajaError: si se abre una caja con otra ya abierta, o se registra
            cualquier otro movimiento sin caja abierta.
    """
    if clave_idempotencia is not None:
        existente = conexion.execute(
            f"SELECT {_COLUMNAS} FROM caja_movimientos WHERE clave_idempotencia = ?", (clave_idempotencia,)
        ).fetchone()
        if existente is not None:
            return _fila_a_movimiento(existente)

    sesion = obtener_sesion_abierta_en_conexion(conexion)
    fecha = conexion.execute("SELECT datetime('now', 'localtime')").fetchone()[0]
    if movimiento.tipo == "APERTURA":
        if sesion is not None:
            raise CajaError("La caja ya está abierta: hay que cerrarla antes de abrir una nueva.")
        sesion_id = conexion.execute(
            """
            INSERT INTO sesiones_caja (estado, origen, fecha_apertura, usuario_apertura_id, fondo_centavos)
            VALUES ('ABIERTA', 'NORMAL', ?, ?, ?)
            """,
            (fecha, usuario_id, movimiento.monto_centavos),
        ).lastrowid
    else:
        if sesion is None:
            raise CajaError(
                "No hay una caja abierta para cerrar."
                if movimiento.tipo == "CIERRE"
                else "No hay una caja abierta."
            )
        sesion_id = sesion.id

    fila = conexion.execute(
        f"""
        INSERT INTO caja_movimientos
            (fecha, tipo, monto_centavos, descripcion, usuario_id, diferencia_centavos, clave_idempotencia,
             sesion_caja_id, origen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING {_COLUMNAS}
        """,
        (
            fecha,
            movimiento.tipo,
            movimiento.monto_centavos,
            movimiento.descripcion,
            usuario_id,
            movimiento.diferencia_centavos,
            clave_idempotencia,
            sesion_id,
            movimiento.origen,
        ),
    ).fetchone()

    if movimiento.tipo == "CIERRE":
        conexion.execute(
            """
            UPDATE sesiones_caja
            SET estado = 'CERRADA', fecha_cierre = ?, usuario_cierre_id = ?,
                contado_centavos = ?, diferencia_centavos = ?
            WHERE id = ? AND estado = 'ABIERTA'
            """,
            (fecha, usuario_id, movimiento.monto_centavos, movimiento.diferencia_centavos, sesion_id),
        )

    # El INGRESO de un cobro de cuenta lo audita quien lo origina, con la acción
    # única `COBRO_CUENTA`: acá no se duplica como `CAJA_INGRESO`.
    if usuario_id is not None and movimiento.origen == "MANUAL":
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, f"CAJA_{movimiento.tipo}", "CAJA", fila["id"], _resumen_de_movimiento(movimiento)
        )
    return _fila_a_movimiento(fila)


def registrar_movimiento(
    movimiento: MovimientoCaja, usuario_id: int | None = None, clave_idempotencia: str | None = None
) -> MovimientoCaja:
    """Registra un movimiento en su propia transacción `BEGIN IMMEDIATE`.
    Ver `registrar_movimiento_en_conexion`."""
    with obtener_conexion(inmediata=True) as conexion:
        return registrar_movimiento_en_conexion(conexion, movimiento, usuario_id, clave_idempotencia)


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


def listar_movimientos_recientes(limite: int) -> list[MovimientoCaja]:
    """Los últimos `limite` movimientos de caja, el más reciente primero (recortados en SQL)."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            f"SELECT {_COLUMNAS} FROM caja_movimientos ORDER BY id DESC LIMIT ?", (limite,)
        ).fetchall()
    return [_fila_a_movimiento(fila) for fila in filas]


def listar_movimientos_de_sesion_en_conexion(conexion: sqlite3.Connection, sesion_id: int) -> list[MovimientoCaja]:
    """Movimientos de una sesión de caja (`sesion_caja_id`), en orden cronológico.

    Usada por el arqueo de caja (ver `services.servicio_caja.calcular_arqueo_de_sesion`
    y `cerrar_caja`, que la compone dentro de su transacción).
    """
    filas = conexion.execute(
        f"SELECT {_COLUMNAS} FROM caja_movimientos WHERE sesion_caja_id = ? ORDER BY id", (sesion_id,)
    ).fetchall()
    return [_fila_a_movimiento(fila) for fila in filas]


def listar_movimientos_de_sesion(sesion_id: int) -> list[MovimientoCaja]:
    """Ver `listar_movimientos_de_sesion_en_conexion`."""
    with obtener_conexion() as conexion:
        return listar_movimientos_de_sesion_en_conexion(conexion, sesion_id)
