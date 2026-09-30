"""Reporte de deuda a proveedores (V1.10-D): estructuras y reglas puras, sin SQL ni reloj global.

El saldo del libro de cada proveedor es la verdad contable agregada. Los importes por vencimiento
(vencida / próxima / vigente / sin vencimiento) son una **estimación por antigüedad (FIFO)** calculada con
`domain.cuentas_a_pagar`, no una imputación real de pagos a compras. Si el libro de un proveedor es
imposible, sus importes FIFO no se fabrican: la fila queda marcada como inconsistente."""

from dataclasses import dataclass
from datetime import date

from domain.cuentas_a_pagar import (
    CompraParaVencimiento,
    MovimientoLibro,
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
    cuyos importes FIFO no existen daría cifras falsas."""

    proveedor_id: int | None
    situacion: str
    filas: tuple[FilaDeudaProveedor, ...]

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


def construir_fila(libro: LibroDeProveedor, hoy: date) -> FilaDeudaProveedor:
    """Fila consistente de un proveedor. Reutiliza el replay FIFO, la clasificación y los buckets de
    `domain.cuentas_a_pagar`. El saldo es el del replay y debe coincidir con el agregado independiente del
    libro.

    Raises:
        LibroProveedorInconsistenteError: si el libro (o su cruce con las compras) es imposible; quien llama
            decide qué hacer (ver `fila_inconsistente`)."""
    estimacion = estimar_pendientes_fifo(libro.movimientos)
    resumen = resumir_vencimientos(clasificar_compras(libro.compras, estimacion, hoy), estimacion.saldo_centavos)
    if estimacion.saldo_centavos != libro.saldo_centavos:
        raise LibroProveedorInconsistenteError(
            f"El saldo del replay ({estimacion.saldo_centavos}) no coincide con el agregado ({libro.saldo_centavos})."
        )
    return FilaDeudaProveedor(
        proveedor_id=libro.proveedor_id,
        nombre=libro.nombre,
        activo=libro.activo,
        consistente=True,
        saldo_total_centavos=estimacion.saldo_centavos,
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
    """Una fila inconsistente siempre se conserva: no se puede descartar que cumpla la situación."""
    if not fila.consistente or situacion == SITUACION_TODAS:
        return True
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
