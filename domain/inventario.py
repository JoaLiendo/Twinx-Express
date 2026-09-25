"""Inventario físico (migración 022, V1.4): entidades y reglas puras.

Un inventario compara lo CONTADO físicamente contra el stock que el sistema tenía EN EL MOMENTO
DEL CONTEO (`stock_esperado`, con la `version_esperada` de `productos.version_stock`). No es
"poner el stock actual en el número contado": si el stock cambió después de contar, el inventario
no se confirma (ver `services.servicio_inventario.confirmar_inventario`).
"""

from dataclasses import dataclass

from excepciones import DatosInvalidosError

ESTADO_ABIERTO = "ABIERTO"
ESTADO_CONFIRMADO = "CONFIRMADO"
ESTADO_CANCELADO = "CANCELADO"


def validar_cantidad_contada(cantidad: object) -> int:
    """Una cantidad contada es un entero no negativo (nunca `bool`, decimales ni texto).

    Raises:
        DatosInvalidosError: si no lo es.
    """
    if isinstance(cantidad, bool) or not isinstance(cantidad, int):
        raise DatosInvalidosError("La cantidad contada debe ser un número entero.")
    if cantidad < 0:
        raise DatosInvalidosError("La cantidad contada no puede ser negativa.")
    return cantidad


@dataclass(frozen=True)
class Inventario:
    id: int
    estado: str
    usuario_id: int
    fecha_inicio: str
    fecha_cierre: str | None
    usuario_cierre_id: int | None
    observaciones: str | None

    @property
    def abierto(self) -> bool:
        return self.estado == ESTADO_ABIERTO


@dataclass(frozen=True)
class ResumenInventario:
    """Un inventario para el historial: quién lo inició y cuánto avanzó."""

    inventario: Inventario
    usuario_nombre_completo: str
    cantidad_lineas: int
    cantidad_contadas: int
    cantidad_ajustes: int


@dataclass(frozen=True)
class LineaConteo:
    """Una línea vista por quien cuenta: NUNCA incluye el stock esperado ni la diferencia (conteo a ciegas)."""

    producto_id: int
    producto_nombre: str
    producto_codigo_barras: str
    producto_activo: bool
    cantidad_contada: int | None
    usuario_conteo_nombre: str | None
    fecha_conteo: str | None

    @property
    def contada(self) -> bool:
        return self.cantidad_contada is not None


@dataclass(frozen=True)
class LineaInventario:
    """Una línea completa (solo OWNER): esperado, contado, diferencia, costo y ajuste asociado.

    `desactualizada` indica, para un inventario abierto, que el stock o la versión actual del
    producto ya no coinciden con los del conteo: hay que recontarlo antes de confirmar.
    """

    producto_id: int
    producto_nombre: str
    producto_codigo_barras: str
    producto_activo: bool
    stock_esperado: int | None
    cantidad_contada: int | None
    costo_unitario_centavos: int | None
    ajuste_id: int | None
    usuario_conteo_nombre: str | None
    fecha_conteo: str | None
    desactualizada: bool = False

    @property
    def contada(self) -> bool:
        return self.cantidad_contada is not None

    @property
    def diferencia(self) -> int | None:
        if self.cantidad_contada is None or self.stock_esperado is None:
            return None
        return self.cantidad_contada - self.stock_esperado

    @property
    def diferencia_valorizada_centavos(self) -> int | None:
        if self.diferencia is None or self.costo_unitario_centavos is None:
            return None
        return self.diferencia * self.costo_unitario_centavos


@dataclass(frozen=True)
class DetalleInventario:
    inventario: Inventario
    usuario_nombre_completo: str
    lineas: list[LineaInventario]


@dataclass(frozen=True)
class ResultadoConfirmacion:
    """Qué hizo la confirmación: ajustes RECUENTO generados, líneas sin diferencia y líneas sin contar."""

    inventario: Inventario
    ajustes_generados: int
    lineas_sin_diferencia: int
    lineas_sin_contar: int
