"""Casos de uso de control de caja (apertura, cierre, movimientos).

Orquesta `domain.caja` (reglas de negocio del movimiento en sí) y
`db.repositorios.caja` (persistencia). Además aplica las reglas de
secuencia de una caja física: no se puede abrir una caja que ya está
abierta, ni cerrarla o registrar ingresos/egresos si no hay ninguna
caja abierta.

Los montos se manejan en centavos (`int`, ver `domain.dinero`).
"""

import logging
from dataclasses import dataclass, field

from db.repositorios import caja as repositorio_caja
from db.repositorios import ventas as repositorio_ventas
from domain.caja import MovimientoCaja, clasificar_diferencia
from domain.venta import Venta
from excepciones import MENSAJE_FORMULARIO_REENVIADO_CON_OTROS_DATOS, CajaError, ClaveIdempotenciaReutilizadaError

logger = logging.getLogger(__name__)


@dataclass
class ArqueoCaja:
    """Resumen de una sesión de caja: total vendido, efectivo estimado y detalle.

    `sesion_abierta` indica si el resumen es de la caja actualmente abierta
    (`True`) o de la última sesión ya cerrada (`False`; con todo en cero
    si nunca se abrió una caja).

    `efectivo_estimado_centavos` es una estimación, no un conteo real:
    solo refleja los movimientos y ventas registrados en el sistema
    (no contempla errores de vuelto ni faltantes/sobrantes físicos).
    """

    total_vendido_centavos: int
    total_efectivo_ventas_centavos: int
    efectivo_estimado_centavos: int
    cantidad_ventas: int
    sesion_abierta: bool = False
    ventas: list[Venta] = field(default_factory=list)


def _caja_esta_abierta() -> bool:
    """True si hay una caja abierta (el último movimiento no es un CIERRE)."""
    ultimo_movimiento = repositorio_caja.obtener_ultimo_movimiento()
    return ultimo_movimiento is not None and ultimo_movimiento.tipo != "CIERRE"


def consultar_estado() -> bool:
    """True si hay una caja abierta actualmente (para badges de estado en las interfaces)."""
    return _caja_esta_abierta()


def listar_movimientos() -> list[MovimientoCaja]:
    """Devuelve el historial completo de movimientos de caja, cronológico."""
    return repositorio_caja.listar_movimientos()


def calcular_arqueo_de_sesion() -> ArqueoCaja:
    """Calcula el arqueo de la sesión de caja vigente (o de la última, si
    ya está cerrada): ventas y movimientos entre su APERTURA y su CIERRE.

    Es por sesión y no por día calendario: una caja que cruza medianoche
    o dos aperturas el mismo día no se mezclan. El efectivo estimado suma
    el efectivo físico que entró (montos de apertura e ingresos manuales,
    más ventas cobradas en efectivo) y resta los egresos manuales; las
    ventas con otros medios de pago (tarjeta, transferencia) no mueven el
    efectivo de la caja.
    """
    sesion = repositorio_caja.obtener_ultima_sesion()
    if sesion is None:
        return ArqueoCaja(
            total_vendido_centavos=0,
            total_efectivo_ventas_centavos=0,
            efectivo_estimado_centavos=0,
            cantidad_ventas=0,
        )

    ventas_de_sesion = repositorio_ventas.listar_ventas_de_sesion(sesion)
    movimientos_de_sesion = repositorio_caja.listar_movimientos_de_sesion(sesion)

    total_vendido_centavos = sum(venta.total_centavos for venta in ventas_de_sesion)
    total_efectivo_ventas_centavos = sum(
        venta.total_centavos for venta in ventas_de_sesion if venta.tipo_pago == "EFECTIVO"
    )

    efectivo_estimado_centavos = total_efectivo_ventas_centavos
    for movimiento in movimientos_de_sesion:
        if movimiento.tipo in ("APERTURA", "INGRESO"):
            efectivo_estimado_centavos += movimiento.monto_centavos
        elif movimiento.tipo == "EGRESO":
            efectivo_estimado_centavos -= movimiento.monto_centavos
        # CIERRE es el monto contado al cerrar la caja, no un ingreso: no se suma.

    return ArqueoCaja(
        total_vendido_centavos=total_vendido_centavos,
        total_efectivo_ventas_centavos=total_efectivo_ventas_centavos,
        efectivo_estimado_centavos=efectivo_estimado_centavos,
        cantidad_ventas=len(ventas_de_sesion),
        sesion_abierta=sesion.abierta,
        ventas=ventas_de_sesion,
    )


def abrir_caja(
    monto_inicial_centavos: int, descripcion: str | None = None, usuario_id: int | None = None
) -> MovimientoCaja:
    """Abre la caja registrando un movimiento de APERTURA con el monto inicial.

    Raises:
        CajaError: si ya hay una caja abierta.
        DatosInvalidosError: si `monto_inicial_centavos` es negativo.

    `usuario_id` (migración 010) es opcional: `None` (el default) para
    el CLI, que no autentica a nadie -- nunca se inventa un usuario.
    """
    if _caja_esta_abierta():
        raise CajaError("La caja ya está abierta: hay que cerrarla antes de abrir una nueva.")

    movimiento = MovimientoCaja(tipo="APERTURA", monto_centavos=monto_inicial_centavos, descripcion=descripcion)
    movimiento_creado = repositorio_caja.registrar_movimiento(movimiento, usuario_id=usuario_id)
    logger.info("Caja abierta con monto inicial %s centavos", movimiento_creado.monto_centavos)
    return movimiento_creado


def cerrar_caja(
    monto_final_centavos: int, descripcion: str | None = None, usuario_id: int | None = None
) -> MovimientoCaja:
    """Cierra la caja registrando un movimiento de CIERRE con el monto
    contado y la diferencia contra el efectivo esperado (migración 011:
    faltante/sobrante).

    Raises:
        CajaError: si no hay una caja abierta para cerrar.
        DatosInvalidosError: si `monto_final_centavos` es negativo.

    `usuario_id` (migración 010): ver `abrir_caja`.

    `diferencia_centavos = monto_final_centavos - efectivo_estimado_centavos`,
    con `efectivo_estimado_centavos` resuelto acá mismo, en el momento del
    cierre, con `calcular_arqueo_de_sesion()` -- la misma función que ya
    usa `/caja/arqueo`, sin duplicar esa lógica. Se calcula recién
    después de confirmar que la caja está abierta y justo antes de
    persistir, para no depender de un valor que el usuario haya visto
    antes en otra pantalla (la única ventana real que queda es la propia
    ejecución de esta función, no el tiempo que tarde el cajero en mirar
    el arqueo y completar el formulario de cierre).
    """
    if not _caja_esta_abierta():
        raise CajaError("No hay una caja abierta para cerrar.")

    efectivo_estimado_centavos = calcular_arqueo_de_sesion().efectivo_estimado_centavos
    diferencia_centavos = monto_final_centavos - efectivo_estimado_centavos

    movimiento = MovimientoCaja(
        tipo="CIERRE",
        monto_centavos=monto_final_centavos,
        descripcion=descripcion,
        diferencia_centavos=diferencia_centavos,
    )
    movimiento_creado = repositorio_caja.registrar_movimiento(movimiento, usuario_id=usuario_id)
    logger.info(
        "Caja cerrada con monto final %s centavos (diferencia %s centavos, %s)",
        movimiento_creado.monto_centavos,
        movimiento_creado.diferencia_centavos,
        clasificar_diferencia(diferencia_centavos),
    )
    return movimiento_creado


def _resolver_reintento(existente: MovimientoCaja, solicitado: MovimientoCaja) -> MovimientoCaja:
    """Una clave ya usada devuelve el movimiento original solo si el pedido
    es el mismo; con otros datos (ej. "atrás" en el navegador y reenviar un
    monto distinto) se rechaza en vez de dar por registrado algo que no lo está."""
    if (existente.tipo, existente.monto_centavos, existente.descripcion) != (
        solicitado.tipo,
        solicitado.monto_centavos,
        solicitado.descripcion,
    ):
        raise ClaveIdempotenciaReutilizadaError(MENSAJE_FORMULARIO_REENVIADO_CON_OTROS_DATOS)
    return existente


def _registrar_movimiento_manual(
    movimiento: MovimientoCaja, mensaje_sin_caja: str, usuario_id: int | None, clave_idempotencia: str | None
) -> MovimientoCaja:
    # El reintento de un movimiento ya registrado se resuelve antes de exigir la
    # caja abierta: si la caja se cerró entre el envío original y el reintento,
    # el reintento sigue devolviendo el resultado original (mismo criterio que ventas).
    if clave_idempotencia is not None:
        existente = repositorio_caja.obtener_por_clave_idempotencia(clave_idempotencia)
        if existente is not None:
            return _resolver_reintento(existente, movimiento)
    if not _caja_esta_abierta():
        raise CajaError(mensaje_sin_caja)
    creado = repositorio_caja.registrar_movimiento(
        movimiento, usuario_id=usuario_id, clave_idempotencia=clave_idempotencia
    )
    # Si otra request con la misma clave ganó la carrera, `registrar_movimiento`
    # devuelve la suya: se valida igual que un reintento.
    return _resolver_reintento(creado, movimiento)


def registrar_ingreso(
    monto_centavos: int,
    descripcion: str,
    usuario_id: int | None = None,
    clave_idempotencia: str | None = None,
) -> MovimientoCaja:
    """Registra un ingreso manual de dinero (ej. cambio inicial, un aporte).

    Raises:
        CajaError: si no hay una caja abierta.
        DatosInvalidosError: si `monto_centavos` es negativo o `descripcion` está vacía.

    `usuario_id` (migración 010): ver `abrir_caja`.

    `clave_idempotencia` (V1.1): un reenvío con la misma clave devuelve el
    movimiento ya registrado en vez de duplicarlo.
    """
    movimiento = MovimientoCaja(tipo="INGRESO", monto_centavos=monto_centavos, descripcion=descripcion)
    movimiento_creado = _registrar_movimiento_manual(
        movimiento, "No se pueden registrar ingresos sin una caja abierta.", usuario_id, clave_idempotencia
    )
    logger.info(
        "Ingreso de caja registrado: %s centavos (%s)",
        movimiento_creado.monto_centavos,
        movimiento_creado.descripcion,
    )
    return movimiento_creado


def registrar_egreso(
    monto_centavos: int,
    descripcion: str,
    usuario_id: int | None = None,
    clave_idempotencia: str | None = None,
) -> MovimientoCaja:
    """Registra un egreso manual de dinero (ej. pago a proveedor, retiro de efectivo).

    Raises:
        CajaError: si no hay una caja abierta.
        DatosInvalidosError: si `monto_centavos` es negativo o `descripcion` está vacía.

    `usuario_id` (migración 010): ver `abrir_caja`.

    `clave_idempotencia` (V1.1): un reenvío con la misma clave devuelve el
    movimiento ya registrado en vez de duplicarlo.
    """
    movimiento = MovimientoCaja(tipo="EGRESO", monto_centavos=monto_centavos, descripcion=descripcion)
    movimiento_creado = _registrar_movimiento_manual(
        movimiento, "No se pueden registrar egresos sin una caja abierta.", usuario_id, clave_idempotencia
    )
    logger.info(
        "Egreso de caja registrado: %s centavos (%s)",
        movimiento_creado.monto_centavos,
        movimiento_creado.descripcion,
    )
    return movimiento_creado
