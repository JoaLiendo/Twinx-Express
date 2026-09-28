"""Pagos a proveedor (migración 025, V1.9-C).

Un pago reduce la deuda del proveedor (`db.repositorios.movimientos_proveedor`, V1.9-A/B). Genera,
en una única transacción `BEGIN IMMEDIATE`:

    rol -> idempotencia -> proveedor -> [efectivo: caja abierta] -> saldo -> [efectivo: EGRESO de
    caja, origen PAGO_PROVEEDOR] -> PAGO en el libro -> auditoría PAGO_PROVEEDOR_REGISTRADO

Si cualquier paso falla no queda nada: ni el egreso (si era efectivo), ni el pago, ni la
auditoría, ni cambios de saldo o de arqueo. Mismo patrón que
`services.servicio_cuenta_corriente.registrar_cobro`, con una diferencia real: un cobro de cliente
es siempre en efectivo, un pago a proveedor admite además transferencia (sin caja de por medio).
"""

import logging

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import caja as repositorio_caja
from db.repositorios import movimientos_proveedor as repositorio_movimientos_proveedor
from db.repositorios import proveedores as repositorio_proveedores
from db.repositorios import usuarios as repositorio_usuarios
from domain.caja import MovimientoCaja
from domain.dinero import centavos_a_texto
from domain.proveedor import (
    MovimientoProveedor,
    calcular_hash_pago_proveedor,
    validar_medio_pago_proveedor,
    validar_observacion_pago,
    validar_pago_proveedor,
)
from domain.usuario import exigir_rol
from excepciones import (
    CajaCerradaError,
    ClaveIdempotenciaReutilizadaError,
    ProveedorNoEncontradoError,
)

logger = logging.getLogger(__name__)

_ROLES_PAGO = frozenset({"OWNER"})


def registrar_pago(
    proveedor_id: int,
    monto_centavos: int,
    medio_pago: str,
    usuario_id: int,
    observacion: str | None = None,
    clave_idempotencia: str | None = None,
) -> MovimientoProveedor:
    """Registra un pago (parcial o total) a la cuenta de un proveedor, en efectivo o transferencia.

    Con `clave_idempotencia`, un reintento con el mismo contenido devuelve el pago original sin
    escribir nada, incluso si la caja se cerró entretanto; con otro contenido se rechaza.

    El estado activo/inactivo del proveedor no interviene: uno inactivo con deuda puede pagarse
    (solo se le prohíben las compras nuevas), mismo criterio que `domain.cliente`.

    Args:
        proveedor_id: id de un proveedor existente (activo o no).
        monto_centavos: `0 < monto_centavos <= saldo` del proveedor.
        medio_pago: 'EFECTIVO' (exige caja abierta, genera un EGRESO) o 'TRANSFERENCIA' (no toca
            caja).
        usuario_id: quien registra el pago; debe ser un OWNER activo.
        observacion: texto libre opcional.
        clave_idempotencia: identificador que el formulario genera una vez y reenvía igual ante
            cualquier reintento: un reenvío devuelve el pago ya registrado sin duplicarlo.

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        PagoProveedorInvalidoError: si el medio no es válido, o no se cumple `0 < monto <= saldo`.
        ClaveIdempotenciaReutilizadaError: si la clave ya se usó con otro contenido.
        ProveedorNoEncontradoError: si el proveedor no existe.
        CajaCerradaError: si el medio es efectivo y no hay una caja abierta.
    """
    validar_medio_pago_proveedor(medio_pago)
    observacion = validar_observacion_pago(observacion)
    contenido_hash = calcular_hash_pago_proveedor(proveedor_id, monto_centavos, medio_pago, observacion)

    with obtener_conexion(inmediata=True) as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _ROLES_PAGO)

        # Antes de exigir caja abierta: un reintento de un pago ya registrado sigue devolviéndolo
        # aunque la caja se haya cerrado entretanto (mismo criterio que registrar_cobro).
        if clave_idempotencia is not None:
            existente = repositorio_movimientos_proveedor.obtener_pago_por_clave_en_conexion(
                conexion, clave_idempotencia
            )
            if existente is not None:
                pago_existente, hash_existente = existente
                if hash_existente != contenido_hash:
                    raise ClaveIdempotenciaReutilizadaError(
                        "La clave de idempotencia ya fue usada para registrar un pago con datos distintos."
                    )
                return pago_existente

        proveedor = repositorio_proveedores.obtener_por_id_incluyendo_inactivos_en_conexion(conexion, proveedor_id)
        if proveedor is None:
            raise ProveedorNoEncontradoError(f"No existe un proveedor con id {proveedor_id}.")

        if medio_pago == "EFECTIVO" and repositorio_caja.obtener_sesion_abierta_en_conexion(conexion) is None:
            raise CajaCerradaError("No hay una caja abierta: abrí la caja antes de registrar pagos en efectivo.")

        saldo_centavos = repositorio_movimientos_proveedor.obtener_saldo_en_conexion(conexion, proveedor_id)
        validar_pago_proveedor(monto_centavos, saldo_centavos)

        caja_movimiento_id = None
        if medio_pago == "EFECTIVO":
            egreso = repositorio_caja.registrar_movimiento_en_conexion(
                conexion,
                MovimientoCaja(
                    tipo="EGRESO",
                    monto_centavos=monto_centavos,
                    descripcion=f"Pago a proveedor: {proveedor.nombre}",
                    origen="PAGO_PROVEEDOR",
                ),
                usuario_id=usuario_id,
            )
            caja_movimiento_id = egreso.id

        pago = repositorio_movimientos_proveedor.registrar_pago_en_conexion(
            conexion,
            proveedor_id,
            monto_centavos,
            medio_pago,
            caja_movimiento_id,
            observacion,
            usuario_id,
            clave_idempotencia,
            contenido_hash if clave_idempotencia is not None else None,
        )
        repositorio_auditoria.registrar_en_conexion(
            conexion,
            usuario_id,
            "PAGO_PROVEEDOR_REGISTRADO",
            "PROVEEDOR",
            proveedor_id,
            f"Pago de ${centavos_a_texto(monto_centavos)} a {proveedor.nombre} ({medio_pago})",
        )

    logger.info(
        "Pago a proveedor registrado: proveedor_id=%s monto_centavos=%s medio_pago=%s usuario_id=%s",
        proveedor_id,
        monto_centavos,
        medio_pago,
        usuario_id,
    )
    return pago
