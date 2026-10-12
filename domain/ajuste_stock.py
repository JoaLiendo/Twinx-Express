"""Entidad y reglas de negocio de los ajustes manuales de stock.

Un ajuste corrige `productos.stock_actual` para reflejar la realidad
física del kiosco (vencimiento, rotura, robo, falla de fábrica o una
diferencia de recuento) sin pasar por una venta ni una compra, dejando
un registro histórico permanente del motivo, el delta y quién lo hizo
(ver `services.servicio_stock.ajustar_stock`).

`MOTIVOS_AJUSTE_VALIDOS` (V1.11-A) incluye motivos legacy (`MERMA`,
`PERDIDA`, `OTRO`) que ya no se ofrecen ni se aceptan para ajustes
manuales nuevos pero que siguen siendo válidos para reconstruir filas
históricas desde la base (de ahí que esta validación -- la del
`__post_init__`, que corre tanto al crear un ajuste nuevo como al leer
uno ya persistido -- no distinga entre ambos casos). La restricción de
motivos manuales vigentes (`MOTIVOS_MANUALES_PERMITIDOS`) y la
dirección del delta según el motivo solo se validan al crear un ajuste
nuevo, con `validar_motivo_manual` (ver
`services.servicio_stock.ajustar_stock`): aplicarlas acá rompería la
lectura de ajustes ya guardados con las reglas de antes de esta
versión.
"""

from dataclasses import dataclass

from excepciones import DatosInvalidosError

MOTIVOS_AJUSTE_VALIDOS = frozenset(
    {"MERMA", "ROTURA", "VENCIMIENTO", "PERDIDA", "ROBO", "RECUENTO", "OTRO", "FALLA_FABRICA"}
)

# Único motivo manual vigente desde V1.11-A: `MERMA`/`PERDIDA`/`OTRO` quedan legacy (solo lectura
# de historial) y `RECUENTO` no es manual (lo genera automáticamente la confirmación de un
# inventario físico, ver `services.servicio_inventario.confirmar_inventario`). Los cuatro motivos
# manuales vigentes son, además, los únicos que restan stock (ver `validar_motivo_manual`): nunca
# sirven para sumar, a diferencia de un RECUENTO, que puede ir en cualquier sentido.
MOTIVOS_MANUALES_PERMITIDOS = frozenset({"VENCIMIENTO", "ROTURA", "ROBO", "FALLA_FABRICA"})

# `OTRO` es el único motivo legacy que no describía por sí mismo qué pasó -- por eso exigía una
# justificación. Ya no se ofrece para ajustes nuevos, pero una fila histórica con `OTRO` sigue
# necesitando su observación al reconstruirse (mismo criterio que ya usa
# `domain.caja.TIPOS_QUE_REQUIEREN_DESCRIPCION` para INGRESO/EGRESO).
MOTIVOS_QUE_REQUIEREN_OBSERVACIONES = frozenset({"OTRO"})


def validar_motivo_manual(motivo: str, delta: int) -> None:
    """Valida un ajuste manual NUEVO (fuera de inventario): el motivo debe ser uno de los
    vigentes (ni legacy ni `RECUENTO`, ver `MOTIVOS_MANUALES_PERMITIDOS`) y, al ser todos de
    pérdida, el delta debe restar stock.

    No se aplica al reconstruir un ajuste ya persistido (ver el docstring del módulo): solo la
    llama `services.servicio_stock.ajustar_stock` antes de crear uno.

    Raises:
        DatosInvalidosError: si el motivo no es manual vigente, o si siéndolo el delta es positivo.
    """
    if motivo not in MOTIVOS_MANUALES_PERMITIDOS:
        raise DatosInvalidosError(
            f"Motivo de ajuste manual inválido: {motivo!r}. "
            f"Debe ser uno de {sorted(MOTIVOS_MANUALES_PERMITIDOS)}."
        )
    if delta > 0:
        raise DatosInvalidosError(
            f"El motivo {motivo} es de pérdida y solo puede restar stock (delta={delta})."
        )


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
    # 027: costo vigente del producto congelado al momento del ajuste. NULL en filas históricas
    # (anteriores a esta versión): nunca se infiere ni se backfillea un costo retroactivo.
    costo_unitario_centavos: int | None = None

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
        if self.costo_unitario_centavos is not None and self.costo_unitario_centavos < 0:
            raise DatosInvalidosError("El costo unitario de un ajuste no puede ser negativo.")


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
