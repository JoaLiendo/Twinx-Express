"""Entidad y reglas de negocio de los ajustes manuales de stock.

Un ajuste corrige `productos.stock_actual` para reflejar la realidad
física del kiosco (merma, rotura, vencimiento, pérdida, robo o una
diferencia de recuento) sin pasar por una venta ni una compra, dejando
un registro histórico permanente del motivo, el delta y quién lo hizo
(ver `services.servicio_stock.ajustar_stock`).
"""

from dataclasses import dataclass

from excepciones import DatosInvalidosError

MOTIVOS_AJUSTE_VALIDOS = frozenset(
    {"MERMA", "ROTURA", "VENCIMIENTO", "PERDIDA", "ROBO", "RECUENTO", "OTRO"}
)

# `OTRO` es el único motivo que no describe por sí mismo qué pasó -- por
# eso exige una justificación, mismo criterio que ya usa
# `domain.caja.TIPOS_QUE_REQUIEREN_DESCRIPCION` para INGRESO/EGRESO.
MOTIVOS_QUE_REQUIEREN_OBSERVACIONES = frozenset({"OTRO"})


@dataclass
class AjusteStock:
    """Un ajuste manual de stock, validado al construirse.

    `stock_anterior`/`stock_resultante` no se recalculan acá: se validan
    por consistencia contra `delta` (nunca se confía en un valor que
    venga ya resuelto de otra capa). `delta` nunca puede ser cero: un
    ajuste que no cambia nada no es un ajuste, es ruido de auditoría.
    """

    producto_id: int
    usuario_id: int
    motivo: str
    delta: int
    stock_anterior: int
    stock_resultante: int
    observaciones: str | None = None
    id: int | None = None
    fecha: str | None = None
    inventario_id: int | None = None  # 022: inventario físico que originó el ajuste (RECUENTO)

    def __post_init__(self) -> None:
        self._validar()

    def _validar(self) -> None:
        if self.motivo not in MOTIVOS_AJUSTE_VALIDOS:
            raise DatosInvalidosError(
                f"Motivo de ajuste de stock inválido: {self.motivo!r}. "
                f"Debe ser uno de {sorted(MOTIVOS_AJUSTE_VALIDOS)}."
            )
        if self.delta == 0:
            raise DatosInvalidosError("El delta de un ajuste de stock no puede ser cero.")
        if self.stock_anterior < 0:
            raise DatosInvalidosError("El stock anterior de un ajuste no puede ser negativo.")
        if self.stock_resultante < 0:
            raise DatosInvalidosError("El stock resultante de un ajuste no puede ser negativo.")
        if self.stock_resultante != self.stock_anterior + self.delta:
            raise DatosInvalidosError(
                "El stock resultante no coincide con stock_anterior + delta "
                f"({self.stock_anterior} + {self.delta} != {self.stock_resultante})."
            )
        if self.motivo in MOTIVOS_QUE_REQUIEREN_OBSERVACIONES and not (
            self.observaciones and self.observaciones.strip()
        ):
            raise DatosInvalidosError(
                f"Los ajustes de motivo {self.motivo} requieren una observación que los justifique."
            )


@dataclass
class AjusteStockConUsuario:
    """Un ajuste ya persistido, con el nombre completo de su usuario ya
    resuelto (historial de un producto, ver
    `db.repositorios.ajustes_stock.listar_por_producto`).

    No es una entidad con reglas de negocio propias -- es una
    composición de solo lectura armada con un único `JOIN`, mismo
    criterio que `domain.compra.ResumenCompra`/`domain.venta.ResumenVenta`.
    Incluye usuarios desactivados (nunca se borran físicamente): el
    nombre sigue resolviéndose sin importar el estado actual del usuario.
    """

    id: int
    producto_id: int
    usuario_id: int
    usuario_nombre_completo: str
    motivo: str
    delta: int
    stock_anterior: int
    stock_resultante: int
    observaciones: str | None
    fecha: str
