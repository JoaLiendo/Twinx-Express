"""Motor puro de lectura de cuentas a pagar (V1.10-B): replay FIFO causal del libro de un proveedor,
clasificación de vencimientos y buckets agregados.

El FIFO es una **estimación de lectura por antigüedad**: la aplicación no imputa pagos a compras concretas
(ver `domain.proveedor`), así que el "pendiente estimado" de una compra responde "qué parte de esta compra
seguiría sin cubrir si los pagos se hubieran aplicado siempre a lo más antiguo". No es un saldo contable
real, no se persiste y no modifica el libro. Este módulo es solo consumidor de hechos: la fuente de verdad
sigue siendo `movimientos_proveedor` (V1.9).

Sin SQL, sin reloj global (`hoy` entra por parámetro) y sin efectos secundarios. Un replay corresponde a
un único proveedor: quien llama pasa solo el libro de ese proveedor.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from types import MappingProxyType

from excepciones import LibroProveedorInconsistenteError

TIPO_CARGO_COMPRA = "CARGO_COMPRA"
TIPO_PAGO = "PAGO"
TIPO_REVERSA_COMPRA = "REVERSA_COMPRA"

ESTADO_COMPRA_ACTIVA = "ACTIVA"
ESTADO_COMPRA_ANULADA = "ANULADA"

ESTADO_ANULADA = "ANULADA"
ESTADO_PAGADA = "PAGADA"
ESTADO_VENCIDA = "VENCIDA"
ESTADO_PROXIMA_A_VENCER = "PROXIMA_A_VENCER"
ESTADO_PENDIENTE = "PENDIENTE"
ESTADO_SIN_VENCIMIENTO = "SIN_VENCIMIENTO"

_ESTADOS_VENCIMIENTO = frozenset(
    {
        ESTADO_ANULADA,
        ESTADO_PAGADA,
        ESTADO_VENCIDA,
        ESTADO_PROXIMA_A_VENCER,
        ESTADO_PENDIENTE,
        ESTADO_SIN_VENCIMIENTO,
    }
)

DIAS_PROXIMO_VENCIMIENTO = 7


@dataclass(frozen=True)
class MovimientoLibro:
    """Un movimiento del libro de un proveedor, reducido a lo que el replay necesita.

    `compra_id` es obligatorio en `CARGO_COMPRA` y `REVERSA_COMPRA`, y debe ser `None` en un `PAGO`
    (los pagos no se imputan a compras). La validez se comprueba al reproducir el libro, no al construir."""

    id: int
    tipo: str
    compra_id: int | None
    monto_centavos: int


@dataclass(frozen=True)
class EstimacionFifo:
    """Resultado del replay FIFO de un libro.

    `pendientes_estimados` tiene una entrada por cada compra con cargo (en orden de aparición): el
    residual estimado, 0 si quedó cubierta por antigüedad o si fue reversada, y `montos_originales` el
    monto de su cargo. `saldo_centavos` es
    `sum(cargos) - sum(pagos) - sum(reversas)` y siempre coincide con la suma de los pendientes."""

    pendientes_estimados: Mapping[int, int]
    montos_originales: Mapping[int, int]
    compras_reversadas: frozenset[int]
    saldo_centavos: int


def _es_entero_positivo(valor: object) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool) and valor > 0


def _validar_movimiento(movimiento: MovimientoLibro, id_anterior: int) -> None:
    if not _es_entero_positivo(movimiento.id):
        raise LibroProveedorInconsistenteError(f"Movimiento con id inválido: {movimiento.id!r}.")
    if movimiento.id <= id_anterior:
        raise LibroProveedorInconsistenteError(
            f"Los movimientos deben venir ordenados por id y sin repetir: {movimiento.id} después de {id_anterior}."
        )
    if not _es_entero_positivo(movimiento.monto_centavos):
        raise LibroProveedorInconsistenteError(
            f"Movimiento {movimiento.id}: el monto debe ser un entero positivo, no {movimiento.monto_centavos!r}."
        )
    if movimiento.tipo == TIPO_PAGO:
        if movimiento.compra_id is not None:
            raise LibroProveedorInconsistenteError(f"Movimiento {movimiento.id}: un pago no referencia una compra.")
    elif movimiento.tipo in (TIPO_CARGO_COMPRA, TIPO_REVERSA_COMPRA):
        if not _es_entero_positivo(movimiento.compra_id):
            raise LibroProveedorInconsistenteError(
                f"Movimiento {movimiento.id}: {movimiento.tipo} requiere una compra, no {movimiento.compra_id!r}."
            )
    else:
        raise LibroProveedorInconsistenteError(f"Movimiento {movimiento.id}: tipo desconocido {movimiento.tipo!r}.")


def estimar_pendientes_fifo(movimientos: Sequence[MovimientoLibro]) -> EstimacionFifo:
    """Reproduce el libro en orden causal y estima el pendiente de cada compra por antigüedad.

    La política de orden es exigir la entrada ordenada por `id` estrictamente creciente (la fecha nunca
    decide) y rechazar lo que no lo esté, en lugar de reordenarla: así un caller que se equivoca no
    obtiene un resultado plausible pero falso.

    Raises:
        LibroProveedorInconsistenteError: ante cualquier hecho imposible (ver la excepción).
    """
    residuales: dict[int, int] = {}  # compra_id -> residual, en orden de aparición del cargo
    abiertas: list[int] = []  # cola FIFO de compras con cargo vigente y aún no reversadas
    montos_originales: dict[int, int] = {}
    reversadas: set[int] = set()
    total_cargos = total_pagos = total_reversas = 0
    id_anterior = 0

    for movimiento in movimientos:
        _validar_movimiento(movimiento, id_anterior)
        id_anterior = movimiento.id
        monto = movimiento.monto_centavos
        compra_id = movimiento.compra_id

        if movimiento.tipo == TIPO_CARGO_COMPRA:
            if compra_id in montos_originales:
                raise LibroProveedorInconsistenteError(f"Movimiento {movimiento.id}: cargo duplicado de la compra {compra_id}.")
            montos_originales[compra_id] = monto
            residuales[compra_id] = monto
            abiertas.append(compra_id)
            total_cargos += monto
        elif movimiento.tipo == TIPO_PAGO:
            if monto > sum(residuales[c] for c in abiertas):
                raise LibroProveedorInconsistenteError(
                    f"Movimiento {movimiento.id}: el pago de {monto} supera el saldo disponible en ese punto."
                )
            restante = monto
            while restante:
                objetivo = abiertas[0]
                consumo = min(restante, residuales[objetivo])
                residuales[objetivo] -= consumo
                restante -= consumo
                if residuales[objetivo] == 0:
                    abiertas.pop(0)
            total_pagos += monto
        else:
            if compra_id not in montos_originales:
                raise LibroProveedorInconsistenteError(f"Movimiento {movimiento.id}: reversa de la compra {compra_id} sin cargo previo.")
            if compra_id in reversadas:
                raise LibroProveedorInconsistenteError(f"Movimiento {movimiento.id}: reversa duplicada de la compra {compra_id}.")
            if residuales[compra_id] != montos_originales[compra_id]:
                raise LibroProveedorInconsistenteError(
                    f"Movimiento {movimiento.id}: la compra {compra_id} ya fue cubierta en parte y no puede reversarse."
                )
            if monto != montos_originales[compra_id]:
                raise LibroProveedorInconsistenteError(
                    f"Movimiento {movimiento.id}: la reversa ({monto}) no coincide con el cargo de la compra {compra_id}."
                )
            abiertas.remove(compra_id)
            residuales[compra_id] = 0
            reversadas.add(compra_id)
            total_reversas += monto

    saldo = total_cargos - total_pagos - total_reversas
    if saldo != sum(residuales.values()):
        raise LibroProveedorInconsistenteError(
            f"El saldo del libro ({saldo}) no coincide con la suma de residuales ({sum(residuales.values())})."
        )
    return EstimacionFifo(
        pendientes_estimados=MappingProxyType(residuales),
        montos_originales=MappingProxyType(montos_originales),
        compras_reversadas=frozenset(reversadas),
        saldo_centavos=saldo,
    )


@dataclass(frozen=True)
class CompraParaVencimiento:
    """Los datos de una compra a crédito que la clasificación necesita, ya convertidos a tipos de dominio."""

    compra_id: int
    fecha_vencimiento: date | None
    estado: str
    monto_original_centavos: int


@dataclass(frozen=True)
class VencimientoCompra:
    """Una compra con su pendiente **estimado** (FIFO por antigüedad, no imputación real) y su estado de
    vencimiento."""

    compra_id: int
    monto_original_centavos: int
    pendiente_estimado_centavos: int
    fecha_vencimiento: date | None
    estado_vencimiento: str


def clasificar_vencimiento(
    estado_compra: str, pendiente_estimado_centavos: int, fecha_vencimiento: date | None, hoy: date
) -> str:
    """Estado de vencimiento de una compra. Precedencia: ANULADA, PAGADA (pendiente 0), VENCIDA
    (vencimiento anterior a hoy), PROXIMA_A_VENCER (de hoy a hoy + 7 días, ambos inclusive), PENDIENTE
    (más allá) y SIN_VENCIMIENTO (sin fecha).

    Raises:
        LibroProveedorInconsistenteError: si el estado de la compra es desconocido o el pendiente es negativo.
    """
    if estado_compra not in (ESTADO_COMPRA_ACTIVA, ESTADO_COMPRA_ANULADA):
        raise LibroProveedorInconsistenteError(f"Estado de compra desconocido: {estado_compra!r}.")
    if pendiente_estimado_centavos < 0:
        raise LibroProveedorInconsistenteError(f"Pendiente estimado negativo: {pendiente_estimado_centavos}.")
    if estado_compra == ESTADO_COMPRA_ANULADA:
        return ESTADO_ANULADA
    if pendiente_estimado_centavos == 0:
        return ESTADO_PAGADA
    if fecha_vencimiento is None:
        return ESTADO_SIN_VENCIMIENTO
    if fecha_vencimiento < hoy:
        return ESTADO_VENCIDA
    if fecha_vencimiento <= hoy + timedelta(days=DIAS_PROXIMO_VENCIMIENTO):
        return ESTADO_PROXIMA_A_VENCER
    return ESTADO_PENDIENTE


def clasificar_compras(
    compras: Sequence[CompraParaVencimiento], estimacion: EstimacionFifo, hoy: date
) -> tuple[VencimientoCompra, ...]:
    """Cruza las compras a crédito de un proveedor con la estimación FIFO de su libro y las clasifica.

    Exige que ambos lados describan lo mismo: cada compra tiene su cargo (con igual monto), cada cargo su
    compra, y `ANULADA` coincide exactamente con tener reversa.

    Raises:
        LibroProveedorInconsistenteError: si compras y libro no concuerdan.
    """
    ids_informados = [compra.compra_id for compra in compras]
    if len(set(ids_informados)) != len(ids_informados):
        raise LibroProveedorInconsistenteError("Hay compras repetidas en la lista a clasificar.")
    faltantes = set(estimacion.pendientes_estimados) - set(ids_informados)
    if faltantes:
        raise LibroProveedorInconsistenteError(f"El libro tiene cargos de compras no informadas: {sorted(faltantes)}.")

    resultado = []
    for compra in compras:
        if compra.compra_id not in estimacion.pendientes_estimados:
            raise LibroProveedorInconsistenteError(f"La compra {compra.compra_id} no tiene cargo en el libro.")
        reversada = compra.compra_id in estimacion.compras_reversadas
        if reversada != (compra.estado == ESTADO_COMPRA_ANULADA):
            raise LibroProveedorInconsistenteError(
                f"La compra {compra.compra_id} está {compra.estado} pero su reversa en el libro es {reversada}."
            )
        if compra.monto_original_centavos != estimacion.montos_originales[compra.compra_id]:
            raise LibroProveedorInconsistenteError(
                f"La compra {compra.compra_id} vale {compra.monto_original_centavos} pero su cargo en el libro es "
                f"{estimacion.montos_originales[compra.compra_id]}."
            )
        pendiente = estimacion.pendientes_estimados[compra.compra_id]
        resultado.append(
            VencimientoCompra(
                compra_id=compra.compra_id,
                monto_original_centavos=compra.monto_original_centavos,
                pendiente_estimado_centavos=pendiente,
                fecha_vencimiento=compra.fecha_vencimiento,
                estado_vencimiento=clasificar_vencimiento(compra.estado, pendiente, compra.fecha_vencimiento, hoy),
            )
        )
    return tuple(resultado)


@dataclass(frozen=True)
class ResumenVencimientos:
    """Saldo estimado repartido por estado de vencimiento. `vigente` corresponde a PENDIENTE."""

    vencida: int
    proxima: int
    vigente: int
    sin_vencimiento: int

    @property
    def total_centavos(self) -> int:
        return self.vencida + self.proxima + self.vigente + self.sin_vencimiento


def resumir_vencimientos(vencimientos: Sequence[VencimientoCompra], saldo_centavos: int) -> ResumenVencimientos:
    """Suma los pendientes estimados por bucket. PAGADA y ANULADA aportan 0.

    Raises:
        LibroProveedorInconsistenteError: si un estado es desconocido, si PAGADA/ANULADA tienen pendiente,
            o si los buckets no suman `saldo_centavos`.
    """
    acumulado = dict.fromkeys(_ESTADOS_VENCIMIENTO, 0)
    for item in vencimientos:
        if item.estado_vencimiento not in _ESTADOS_VENCIMIENTO:
            raise LibroProveedorInconsistenteError(f"Estado de vencimiento desconocido: {item.estado_vencimiento!r}.")
        if item.estado_vencimiento in (ESTADO_PAGADA, ESTADO_ANULADA) and item.pendiente_estimado_centavos != 0:
            raise LibroProveedorInconsistenteError(
                f"La compra {item.compra_id} figura {item.estado_vencimiento} con pendiente {item.pendiente_estimado_centavos}."
            )
        acumulado[item.estado_vencimiento] += item.pendiente_estimado_centavos
    resumen = ResumenVencimientos(
        vencida=acumulado[ESTADO_VENCIDA],
        proxima=acumulado[ESTADO_PROXIMA_A_VENCER],
        vigente=acumulado[ESTADO_PENDIENTE],
        sin_vencimiento=acumulado[ESTADO_SIN_VENCIMIENTO],
    )
    if resumen.total_centavos != saldo_centavos:
        raise LibroProveedorInconsistenteError(
            f"Los buckets suman {resumen.total_centavos} pero el saldo del libro es {saldo_centavos}."
        )
    return resumen
