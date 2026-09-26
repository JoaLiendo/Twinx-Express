"""Kardex de un producto (V1.7-C): por qué tiene el stock que tiene.

Solo lectura. Arma la lista de movimientos con los eventos reales (ver `db.repositorios.movimientos_stock`) y
reconstruye los saldos hacia atrás desde `productos.stock_actual`: el sistema no guarda un stock inicial
histórico, así que el saldo inicial siempre es RECONSTRUIDO, nunca un dato persistido ni un cero supuesto.
"""

import re
from datetime import date, timedelta

from db.repositorios import movimientos_stock as repositorio_movimientos
from domain.movimiento_stock import (
    TIPO_ANULACION_COMPRA,
    TIPO_ANULACION_VENTA,
    TIPO_COMPRA,
    TIPO_RECUENTO,
    TIPO_VENTA,
    Kardex,
    MovimientoStock,
)
from excepciones import DatosInvalidosError, ProductoNoEncontradoError

_FECHA_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
_MENSAJE_FECHAS = "Las fechas del kardex deben tener el formato AAAA-MM-DD y ser válidas."


def _fecha_valida(texto: str) -> date:
    try:
        if not _FECHA_ISO.fullmatch(texto):
            raise ValueError
        return date.fromisoformat(texto)
    except ValueError:
        raise DatosInvalidosError(_MENSAJE_FECHAS) from None


def _limites(fecha_desde: str | None, fecha_hasta: str | None) -> tuple[str | None, str | None]:
    """`(inicio, fin_exclusivo)` como texto comparable con las columnas de fecha/hora (sin aplicar `date()` a
    la columna); `None` deja el extremo abierto."""
    desde = _fecha_valida(fecha_desde) if fecha_desde else None
    hasta = _fecha_valida(fecha_hasta) if fecha_hasta else None
    if desde and hasta and desde > hasta:
        raise DatosInvalidosError("La fecha desde no puede ser posterior a la fecha hasta.")
    try:
        fin_exclusivo = (hasta + timedelta(days=1)).isoformat() if hasta else None
    except OverflowError:
        raise DatosInvalidosError(_MENSAJE_FECHAS) from None
    return (desde.isoformat() if desde else None), fin_exclusivo


def _referencia(tipo: str, fuente_id: int, inventario_id: int | None) -> str:
    if tipo in (TIPO_VENTA, TIPO_ANULACION_VENTA):
        return f"Venta #{fuente_id}"
    if tipo in (TIPO_COMPRA, TIPO_ANULACION_COMPRA):
        return f"Compra #{fuente_id}"
    if tipo == TIPO_RECUENTO and inventario_id is not None:
        return f"Inventario #{inventario_id}"
    return f"Ajuste #{fuente_id}"


def _descripcion(tipo: str, detalle: str | None) -> str:
    if tipo == TIPO_VENTA:
        return f"Venta ({detalle})" if detalle else "Venta"
    if tipo == TIPO_ANULACION_VENTA:
        return f"Reversión por anulación de venta ({detalle})" if detalle else "Reversión por anulación de venta"
    if tipo == TIPO_COMPRA:
        return f"Compra a {detalle}" if detalle else "Compra"
    if tipo == TIPO_ANULACION_COMPRA:
        return f"Reversión por anulación de compra ({detalle})" if detalle else "Reversión por anulación de compra"
    return detalle or "Ajuste de stock"


def generar_kardex(producto_id: int, fecha_desde: str | None = None, fecha_hasta: str | None = None) -> Kardex:
    """Movimientos de stock del producto (activo o inactivo) en el período pedido; sin fechas, toda su historia.

    `saldo_inicial` = `stock_actual` menos todos los movimientos desde el inicio del período (también los
    posteriores a su fin), así los saldos del período son coherentes con el stock real. En los ajustes, el
    `stock_resultante` que ellos mismos guardaron se contrasta con el saldo reconstruido (`saldo_verificado`).

    Raises:
        ProductoNoEncontradoError: si el producto no existe.
        DatosInvalidosError: si una fecha no es `AAAA-MM-DD` válida o `desde` es posterior a `hasta`.
    """
    inicio, fin_exclusivo = _limites(fecha_desde, fecha_hasta)
    lectura = repositorio_movimientos.leer_movimientos(producto_id, inicio, fin_exclusivo)
    if lectura is None:
        raise ProductoNoEncontradoError(f"No existe un producto con id {producto_id}.")

    saldo = lectura.producto.stock_actual - lectura.suma_desde_inicio
    saldo_inicial = saldo
    movimientos = []
    for fila in lectura.movimientos:
        saldo += fila.cantidad
        movimientos.append(
            MovimientoStock(
                fecha=fila.fecha,
                tipo=fila.tipo,
                referencia=_referencia(fila.tipo, fila.fuente_id, fila.inventario_id),
                cantidad=fila.cantidad,
                saldo=saldo,
                descripcion=_descripcion(fila.tipo, fila.detalle),
                saldo_verificado=None if fila.saldo_ancla is None else fila.saldo_ancla == saldo,
            )
        )
    producto = lectura.producto
    return Kardex(
        producto_id=producto.id,
        codigo_barras=producto.codigo_barras,
        nombre=producto.nombre,
        activo=producto.activo,
        stock_actual=producto.stock_actual,
        fecha_desde=fecha_desde or None,
        fecha_hasta=fecha_hasta or None,
        saldo_inicial=saldo_inicial,
        saldo_final=saldo,
        movimientos=movimientos,
    )
