"""Lectura de los movimientos de stock de un producto (kardex, V1.7-C).

No hay tabla de movimientos: se arman con UNION ALL de las fuentes reales que cambian `productos.stock_actual`,
todas filtradas por producto desde SQL:

    * ventas (salida en `ventas.fecha`) y su anulación (entrada en `fecha_anulacion`);
    * compras (entrada en `compras.fecha`) y su anulación (salida en `fecha_anulacion`);
    * `ajustes_stock` (delta real): el ajuste de un inventario físico ya es un ajuste de motivo `RECUENTO`,
      así que `inventarios`/`inventario_lineas` NO son fuente (sumarlos duplicaría el movimiento).

Orden total y estable: `(fecha, prioridad, fuente_id)`. Como las fechas tienen resolución de segundos y pueden
empatar, `prioridad` pone primero las operaciones originales (compra, venta, ajuste) y después las
reversiones (anulación de compra, anulación de venta): una reversión nunca queda antes de su original.
`fuente_id` (id de la venta, compra o ajuste) desempata dentro de un mismo tipo.
"""

from dataclasses import dataclass

from db.conexion import obtener_conexion
from db.repositorios import productos as repositorio_productos
from domain.movimiento_stock import (
    TIPO_AJUSTE,
    TIPO_ANULACION_COMPRA,
    TIPO_ANULACION_VENTA,
    TIPO_COMPRA,
    TIPO_RECUENTO,
    TIPO_VENTA,
)
from domain.producto import Producto

# Las ramas están escritas a propósito en un orden distinto al de `prioridad`: el orden final lo define el
# `ORDER BY`, nunca el orden casual del UNION.
_MOVIMIENTOS = f"""
    SELECT v.fecha_anulacion AS fecha, '{TIPO_ANULACION_VENTA}' AS tipo, 5 AS prioridad, v.id AS fuente_id,
           SUM(d.cantidad) AS cantidad, NULL AS saldo_ancla, v.motivo_anulacion AS detalle,
           NULL AS inventario_id
    FROM detalle_venta d JOIN ventas v ON v.id = d.venta_id
    WHERE d.producto_id = :producto_id AND v.estado = 'ANULADA'
    GROUP BY v.id
  UNION ALL
    SELECT c.fecha_anulacion, '{TIPO_ANULACION_COMPRA}', 4, c.id, -SUM(d.cantidad), NULL, c.motivo_anulacion, NULL
    FROM detalle_compra d JOIN compras c ON c.id = d.compra_id
    WHERE d.producto_id = :producto_id AND c.estado = 'ANULADA'
    GROUP BY c.id
  UNION ALL
    SELECT a.fecha,
           CASE WHEN a.motivo = 'RECUENTO' THEN '{TIPO_RECUENTO}' ELSE '{TIPO_AJUSTE}' END,
           3, a.id, a.delta, a.stock_resultante,
           a.motivo || CASE WHEN a.observaciones IS NOT NULL THEN ': ' || a.observaciones ELSE '' END,
           a.inventario_id
    FROM ajustes_stock a
    WHERE a.producto_id = :producto_id
  UNION ALL
    SELECT v.fecha, '{TIPO_VENTA}', 2, v.id, -SUM(d.cantidad), NULL, v.tipo_pago, NULL
    FROM detalle_venta d JOIN ventas v ON v.id = d.venta_id
    WHERE d.producto_id = :producto_id
    GROUP BY v.id
  UNION ALL
    SELECT c.fecha, '{TIPO_COMPRA}', 1, c.id, SUM(d.cantidad), NULL, p.nombre, NULL
    FROM detalle_compra d JOIN compras c ON c.id = d.compra_id JOIN proveedores p ON p.id = c.proveedor_id
    WHERE d.producto_id = :producto_id
    GROUP BY c.id
"""


@dataclass(frozen=True)
class FilaMovimiento:
    fecha: str
    tipo: str
    fuente_id: int
    cantidad: int
    saldo_ancla: int | None
    detalle: str | None
    inventario_id: int | None


@dataclass(frozen=True)
class LecturaKardex:
    """`suma_desde_inicio`: suma con signo de TODOS los movimientos desde `desde_inicio` en adelante (también
    los posteriores al fin del período), lo que permite reconstruir el saldo inicial desde `stock_actual`."""

    producto: Producto
    suma_desde_inicio: int
    movimientos: list[FilaMovimiento]


def leer_movimientos(
    producto_id: int, desde_inicio: str | None, hasta_exclusivo: str | None
) -> LecturaKardex | None:
    """Producto (activo o no), suma de movimientos desde el inicio y movimientos del período
    `[desde_inicio, hasta_exclusivo)` ordenados; `None` en un límite lo deja abierto. `None` si el producto no
    existe. Todo se lee dentro de una misma transacción, así el stock y los movimientos son coherentes."""
    parametros = {"producto_id": producto_id, "inicio": desde_inicio, "fin": hasta_exclusivo}
    with obtener_conexion() as conexion:
        conexion.execute("BEGIN")
        producto = repositorio_productos.obtener_por_id_en_conexion_incluyendo_inactivos(conexion, producto_id)
        if producto is None:
            return None
        suma = conexion.execute(
            f"SELECT COALESCE(SUM(cantidad), 0) FROM ({_MOVIMIENTOS}) WHERE (:inicio IS NULL OR fecha >= :inicio)",
            parametros,
        ).fetchone()[0]
        filas = conexion.execute(
            f"""
            SELECT fecha, tipo, fuente_id, cantidad, saldo_ancla, detalle, inventario_id
            FROM ({_MOVIMIENTOS})
            WHERE (:inicio IS NULL OR fecha >= :inicio) AND (:fin IS NULL OR fecha < :fin)
            ORDER BY fecha, prioridad, fuente_id
            """,
            parametros,
        ).fetchall()
    return LecturaKardex(
        producto=producto,
        suma_desde_inicio=suma,
        movimientos=[
            FilaMovimiento(
                fecha=fila["fecha"],
                tipo=fila["tipo"],
                fuente_id=fila["fuente_id"],
                cantidad=fila["cantidad"],
                saldo_ancla=fila["saldo_ancla"],
                detalle=fila["detalle"],
                inventario_id=fila["inventario_id"],
            )
            for fila in filas
        ],
    )
