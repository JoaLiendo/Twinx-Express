"""Repositorio de acceso a datos de compras (ingreso de mercadería) y
su detalle (Fase 4B).

`registrar_compra_con_detalle` inserta la cabecera de la compra y
todas las líneas de `detalle_compra` usando la conexión que le pasa
quien la invoca, en vez de abrir una propia: así puede formar parte de
una transacción más amplia que también valida proveedor/productos e
incrementa stock/costo (ver `services.servicio_compras.registrar_compra`).
Mismo patrón que `db.repositorios.ventas.registrar_venta_con_detalle`.

Las compras son inmutables en esta fase: no hay `actualizar` ni
`eliminar` acá (ver `services.servicio_compras`).
"""

import sqlite3

from db.conexion import obtener_conexion
from domain.compra import Compra, DetalleCompra, ItemCompra, LineaDetalleCompra, ResumenCompra

_COLUMNAS_COMPRA = "id, proveedor_id, usuario_id, fecha, observaciones, total_centavos"
_COLUMNAS_DETALLE = "id, compra_id, producto_id, cantidad, costo_unitario_centavos, subtotal_centavos"

# Base del historial (Fase 4C): resuelve proveedor y usuario con JOIN y
# cuenta las líneas con COUNT + LEFT JOIN, todo en una sola consulta,
# para no golpear la base una vez por fila del listado (ver
# `listar_resumen` y `obtener_resumen_por_id`). El INNER JOIN con
# `proveedores`/`usuarios` es seguro: ambas FK son `ON DELETE RESTRICT`,
# así que una compra nunca puede referenciar un proveedor o usuario que
# ya no exista (como mucho, uno inactivo -- que este JOIN sigue
# encontrando igual, porque `activo` no es parte de la condición).
_CONSULTA_RESUMEN_BASE = """
    SELECT
        c.id, c.fecha, c.observaciones, c.total_centavos,
        p.nombre AS proveedor_nombre,
        u.nombre_completo AS usuario_nombre_completo,
        COUNT(dc.id) AS cantidad_lineas
    FROM compras c
    JOIN proveedores p ON p.id = c.proveedor_id
    JOIN usuarios u ON u.id = c.usuario_id
    LEFT JOIN detalle_compra dc ON dc.compra_id = c.id
"""


def _fila_a_resumen(fila: sqlite3.Row) -> ResumenCompra:
    return ResumenCompra(
        id=fila["id"],
        fecha=fila["fecha"],
        proveedor_nombre=fila["proveedor_nombre"],
        usuario_nombre_completo=fila["usuario_nombre_completo"],
        cantidad_lineas=fila["cantidad_lineas"],
        total_centavos=fila["total_centavos"],
        observaciones=fila["observaciones"],
    )


def _fila_a_linea_detalle(fila: sqlite3.Row) -> LineaDetalleCompra:
    return LineaDetalleCompra(
        id=fila["id"],
        compra_id=fila["compra_id"],
        producto_id=fila["producto_id"],
        producto_nombre=fila["producto_nombre"],
        producto_unidad_medida=fila["producto_unidad_medida"],
        cantidad=fila["cantidad"],
        costo_unitario_centavos=fila["costo_unitario_centavos"],
        subtotal_centavos=fila["subtotal_centavos"],
    )


def _fila_a_compra(fila: sqlite3.Row) -> Compra:
    return Compra(
        id=fila["id"],
        proveedor_id=fila["proveedor_id"],
        usuario_id=fila["usuario_id"],
        fecha=fila["fecha"],
        observaciones=fila["observaciones"],
        total_centavos=fila["total_centavos"],
    )


def _fila_a_detalle(fila: sqlite3.Row) -> DetalleCompra:
    return DetalleCompra(
        id=fila["id"],
        compra_id=fila["compra_id"],
        producto_id=fila["producto_id"],
        cantidad=fila["cantidad"],
        costo_unitario_centavos=fila["costo_unitario_centavos"],
        subtotal_centavos=fila["subtotal_centavos"],
    )


def obtener_por_clave_idempotencia_en_conexion(
    conexion: sqlite3.Connection, clave_idempotencia: str
) -> Compra | None:
    """Compra ya registrada con esa clave de idempotencia (migración 014), o `None`."""
    fila = conexion.execute(
        f"SELECT {_COLUMNAS_COMPRA} FROM compras WHERE clave_idempotencia = ?", (clave_idempotencia,)
    ).fetchone()
    return _fila_a_compra(fila) if fila is not None else None


def listar_lineas_en_conexion(conexion: sqlite3.Connection, compra_id: int) -> list[tuple[int, int, int]]:
    """Líneas de una compra como `(producto_id, cantidad, costo_unitario_centavos)`,
    ordenadas, dentro de la conexión recibida (sin abrir otra)."""
    filas = conexion.execute(
        "SELECT producto_id, cantidad, costo_unitario_centavos FROM detalle_compra "
        "WHERE compra_id = ? ORDER BY producto_id",
        (compra_id,),
    ).fetchall()
    return [(fila["producto_id"], fila["cantidad"], fila["costo_unitario_centavos"]) for fila in filas]


def registrar_compra_con_detalle(
    conexion: sqlite3.Connection,
    proveedor_id: int,
    usuario_id: int,
    observaciones: str | None,
    total_centavos: int,
    items_con_subtotal: list[tuple[ItemCompra, int]],
    clave_idempotencia: str | None = None,
) -> Compra:
    """Inserta la compra y su detalle dentro de la conexión recibida.

    `total_centavos` y cada `subtotal_centavos` de `items_con_subtotal`
    ya vienen calculados por quien invoca (siempre el servidor: ver
    `services.servicio_compras.registrar_compra`), nunca se recalculan
    ni se confía en un valor externo acá. No hace *commit* ni
    *rollback*: eso lo controla el `with obtener_conexion()` de quien
    invoca, para que la compra, su detalle y el incremento de
    stock/costo queden todos dentro de una única transacción atómica.
    """
    fila_compra = conexion.execute(
        f"""
        INSERT INTO compras (proveedor_id, usuario_id, observaciones, total_centavos, clave_idempotencia)
        VALUES (?, ?, ?, ?, ?)
        RETURNING {_COLUMNAS_COMPRA}
        """,
        (proveedor_id, usuario_id, observaciones, total_centavos, clave_idempotencia),
    ).fetchone()

    compra_id = fila_compra["id"]

    for item, subtotal_centavos in items_con_subtotal:
        conexion.execute(
            """
            INSERT INTO detalle_compra
                (compra_id, producto_id, cantidad, costo_unitario_centavos, subtotal_centavos)
            VALUES (?, ?, ?, ?, ?)
            """,
            (compra_id, item.producto_id, item.cantidad, item.costo_unitario_centavos, subtotal_centavos),
        )

    return _fila_a_compra(fila_compra)


def obtener_por_id(compra_id: int) -> Compra | None:
    """Busca una compra por id, o `None` si no existe."""
    consulta = f"SELECT {_COLUMNAS_COMPRA} FROM compras WHERE id = ?"
    with obtener_conexion() as conexion:
        fila = conexion.execute(consulta, (compra_id,)).fetchone()
    return _fila_a_compra(fila) if fila is not None else None


def listar_todas() -> list[Compra]:
    """Devuelve todas las compras, más recientes primero (listado)."""
    consulta = f"SELECT {_COLUMNAS_COMPRA} FROM compras ORDER BY id DESC"
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta).fetchall()
    return [_fila_a_compra(fila) for fila in filas]


def listar_detalle(compra_id: int) -> list[DetalleCompra]:
    """Devuelve las líneas de una compra (vista de detalle). Lista
    vacía si la compra no existe o no tiene líneas."""
    consulta = f"SELECT {_COLUMNAS_DETALLE} FROM detalle_compra WHERE compra_id = ? ORDER BY id"
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta, (compra_id,)).fetchall()
    return [_fila_a_detalle(fila) for fila in filas]


def listar_resumen(
    proveedor_id: int | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
) -> list[ResumenCompra]:
    """Historial de compras (Fase 4C): cabecera + nombre de proveedor +
    nombre completo de usuario + cantidad de líneas, en una sola
    consulta con JOIN (sin N+1), más recientes primero.

    Los tres filtros son opcionales y se combinan con AND. `fecha_desde`/
    `fecha_hasta` son texto "YYYY-MM-DD" (lo que manda un `<input
    type="date">") y se comparan solo por fecha, ignorando la hora,
    con la función `date(...)` de SQLite -- un valor no parseable hace
    que esa condición no matchee ninguna fila (lista vacía, nunca un
    error): comportamiento intencional, no se agrega validación extra
    para un filtro que ya de por sí solo acota un listado existente.
    """
    condiciones = []
    parametros: list[object] = []
    if proveedor_id is not None:
        condiciones.append("c.proveedor_id = ?")
        parametros.append(proveedor_id)
    if fecha_desde is not None:
        condiciones.append("date(c.fecha) >= date(?)")
        parametros.append(fecha_desde)
    if fecha_hasta is not None:
        condiciones.append("date(c.fecha) <= date(?)")
        parametros.append(fecha_hasta)

    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    consulta = f"{_CONSULTA_RESUMEN_BASE} {where} GROUP BY c.id ORDER BY c.id DESC"
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta, parametros).fetchall()
    return [_fila_a_resumen(fila) for fila in filas]


def obtener_resumen_por_id(compra_id: int) -> ResumenCompra | None:
    """Igual que `listar_resumen`, pero para una sola compra (encabezado
    de la pantalla de detalle). `None` si no existe."""
    consulta = f"{_CONSULTA_RESUMEN_BASE} WHERE c.id = ? GROUP BY c.id"
    with obtener_conexion() as conexion:
        fila = conexion.execute(consulta, (compra_id,)).fetchone()
    return _fila_a_resumen(fila) if fila is not None else None


def listar_detalle_con_producto(compra_id: int) -> list[LineaDetalleCompra]:
    """Igual que `listar_detalle`, pero cada línea ya trae el nombre y
    la unidad de medida de su producto resueltos con JOIN (pantalla de
    detalle, Fase 4C) -- evita una consulta de producto por línea."""
    consulta = """
        SELECT
            dc.id, dc.compra_id, dc.producto_id, dc.cantidad,
            dc.costo_unitario_centavos, dc.subtotal_centavos,
            pr.nombre AS producto_nombre, pr.unidad_medida AS producto_unidad_medida
        FROM detalle_compra dc
        JOIN productos pr ON pr.id = dc.producto_id
        WHERE dc.compra_id = ?
        ORDER BY dc.id
    """
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta, (compra_id,)).fetchall()
    return [_fila_a_linea_detalle(fila) for fila in filas]
