"""Partidas de stock (V1.12): existencias concretas de un producto con su vencimiento y costo propios.

Reglas puras (sin SQL): orden FEFO, asignación de un consumo entre partidas y estado de vencimiento.
Diseño e invariantes: `docs/V1.12_PARTIDAS.md`.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from excepciones import DatosInvalidosError, StockInsuficienteError

ORIGEN_HEREDADA = "HEREDADA"
ORIGEN_STOCK_INICIAL = "STOCK_INICIAL"
ORIGEN_COMPRA = "COMPRA"
ORIGEN_RECUENTO = "RECUENTO"
ORIGEN_AJUSTE = "AJUSTE"
ORIGEN_ANULACION_VENTA = "ANULACION_VENTA"

CRITERIO_FEFO = "FEFO"
CRITERIO_ELEGIDA = "ELEGIDA"
CRITERIO_UNICA = "UNICA"
CRITERIO_REVERSION = "REVERSION"

ETIQUETAS_ORIGEN = {
    ORIGEN_HEREDADA: "Existencia heredada",
    ORIGEN_STOCK_INICIAL: "Stock inicial",
    ORIGEN_COMPRA: "Compra",
    ORIGEN_RECUENTO: "Ajuste de inventario",
    ORIGEN_AJUSTE: "Ajuste",
    ORIGEN_ANULACION_VENTA: "Devuelta por anulación de venta",
}


@dataclass(frozen=True)
class Partida:
    id: int
    producto_id: int
    origen: str
    cantidad_original: int
    cantidad_disponible: int
    fecha_vencimiento: str | None
    costo_unitario_centavos: int | None
    fecha_ingreso: str | None
    detalle_compra_id: int | None = None
    ajuste_id: int | None = None
    detalle_venta_id: int | None = None

    def vencida(self, hoy: str) -> bool:
        """Vence el día indicado inclusive: el día del vencimiento todavía se puede vender."""
        return self.fecha_vencimiento is not None and self.fecha_vencimiento < hoy

    @property
    def etiqueta_origen(self) -> str:
        return ETIQUETAS_ORIGEN.get(self.origen, self.origen)


@dataclass(frozen=True)
class Asignacion:
    """Parte de un consumo que sale de una partida concreta, con el criterio que la eligió."""

    partida_id: int
    cantidad: int
    criterio: str


def orden_fefo(partidas: list[Partida], hoy: str) -> list[Partida]:
    """Orden de consumo (First Expired, First Out), determinístico:

    1. no vencidas con vencimiento conocido, la que vence antes primero;
    2. sin vencimiento conocido (la heredada y las demás sin fecha), la más antigua primero;
    3. vencidas, la que venció antes primero (consumirlas exige autorización, V1.12-D).

    Desempate siempre por id. Así nunca se deja vencer stock con fecha conocida por consumir antes
    stock sin fecha, y lo vencido queda último porque no debería venderse.
    """

    def clave(partida: Partida) -> tuple:
        if partida.fecha_vencimiento is None:
            return (1, "", partida.id)
        if partida.vencida(hoy):
            return (2, partida.fecha_vencimiento, partida.id)
        return (0, partida.fecha_vencimiento, partida.id)

    return sorted(partidas, key=clave)


def asignar_consumo(
    partidas: list[Partida], cantidad: int, hoy: str, partida_elegida_id: int | None = None
) -> list[Asignacion]:
    """Reparte `cantidad` entre las partidas con disponible.

    Con `partida_elegida_id`, todo sale de esa partida (`ELEGIDA`). Sin elección: si hay una única
    partida con disponible, `UNICA`; si no, `FEFO` (la partida exacta no se conocía: la asignación es
    la regla, y queda dicho así en cada movimiento).

    Raises:
        StockInsuficienteError: si las partidas no alcanzan (o la elegida no alcanza / no existe).
    """
    con_disponible = [p for p in partidas if p.cantidad_disponible > 0]
    if partida_elegida_id is not None:
        elegida = next((p for p in con_disponible if p.id == partida_elegida_id), None)
        if elegida is None or elegida.cantidad_disponible < cantidad:
            raise StockInsuficienteError("La partida elegida no tiene disponible suficiente.")
        return [Asignacion(elegida.id, cantidad, CRITERIO_ELEGIDA)]
    if sum(p.cantidad_disponible for p in con_disponible) < cantidad:
        raise StockInsuficienteError("Las partidas del producto no alcanzan para la cantidad pedida.")
    criterio = CRITERIO_UNICA if len(con_disponible) == 1 else CRITERIO_FEFO
    asignaciones: list[Asignacion] = []
    pendiente = cantidad
    for partida in orden_fefo(con_disponible, hoy):
        if pendiente == 0:
            break
        tomada = min(pendiente, partida.cantidad_disponible)
        asignaciones.append(Asignacion(partida.id, tomada, criterio))
        pendiente -= tomada
    return asignaciones


@dataclass(frozen=True)
class ConsumoDeVenta:
    """Lo que una línea de venta consumió (neto) de una partida, con el costo real de esa partida si se
    conoce. No reemplaza `detalle_venta.costo_unitario_centavos` (el costo vigente al vender, base de
    los márgenes históricos): lo complementa con el costo de lo que efectivamente salió."""

    detalle_venta_id: int
    partida_id: int
    producto_nombre: str
    origen: str
    fecha_vencimiento: str | None
    costo_unitario_centavos: int | None
    cantidad: int

    @property
    def etiqueta_origen(self) -> str:
        return ETIQUETAS_ORIGEN.get(self.origen, self.origen)

    @property
    def costo_total_centavos(self) -> int | None:
        if self.costo_unitario_centavos is None:
            return None
        return self.costo_unitario_centavos * self.cantidad


DIAS_ALERTA_POR_DEFECTO = 10
DIAS_ALERTA_MAXIMO = 365
ESTADO_VENCIDO = "VENCIDO"
ESTADO_PROXIMO = "PROXIMO_A_VENCER"


def validar_dias_alerta(texto: str) -> int:
    """Días de anticipación de la alerta "Próximo a vencer" (configuración global del OWNER)."""
    try:
        dias = int(str(texto).strip())
    except ValueError:
        raise DatosInvalidosError("Los días de alerta deben ser un número entero.") from None
    if not 0 <= dias <= DIAS_ALERTA_MAXIMO:
        raise DatosInvalidosError(f"Los días de alerta deben estar entre 0 y {DIAS_ALERTA_MAXIMO}.")
    return dias


def estado_de_vencimiento(fecha_vencimiento: str | None, hoy: str, dias_alerta: int) -> str | None:
    """`VENCIDO`, `PROXIMO_A_VENCER` (de hoy a hoy + días) o `None` (vigente o sin vencimiento conocido)."""
    if fecha_vencimiento is None:
        return None
    if fecha_vencimiento < hoy:
        return ESTADO_VENCIDO
    limite = (date.fromisoformat(hoy) + timedelta(days=dias_alerta)).isoformat()
    return ESTADO_PROXIMO if fecha_vencimiento <= limite else None


@dataclass(frozen=True)
class ResumenVencimientos:
    """Lectura operativa de los vencimientos de un producto (solo partidas con disponible)."""

    proximo_vencimiento: str | None
    cantidad_proxima: int
    cantidad_vencida: int
    cantidad_sin_vencimiento: int
    estado: str | None


def resumir_vencimientos(partidas: list[Partida], hoy: str, dias_alerta: int) -> ResumenVencimientos:
    """Próximo vencimiento vigente y sus unidades, unidades vencidas y sin vencimiento conocido. El estado
    del producto es el más urgente: vencido si tiene algo vencido; si no, próximo a vencer."""
    con_disponible = [p for p in partidas if p.cantidad_disponible > 0]
    vigentes = sorted(p.fecha_vencimiento for p in con_disponible if p.fecha_vencimiento and not p.vencida(hoy))
    proximo = vigentes[0] if vigentes else None
    vencida = sum(p.cantidad_disponible for p in con_disponible if p.vencida(hoy))
    estado = ESTADO_VENCIDO if vencida else estado_de_vencimiento(proximo, hoy, dias_alerta)
    return ResumenVencimientos(
        proximo_vencimiento=proximo,
        cantidad_proxima=sum(p.cantidad_disponible for p in con_disponible if p.fecha_vencimiento == proximo) if proximo else 0,
        cantidad_vencida=vencida,
        cantidad_sin_vencimiento=sum(p.cantidad_disponible for p in con_disponible if p.fecha_vencimiento is None),
        estado=estado,
    )
