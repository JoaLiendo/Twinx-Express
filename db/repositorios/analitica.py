"""Consultas agregadas de solo lectura de la analítica (V1.17).

Todas reciben límites de fecha/hora como texto comparable (`inicio` inclusive, `fin_exclusivo`
exclusivo, formato `AAAA-MM-DD` o `AAAA-MM-DD HH:MM:SS`) y filtros opcionales ya validados por el
servicio. Nunca escriben. Solo ventas y compras `ACTIVA`: una venta anulada (también por nota de
crédito) o una compra anulada no cuentan.

Los costos de una venta se leen de dos fuentes que se devuelven por separado (ver
`domain.analitica.LineaVentaDia`): el de referencia (`detalle_venta.costo_unitario_centavos`) y el real
del libro de partidas (`movimientos_partida` × `partidas_stock.costo_unitario_centavos`). Un `NULL`
jamás se trata como 0.
"""

from collections.abc import Iterable

from db.conexion import obtener_conexion
from domain.analitica import (
    CambioPrecioListado,
    CompraCategoriaProveedor,
    CostoProductoProveedor,
    EvolucionPrecioProducto,
    LineaVentaDia,
    ObligacionPendiente,
    VentasDia,
)


def _filtro_categorias(columna: str, categoria_ids: Iterable[int] | None, parametros: list[object]) -> str:
    if categoria_ids is None:
        return ""
    ids = sorted(set(categoria_ids))
    if not ids:
        return " AND 0"
    parametros.extend(ids)
    return f" AND {columna} IN ({', '.join('?' for _ in ids)})"


def _filtro_producto(columna: str, producto_id: int | None, parametros: list[object]) -> str:
    if producto_id is None:
        return ""
    parametros.append(producto_id)
    return f" AND {columna} = ?"


# --- Ventas y margen ---------------------------------------------------------------------------------


def ventas_por_dia(
    inicio: str, fin_exclusivo: str, producto_id: int | None = None, categoria_ids: Iterable[int] | None = None
) -> list[VentasDia]:
    """Tickets, unidades y total por día de ventas activas. Con filtro de producto o categoría, los
    tickets son las ventas que incluyeron esos productos y el total es solo la facturación de esas líneas."""
    parametros: list[object] = [inicio, fin_exclusivo]
    filtro = _filtro_producto("d.producto_id", producto_id, parametros) + _filtro_categorias(
        "p.categoria_id", categoria_ids, parametros
    )
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            f"""
            SELECT substr(v.fecha, 1, 10) AS fecha, COUNT(DISTINCT v.id) AS cantidad_ventas,
                   SUM(d.cantidad) AS unidades, SUM(d.subtotal_centavos) AS total
            FROM ventas v
            JOIN detalle_venta d ON d.venta_id = v.id
            JOIN productos p ON p.id = d.producto_id
            WHERE v.estado = 'ACTIVA' AND v.fecha >= ? AND v.fecha < ?{filtro}
            GROUP BY substr(v.fecha, 1, 10)
            ORDER BY fecha
            """,
            parametros,
        ).fetchall()
    return [VentasDia(fila["fecha"], fila["cantidad_ventas"], fila["unidades"], fila["total"]) for fila in filas]


def ventas_por_mes_historico() -> list[tuple[str, int, int]]:
    """`(AAAA-MM, cantidad_ventas, total_centavos)` de toda la historia de ventas activas, cronológico."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            """
            SELECT substr(fecha, 1, 7) AS mes, COUNT(*) AS cantidad, SUM(total_centavos) AS total
            FROM ventas WHERE estado = 'ACTIVA' GROUP BY substr(fecha, 1, 7) ORDER BY mes
            """
        ).fetchall()
    return [(fila["mes"], fila["cantidad"], fila["total"]) for fila in filas]


def lineas_de_venta_por_dia(
    inicio: str, fin_exclusivo: str, producto_id: int | None = None, categoria_ids: Iterable[int] | None = None
) -> list[LineaVentaDia]:
    """Por producto y día: unidades, facturación, costo de referencia y costo real de partidas, con las
    unidades que cada base cubre. Una línea sin movimientos de partida (venta anterior a V1.12) aporta
    unidades sin costo real; una partida sin costo (heredada) también."""
    parametros: list[object] = [inicio, fin_exclusivo]
    filtro = _filtro_producto("d.producto_id", producto_id, parametros) + _filtro_categorias(
        "p.categoria_id", categoria_ids, parametros
    )
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            f"""
            WITH lineas AS (
                SELECT d.id, substr(v.fecha, 1, 10) AS fecha, d.producto_id, d.cantidad, d.subtotal_centavos,
                       d.costo_unitario_centavos AS costo_ref
                FROM ventas v JOIN detalle_venta d ON d.venta_id = v.id
                JOIN productos p ON p.id = d.producto_id
                WHERE v.estado = 'ACTIVA' AND v.fecha >= ? AND v.fecha < ?{filtro}
            ),
            reales AS (
                SELECT m.detalle_venta_id AS linea_id,
                       SUM(CASE WHEN s.costo_unitario_centavos IS NOT NULL THEN -m.delta ELSE 0 END) AS unidades_real,
                       SUM(CASE WHEN s.costo_unitario_centavos IS NOT NULL
                                THEN -m.delta * s.costo_unitario_centavos ELSE 0 END) AS costo_real
                FROM movimientos_partida m JOIN partidas_stock s ON s.id = m.partida_id
                WHERE m.detalle_venta_id IN (SELECT id FROM lineas)
                GROUP BY m.detalle_venta_id
            )
            SELECT l.fecha, l.producto_id, p.nombre AS producto_nombre, p.categoria_id, c.nombre AS categoria_nombre,
                   SUM(l.cantidad) AS unidades, SUM(l.subtotal_centavos) AS facturado,
                   SUM(CASE WHEN l.costo_ref IS NOT NULL THEN l.cantidad ELSE 0 END) AS unidades_ref,
                   SUM(CASE WHEN l.costo_ref IS NOT NULL THEN l.cantidad * l.costo_ref ELSE 0 END) AS costo_ref,
                   SUM(CASE WHEN l.costo_ref IS NOT NULL THEN l.subtotal_centavos ELSE 0 END) AS facturado_ref,
                   SUM(COALESCE(r.unidades_real, 0)) AS unidades_real,
                   SUM(COALESCE(r.costo_real, 0)) AS costo_real,
                   SUM(CASE WHEN l.costo_ref IS NOT NULL THEN COALESCE(r.unidades_real, 0) ELSE 0 END) AS unidades_comp,
                   SUM(CASE WHEN l.costo_ref IS NOT NULL THEN COALESCE(r.unidades_real, 0) * l.costo_ref ELSE 0 END)
                       AS costo_ref_comp,
                   SUM(CASE WHEN l.costo_ref IS NOT NULL THEN COALESCE(r.costo_real, 0) ELSE 0 END) AS costo_real_comp
            FROM lineas l
            JOIN productos p ON p.id = l.producto_id
            LEFT JOIN categorias c ON c.id = p.categoria_id
            LEFT JOIN reales r ON r.linea_id = l.id
            GROUP BY l.fecha, l.producto_id
            ORDER BY l.fecha, p.nombre COLLATE NOCASE, l.producto_id
            """,
            parametros,
        ).fetchall()
    return [
        LineaVentaDia(
            fecha=fila["fecha"],
            producto_id=fila["producto_id"],
            producto_nombre=fila["producto_nombre"],
            categoria_id=fila["categoria_id"],
            categoria_nombre=fila["categoria_nombre"],
            unidades=fila["unidades"],
            facturado_centavos=fila["facturado"],
            unidades_con_costo_ref=fila["unidades_ref"],
            costo_ref_centavos=fila["costo_ref"],
            facturado_con_costo_ref_centavos=fila["facturado_ref"],
            unidades_costo_real=fila["unidades_real"],
            costo_real_centavos=fila["costo_real"],
            unidades_comparables=fila["unidades_comp"],
            costo_ref_comparable_centavos=fila["costo_ref_comp"],
            costo_real_comparable_centavos=fila["costo_real_comp"],
        )
        for fila in filas
    ]


def unidades_vendidas_por_producto(inicio: str, fin_exclusivo: str) -> dict[int, int]:
    """Unidades vendidas (ventas activas) por producto en el período; los que no vendieron no aparecen."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            """
            SELECT d.producto_id, SUM(d.cantidad) AS unidades
            FROM ventas v JOIN detalle_venta d ON d.venta_id = v.id
            WHERE v.estado = 'ACTIVA' AND v.fecha >= ? AND v.fecha < ?
            GROUP BY d.producto_id
            """,
            (inicio, fin_exclusivo),
        ).fetchall()
    return {fila["producto_id"]: fila["unidades"] for fila in filas}


# --- Precios y costos --------------------------------------------------------------------------------


def evolucion_de_precios(
    inicio: str, fin_exclusivo: str, producto_id: int | None = None, categoria_ids: Iterable[int] | None = None
) -> list[EvolucionPrecioProducto]:
    """Por producto y campo con cambios en el período: valor anterior al primer cambio, valor del último,
    cantidad de cambios y valor vigente. Solo hechos del historial: sin cambios registrados no hay fila."""
    parametros: list[object] = [inicio, fin_exclusivo]
    filtro = _filtro_producto("h.producto_id", producto_id, parametros) + _filtro_categorias(
        "p.categoria_id", categoria_ids, parametros
    )
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            f"""
            WITH eventos AS (
                SELECT h.id, h.producto_id, h.campo, h.precio_anterior_centavos, h.precio_nuevo_centavos
                FROM historial_precios h JOIN productos p ON p.id = h.producto_id
                WHERE h.fecha >= ? AND h.fecha < ?{filtro}
            ),
            limites AS (
                SELECT producto_id, campo, MIN(id) AS primero, MAX(id) AS ultimo, COUNT(*) AS cambios
                FROM eventos GROUP BY producto_id, campo
            )
            SELECT l.producto_id, p.nombre AS producto_nombre, p.categoria_id, c.nombre AS categoria_nombre,
                   l.campo, l.cambios,
                   e1.precio_anterior_centavos AS inicial, e2.precio_nuevo_centavos AS final,
                   CASE l.campo WHEN 'VENTA' THEN p.precio_venta_centavos ELSE p.precio_costo_centavos END AS vigente
            FROM limites l
            JOIN eventos e1 ON e1.id = l.primero
            JOIN eventos e2 ON e2.id = l.ultimo
            JOIN productos p ON p.id = l.producto_id
            LEFT JOIN categorias c ON c.id = p.categoria_id
            ORDER BY p.nombre COLLATE NOCASE, l.producto_id, l.campo
            """,
            parametros,
        ).fetchall()
    return [
        EvolucionPrecioProducto(
            producto_id=fila["producto_id"],
            producto_nombre=fila["producto_nombre"],
            categoria_id=fila["categoria_id"],
            categoria_nombre=fila["categoria_nombre"],
            campo=fila["campo"],
            cambios=fila["cambios"],
            inicial_centavos=fila["inicial"],
            final_centavos=fila["final"],
            vigente_centavos=fila["vigente"],
        )
        for fila in filas
    ]


def cambios_de_precio_por_mes(
    inicio: str, fin_exclusivo: str, producto_id: int | None = None, categoria_ids: Iterable[int] | None = None
) -> list[tuple[str, str, int, int]]:
    """`(AAAA-MM, campo, subas, bajas)` de los cambios de precio del período, cronológico."""
    parametros: list[object] = [inicio, fin_exclusivo]
    filtro = _filtro_producto("h.producto_id", producto_id, parametros) + _filtro_categorias(
        "p.categoria_id", categoria_ids, parametros
    )
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            f"""
            SELECT substr(h.fecha, 1, 7) AS mes, h.campo,
                   SUM(h.precio_nuevo_centavos > h.precio_anterior_centavos) AS subas,
                   SUM(h.precio_nuevo_centavos < h.precio_anterior_centavos) AS bajas
            FROM historial_precios h JOIN productos p ON p.id = h.producto_id
            WHERE h.fecha >= ? AND h.fecha < ?{filtro}
            GROUP BY substr(h.fecha, 1, 7), h.campo
            ORDER BY mes, h.campo
            """,
            parametros,
        ).fetchall()
    return [(fila["mes"], fila["campo"], fila["subas"], fila["bajas"]) for fila in filas]


def listar_cambios_de_precio(
    inicio: str,
    fin_exclusivo: str,
    producto_id: int | None = None,
    categoria_ids: Iterable[int] | None = None,
    limite: int = 200,
) -> list[CambioPrecioListado]:
    """Últimos cambios de precio del período (más recientes primero), con producto, usuario y origen."""
    parametros: list[object] = [inicio, fin_exclusivo]
    filtro = _filtro_producto("h.producto_id", producto_id, parametros) + _filtro_categorias(
        "p.categoria_id", categoria_ids, parametros
    )
    parametros.append(limite)
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            f"""
            SELECT h.id, h.fecha, h.producto_id, p.nombre AS producto_nombre, h.campo,
                   h.precio_anterior_centavos, h.precio_nuevo_centavos, h.origen,
                   u.nombre_completo AS usuario_nombre_completo
            FROM historial_precios h
            JOIN productos p ON p.id = h.producto_id
            LEFT JOIN usuarios u ON u.id = h.usuario_id
            WHERE h.fecha >= ? AND h.fecha < ?{filtro}
            ORDER BY h.id DESC
            LIMIT ?
            """,
            parametros,
        ).fetchall()
    return [
        CambioPrecioListado(
            id=fila["id"],
            fecha=fila["fecha"],
            producto_id=fila["producto_id"],
            producto_nombre=fila["producto_nombre"],
            campo=fila["campo"],
            precio_anterior_centavos=fila["precio_anterior_centavos"],
            precio_nuevo_centavos=fila["precio_nuevo_centavos"],
            origen=fila["origen"],
            usuario_nombre_completo=fila["usuario_nombre_completo"],
        )
        for fila in filas
    ]


# --- Deuda -------------------------------------------------------------------------------------------


def obligaciones_pendientes_de_clientes() -> list[ObligacionPendiente]:
    """Toda obligación de cliente con saldo pendiente, con los datos del cliente (activo o no)."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            """
            SELECT o.cliente_id, c.nombre, c.activo, o.saldo_pendiente_centavos, o.fecha_origen
            FROM obligaciones_cliente o JOIN clientes c ON c.id = o.cliente_id
            WHERE o.saldo_pendiente_centavos > 0
            ORDER BY o.cliente_id, o.fecha_origen, o.id
            """
        ).fetchall()
    return [
        ObligacionPendiente(
            cliente_id=fila["cliente_id"],
            cliente_nombre=fila["nombre"],
            cliente_activo=bool(fila["activo"]),
            saldo_pendiente_centavos=fila["saldo_pendiente_centavos"],
            fecha_origen=fila["fecha_origen"],
        )
        for fila in filas
    ]


def strikes_y_cierres_de_credito() -> tuple[dict[int, int], set[int]]:
    """Strikes registrados por cliente y clientes con un cierre de crédito abierto (sin reapertura)."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            """
            SELECT cliente_id, SUM(fecha_strike IS NOT NULL) AS strikes,
                   SUM(fecha_reapertura IS NULL) AS abiertos
            FROM episodios_credito GROUP BY cliente_id
            """
        ).fetchall()
    strikes = {fila["cliente_id"]: fila["strikes"] for fila in filas if fila["strikes"]}
    cerrados = {fila["cliente_id"] for fila in filas if fila["abiertos"]}
    return strikes, cerrados


def movimientos_cuenta_por_mes() -> list[tuple[str, int, int]]:
    """`(AAAA-MM, cargos, cobros)` de la cuenta corriente de clientes, cronológico (toda la historia)."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            """
            SELECT substr(fecha, 1, 7) AS mes,
                   SUM(CASE WHEN tipo = 'CARGO' THEN monto_centavos ELSE 0 END) AS cargos,
                   SUM(CASE WHEN tipo = 'COBRO' THEN monto_centavos ELSE 0 END) AS cobros
            FROM movimientos_cuenta GROUP BY substr(fecha, 1, 7) ORDER BY mes
            """
        ).fetchall()
    return [(fila["mes"], fila["cargos"], fila["cobros"]) for fila in filas]


def movimientos_proveedor_por_mes() -> list[tuple[str, int, int]]:
    """`(AAAA-MM, cargos, pagos_y_reversas)` del libro de proveedores, cronológico (toda la historia)."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            """
            SELECT substr(fecha, 1, 7) AS mes,
                   SUM(CASE WHEN tipo = 'CARGO_COMPRA' THEN monto_centavos ELSE 0 END) AS cargos,
                   SUM(CASE WHEN tipo IN ('PAGO', 'REVERSA_COMPRA') THEN monto_centavos ELSE 0 END) AS pagos
            FROM movimientos_proveedor GROUP BY substr(fecha, 1, 7) ORDER BY mes
            """
        ).fetchall()
    return [(fila["mes"], fila["cargos"], fila["pagos"]) for fila in filas]


# --- Proveedores -------------------------------------------------------------------------------------


def costos_por_producto_y_proveedor(inicio: str, fin_exclusivo: str) -> list[CostoProductoProveedor]:
    """Primer y último costo unitario pagado a cada proveedor por cada producto (compras activas del período)."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            """
            WITH lineas AS (
                SELECT d.id, c.proveedor_id, d.producto_id, d.costo_unitario_centavos
                FROM compras c JOIN detalle_compra d ON d.compra_id = c.id
                WHERE c.estado = 'ACTIVA' AND c.fecha >= ? AND c.fecha < ?
            ),
            limites AS (
                SELECT proveedor_id, producto_id, MIN(id) AS primera, MAX(id) AS ultima, COUNT(*) AS compras
                FROM lineas GROUP BY proveedor_id, producto_id
            )
            SELECT l.proveedor_id, l.producto_id, p.nombre AS producto_nombre, l.compras,
                   a.costo_unitario_centavos AS inicial, b.costo_unitario_centavos AS final
            FROM limites l
            JOIN lineas a ON a.id = l.primera
            JOIN lineas b ON b.id = l.ultima
            JOIN productos p ON p.id = l.producto_id
            ORDER BY l.proveedor_id, p.nombre COLLATE NOCASE, l.producto_id
            """,
            (inicio, fin_exclusivo),
        ).fetchall()
    return [
        CostoProductoProveedor(
            proveedor_id=fila["proveedor_id"],
            producto_id=fila["producto_id"],
            producto_nombre=fila["producto_nombre"],
            compras=fila["compras"],
            costo_inicial_centavos=fila["inicial"],
            costo_final_centavos=fila["final"],
        )
        for fila in filas
    ]


def compras_por_proveedor_y_categoria(inicio: str, fin_exclusivo: str) -> list[CompraCategoriaProveedor]:
    """Importe comprado a cada proveedor por categoría del producto (compras activas del período)."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            """
            SELECT c.proveedor_id, p.categoria_id, cat.nombre AS categoria_nombre, SUM(d.subtotal_centavos) AS total
            FROM compras c
            JOIN detalle_compra d ON d.compra_id = c.id
            JOIN productos p ON p.id = d.producto_id
            LEFT JOIN categorias cat ON cat.id = p.categoria_id
            WHERE c.estado = 'ACTIVA' AND c.fecha >= ? AND c.fecha < ?
            GROUP BY c.proveedor_id, p.categoria_id
            """,
            (inicio, fin_exclusivo),
        ).fetchall()
    return [
        CompraCategoriaProveedor(fila["proveedor_id"], fila["categoria_id"], fila["categoria_nombre"], fila["total"])
        for fila in filas
    ]


def contadores_de_proveedores() -> dict[int, tuple[int, int, int]]:
    """Por proveedor: `(evaluaciones, reclamos_total, saldo_centavos)`; solo los que tienen algo."""
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            """
            SELECT p.id,
                   (SELECT COUNT(*) FROM evaluaciones_proveedor e WHERE e.proveedor_id = p.id) AS evaluaciones,
                   (SELECT COUNT(*) FROM reclamos_proveedor r WHERE r.proveedor_id = p.id) AS reclamos,
                   (SELECT COALESCE(SUM(CASE WHEN tipo = 'CARGO_COMPRA' THEN monto_centavos
                                             ELSE -monto_centavos END), 0)
                    FROM movimientos_proveedor m WHERE m.proveedor_id = p.id) AS saldo
            FROM proveedores p
            """
        ).fetchall()
    return {fila["id"]: (fila["evaluaciones"], fila["reclamos"], fila["saldo"]) for fila in filas}
