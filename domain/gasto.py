"""Entidad y reglas de los gastos operativos (migración 048).

Un gasto es dinero que sale del negocio y que no es mercadería: alquiler, servicios, salarios,
etc. La mercadería no se registra acá porque su costo se reconoce al vender (margen bruto); contarla
también como gasto duplicaría el costo. Los importes están en centavos (`int`, ver `domain.dinero`).
"""

from dataclasses import dataclass
from datetime import date

from domain.fechas import fecha_de_formulario
from excepciones import DatosInvalidosError

CATEGORIA_OTRO = "OTRO"

# Orden de presentación en formularios y reportes.
CATEGORIAS_GASTO: tuple[tuple[str, str], ...] = (
    ("ALQUILER", "Alquiler"),
    ("ELECTRICIDAD", "Electricidad"),
    ("AGUA", "Agua"),
    ("INTERNET", "Internet"),
    ("SERVICIOS", "Servicios"),
    ("SALARIOS", "Salarios"),
    ("MANTENIMIENTO", "Mantenimiento"),
    ("TRANSPORTE", "Transporte"),
    ("COMISIONES", "Comisiones"),
    ("IMPUESTOS_Y_TASAS", "Impuestos y tasas"),
    (CATEGORIA_OTRO, "Otros (con descripción)"),
)
ETIQUETAS_CATEGORIA = dict(CATEGORIAS_GASTO)
CATEGORIAS_VALIDAS = frozenset(ETIQUETAS_CATEGORIA)

MEDIO_EFECTIVO_CAJA = "EFECTIVO_CAJA"
MEDIOS_PAGO: tuple[tuple[str, str], ...] = (
    (MEDIO_EFECTIVO_CAJA, "Efectivo de la caja abierta"),
    ("EFECTIVO", "Efectivo fuera de la caja"),
    ("TRANSFERENCIA", "Transferencia"),
    ("TARJETA", "Tarjeta"),
    ("DEBITO_AUTOMATICO", "Débito automático"),
    ("OTRO", "Otro medio"),
)
ETIQUETAS_MEDIO_PAGO = dict(MEDIOS_PAGO)
MEDIOS_PAGO_VALIDOS = frozenset(ETIQUETAS_MEDIO_PAGO)

ESTADO_VIGENTE = "VIGENTE"
ESTADO_ANULADO = "ANULADO"

LARGO_MAXIMO_DESCRIPCION = 200
LARGO_MAXIMO_REFERENCIA = 80
LARGO_MAXIMO_RESPONSABLE = 80
LARGO_MAXIMO_OBSERVACIONES = 1000
LARGO_MAXIMO_MOTIVO = 300
IMPORTE_MAXIMO_CENTAVOS = 10**13


@dataclass(frozen=True)
class DatosGasto:
    """Lo que el OWNER carga al registrar un gasto, ya validado y normalizado (`validar_datos_gasto`)."""

    fecha: str
    categoria: str
    descripcion: str | None
    importe_centavos: int
    medio_pago: str
    referencia: str | None
    responsable: str | None
    observaciones: str | None

    @property
    def paga_con_caja(self) -> bool:
        return self.medio_pago == MEDIO_EFECTIVO_CAJA


@dataclass(frozen=True)
class Gasto:
    """Un gasto persistido."""

    id: int
    fecha: str
    categoria: str
    descripcion: str | None
    importe_centavos: int
    medio_pago: str
    referencia: str | None
    responsable: str | None
    observaciones: str | None
    usuario_id: int
    usuario_nombre: str | None
    creado_en: str
    modificado_en: str | None
    estado: str
    anulado_en: str | None
    anulado_por_usuario_id: int | None
    anulado_por_nombre: str | None
    motivo_anulacion: str | None
    caja_movimiento_id: int | None
    caja_movimiento_reintegro_id: int | None
    clave_idempotencia: str | None

    @property
    def categoria_etiqueta(self) -> str:
        return ETIQUETAS_CATEGORIA.get(self.categoria, self.categoria)

    @property
    def medio_pago_etiqueta(self) -> str:
        return ETIQUETAS_MEDIO_PAGO.get(self.medio_pago, self.medio_pago)

    @property
    def vigente(self) -> bool:
        return self.estado == ESTADO_VIGENTE

    @property
    def paga_con_caja(self) -> bool:
        return self.medio_pago == MEDIO_EFECTIVO_CAJA

    @property
    def anulado_con_reintegro(self) -> bool:
        return self.caja_movimiento_reintegro_id is not None


def _texto_acotado(texto: str | None, largo_maximo: int, nombre: str) -> str | None:
    """Recorta espacios y convierte el vacío en `None`; rechaza lo que excede el largo del campo."""
    limpio = (texto or "").strip()
    if not limpio:
        return None
    if len(limpio) > largo_maximo:
        raise DatosInvalidosError(f"{nombre} no puede superar los {largo_maximo} caracteres.")
    return limpio


def validar_categoria(categoria: str | None) -> str:
    if categoria not in CATEGORIAS_VALIDAS:
        raise DatosInvalidosError("Elegí una categoría de gasto válida.")
    return categoria


def validar_descripcion(categoria: str, descripcion: str | None) -> str | None:
    limpia = _texto_acotado(descripcion, LARGO_MAXIMO_DESCRIPCION, "La descripción")
    if categoria == CATEGORIA_OTRO and limpia is None:
        raise DatosInvalidosError("Un gasto de la categoría \"Otros\" exige una descripción que lo explique.")
    return limpia


def validar_importe(importe_centavos: int) -> int:
    if importe_centavos <= 0:
        raise DatosInvalidosError("El importe del gasto debe ser mayor que cero.")
    if importe_centavos > IMPORTE_MAXIMO_CENTAVOS:
        raise DatosInvalidosError("El importe del gasto es demasiado grande.")
    return importe_centavos


def validar_medio_pago(medio_pago: str | None) -> str:
    if medio_pago not in MEDIOS_PAGO_VALIDOS:
        raise DatosInvalidosError("Elegí cómo se pagó el gasto.")
    return medio_pago


def validar_fecha_gasto(fecha: str | None, hoy: date | None = None) -> str:
    """La fecha del gasto es un día (`AAAA-MM-DD`) que no puede ser futuro: un gasto se registra cuando
    ya ocurrió (lo programado no es un gasto todavía)."""
    valor = fecha_de_formulario(fecha or "", "La fecha del gasto no es válida.")
    if valor > (hoy or date.today()):
        raise DatosInvalidosError("La fecha del gasto no puede ser futura.")
    return valor.isoformat()


def validar_datos_gasto(
    *,
    fecha: str | None,
    categoria: str | None,
    descripcion: str | None,
    importe_centavos: int,
    medio_pago: str | None,
    referencia: str | None,
    responsable: str | None,
    observaciones: str | None,
    hoy: date | None = None,
) -> DatosGasto:
    """Valida y normaliza todos los campos de un gasto nuevo.

    Raises:
        DatosInvalidosError: ante cualquier campo inválido, con un mensaje para el usuario.
    """
    categoria_valida = validar_categoria(categoria)
    return DatosGasto(
        fecha=validar_fecha_gasto(fecha, hoy),
        categoria=categoria_valida,
        descripcion=validar_descripcion(categoria_valida, descripcion),
        importe_centavos=validar_importe(importe_centavos),
        medio_pago=validar_medio_pago(medio_pago),
        referencia=_texto_acotado(referencia, LARGO_MAXIMO_REFERENCIA, "La referencia"),
        responsable=_texto_acotado(responsable, LARGO_MAXIMO_RESPONSABLE, "El responsable"),
        observaciones=_texto_acotado(observaciones, LARGO_MAXIMO_OBSERVACIONES, "Las observaciones"),
    )


@dataclass(frozen=True)
class CambiosGasto:
    """Campos descriptivos editables de un gasto vigente (lo económico no se edita: se anula)."""

    categoria: str
    descripcion: str | None
    referencia: str | None
    responsable: str | None
    observaciones: str | None


def validar_cambios_gasto(
    *,
    categoria: str | None,
    descripcion: str | None,
    referencia: str | None,
    responsable: str | None,
    observaciones: str | None,
) -> CambiosGasto:
    categoria_valida = validar_categoria(categoria)
    return CambiosGasto(
        categoria=categoria_valida,
        descripcion=validar_descripcion(categoria_valida, descripcion),
        referencia=_texto_acotado(referencia, LARGO_MAXIMO_REFERENCIA, "La referencia"),
        responsable=_texto_acotado(responsable, LARGO_MAXIMO_RESPONSABLE, "El responsable"),
        observaciones=_texto_acotado(observaciones, LARGO_MAXIMO_OBSERVACIONES, "Las observaciones"),
    )


def describir_cambios(gasto: Gasto, cambios: CambiosGasto) -> list[str]:
    """Lista legible de lo que cambia (para la auditoría); vacía si nada cambia."""
    etiquetas = (
        ("categoria", "categoría"),
        ("descripcion", "descripción"),
        ("referencia", "referencia"),
        ("responsable", "responsable"),
        ("observaciones", "observaciones"),
    )
    diferencias = []
    for campo, etiqueta in etiquetas:
        anterior, nuevo = getattr(gasto, campo), getattr(cambios, campo)
        if anterior != nuevo:
            diferencias.append(f"{etiqueta}: {anterior or '—'} → {nuevo or '—'}")
    return diferencias


def validar_motivo_anulacion(motivo: str | None) -> str:
    limpio = _texto_acotado(motivo, LARGO_MAXIMO_MOTIVO, "El motivo")
    if limpio is None:
        raise DatosInvalidosError("Indicá el motivo de la anulación del gasto.")
    return limpio


def validar_reintegro_a_caja(medio_pago: str, reintegro_a_caja: bool | None) -> bool:
    """Al anular un gasto pagado con la caja, el OWNER dice si el efectivo vuelve (no se asume: pudo ser un
    error de carga o un pago que ya se hizo y no vuelve). Los demás gastos nunca tocaron la caja."""
    if medio_pago != MEDIO_EFECTIVO_CAJA:
        if reintegro_a_caja:
            raise DatosInvalidosError("Este gasto no se pagó con efectivo de la caja: no hay nada que reintegrar.")
        return False
    if reintegro_a_caja is None:
        raise DatosInvalidosError("Indicá si el efectivo de este gasto vuelve a la caja o no.")
    return reintegro_a_caja


def descripcion_para_caja(datos: DatosGasto) -> str:
    """Texto del movimiento de caja que respalda el gasto (el libro de caja se lee sin abrir el gasto)."""
    etiqueta = ETIQUETAS_CATEGORIA[datos.categoria].replace(" (con descripción)", "")
    texto = f"Gasto operativo: {etiqueta}"
    if datos.descripcion:
        texto += f" – {datos.descripcion}"
    return texto[:250]
