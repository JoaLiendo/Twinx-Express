"""Repositorio de clientes y de su cuenta corriente (migración 020).

Solo SQL parametrizado. Las funciones `*_en_conexion` se componen dentro de la
transacción `BEGIN IMMEDIATE` del servicio (venta a cuenta, cobro), de modo que
cliente, venta, ingreso de caja, libro y auditoría se confirman o se revierten
juntos. Las reglas comerciales las valida el servicio; el esquema (CHECK,
triggers, FK `RESTRICT`) las garantiza como última defensa.
"""

import sqlite3

from db.conexion import obtener_conexion
from domain.cliente import Cliente, ClienteConSaldo, DeudaTotal, MovimientoCuenta, ResumenCuenta, SaldoTrasVenta
from domain.reportes_operativos import CobranzaDeCliente, DeudorCuenta

_COLUMNAS = "id, nombre, telefono, email, direccion, observaciones, activo, fecha_creacion"
_COLUMNAS_MOVIMIENTO = (
    "id, fecha, cliente_id, tipo, monto_centavos, descripcion, venta_id, caja_movimiento_id, usuario_id"
)

_SALDO = "COALESCE(SUM(CASE tipo WHEN 'CARGO' THEN monto_centavos ELSE -monto_centavos END), 0)"


def _fila_a_cliente(fila: sqlite3.Row) -> Cliente:
    return Cliente(
        id=fila["id"],
        nombre=fila["nombre"],
        telefono=fila["telefono"],
        email=fila["email"],
        direccion=fila["direccion"],
        observaciones=fila["observaciones"],
        activo=bool(fila["activo"]),
        fecha_creacion=fila["fecha_creacion"],
    )


def _fila_a_movimiento(fila: sqlite3.Row) -> MovimientoCuenta:
    return MovimientoCuenta(
        id=fila["id"],
        fecha=fila["fecha"],
        cliente_id=fila["cliente_id"],
        tipo=fila["tipo"],
        monto_centavos=fila["monto_centavos"],
        descripcion=fila["descripcion"],
        venta_id=fila["venta_id"],
        caja_movimiento_id=fila["caja_movimiento_id"],
        usuario_id=fila["usuario_id"],
    )


def crear_cliente_en_conexion(conexion: sqlite3.Connection, cliente: Cliente) -> Cliente:
    """Inserta el cliente (siempre activo) y lo devuelve con `id` y `fecha_creacion`."""
    fila = conexion.execute(
        f"""
        INSERT INTO clientes (nombre, telefono, email, direccion, observaciones)
        VALUES (?, ?, ?, ?, ?)
        RETURNING {_COLUMNAS}
        """,
        (cliente.nombre, cliente.telefono, cliente.email, cliente.direccion, cliente.observaciones),
    ).fetchone()
    return _fila_a_cliente(fila)


def actualizar_cliente_en_conexion(conexion: sqlite3.Connection, cliente: Cliente) -> Cliente | None:
    """Actualiza los datos editables del cliente (no su estado `activo`).
    Devuelve `None` si no existe."""
    fila = conexion.execute(
        f"""
        UPDATE clientes
        SET nombre = ?, telefono = ?, email = ?, direccion = ?, observaciones = ?
        WHERE id = ?
        RETURNING {_COLUMNAS}
        """,
        (cliente.nombre, cliente.telefono, cliente.email, cliente.direccion, cliente.observaciones, cliente.id),
    ).fetchone()
    return _fila_a_cliente(fila) if fila is not None else None


def actualizar_activo_en_conexion(conexion: sqlite3.Connection, cliente_id: int, activo: bool) -> Cliente | None:
    """Activa o desactiva al cliente. Devuelve `None` si no existe. Un trigger del
    esquema rechaza desactivar a un cliente con saldo pendiente."""
    fila = conexion.execute(
        f"UPDATE clientes SET activo = ? WHERE id = ? RETURNING {_COLUMNAS}", (int(activo), cliente_id)
    ).fetchone()
    return _fila_a_cliente(fila) if fila is not None else None


def obtener_por_id_en_conexion(conexion: sqlite3.Connection, cliente_id: int) -> Cliente | None:
    """Cliente por id, activo o no; `None` si no existe."""
    fila = conexion.execute(f"SELECT {_COLUMNAS} FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    return _fila_a_cliente(fila) if fila is not None else None


def obtener_por_id(cliente_id: int) -> Cliente | None:
    """Ver `obtener_por_id_en_conexion`."""
    with obtener_conexion() as conexion:
        return obtener_por_id_en_conexion(conexion, cliente_id)


def listar(solo_activos: bool = False) -> list[Cliente]:
    """Clientes ordenados por nombre (sin distinguir mayúsculas) y luego por id."""
    condicion = "WHERE activo = 1" if solo_activos else ""
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            f"SELECT {_COLUMNAS} FROM clientes {condicion} ORDER BY nombre COLLATE NOCASE, id"
        ).fetchall()
    return [_fila_a_cliente(fila) for fila in filas]


def obtener_saldo_en_conexion(conexion: sqlite3.Connection, cliente_id: int) -> int:
    """Saldo de la cuenta corriente en centavos: `sum(CARGO) - sum(COBRO)`."""
    return conexion.execute(
        f"SELECT {_SALDO} FROM movimientos_cuenta WHERE cliente_id = ?", (cliente_id,)
    ).fetchone()[0]


def obtener_saldo(cliente_id: int) -> int:
    """Ver `obtener_saldo_en_conexion`."""
    with obtener_conexion() as conexion:
        return obtener_saldo_en_conexion(conexion, cliente_id)


def obtener_saldo_tras_cargo_de_venta(venta_id: int) -> SaldoTrasVenta | None:
    """Cliente y saldo de su cuenta justo después del CARGO de una venta a cuenta (el libro hasta ese
    movimiento, sin contar lo posterior); `None` si la venta no generó un cargo. Usa `_SALDO`, la misma
    expresión que el resto de los saldos."""
    with obtener_conexion() as conexion:
        fila = conexion.execute(
            f"""
            SELECT c.nombre AS nombre,
                   (SELECT {_SALDO} FROM movimientos_cuenta s WHERE s.cliente_id = m.cliente_id AND s.id <= m.id) AS saldo
            FROM movimientos_cuenta m JOIN clientes c ON c.id = m.cliente_id
            WHERE m.venta_id = ? AND m.tipo = 'CARGO'
            """,
            (venta_id,),
        ).fetchone()
    return SaldoTrasVenta(cliente_nombre=fila["nombre"], saldo_centavos=fila["saldo"]) if fila is not None else None


def obtener_deuda_total() -> DeudaTotal:
    """Suma de los saldos positivos y cantidad de clientes en deuda (activos o inactivos), agregada en SQL."""
    with obtener_conexion() as conexion:
        fila = conexion.execute(
            f"""
            SELECT COALESCE(SUM(saldo), 0) AS total, COUNT(*) AS cantidad FROM (
                SELECT {_SALDO} AS saldo FROM movimientos_cuenta GROUP BY cliente_id HAVING saldo > 0
            )
            """
        ).fetchone()
    return DeudaTotal(total_centavos=fila["total"], cantidad_clientes=fila["cantidad"])


def listar_deudores() -> list[DeudorCuenta]:
    """Clientes con saldo positivo (activos o no), el que más debe primero; el saldo se agrega en SQL con
    `_SALDO`, la misma expresión que `obtener_deuda_total` y el resto de los saldos."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            f"""
            SELECT c.id, c.nombre, c.activo, m.saldo
            FROM clientes c
            JOIN (SELECT cliente_id, {_SALDO} AS saldo FROM movimientos_cuenta
                  GROUP BY cliente_id HAVING saldo > 0) m ON m.cliente_id = c.id
            ORDER BY m.saldo DESC, c.nombre COLLATE NOCASE, c.id
            """
        ).fetchall()
    return [DeudorCuenta(fila["id"], fila["nombre"], bool(fila["activo"]), fila["saldo"]) for fila in filas]


def resumir_cobranzas(desde_inicio: str, hasta_exclusivo: str) -> list[CobranzaDeCliente]:
    """Cobros (solo `COBRO`, nunca cargos) del período `[desde_inicio, hasta_exclusivo)` agrupados por
    cliente en SQL: cantidad y monto; el mayor monto primero."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            """
            SELECT c.id, c.nombre, COUNT(*) AS cobros, SUM(m.monto_centavos) AS total
            FROM movimientos_cuenta m JOIN clientes c ON c.id = m.cliente_id
            WHERE m.tipo = 'COBRO' AND m.fecha >= ? AND m.fecha < ?
            GROUP BY c.id
            ORDER BY total DESC, c.nombre COLLATE NOCASE, c.id
            """,
            (desde_inicio, hasta_exclusivo),
        ).fetchall()
    return [CobranzaDeCliente(fila["id"], fila["nombre"], fila["cobros"], fila["total"]) for fila in filas]


def obtener_resumen_cuenta_en_conexion(conexion: sqlite3.Connection, cliente_id: int) -> ResumenCuenta:
    """Saldo, total de cargos y total de cobros en UNA consulta sobre el libro. El saldo usa la
    misma expresión (`_SALDO`) que `obtener_saldo_en_conexion`, así que no puede divergir."""
    fila = conexion.execute(
        f"""
        SELECT {_SALDO} AS saldo,
               COALESCE(SUM(CASE tipo WHEN 'CARGO' THEN monto_centavos END), 0) AS cargos,
               COALESCE(SUM(CASE tipo WHEN 'COBRO' THEN monto_centavos END), 0) AS cobros
        FROM movimientos_cuenta WHERE cliente_id = ?
        """,
        (cliente_id,),
    ).fetchone()
    return ResumenCuenta(
        saldo_centavos=fila["saldo"], total_cargos_centavos=fila["cargos"], total_cobros_centavos=fila["cobros"]
    )


def listar_movimientos_cuenta_recientes_en_conexion(
    conexion: sqlite3.Connection, cliente_id: int
) -> list[MovimientoCuenta]:
    """Libro de la cuenta del cliente, del movimiento más reciente al más antiguo."""
    filas = conexion.execute(
        f"SELECT {_COLUMNAS_MOVIMIENTO} FROM movimientos_cuenta WHERE cliente_id = ? ORDER BY id DESC",
        (cliente_id,),
    ).fetchall()
    return [_fila_a_movimiento(fila) for fila in filas]


def _escapar_comodines_like(texto: str) -> str:
    """Escapa `\\`, `%` y `_` para que lo que tipea el usuario no se interprete como patrón LIKE."""
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def listar_con_saldo(texto: str | None = None, estado: str = "activos", limite: int | None = None) -> list[ClienteConSaldo]:
    """Clientes con su saldo, en una sola consulta (sin N+1), ordenados por nombre.

    `texto` busca por coincidencia parcial en nombre o teléfono, con los comodines de SQL
    escapados. `estado` es `activos`, `inactivos` o `todos` (validado por el servicio).
    """
    condiciones: list[str] = []
    parametros: list[object] = []
    if estado == "activos":
        condiciones.append("c.activo = 1")
    elif estado == "inactivos":
        condiciones.append("c.activo = 0")
    texto = (texto or "").strip()
    if texto:
        patron = f"%{_escapar_comodines_like(texto)}%"
        condiciones.append("(c.nombre LIKE ? ESCAPE '\\' OR c.telefono LIKE ? ESCAPE '\\')")
        parametros.extend([patron, patron])
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    tope = "LIMIT ?" if limite is not None else ""
    if limite is not None:
        parametros.append(limite)
    consulta = f"""
        SELECT c.id, c.nombre, c.telefono, c.email, c.direccion, c.observaciones, c.activo, c.fecha_creacion,
               COALESCE(m.saldo, 0) AS saldo
        FROM clientes c
        LEFT JOIN (
            SELECT cliente_id, SUM(CASE tipo WHEN 'CARGO' THEN monto_centavos ELSE -monto_centavos END) AS saldo
            FROM movimientos_cuenta GROUP BY cliente_id
        ) m ON m.cliente_id = c.id
        {where}
        ORDER BY c.nombre COLLATE NOCASE, c.id
        {tope}
    """
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta, parametros).fetchall()
    return [ClienteConSaldo(cliente=_fila_a_cliente(fila), saldo_centavos=fila["saldo"]) for fila in filas]


def listar_movimientos_cuenta(cliente_id: int) -> list[MovimientoCuenta]:
    """Libro de la cuenta del cliente, en orden cronológico."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            f"SELECT {_COLUMNAS_MOVIMIENTO} FROM movimientos_cuenta WHERE cliente_id = ? ORDER BY id",
            (cliente_id,),
        ).fetchall()
    return [_fila_a_movimiento(fila) for fila in filas]


def registrar_cargo_en_conexion(
    conexion: sqlite3.Connection,
    cliente_id: int,
    monto_centavos: int,
    venta_id: int,
    usuario_id: int | None,
    descripcion: str | None,
) -> MovimientoCuenta:
    """Inserta el CARGO de una venta a cuenta. Un trigger del esquema exige que la
    venta sea a cuenta, activa, del mismo cliente, de la sesión abierta y por el mismo total."""
    fila = conexion.execute(
        f"""
        INSERT INTO movimientos_cuenta (cliente_id, tipo, monto_centavos, descripcion, venta_id, usuario_id)
        VALUES (?, 'CARGO', ?, ?, ?, ?)
        RETURNING {_COLUMNAS_MOVIMIENTO}
        """,
        (cliente_id, monto_centavos, descripcion, venta_id, usuario_id),
    ).fetchone()
    return _fila_a_movimiento(fila)


def registrar_cobro_en_conexion(
    conexion: sqlite3.Connection,
    cliente_id: int,
    monto_centavos: int,
    caja_movimiento_id: int,
    usuario_id: int | None,
    descripcion: str | None,
    clave_idempotencia: str | None,
    contenido_hash: str | None,
) -> MovimientoCuenta:
    """Inserta el COBRO respaldado por el INGRESO de caja `caja_movimiento_id`. Un trigger
    del esquema verifica que el cliente exista, el origen/tipo/monto/sesión del ingreso y `monto <= saldo`
    (el cliente puede estar inactivo: un inactivo con deuda puede pagarla)."""
    fila = conexion.execute(
        f"""
        INSERT INTO movimientos_cuenta
            (cliente_id, tipo, monto_centavos, descripcion, caja_movimiento_id, usuario_id,
             clave_idempotencia, contenido_hash)
        VALUES (?, 'COBRO', ?, ?, ?, ?, ?, ?)
        RETURNING {_COLUMNAS_MOVIMIENTO}
        """,
        (cliente_id, monto_centavos, descripcion, caja_movimiento_id, usuario_id, clave_idempotencia, contenido_hash),
    ).fetchone()
    return _fila_a_movimiento(fila)


def obtener_cobro_por_clave_en_conexion(
    conexion: sqlite3.Connection, clave_idempotencia: str
) -> tuple[MovimientoCuenta, str | None] | None:
    """Cobro ya registrado con esa clave de idempotencia: `(movimiento, contenido_hash)` o `None`."""
    fila = conexion.execute(
        f"SELECT {_COLUMNAS_MOVIMIENTO}, contenido_hash FROM movimientos_cuenta WHERE clave_idempotencia = ?",
        (clave_idempotencia,),
    ).fetchone()
    if fila is None:
        return None
    return _fila_a_movimiento(fila), fila["contenido_hash"]


def obtener_cobro_por_clave(clave_idempotencia: str) -> tuple[MovimientoCuenta, str | None] | None:
    """Ver `obtener_cobro_por_clave_en_conexion`."""
    with obtener_conexion() as conexion:
        return obtener_cobro_por_clave_en_conexion(conexion, clave_idempotencia)
