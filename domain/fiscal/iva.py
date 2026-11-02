"""Tratamiento de IVA de los productos y cálculo de los importes fiscales de un comprobante.

Twinx guarda precios FINALES (lo que paga el consumidor) en centavos: esa sigue siendo la fuente
comercial. Para WSFEv1 se deriva, con `Decimal`, la base neta y el IVA contenido de cada grupo de
alícuota. El total pagado nunca se altera para acomodar a ARCA: los importes fiscales se reconcilian
exactamente contra él, al centavo.

Redondeo (determinista y explicable): por grupo de alícuota, no por línea,
    neto = redondeo_mitad_arriba(bruto / (1 + tasa))   (al centavo)
    IVA  = bruto - neto
así `neto + IVA` reconstruye exactamente el bruto cobrado y el desvío contra `neto × tasa` es, como
máximo, medio centavo por alícuota.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from domain.fiscal import otros_impuestos, tablas_arca
from excepciones import DatosInvalidosError, FiscalizacionBloqueadaError

GRAVADO = "GRAVADO"
EXENTO = "EXENTO"
NO_GRAVADO = "NO_GRAVADO"
SIN_CLASIFICAR = "SIN_CLASIFICAR"

TRATAMIENTOS_IVA = {GRAVADO: "Gravado", EXENTO: "Exento", NO_GRAVADO: "No gravado"}


@dataclass(frozen=True)
class TratamientoIva:
    """Cómo trata el IVA a un producto. Un producto sin `TratamientoIva` está SIN_CLASIFICAR."""

    tratamiento: str
    alicuota_iva_id: int | None = None

    def __post_init__(self) -> None:
        if self.tratamiento not in TRATAMIENTOS_IVA:
            raise DatosInvalidosError(f"Tratamiento de IVA inválido: {self.tratamiento!r}.")
        if self.tratamiento == GRAVADO:
            if self.alicuota_iva_id not in tablas_arca.ALICUOTAS_IVA:
                raise DatosInvalidosError("Un producto gravado requiere una alícuota de IVA válida.")
        elif self.alicuota_iva_id is not None:
            raise DatosInvalidosError("Solo un producto gravado lleva alícuota de IVA.")

    @property
    def etiqueta(self) -> str:
        if self.tratamiento == GRAVADO:
            return f"Gravado {tablas_arca.etiqueta_alicuota(self.alicuota_iva_id)}"
        return TRATAMIENTOS_IVA[self.tratamiento]

    @classmethod
    def desde_texto(cls, valor: str) -> "TratamientoIva":
        """Parsea el valor de un formulario: 'GRAVADO:5', 'EXENTO', 'NO_GRAVADO'."""
        tratamiento, _, alicuota = (valor or "").strip().partition(":")
        if tratamiento == GRAVADO:
            try:
                return cls(GRAVADO, int(alicuota))
            except ValueError as error:
                raise DatosInvalidosError("Elegí la alícuota de IVA del producto gravado.") from error
        return cls(tratamiento)

    @property
    def como_texto(self) -> str:
        return f"{GRAVADO}:{self.alicuota_iva_id}" if self.tratamiento == GRAVADO else self.tratamiento


OPCIONES_TRATAMIENTO = tuple(
    [TratamientoIva(GRAVADO, alicuota) for alicuota in (5, 4, 6, 8, 9, 3)]
    + [TratamientoIva(EXENTO), TratamientoIva(NO_GRAVADO)]
)
"""Opciones que ofrece la interfaz (21 % primero por ser la más común, pero nunca preseleccionada)."""


@dataclass(frozen=True)
class LineaFiscal:
    """Una línea de la venta tal como entra al comprobante (snapshot: nombre y precio congelados).

    `otros_impuestos`: clasificación del producto respecto de los otros impuestos nacionales indirectos
    (ver `otros_impuestos`). Por defecto SIN_CLASIFICAR: lo que nadie confirmó, bloquea."""

    producto_id: int | None
    codigo: str
    descripcion: str
    cantidad: int
    precio_unitario_centavos: int
    subtotal_centavos: int
    tratamiento: TratamientoIva | None
    otros_impuestos: str = otros_impuestos.SIN_CLASIFICAR

    def __post_init__(self) -> None:
        otros_impuestos.validar_clasificacion(self.otros_impuestos)
        if self.cantidad <= 0 or self.precio_unitario_centavos < 0:
            raise DatosInvalidosError("Línea fiscal con cantidad o precio inválidos.")
        if self.subtotal_centavos != self.precio_unitario_centavos * self.cantidad:
            raise DatosInvalidosError("El subtotal de una línea fiscal no coincide con precio × cantidad.")


@dataclass(frozen=True)
class AlicuotaFiscal:
    """Un renglón `AlicIva` de WSFEv1: base imponible e IVA de una alícuota, en centavos."""

    alicuota_iva_id: int
    base_imponible_centavos: int
    importe_centavos: int


@dataclass(frozen=True)
class ImportesFiscales:
    """Los importes del comprobante en centavos. Valida `ImpTotal = ImpTotConc + ImpNeto + ImpOpEx +
    ImpTrib + ImpIVA` y que las alícuotas sumen exactamente neto e IVA."""

    total_centavos: int
    no_gravado_centavos: int
    neto_centavos: int
    exento_centavos: int
    tributos_centavos: int
    iva_centavos: int
    alicuotas: tuple[AlicuotaFiscal, ...] = ()

    def __post_init__(self) -> None:
        componentes = (
            self.no_gravado_centavos,
            self.neto_centavos,
            self.exento_centavos,
            self.tributos_centavos,
            self.iva_centavos,
        )
        if any(valor < 0 for valor in componentes) or self.total_centavos < 0:
            raise DatosInvalidosError("Los importes fiscales no pueden ser negativos.")
        if self.total_centavos != sum(componentes):
            raise DatosInvalidosError("Los importes fiscales no reconcilian con el total.")
        if self.alicuotas:
            if sum(a.base_imponible_centavos for a in self.alicuotas) != self.neto_centavos:
                raise DatosInvalidosError("Las bases imponibles no suman el neto gravado.")
            if sum(a.importe_centavos for a in self.alicuotas) != self.iva_centavos:
                raise DatosInvalidosError("El IVA por alícuota no suma el IVA total.")


def _neto_contenido(bruto_centavos: int, tasa: Decimal) -> int:
    return int((Decimal(bruto_centavos) / (Decimal(1) + tasa)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def calcular_importes(
    lineas: list[LineaFiscal], clase: str, *, receptor_consumidor_final: bool = True
) -> ImportesFiscales:
    """Importes fiscales de las líneas para un comprobante de `clase` A, B o C.

    Clase C (emisor monotributista o exento): no discrimina IVA; el total va como neto y el tratamiento
    de IVA de los productos no interviene. Clases A y B: cada línea necesita su tratamiento de IVA.

    Otros impuestos nacionales indirectos (Twinx no los calcula, así que nunca informa en `ImpTrib` ni como
    cero lo que no conoce): en A, B y C a consumidor final cada producto debe estar en NO_APLICA; en C a otro
    receptor solo bloquea REQUIERE_TRATAMIENTO (ver `otros_impuestos.a_exigir`). Por omisión se asume
    consumidor final, la regla más estricta.

    Raises:
        FiscalizacionBloqueadaError: algún producto sin IVA (A/B), sin confirmar sus otros impuestos
            nacionales indirectos o que requiere un tratamiento especial (los nombra a todos).
    """
    if not lineas:
        raise DatosInvalidosError("Un comprobante fiscal necesita al menos una línea.")
    if clase not in ("A", "B", "C"):
        raise DatosInvalidosError(f"Clase de comprobante inválida: {clase!r}.")
    total = sum(linea.subtotal_centavos for linea in lineas)

    sin_iva = [] if clase == "C" else sorted({linea.descripcion for linea in lineas if linea.tratamiento is None})
    motivos_iva = [f"Clasificá el IVA de «{nombre}»." for nombre in sin_iva]
    clasificaciones = otros_impuestos.a_exigir(
        ((linea.descripcion, linea.otros_impuestos) for linea in lineas), clase, receptor_consumidor_final
    )
    motivos_otros = otros_impuestos.motivos_de_bloqueo(clasificaciones)
    if motivos_iva and motivos_otros:
        raise FiscalizacionBloqueadaError(
            "Hay productos con datos fiscales pendientes: completalos antes de fiscalizar.", motivos_iva + motivos_otros
        )
    if motivos_iva:
        raise FiscalizacionBloqueadaError("Hay productos sin tratamiento de IVA: clasificalos antes de fiscalizar.", motivos_iva)
    otros_impuestos.exigir_conocidos(clasificaciones)
    if clase == "C":
        return ImportesFiscales(total, 0, total, 0, 0, 0)

    bruto_por_alicuota: dict[int, int] = {}
    exento = no_gravado = 0
    for linea in lineas:
        tratamiento = linea.tratamiento
        if tratamiento.tratamiento == GRAVADO:
            bruto_por_alicuota[tratamiento.alicuota_iva_id] = (
                bruto_por_alicuota.get(tratamiento.alicuota_iva_id, 0) + linea.subtotal_centavos
            )
        elif tratamiento.tratamiento == EXENTO:
            exento += linea.subtotal_centavos
        else:
            no_gravado += linea.subtotal_centavos

    alicuotas = []
    for alicuota_id in sorted(bruto_por_alicuota):
        bruto = bruto_por_alicuota[alicuota_id]
        neto = _neto_contenido(bruto, tablas_arca.ALICUOTAS_IVA[alicuota_id])
        alicuotas.append(AlicuotaFiscal(alicuota_id, neto, bruto - neto))
    neto_total = sum(a.base_imponible_centavos for a in alicuotas)
    iva_total = sum(a.importe_centavos for a in alicuotas)
    importes = ImportesFiscales(total, no_gravado, neto_total, exento, 0, iva_total, tuple(alicuotas))
    if importes.total_centavos != total:
        raise DatosInvalidosError("Los importes fiscales no reconcilian con el total de la venta.")
    return importes
