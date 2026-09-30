"""Reporte de deuda a proveedores (V1.10-D): estructuras y reglas puras, sin SQL ni reloj global.

El saldo del libro de cada proveedor es la verdad contable agregada. Los importes por vencimiento
(vencida / próxima / vigente / sin vencimiento) son una **estimación por antigüedad (FIFO)** calculada con
`domain.cuentas_a_pagar`, no una imputación real de pagos a compras. Si el libro de un proveedor es
imposible, sus importes FIFO no se fabrican: la fila queda marcada como inconsistente."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType

from domain.cuentas_a_pagar import (
    CompraParaVencimiento,
    MovimientoLibro,
    ResumenVencimientos,
    VencimientoCompra,
    clasificar_compras,
    estimar_pendientes_fifo,
    resumir_vencimientos,
)
from excepciones import DatosInvalidosError, LibroProveedorInconsistenteError

SITUACION_TODAS = ""
SITUACION_VENCIDA = "vencida"
SITUACION_PROXIMA = "proxima"
SITUACION_SIN_VENCIMIENTO = "sin_vencimiento"

SITUACIONES_VALIDAS = frozenset(
    {SITUACION_TODAS, SITUACION_VENCIDA, SITUACION_PROXIMA, SITUACION_SIN_VENCIMIENTO}
)


@dataclass(frozen=True)
class LibroDeProveedor:
    """Lo leído de un proveedor en una única lectura consistente. `saldo_centavos` es el agregado SQL del
    libro (independiente del replay); `ultima_compra` y `ultimo_pago` son fechas `AAAA-MM-DD` o `None`."""

    proveedor_id: int
    nombre: str
    activo: bool
    saldo_centavos: int
    movimientos: tuple[MovimientoLibro, ...]
    compras: tuple[CompraParaVencimiento, ...]
    ultima_compra: str | None
    ultimo_pago: str | None
    fechas_compras: Mapping[int, str]  # compra_id -> AAAA-MM-DD, de las compras a crédito


@dataclass(frozen=True)
class FilaDeudaProveedor:
    """Una fila del reporte. Si `consistente` es `False`, los cuatro importes FIFO son `None` (y el saldo
    solo se informa si el agregado del libro es un número no negativo, único caso en que sigue siendo
    creíble)."""

    proveedor_id: int
    nombre: str
    activo: bool
    consistente: bool
    saldo_total_centavos: int | None
    vencida_centavos: int | None
    proxima_centavos: int | None
    vigente_centavos: int | None
    sin_vencimiento_centavos: int | None
    ultima_compra: str | None
    ultimo_pago: str | None


@dataclass(frozen=True)
class ReporteDeudaProveedores:
    """Filas del reporte más sus totales. Los totales suman solo las filas consistentes: mezclar una fila
    cuyos importes FIFO no existen daría cifras falsas. `excluidos_por_inconsistencia` cuenta los proveedores
    de libro inconsistente que un filtro de situación dejó afuera por no poder clasificarse."""

    proveedor_id: int | None
    situacion: str
    filas: tuple[FilaDeudaProveedor, ...]
    excluidos_por_inconsistencia: int = 0

    @property
    def _consistentes(self) -> tuple[FilaDeudaProveedor, ...]:
        return tuple(fila for fila in self.filas if fila.consistente)

    @property
    def cantidad_inconsistentes(self) -> int:
        return sum(1 for fila in self.filas if not fila.consistente)

    @property
    def total_saldo_centavos(self) -> int:
        return sum(fila.saldo_total_centavos for fila in self._consistentes)

    @property
    def total_vencida_centavos(self) -> int:
        return sum(fila.vencida_centavos for fila in self._consistentes)

    @property
    def total_proxima_centavos(self) -> int:
        return sum(fila.proxima_centavos for fila in self._consistentes)

    @property
    def total_vigente_centavos(self) -> int:
        return sum(fila.vigente_centavos for fila in self._consistentes)

    @property
    def total_sin_vencimiento_centavos(self) -> int:
        return sum(fila.sin_vencimiento_centavos for fila in self._consistentes)


def validar_situacion(situacion: str | None) -> str:
    """Normaliza el filtro de situación (`None` o vacío = todas).

    Raises:
        DatosInvalidosError: si no es una de las opciones admitidas.
    """
    valor = (situacion or "").strip()
    if valor not in SITUACIONES_VALIDAS:
        raise DatosInvalidosError("La situación elegida no es válida.")
    return valor


def _reproducir(libro: LibroDeProveedor, hoy: date) -> tuple[tuple[VencimientoCompra, ...], ResumenVencimientos, int]:
    """Replay FIFO + clasificación + buckets del libro: `(vencimientos, resumen, saldo)`. Único punto donde
    se combina el motor de `domain.cuentas_a_pagar` para reporte y ficha.

    Raises:
        LibroProveedorInconsistenteError: si el libro, su cruce con las compras o su saldo son imposibles."""
    estimacion = estimar_pendientes_fifo(libro.movimientos)
    vencimientos = clasificar_compras(libro.compras, estimacion, hoy)
    resumen = resumir_vencimientos(vencimientos, estimacion.saldo_centavos)
    if estimacion.saldo_centavos != libro.saldo_centavos:
        raise LibroProveedorInconsistenteError(
            f"El saldo del replay ({estimacion.saldo_centavos}) no coincide con el agregado ({libro.saldo_centavos})."
        )
    return vencimientos, resumen, estimacion.saldo_centavos


def construir_fila(libro: LibroDeProveedor, hoy: date) -> FilaDeudaProveedor:
    """Fila consistente de un proveedor. Reutiliza el replay FIFO, la clasificación y los buckets de
    `domain.cuentas_a_pagar`. El saldo es el del replay y debe coincidir con el agregado independiente del
    libro.

    Raises:
        LibroProveedorInconsistenteError: si el libro (o su cruce con las compras) es imposible; quien llama
            decide qué hacer (ver `fila_inconsistente`)."""
    _, resumen, saldo = _reproducir(libro, hoy)
    return FilaDeudaProveedor(
        proveedor_id=libro.proveedor_id,
        nombre=libro.nombre,
        activo=libro.activo,
        consistente=True,
        saldo_total_centavos=saldo,
        vencida_centavos=resumen.vencida,
        proxima_centavos=resumen.proxima,
        vigente_centavos=resumen.vigente,
        sin_vencimiento_centavos=resumen.sin_vencimiento,
        ultima_compra=libro.ultima_compra,
        ultimo_pago=libro.ultimo_pago,
    )


def fila_inconsistente(libro: LibroDeProveedor) -> FilaDeudaProveedor:
    """Fila de un proveedor cuyo libro no se pudo reproducir: sin importes FIFO."""
    saldo_confiable = libro.saldo_centavos if libro.saldo_centavos >= 0 else None
    return FilaDeudaProveedor(
        proveedor_id=libro.proveedor_id,
        nombre=libro.nombre,
        activo=libro.activo,
        consistente=False,
        saldo_total_centavos=saldo_confiable,
        vencida_centavos=None,
        proxima_centavos=None,
        vigente_centavos=None,
        sin_vencimiento_centavos=None,
        ultima_compra=libro.ultima_compra,
        ultimo_pago=libro.ultimo_pago,
    )


def cumple_situacion(fila: FilaDeudaProveedor, situacion: str) -> bool:
    """Sin filtro de situación toda fila coincide (también la inconsistente: no se esconde un libro roto).
    Con filtro, una fila inconsistente NO coincide: sin importes FIFO no puede demostrarse que la cumpla."""
    if situacion == SITUACION_TODAS:
        return True
    if not fila.consistente:
        return False
    importe = {
        SITUACION_VENCIDA: fila.vencida_centavos,
        SITUACION_PROXIMA: fila.proxima_centavos,
        SITUACION_SIN_VENCIMIENTO: fila.sin_vencimiento_centavos,
    }[situacion]
    return importe > 0


def es_listable(fila: FilaDeudaProveedor) -> bool:
    """Por defecto solo se listan proveedores con deuda. Una fila inconsistente se lista siempre, aunque su
    saldo sea 0 o negativo: un libro imposible no debe quedar escondido."""
    return not fila.consistente or fila.saldo_total_centavos > 0


@dataclass(frozen=True)
class CompraAbierta:
    """Compra a crédito activa con pendiente **estimado** (FIFO por antigüedad) mayor que 0."""

    compra_id: int
    fecha: str
    total_centavos: int
    fecha_vencimiento: date | None
    pendiente_estimado_centavos: int
    estado_vencimiento: str


@dataclass(frozen=True)
class ProximoVencimiento:
    """Fecha de vencimiento más cercana (desde hoy, inclusive) entre las compras con pendiente estimado, y la
    suma de esos pendientes estimados en esa fecha."""

    fecha: date
    importe_centavos: int


@dataclass(frozen=True)
class CuentaProveedor:
    """Cuenta a pagar de un proveedor para su ficha y para el detalle de sus compras.

    `saldo_centavos` es el agregado real del libro. Todo lo demás (buckets, compras abiertas, próximo
    vencimiento, pendiente por compra) es una **estimación por antigüedad (FIFO)** y queda en `None`/vacío si
    el libro es inconsistente (`consistente` = `False`)."""

    consistente: bool
    saldo_centavos: int
    vencida_centavos: int | None
    proxima_centavos: int | None
    vigente_centavos: int | None
    sin_vencimiento_centavos: int | None
    compras_abiertas: tuple[CompraAbierta, ...]
    proximo_vencimiento: ProximoVencimiento | None
    pendientes_por_compra: Mapping[int, int]

    @property
    def saldo_confiable(self) -> bool:
        """Un libro inconsistente con saldo negativo (imposible) no tiene un saldo creíble que mostrar."""
        return self.consistente or self.saldo_centavos >= 0

    def pendiente_de(self, compra_id: int) -> int | None:
        """Pendiente estimado de una compra a crédito (0 si está cubierta o anulada), o `None` si no aplica
        (compra de contado, ajena al proveedor o libro inconsistente)."""
        return self.pendientes_por_compra.get(compra_id)


def _proximo_vencimiento(abiertas: tuple[CompraAbierta, ...], hoy: date) -> ProximoVencimiento | None:
    con_fecha = [c for c in abiertas if c.fecha_vencimiento is not None and c.fecha_vencimiento >= hoy]
    if not con_fecha:
        return None
    fecha = min(c.fecha_vencimiento for c in con_fecha)
    return ProximoVencimiento(fecha, sum(c.pendiente_estimado_centavos for c in con_fecha if c.fecha_vencimiento == fecha))


def construir_cuenta(libro: LibroDeProveedor, hoy: date) -> CuentaProveedor:
    """Cuenta consistente de un proveedor, con el mismo replay que la fila del reporte (`_reproducir`).

    Raises:
        LibroProveedorInconsistenteError: si el libro es imposible (ver `cuenta_inconsistente`)."""
    vencimientos, resumen, saldo = _reproducir(libro, hoy)
    abiertas = tuple(
        CompraAbierta(
            compra_id=v.compra_id,
            fecha=libro.fechas_compras[v.compra_id],
            total_centavos=v.monto_original_centavos,
            fecha_vencimiento=v.fecha_vencimiento,
            pendiente_estimado_centavos=v.pendiente_estimado_centavos,
            estado_vencimiento=v.estado_vencimiento,
        )
        for v in vencimientos
        if v.pendiente_estimado_centavos > 0
    )
    return CuentaProveedor(
        consistente=True,
        saldo_centavos=saldo,
        vencida_centavos=resumen.vencida,
        proxima_centavos=resumen.proxima,
        vigente_centavos=resumen.vigente,
        sin_vencimiento_centavos=resumen.sin_vencimiento,
        compras_abiertas=abiertas,
        proximo_vencimiento=_proximo_vencimiento(abiertas, hoy),
        pendientes_por_compra=MappingProxyType({v.compra_id: v.pendiente_estimado_centavos for v in vencimientos}),
    )


def cuenta_inconsistente(saldo_centavos: int) -> CuentaProveedor:
    """Cuenta de un libro que no se pudo reproducir: solo el saldo agregado, sin cifras FIFO."""
    return CuentaProveedor(
        consistente=False,
        saldo_centavos=saldo_centavos,
        vencida_centavos=None,
        proxima_centavos=None,
        vigente_centavos=None,
        sin_vencimiento_centavos=None,
        compras_abiertas=(),
        proximo_vencimiento=None,
        pendientes_por_compra=MappingProxyType({}),
    )


def cuenta_vacia() -> CuentaProveedor:
    """Cuenta de un proveedor sin movimientos en el libro."""
    return CuentaProveedor(
        consistente=True,
        saldo_centavos=0,
        vencida_centavos=0,
        proxima_centavos=0,
        vigente_centavos=0,
        sin_vencimiento_centavos=0,
        compras_abiertas=(),
        proximo_vencimiento=None,
        pendientes_por_compra=MappingProxyType({}),
    )
