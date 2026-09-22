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
from domain.venta import ItemVenta, LineaVenta, ProductoMasVendido, ResumenVenta, Venta, VentaConDetalle


def registrar_venta_con_detalle(
    conexion: sqlite3.Connection,
    total_centavos: int,
    tipo_pago: str,
    items_con_precio: list[tuple[ItemVenta, int]],
    clave_idempotencia: str | None = None,
    contenido_hash: str | None = None,
    usuario_id: int | None = None,
    costos_unitarios_por_producto_id: dict[int, int] | None = None,
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

    `usuario_id` (migración 009) es opcional por el mismo motivo que
    `clave_idempotencia`: el CLI no autentica a nadie, así que sus
    ventas quedan con `usuario_id = NULL`, nunca con un usuario
    inventado.

    `costos_unitarios_por_producto_id` (migración 009) es un diccionario
    opcional `producto_id -> costo_unitario_centavos`, separado de
    `items_con_precio` a propósito para no cambiar la forma de esa
    tupla (evita tocar los call sites existentes que la construyen a
    mano). Un producto ausente del diccionario -- o el diccionario
    entero ausente -- deja `costo_unitario_centavos = NULL` en esa
    línea, nunca un valor inventado.

    No hace *commit* ni *rollback*: eso lo controla el
    `with obtener_conexion()` de quien invoca esta función, para que
    la venta, su detalle y el descuento de stock queden todos dentro
    de una única transacción atómica.
    """
    fila_venta = conexion.execute(
        """
        INSERT INTO ventas (total_centavos, tipo_pago, clave_idempotencia, contenido_hash, usuario_id)
        VALUES (?, ?, ?, ?, ?)
        RETURNING id, fecha, total_centavos, tipo_pago
        """,
        (total_centavos, tipo_pago, clave_idempotencia, contenido_hash, usuario_id),
    ).fetchone()

    venta_id = fila_venta["id"]

    for item, precio_unitario_centavos in items_con_precio:
        subtotal_centavos = precio_unitario_centavos * item.cantidad
        costo_unitario_centavos = (
            None
            if costos_unitarios_por_producto_id is None
            else costos_unitarios_por_producto_id.get(item.producto_id)
        )
        conexion.execute(
            """
            INSERT INTO detalle_venta
                (venta_id, producto_id, cantidad, precio_unitario_centavos, subtotal_centavos,
                 costo_unitario_centavos)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                venta_id,
                item.producto_id,
                item.cantidad,
                precio_unitario_centavos,
                subtotal_centavos,
                costo_unitario_centavos,
            ),
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


def listar_en_rango(fecha_desde: str | None = None, fecha_hasta: str | None = None) -> list[Venta]:
    """Ventas dentro de un rango de fechas (módulo de Reportes), más
    recientes primero. Ambos límites son opcionales e inclusivos.

    Mismo criterio que `db.repositorios.compras.listar_resumen`:
    `fecha_desde`/`fecha_hasta` son texto "YYYY-MM-DD" (lo que manda un
    `<input type="date">`) y se comparan solo por fecha, ignorando la
    hora, con la función `date(...)` de SQLite -- un valor no parseable
    hace que esa condición no matchee ninguna fila (lista vacía, nunca
    un error).
    """
    condiciones = []
    parametros: list[object] = []
    if fecha_desde is not None:
        condiciones.append("date(fecha) >= date(?)")
        parametros.append(fecha_desde)
    if fecha_hasta is not None:
        condiciones.append("date(fecha) <= date(?)")
        parametros.append(fecha_hasta)

    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    consulta = f"SELECT id, fecha, total_centavos, tipo_pago FROM ventas {where} ORDER BY id DESC"
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta, parametros).fetchall()
    return [_fila_a_venta(fila) for fila in filas]


def listar_productos_mas_vendidos_en_rango(
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    limite: int = 10,
) -> list[ProductoMasVendido]:
    """Ranking de productos por unidades vendidas dentro de un rango de
    fechas (módulo de Reportes), mayor a menor. Mismos filtros
    opcionales/inclusivos que `listar_en_rango`.

    Une `detalle_venta` con `ventas` (para filtrar por fecha de la
    cabecera, `detalle_venta` no tiene su propia fecha) y con
    `productos` (para el nombre actual). El `JOIN` con `productos` es
    seguro (no `LEFT JOIN`): `detalle_venta.producto_id` es
    `ON DELETE RESTRICT`, un producto con ventas nunca se borra
    físicamente, como mucho se desactiva -- pero sigue apareciendo acá
    con su nombre actual, ranking histórico incluido.

    Costo y margen (Reportes V2) se calculan solo sobre las líneas con
    `costo_unitario_centavos` conocido -- `unidades_con_costo_conocido`
    puede ser menor a `unidades_vendidas` si el producto tiene ventas de
    antes de que existiera ese dato. Nunca se trata `NULL` como `0`: una
    línea sin costo conocido no aporta nada a `costo_total_centavos` ni
    a la facturación usada para calcular el margen de ese producto (ver
    `domain.venta.ProductoMasVendido`).
    """
    condiciones = []
    parametros: list[object] = []
    if fecha_desde is not None:
        condiciones.append("date(v.fecha) >= date(?)")
        parametros.append(fecha_desde)
    if fecha_hasta is not None:
        condiciones.append("date(v.fecha) <= date(?)")
        parametros.append(fecha_hasta)

    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    consulta = f"""
        SELECT
            p.id AS producto_id,
            p.nombre AS producto_nombre,
            SUM(dv.cantidad) AS unidades_vendidas,
            SUM(dv.subtotal_centavos) AS total_vendido_centavos,
            SUM(CASE WHEN dv.costo_unitario_centavos IS NOT NULL THEN dv.cantidad ELSE 0 END)
                AS unidades_con_costo_conocido,
            SUM(CASE WHEN dv.costo_unitario_centavos IS NOT NULL
                     THEN dv.costo_unitario_centavos * dv.cantidad ELSE 0 END) AS costo_total_centavos,
            SUM(CASE WHEN dv.costo_unitario_centavos IS NOT NULL THEN dv.subtotal_centavos ELSE 0 END)
                AS venta_total_con_costo_centavos
        FROM detalle_venta dv
        JOIN ventas v ON v.id = dv.venta_id
        JOIN productos p ON p.id = dv.producto_id
        {where}
        GROUP BY p.id
        ORDER BY unidades_vendidas DESC, total_vendido_centavos DESC
        LIMIT ?
    """
    parametros.append(limite)
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta, parametros).fetchall()

    resultado = []
    for fila in filas:
        unidades_con_costo_conocido = fila["unidades_con_costo_conocido"]
        if unidades_con_costo_conocido > 0:
            costo_total_centavos = fila["costo_total_centavos"]
            margen_bruto_centavos = fila["venta_total_con_costo_centavos"] - costo_total_centavos
        else:
            costo_total_centavos = None
            margen_bruto_centavos = None
        resultado.append(
            ProductoMasVendido(
                producto_id=fila["producto_id"],
                producto_nombre=fila["producto_nombre"],
                unidades_vendidas=fila["unidades_vendidas"],
                total_vendido_centavos=fila["total_vendido_centavos"],
                unidades_con_costo_conocido=unidades_con_costo_conocido,
                costo_total_centavos=costo_total_centavos,
                margen_bruto_centavos=margen_bruto_centavos,
            )
        )
    return resultado


def calcular_rentabilidad_en_rango(
    fecha_desde: str | None = None, fecha_hasta: str | None = None
) -> tuple[int, int, int]:
    """Rentabilidad agregada del período (Reportes V2), en una sola
    consulta SQL. Devuelve la tupla
    `(costo_total_centavos, venta_total_con_costo_centavos, cantidad_ventas_sin_costo_historico)`.

    `venta_total_con_costo_centavos` es la facturación **solo** de las
    líneas con `costo_unitario_centavos` conocido -- no la facturación
    total del período: `services.servicio_reportes` calcula el margen
    bruto porcentual como `margen_bruto / venta_total_con_costo_centavos`,
    y usar ahí la facturación total abarataría artificialmente el margen
    con ventas que no aportaron nada al costo. `cantidad_ventas_sin_costo_historico`
    cuenta ventas (`DISTINCT venta_id`), no líneas: la unidad que le
    importa a quien lee el reporte es "cuántas ventas quedaron afuera",
    no cuántas líneas sueltas.

    Nunca trata una línea con costo `NULL` como costo `0`: esa línea no
    suma ni al costo ni a la facturación de este cálculo, en vez de
    inflar la facturación "gratis" o inventar un costo.
    """
    condiciones = []
    parametros: list[object] = []
    if fecha_desde is not None:
        condiciones.append("date(v.fecha) >= date(?)")
        parametros.append(fecha_desde)
    if fecha_hasta is not None:
        condiciones.append("date(v.fecha) <= date(?)")
        parametros.append(fecha_hasta)

    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    consulta = f"""
        SELECT
            SUM(CASE WHEN dv.costo_unitario_centavos IS NOT NULL
                     THEN dv.costo_unitario_centavos * dv.cantidad ELSE 0 END) AS costo_total_centavos,
            SUM(CASE WHEN dv.costo_unitario_centavos IS NOT NULL THEN dv.subtotal_centavos ELSE 0 END)
                AS venta_total_con_costo_centavos,
            COUNT(DISTINCT CASE WHEN dv.costo_unitario_centavos IS NULL THEN dv.venta_id END)
                AS ventas_sin_costo_historico
        FROM detalle_venta dv
        JOIN ventas v ON v.id = dv.venta_id
        {where}
    """
    with obtener_conexion() as conexion:
        fila = conexion.execute(consulta, parametros).fetchone()

    return (
        fila["costo_total_centavos"] or 0,
        fila["venta_total_con_costo_centavos"] or 0,
        fila["ventas_sin_costo_historico"] or 0,
    )


def listar_ventas_por_usuario_en_rango(
    fecha_desde: str | None = None, fecha_hasta: str | None = None
) -> list[tuple[int, str, bool, int, int]]:
    """Ventas agrupadas por vendedor (Reportes V2), de mayor a menor
    facturación. Cada tupla es
    `(usuario_id, nombre_completo, activo, cantidad_ventas, total_vendido_centavos)`.

    `JOIN usuarios` (no `LEFT JOIN`) excluye de forma natural las ventas
    con `usuario_id IS NULL` (CLI, o anteriores a que este campo
    existiera) de este ranking puntual -- sin que haga falta un `WHERE`
    aparte. Esas ventas siguen contando en las métricas generales del
    reporte (`ReporteVentas.cantidad_ventas`/`total_facturado_centavos`),
    que no pasan por esta función.

    No filtra por `usuarios.activo`: un usuario desactivado después de
    haber vendido sigue apareciendo con su historial completo -- nunca
    se borra un usuario físicamente (ver `db.repositorios.usuarios`), y
    las ventas ya ocurrieron con independencia de su estado actual.
    """
    condiciones = []
    parametros: list[object] = []
    if fecha_desde is not None:
        condiciones.append("date(v.fecha) >= date(?)")
        parametros.append(fecha_desde)
    if fecha_hasta is not None:
        condiciones.append("date(v.fecha) <= date(?)")
        parametros.append(fecha_hasta)

    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    consulta = f"""
        SELECT
            u.id AS usuario_id,
            u.nombre_completo AS nombre_completo,
            u.activo AS activo,
            COUNT(v.id) AS cantidad_ventas,
            SUM(v.total_centavos) AS total_vendido_centavos
        FROM ventas v
        JOIN usuarios u ON u.id = v.usuario_id
        {where}
        GROUP BY u.id
        ORDER BY total_vendido_centavos DESC
    """
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta, parametros).fetchall()
    return [
        (
            fila["usuario_id"],
            fila["nombre_completo"],
            bool(fila["activo"]),
            fila["cantidad_ventas"],
            fila["total_vendido_centavos"],
        )
        for fila in filas
    ]


_CONSULTA_RESUMEN_VENTA_BASE = """
    SELECT
        v.id AS id,
        v.fecha AS fecha,
        v.total_centavos AS total_centavos,
        v.tipo_pago AS tipo_pago,
        u.nombre_completo AS vendedor_nombre,
        COUNT(dv.id) AS cantidad_lineas
    FROM ventas v
    LEFT JOIN usuarios u ON u.id = v.usuario_id
    LEFT JOIN detalle_venta dv ON dv.venta_id = v.id
"""


def _fila_a_resumen_venta(fila: sqlite3.Row) -> ResumenVenta:
    return ResumenVenta(
        id=fila["id"],
        fecha=fila["fecha"],
        total_centavos=fila["total_centavos"],
        tipo_pago=fila["tipo_pago"],
        vendedor_nombre=fila["vendedor_nombre"],
        cantidad_lineas=fila["cantidad_lineas"],
    )


def listar_resumen(
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    tipo_pago: str | None = None,
) -> list[ResumenVenta]:
    """Historial de ventas (Historial de Ventas): cabecera + vendedor +
    cantidad de líneas ya resueltos, en una sola consulta con `JOIN`
    (sin N+1), más recientes primero.

    `LEFT JOIN usuarios` (no `JOIN`): `ventas.usuario_id` es nullable
    (ventas anteriores a esa migración, o del CLI, que no autentica a
    nadie) -- un `JOIN` normal descartaría esas ventas del listado en
    vez de mostrarlas con `vendedor_nombre = NULL`. No filtra por
    `usuarios.activo`: un usuario desactivado después de vender sigue
    apareciendo con su nombre (mismo criterio que
    `listar_ventas_por_usuario_en_rango`).

    Los tres filtros son opcionales y se combinan con `AND`, mismo
    criterio que el resto del repositorio: `fecha_desde`/`fecha_hasta`
    son texto "YYYY-MM-DD" comparado solo por fecha con `date(...)`.
    """
    condiciones = []
    parametros: list[object] = []
    if fecha_desde is not None:
        condiciones.append("date(v.fecha) >= date(?)")
        parametros.append(fecha_desde)
    if fecha_hasta is not None:
        condiciones.append("date(v.fecha) <= date(?)")
        parametros.append(fecha_hasta)
    if tipo_pago is not None:
        condiciones.append("v.tipo_pago = ?")
        parametros.append(tipo_pago)

    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    consulta = f"{_CONSULTA_RESUMEN_VENTA_BASE} {where} GROUP BY v.id ORDER BY v.id DESC"
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta, parametros).fetchall()
    return [_fila_a_resumen_venta(fila) for fila in filas]


def obtener_resumen_por_id(venta_id: int) -> ResumenVenta | None:
    """Igual que `listar_resumen`, pero para una sola venta (cabecera de
    la pantalla de detalle del Historial). `None` si no existe.

    No reemplaza a `obtener_venta_con_detalle` (que sigue siendo la
    fuente de las líneas, sin ningún cambio): esta función solo resuelve
    la cabecera con vendedor y cantidad de líneas.
    """
    consulta = f"{_CONSULTA_RESUMEN_VENTA_BASE} WHERE v.id = ? GROUP BY v.id"
    with obtener_conexion() as conexion:
        fila = conexion.execute(consulta, (venta_id,)).fetchone()
    return _fila_a_resumen_venta(fila) if fila is not None else None
