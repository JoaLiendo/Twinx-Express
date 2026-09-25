"""Relación producto-proveedor (migración 021, V1.4).

Dice qué proveedores le venden un producto y cuál es el principal. No guarda
costos: el costo de compra sale de `detalle_compra` y del costo vigente del
producto (ver `db.repositorios.producto_proveedor.listar_principales_con_costo`).
"""

from dataclasses import dataclass

from excepciones import DatosInvalidosError

LONGITUD_MAXIMA_CODIGO_PROVEEDOR = 60


def normalizar_codigo_proveedor(texto: str | None) -> str | None:
    """Código con el que el proveedor conoce al producto: opcional, sin espacios de más.

    Raises:
        DatosInvalidosError: si supera `LONGITUD_MAXIMA_CODIGO_PROVEEDOR`.
    """
    limpio = (texto or "").strip()
    if not limpio:
        return None
    if len(limpio) > LONGITUD_MAXIMA_CODIGO_PROVEEDOR:
        raise DatosInvalidosError(
            f"El código del proveedor no puede superar {LONGITUD_MAXIMA_CODIGO_PROVEEDOR} caracteres."
        )
    return limpio


@dataclass(frozen=True)
class VinculoProveedor:
    """Un vínculo producto-proveedor tal como está guardado."""

    id: int
    producto_id: int
    proveedor_id: int
    codigo_proveedor: str | None
    es_principal: bool
    fecha_creacion: str


@dataclass(frozen=True)
class ProductoDeProveedor:
    """Un producto vinculado a un proveedor, con lo necesario para mostrarlo en su ficha."""

    vinculo: VinculoProveedor
    producto_codigo_barras: str
    producto_nombre: str
    producto_activo: bool


@dataclass(frozen=True)
class ProveedorPrincipal:
    """Proveedor principal de un producto y el último costo con el que se le compró.

    `ultimo_costo_centavos` es el costo de la línea de compra de mayor `detalle_compra.id`
    de ese producto con ese proveedor; `None` si nunca se le compró (p. ej. vínculo manual).
    """

    producto_id: int
    proveedor_id: int
    proveedor_nombre: str
    proveedor_activo: bool
    ultimo_costo_centavos: int | None
