"""Repositorio de acceso a datos de ventas y su detalle.

`registrar_venta_con_detalle` inserta la cabecera de la venta y todas
las líneas de `detalle_venta` usando la conexión que le pasa quien la
invoca, en vez de abrir una propia: así puede formar parte de una
transacción más amplia que también descuenta stock (ver
`services.servicio_ventas.registrar_venta`). El tipo de pago además
está validado por un CHECK en el esquema
(`db/migraciones/001_inicial.sql`) como última línea de defensa.

Los montos se manejan en centavos (`int`): ver `domain.dinero`.
"""

import sqlite3

from db.conexion import obtener_conexion
from domain.venta import ItemVenta, LineaVenta, Venta, VentaConDetalle


def registrar_venta_con_detalle(
    conexion: sqlite3.Connection,
    total_centavos: int,
    tipo_pago: str,
    items_con_precio: list[tuple[ItemVenta, int]],
    clave_idempotencia: str | None = None,
    contenido_hash: str | None = None,
) -> Venta:
    """Inserta la venta y su detalle dentro de la conexión recibida.

    `items_con_precio` son pares (`ItemVenta`, precio_unitario_centavos)
    ya resueltos por el servicio: el precio se congela al momento de la
    venta, no se vuelve a consultar el producto más adelante. El
    subtotal de cada línea se calcula con aritmética entera exacta
    (`precio_unitario_centavos * cantidad`), sin ningún redondeo.

    `clave_idempotencia`/`contenido_hash` son opcionales (Fase 5A): el
    CLI y los llamados directos al servicio sin clave siguen
    funcionando igual que antes. Si `clave_idempotencia` ya existe en
    otra venta, el `INSERT` falla con `sqlite3.IntegrityError` (columna
    `UNIQUE`) -- lo traduce `db.conexion.obtener_conexion` a
    `ErrorBaseDatos`; `services.servicio_ventas.registrar_venta` es
    quien decide qué hacer con ese conflicto (ver su docstring).

    No hace *commit* ni *rollback*: eso lo controla el
    `with obtener_conexion()` de quien invoca esta función, para que
    la venta, su detalle y el descuento de stock queden todos dentro
    de una única transacción atómica.
    """
    fila_venta = conexion.execute(
        """
        INSERT INTO ventas (total_centavos, tipo_pago, clave_idempotencia, contenido_hash)
        VALUES (?, ?, ?, ?)
        RETURNING id, fecha, total_centavos, tipo_pago
        """,
        (total_centavos, tipo_pago, clave_idempotencia, contenido_hash),
    ).fetchone()

    venta_id = fila_venta["id"]

    for item, precio_unitario_centavos in items_con_precio:
        subtotal_centavos = precio_unitario_centavos * item.cantidad
        conexion.execute(
            """
            INSERT INTO detalle_venta
                (venta_id, producto_id, cantidad, precio_unitario_centavos, subtotal_centavos)
            VALUES (?, ?, ?, ?, ?)
            """,
            (venta_id, item.producto_id, item.cantidad, precio_unitario_centavos, subtotal_centavos),
        )

    return Venta(
        id=venta_id,
        fecha=fila_venta["fecha"],
        total_centavos=fila_venta["total_centavos"],
        tipo_pago=fila_venta["tipo_pago"],
    )


def _fila_a_venta(fila: sqlite3.Row) -> Venta:
    return Venta(
        id=fila["id"],
        fecha=fila["fecha"],
        total_centavos=fila["total_centavos"],
        tipo_pago=fila["tipo_pago"],
    )


def obtener_por_clave_idempotencia_en_conexion(
    conexion: sqlite3.Connection, clave_idempotencia: str
) -> tuple[Venta, str] | None:
    """Busca una venta por su clave de idempotencia usando una conexión
    ya abierta (Fase 5A). Devuelve `(venta, contenido_hash)` o `None`.

    Pensada para componerse dentro de la transacción de
    `services.servicio_ventas.registrar_venta`: tanto el chequeo previo
    (¿ya existe?) como la lectura de recuperación tras perder una
    carrera de escritura (ver su docstring) pasan por acá.
    """
    fila = conexion.execute(
        "SELECT id, fecha, total_centavos, tipo_pago, contenido_hash FROM ventas WHERE clave_idempotencia = ?",
        (clave_idempotencia,),
    ).fetchone()
    if fila is None:
        return None
    return _fila_a_venta(fila), fila["contenido_hash"]


def obtener_por_clave_idempotencia(clave_idempotencia: str) -> tuple[Venta, str] | None:
    """Igual que `obtener_por_clave_idempotencia_en_conexion`, en su
    propia conexión (uso desde tests o fuera de una transacción más
    amplia)."""
    with obtener_conexion() as conexion:
        return obtener_por_clave_idempotencia_en_conexion(conexion, clave_idempotencia)


def obtener_venta_con_detalle(venta_id: int) -> VentaConDetalle | None:
    """Lectura histórica de una venta ya confirmada junto con sus líneas
    (Fase 5D: ticket imprimible). Devuelve `None` si `venta_id` no existe.

    Una única sentencia SQL (`ventas JOIN detalle_venta JOIN productos`),
    nunca dos ni 1+N: cada fila del resultado es una línea de la venta,
    con los datos de la cabecera repetidos en todas (se toman de la
    primera fila, son los mismos en cualquiera). El `JOIN` con
    `detalle_venta` (no un `LEFT JOIN`) es seguro sin perder ninguna
    venta real: `servicio_ventas.registrar_venta` -- el único llamador
    de `registrar_venta_con_detalle` en toda la aplicación -- rechaza
    `items` vacío *antes* de invocarlo, y esa función inserta la venta
    y su detalle en la misma transacción. Una venta persistida por el
    flujo real de la app **siempre** tiene al menos una línea; por lo
    tanto, cero filas de resultado significa inequívocamente "no existe
    `venta_id`", nunca "existe pero sin detalle".

    El precio unitario y el subtotal de cada línea son los que ya
    quedaron congelados en `detalle_venta` al momento de la venta --
    nunca se recalculan desde el precio actual del producto. El nombre,
    en cambio, es el *actual* de `productos` (no hay snapshot de
    nombre): un producto renombrado después de la venta va a aparecer
    con el nombre nuevo en un ticket reimpreso -- limitación conocida y
    aceptada (ver diseño de Fase 5D), no un error. El `JOIN` con
    `productos` también es seguro (no `LEFT JOIN`) porque
    `detalle_venta.producto_id` tiene `ON DELETE RESTRICT`: un producto
    con ventas nunca se borra físicamente, como mucho se desactiva
    (`activo=0`).
    """
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            """
            SELECT ventas.id AS venta_id,
                   ventas.fecha AS venta_fecha,
                   ventas.total_centavos AS venta_total_centavos,
                   ventas.tipo_pago AS venta_tipo_pago,
                   productos.nombre AS producto_nombre,
                   detalle_venta.cantidad AS cantidad,
                   detalle_venta.precio_unitario_centavos AS precio_unitario_centavos,
                   detalle_venta.subtotal_centavos AS subtotal_centavos
            FROM ventas
            JOIN detalle_venta ON detalle_venta.venta_id = ventas.id
            JOIN productos ON productos.id = detalle_venta.producto_id
            WHERE ventas.id = ?
            ORDER BY detalle_venta.id
            """,
            (venta_id,),
        ).fetchall()

    if not filas:
        return None

    primera = filas[0]
    venta = Venta(
        id=primera["venta_id"],
        fecha=primera["venta_fecha"],
        total_centavos=primera["venta_total_centavos"],
        tipo_pago=primera["venta_tipo_pago"],
    )
    lineas = [
        LineaVenta(
            producto_nombre=fila["producto_nombre"],
            cantidad=fila["cantidad"],
            precio_unitario_centavos=fila["precio_unitario_centavos"],
            subtotal_centavos=fila["subtotal_centavos"],
        )
        for fila in filas
    ]
    return VentaConDetalle(venta=venta, lineas=lineas)


def listar_ventas_del_dia() -> list[Venta]:
    """Devuelve las ventas registradas hoy (hora local), más recientes primero.

    Usada por el arqueo de caja para calcular el total vendido y el
    efectivo estimado del día (ver `services.servicio_caja.calcular_arqueo_del_dia`).
    """
    consulta = """
        SELECT id, fecha, total_centavos, tipo_pago FROM ventas
        WHERE date(fecha) = date('now', 'localtime')
        ORDER BY id DESC
    """
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta).fetchall()
    return [_fila_a_venta(fila) for fila in filas]
