"""Lectura de cuentas a pagar para el reporte de deuda a proveedores (V1.10-D).

Solo lectura. Trae, en una única transacción, el libro (`movimientos_proveedor`) y las compras a crédito
de los proveedores con movimientos, para que el replay FIFO de `domain.cuentas_a_pagar` nunca mezcle un
libro anterior a un pago con compras posteriores (o al revés).
"""

from collections import defaultdict
from datetime import date

from db.conexion import obtener_conexion
from domain.cuentas_a_pagar import CompraParaVencimiento, MovimientoLibro
from domain.deuda_proveedores import LibroDeProveedor

_SALDO = (
    "COALESCE(SUM(CASE m.tipo"
    " WHEN 'CARGO_COMPRA' THEN m.monto_centavos"
    " WHEN 'PAGO' THEN -m.monto_centavos"
    " WHEN 'REVERSA_COMPRA' THEN -m.monto_centavos END), 0)"
)


def leer_libros_de_proveedores(proveedor_id: int | None = None) -> list[LibroDeProveedor]:
    """Libro, compras a crédito, última compra activa y último pago de cada proveedor con movimientos
    (o solo del pedido), ordenados por nombre. Todo sale de una misma transacción de lectura (`BEGIN`
    explícito: sin él, cada `SELECT` vería un estado distinto si otra escritura se confirma en el medio).

    La última compra es la ACTIVA de mayor `id` (cualquier condición de pago); el último pago es el `PAGO`
    de mayor `id`. Ambas fechas se devuelven como `AAAA-MM-DD`. No interpreta la consistencia del libro."""
    with obtener_conexion() as conexion:
        conexion.execute("BEGIN")
        proveedores = conexion.execute(
            f"""
            SELECT p.id, p.nombre, p.activo, {_SALDO} AS saldo
            FROM proveedores p JOIN movimientos_proveedor m ON m.proveedor_id = p.id
            WHERE (? IS NULL OR p.id = ?)
            GROUP BY p.id ORDER BY p.nombre COLLATE NOCASE, p.id
            """,
            (proveedor_id, proveedor_id),
        ).fetchall()
        movimientos: dict[int, list[MovimientoLibro]] = defaultdict(list)
        for fila in conexion.execute(
            "SELECT id, proveedor_id, tipo, compra_id, monto_centavos FROM movimientos_proveedor"
            " WHERE (? IS NULL OR proveedor_id = ?) ORDER BY id",
            (proveedor_id, proveedor_id),
        ):
            movimientos[fila["proveedor_id"]].append(
                MovimientoLibro(fila["id"], fila["tipo"], fila["compra_id"], fila["monto_centavos"])
            )
        compras: dict[int, list[CompraParaVencimiento]] = defaultdict(list)
        fechas: dict[int, dict[int, str]] = defaultdict(dict)
        for fila in conexion.execute(
            "SELECT id, proveedor_id, estado, total_centavos, fecha_vencimiento, substr(fecha, 1, 10) AS dia FROM compras"
            " WHERE condicion_pago = 'CREDITO' AND (? IS NULL OR proveedor_id = ?) ORDER BY id",
            (proveedor_id, proveedor_id),
        ):
            vencimiento = date.fromisoformat(fila["fecha_vencimiento"]) if fila["fecha_vencimiento"] else None
            compras[fila["proveedor_id"]].append(
                CompraParaVencimiento(fila["id"], vencimiento, fila["estado"], fila["total_centavos"])
            )
            fechas[fila["proveedor_id"]][fila["id"]] = fila["dia"]
        ultimas_compras = dict(conexion.execute(
            "SELECT proveedor_id, substr(fecha, 1, 10) FROM compras WHERE id IN"
            " (SELECT MAX(id) FROM compras WHERE estado = 'ACTIVA' AND (? IS NULL OR proveedor_id = ?)"
            " GROUP BY proveedor_id)",
            (proveedor_id, proveedor_id),
        ).fetchall())
        ultimos_pagos = dict(conexion.execute(
            "SELECT proveedor_id, substr(fecha, 1, 10) FROM movimientos_proveedor WHERE id IN"
            " (SELECT MAX(id) FROM movimientos_proveedor WHERE tipo = 'PAGO' AND (? IS NULL OR proveedor_id = ?)"
            " GROUP BY proveedor_id)",
            (proveedor_id, proveedor_id),
        ).fetchall())
    return [
        LibroDeProveedor(
            proveedor_id=fila["id"],
            nombre=fila["nombre"],
            activo=bool(fila["activo"]),
            saldo_centavos=fila["saldo"],
            movimientos=tuple(movimientos[fila["id"]]),
            compras=tuple(compras[fila["id"]]),
            ultima_compra=ultimas_compras.get(fila["id"]),
            ultimo_pago=ultimos_pagos.get(fila["id"]),
            fechas_compras=fechas[fila["id"]],
        )
        for fila in proveedores
    ]
