"""Entidad Venta y su detalle, y reglas de negocio asociadas.

`ItemVenta` representa una línea del carrito antes de confirmarse la
venta (qué producto, qué cantidad); la resolución de precios y el
descuento de stock ocurren en `services.servicio_ventas.registrar_venta`.
`Venta` representa una venta ya confirmada y persistida.
"""

import hashlib
import json
from dataclasses import dataclass

from excepciones import DatosInvalidosError

TIPOS_PAGO_VALIDOS = frozenset({"EFECTIVO", "TARJETA", "TRANSFERENCIA", "OTRO"})


@dataclass
class ItemVenta:
    """Una línea de venta: producto pedido y cantidad."""

    producto_id: int
    cantidad: int

    def __post_init__(self) -> None:
        if self.cantidad <= 0:
            raise DatosInvalidosError("La cantidad de un ítem de venta debe ser mayor a cero.")


def calcular_hash_contenido(items: list[ItemVenta], tipo_pago: str) -> str:
    """Hash canónico y determinista del contenido lógico de una venta
    (Fase 5A: idempotencia de `services.servicio_ventas.registrar_venta`).

    No incluye ningún precio: el precio nunca es parte de la autoridad
    de la venta (siempre lo resuelve el servidor desde `productos` al
    confirmar), así que no tiene sentido que participe en detectar si
    dos requests representan "el mismo pedido".

    Las líneas se agregan por `producto_id` (dos líneas del mismo
    producto que sumadas dan la misma cantidad total producen el mismo
    hash que una sola línea con esa cantidad -- mismo criterio que ya
    usa `registrar_venta` para sumar cantidades repetidas) y se
    ordenan por `producto_id`, así el resultado no depende del orden en
    que el cliente haya serializado las líneas.
    """
    cantidad_por_producto: dict[int, int] = {}
    for item in items:
        cantidad_por_producto[item.producto_id] = cantidad_por_producto.get(item.producto_id, 0) + item.cantidad

    contenido_canonico = {
        "tipo_pago": tipo_pago,
        "items": sorted(cantidad_por_producto.items()),
    }
    texto_canonico = json.dumps(contenido_canonico, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(texto_canonico.encode("utf-8")).hexdigest()


@dataclass
class Venta:
    """Una venta ya confirmada y persistida, con su total y forma de pago.

    `total_centavos` está en centavos (`int`): la suma de los
    subtotales de sus líneas, calculada con aritmética entera exacta
    (ver `services.servicio_ventas.registrar_venta`).
    """

    id: int
    fecha: str
    total_centavos: int
    tipo_pago: str


@dataclass
class LineaVenta:
    """Una línea ya vendida, con el nombre del producto para mostrarla
    (Fase 5D: ticket imprimible).

    A diferencia de `ItemVenta` (la entrada antes de confirmar la
    venta, sin precio ni nombre), esto representa un `detalle_venta`
    ya persistido: `precio_unitario_centavos`/`subtotal_centavos`
    quedaron congelados al momento de la venta y nunca se recalculan
    para mostrar un ticket. `producto_nombre` es el nombre *actual*
    del producto (vía JOIN, ver `db.repositorios.ventas`), no uno
    historizado -- un producto renombrado después de la venta va a
    aparecer con el nombre nuevo en un ticket reimpreso.
    """

    producto_nombre: str
    cantidad: int
    precio_unitario_centavos: int
    subtotal_centavos: int


@dataclass
class VentaConDetalle:
    """Una venta ya persistida junto con sus líneas -- lo que necesita
    un ticket/recibo (Fase 5D). `lineas` mantiene el mismo orden en que
    se insertaron (ver `db.repositorios.ventas.obtener_venta_con_detalle`)."""

    venta: Venta
    lineas: list[LineaVenta]


@dataclass
class ProductoMasVendido:
    """Un producto y su desempeño de ventas dentro de un período (módulo
    de Reportes): unidades vendidas y facturación que generó.

    No es una entidad con reglas de negocio propias -- es una
    composición de solo lectura armada con un único `JOIN` + `GROUP BY`
    (ver `db.repositorios.ventas.listar_productos_mas_vendidos_en_rango`),
    mismo criterio que `domain.compra.ResumenCompra`. Deliberadamente no
    incluye costo ni margen: `detalle_venta` no guarda el costo del
    producto al momento de la venta, solo su precio de venta congelado
    (ver `services.servicio_reportes` para el detalle de esta limitación).
    """

    producto_id: int
    producto_nombre: str
    unidades_vendidas: int
    total_vendido_centavos: int
