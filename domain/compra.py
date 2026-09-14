"""Entidad Compra (ingreso de mercadería) y su detalle (Fase 4B).

`ItemCompra` representa una línea de compra antes de confirmarse (qué
producto, cantidad y costo unitario pedidos): el subtotal y el total
siempre los calcula el servidor (`services.servicio_compras.registrar_compra`),
nunca se confía en un valor recibido del cliente. `Compra` representa
una compra ya confirmada y persistida (cabecera); `DetalleCompra`, una
línea ya persistida de esa compra.
"""

from dataclasses import dataclass

from excepciones import DatosInvalidosError


@dataclass
class ItemCompra:
    """Una línea de compra: producto, cantidad y costo unitario pedidos."""

    producto_id: int
    cantidad: int
    costo_unitario_centavos: int

    def __post_init__(self) -> None:
        if self.cantidad <= 0:
            raise DatosInvalidosError("La cantidad de un ítem de compra debe ser mayor a cero.")
        if self.costo_unitario_centavos < 0:
            raise DatosInvalidosError("El costo unitario no puede ser negativo.")


@dataclass
class Compra:
    """Una compra (ingreso de mercadería) ya confirmada y persistida.

    Es inmutable en esta fase: no hay edición, anulación ni reversión
    (ver `services.servicio_compras`). `total_centavos` es siempre la
    suma de los subtotales de su detalle, calculada por el servidor.
    """

    id: int
    proveedor_id: int
    usuario_id: int
    fecha: str
    total_centavos: int
    observaciones: str | None = None


@dataclass
class DetalleCompra:
    """Una línea ya persistida de una compra."""

    id: int
    compra_id: int
    producto_id: int
    cantidad: int
    costo_unitario_centavos: int
    subtotal_centavos: int


@dataclass
class ResumenCompra:
    """Vista de una compra para el historial y el encabezado de detalle
    (Fase 4C): la cabecera ya resuelta con el nombre de su proveedor, el
    nombre completo de quien la registró, y su cantidad de líneas.

    No es una entidad con reglas de negocio propias -- es una
    composición de solo lectura armada con un único `JOIN` (ver
    `db.repositorios.compras.listar_resumen`), justamente para no
    resolver esos nombres con una consulta aparte por cada compra.
    """

    id: int
    fecha: str
    proveedor_nombre: str
    usuario_nombre_completo: str
    cantidad_lineas: int
    total_centavos: int
    observaciones: str | None = None


@dataclass
class LineaDetalleCompra:
    """Una línea de compra para la pantalla de detalle (Fase 4C), ya
    resuelta con el nombre y la unidad de medida de su producto (mismo
    criterio de solo lectura que `ResumenCompra`: un único `JOIN`, ver
    `db.repositorios.compras.listar_detalle_con_producto`)."""

    id: int
    compra_id: int
    producto_id: int
    producto_nombre: str
    producto_unidad_medida: str
    cantidad: int
    costo_unitario_centavos: int
    subtotal_centavos: int
