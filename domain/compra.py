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

    Sus líneas no se editan: solo puede pasar de `ACTIVA` a `ANULADA`
    (ver `services.servicio_compras.anular_compra`). `total_centavos` es
    siempre la suma de los subtotales de su detalle, calculada por el servidor.
    """

    id: int
    proveedor_id: int
    usuario_id: int
    fecha: str
    total_centavos: int
    observaciones: str | None = None
    estado: str = "ACTIVA"
    costo_trazable: bool = False


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
    estado: str = "ACTIVA"
    motivo_anulacion: str | None = None
    observaciones_anulacion: str | None = None
    fecha_anulacion: str | None = None


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


# --- Anulación (V1.7-B) ---------------------------------------------------------------------------

MOTIVOS_ANULACION_COMPRA_VALIDOS = frozenset({"ERROR_CARGA", "MERCADERIA_NO_RECIBIDA", "DEVOLUCION_A_PROVEEDOR", "OTRO"})
MOTIVOS_ANULACION_COMPRA_QUE_REQUIEREN_OBSERVACIONES = frozenset({"OTRO"})

# Causas estables por las que el costo se conserva al anular (quedan en la auditoría).
CAUSA_COMPRA_HISTORICA = "COMPRA_HISTORICA"
CAUSA_SIN_CAMBIO_COSTO = "SIN_CAMBIO_COSTO"
CAUSA_COMPRA_POSTERIOR = "COMPRA_POSTERIOR"
CAUSA_EVENTO_COSTO_POSTERIOR = "EVENTO_COSTO_POSTERIOR"
CAUSA_COSTO_ACTUAL_DIVERGENTE = "COSTO_ACTUAL_DIVERGENTE"


def validar_motivo_anulacion_compra(motivo: str, observaciones: str | None) -> None:
    """Valida motivo y observaciones de una anulación de compra, antes de tocar stock o costo."""
    if motivo not in MOTIVOS_ANULACION_COMPRA_VALIDOS:
        raise DatosInvalidosError(
            f"Motivo de anulación inválido: {motivo!r}. Debe ser uno de {sorted(MOTIVOS_ANULACION_COMPRA_VALIDOS)}."
        )
    if motivo in MOTIVOS_ANULACION_COMPRA_QUE_REQUIEREN_OBSERVACIONES and not (observaciones and observaciones.strip()):
        raise DatosInvalidosError(f"Las anulaciones de motivo {motivo} requieren una observación que las justifique.")


@dataclass(frozen=True)
class LineaParaAnular:
    """Línea de una compra a anular, con el evento de costo que ella misma produjo (si lo hubo)."""

    producto_id: int
    cantidad: int
    historial_precio_id: int | None
    precio_anterior_centavos: int | None
    precio_nuevo_centavos: int | None


@dataclass(frozen=True)
class DecisionCosto:
    """Qué hacer con el costo de un producto al anular una línea: `restaurar_a_centavos` no es `None`
    solo si se demostró que es reversible; si no, `causa` explica por qué se conserva."""

    producto_id: int
    restaurar_a_centavos: int | None
    causa: str | None


def decidir_costo_de_linea(
    *,
    costo_trazable: bool,
    linea: LineaParaAnular,
    ultimo_evento_costo_id: int | None,
    hay_compra_activa_posterior: bool,
    costo_vigente_centavos: int,
) -> DecisionCosto:
    """Restaura el costo anterior solo si se cumplen TODAS las condiciones demostrables; nunca infiere.

    C1: la línea tiene el evento de costo que produjo (compra con trazabilidad).
    C2: ese evento sigue siendo el último de costo del producto (orden por id, no por fecha).
    C3: no hay otra compra ACTIVA posterior (por id de cabecera) del producto, aunque haya usado el mismo
        costo y por eso no haya dejado evento.
    C4: el costo vigente sigue siendo el que fijó ese evento.
    Una compra histórica (`costo_trazable` falso) nunca restaura: no hay forma demostrable de saber qué
    evento produjo. `COMPRA_POSTERIOR` se evalúa antes que C2 por ser la causa más informativa.
    """
    if not costo_trazable:
        return DecisionCosto(linea.producto_id, None, CAUSA_COMPRA_HISTORICA)
    if linea.historial_precio_id is None:
        return DecisionCosto(linea.producto_id, None, CAUSA_SIN_CAMBIO_COSTO)
    if hay_compra_activa_posterior:
        return DecisionCosto(linea.producto_id, None, CAUSA_COMPRA_POSTERIOR)
    if ultimo_evento_costo_id != linea.historial_precio_id:
        return DecisionCosto(linea.producto_id, None, CAUSA_EVENTO_COSTO_POSTERIOR)
    if costo_vigente_centavos != linea.precio_nuevo_centavos:
        return DecisionCosto(linea.producto_id, None, CAUSA_COSTO_ACTUAL_DIVERGENTE)
    return DecisionCosto(linea.producto_id, linea.precio_anterior_centavos, None)
