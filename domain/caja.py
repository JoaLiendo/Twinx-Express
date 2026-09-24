"""Entidad y reglas de negocio de los movimientos de caja.

Todo movimiento de caja (apertura, cierre, ingreso o egreso) se
modela como `MovimientoCaja`. El monto se expresa en **centavos**
(`int`, nunca `float`, ver `domain.dinero`) y siempre es un valor no
negativo: la dirección del dinero (si entra o sale) la determina
`tipo`, no el signo del monto.
"""

from dataclasses import dataclass

from excepciones import DatosInvalidosError

TIPOS_MOVIMIENTO_VALIDOS = frozenset({"APERTURA", "CIERRE", "INGRESO", "EGRESO"})

# Migración 020: `COBRO_CUENTA` es el INGRESO que respalda un cobro de cuenta
# corriente; el resto de los movimientos son `MANUAL`.
ORIGENES_MOVIMIENTO_VALIDOS = frozenset({"MANUAL", "COBRO_CUENTA"})

# Los movimientos manuales (a diferencia de apertura/cierre) exigen una
# descripción que los justifique, ej. "pago a proveedor" o "retiro de efectivo".
TIPOS_QUE_REQUIEREN_DESCRIPCION = frozenset({"INGRESO", "EGRESO"})


@dataclass
class MovimientoCaja:
    """Un movimiento de caja, validado al construirse.

    `diferencia_centavos` (faltante/sobrante del cierre) solo tiene
    sentido para `tipo == "CIERRE"`: es el resultado de comparar el
    efectivo contado (`monto_centavos` de esa misma fila) contra el
    efectivo esperado en ese momento (ver
    `services.servicio_caja.cerrar_caja`, que es quien lo calcula --
    este dataclass solo lo transporta y lo valida). `None` para
    cualquier otro tipo de movimiento, y también para cierres
    anteriores a que este campo existiera.

    `origen` (migración 020) es `MANUAL` salvo el INGRESO de un cobro de
    cuenta corriente (`COBRO_CUENTA`), que solo crea
    `services.servicio_cuenta_corriente.registrar_cobro`. Una vez creado,
    el esquema no permite modificarlo.
    """

    tipo: str
    monto_centavos: int
    descripcion: str | None = None
    id: int | None = None
    fecha: str | None = None
    diferencia_centavos: int | None = None
    origen: str = "MANUAL"

    def __post_init__(self) -> None:
        self._validar()

    def _validar(self) -> None:
        if self.tipo not in TIPOS_MOVIMIENTO_VALIDOS:
            raise DatosInvalidosError(
                f"Tipo de movimiento de caja inválido: {self.tipo!r}. "
                f"Debe ser uno de {sorted(TIPOS_MOVIMIENTO_VALIDOS)}."
            )
        if self.monto_centavos < 0:
            raise DatosInvalidosError("El monto de un movimiento de caja no puede ser negativo.")
        if self.tipo in TIPOS_QUE_REQUIEREN_DESCRIPCION and not (
            self.descripcion and self.descripcion.strip()
        ):
            raise DatosInvalidosError(
                f"Los movimientos de tipo {self.tipo} requieren una descripción que los justifique."
            )
        if self.origen not in ORIGENES_MOVIMIENTO_VALIDOS:
            raise DatosInvalidosError(
                f"Origen de movimiento de caja inválido: {self.origen!r}. "
                f"Debe ser uno de {sorted(ORIGENES_MOVIMIENTO_VALIDOS)}."
            )
        if self.origen == "COBRO_CUENTA" and self.tipo != "INGRESO":
            raise DatosInvalidosError("Un movimiento de cobro de cuenta solo puede ser un INGRESO.")
        if self.diferencia_centavos is not None and self.tipo != "CIERRE":
            raise DatosInvalidosError(
                f"Solo un movimiento de tipo CIERRE puede tener diferencia_centavos (recibido: {self.tipo!r})."
            )


@dataclass(frozen=True)
class SesionCaja:
    """Una sesión de caja (tabla `sesiones_caja`, migración 019): desde una
    apertura explícita hasta su cierre. Es la unidad sobre la que se calcula
    el arqueo: no depende del día calendario, así una caja que cruza
    medianoche o dos cajas el mismo día no se mezclan entre sí.

    `origen` distingue cómo nació la fila: `NORMAL` (abierta por la
    aplicación desde la V1.3), `RECONSTRUIDA` (derivada de los movimientos
    de la V1.2 por la migración; siempre `CERRADA`, con `fecha_cierre`
    `None` si su cierre nunca se registró) o `LEGADO` (contenedor de datos
    históricos sin sesión reconstruible: nunca es una caja real ni admite
    operaciones nuevas).
    """

    id: int
    estado: str
    origen: str
    fecha_apertura: str
    fecha_cierre: str | None = None
    fondo_centavos: int | None = None
    contado_centavos: int | None = None
    diferencia_centavos: int | None = None

    @property
    def abierta(self) -> bool:
        return self.estado == "ABIERTA"


def clasificar_diferencia(diferencia_centavos: int) -> str:
    """Clasifica el resultado de un cierre de caja: 'SOBRANTE' si el
    efectivo contado superó al esperado, 'FALTANTE' si fue menor,
    'CUADRADA' si coinciden exactamente."""
    if diferencia_centavos > 0:
        return "SOBRANTE"
    if diferencia_centavos < 0:
        return "FALTANTE"
    return "CUADRADA"
