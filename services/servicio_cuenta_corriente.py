"""Cobros de cuenta corriente (migración 020).

Un cobro es un pago en efectivo del cliente contra su saldo. Genera, en una
única transacción `BEGIN IMMEDIATE`:

    idempotencia -> cliente -> caja abierta -> saldo -> INGRESO de caja
    (origen COBRO_CUENTA) -> COBRO en el libro -> imputaciones -> auditoría COBRO_CUENTA

Si cualquier paso falla no queda nada: ni el ingreso, ni el cobro, ni las
imputaciones, ni la auditoría, ni cambios de saldo o de arqueo. El ingreso de caja
se audita solo como `COBRO_CUENTA` (no también como `CAJA_INGRESO`).

Imputación (V1.14-D): por defecto FIFO, a las obligaciones pendientes más antiguas.
El OWNER puede, como excepción, repartir el cobro a mano con una observación
obligatoria; queda marcada `MANUAL` y auditada aparte. Las imputaciones son
append-only: nunca se recalculan a partir del saldo total.
"""

import logging

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import caja as repositorio_caja
from db.repositorios import clientes as repositorio_clientes
from db.repositorios import obligaciones as repositorio_obligaciones
from db.repositorios import usuarios as repositorio_usuarios
from domain.caja import MovimientoCaja
from domain.cliente import (
    MEDIO_PAGO_COBRO,
    TIPO_IMPUTACION_FIFO,
    TIPO_IMPUTACION_MANUAL,
    MovimientoCuenta,
    Obligacion,
    SaldoTrasVenta,
    calcular_hash_cobro,
    imputar_fifo,
    validar_cobro,
    validar_descripcion_movimiento,
    validar_imputacion_manual,
    validar_observacion_imputacion,
)
from domain.dinero import centavos_a_texto
from domain.usuario import exigir_rol
from excepciones import (
    CajaCerradaError,
    ClaveIdempotenciaReutilizadaError,
    ClienteNoEncontradoError,
)

logger = logging.getLogger(__name__)

_ROLES_COBRO = frozenset({"OWNER", "CASHIER"})
_SOLO_OWNER = frozenset({"OWNER"})


def obtener_saldo_tras_venta(venta_id: int) -> SaldoTrasVenta | None:
    """Cliente y saldo de su cuenta justo después de una venta a cuenta (para el ticket); `None` si
    la venta no fue a cuenta. Sale del libro (`movimientos_cuenta`), no se recalcula aparte."""
    return repositorio_clientes.obtener_saldo_tras_cargo_de_venta(venta_id)


def listar_obligaciones_pendientes(cliente_id: int) -> list[Obligacion]:
    """Obligaciones con saldo del cliente, la más antigua primero: el orden en que las paga un cobro FIFO."""
    with obtener_conexion() as conexion:
        return repositorio_obligaciones.listar_pendientes_en_conexion(conexion, cliente_id)


def registrar_cobro(
    cliente_id: int,
    monto_centavos: int,
    usuario_id: int,
    descripcion: str | None = None,
    clave_idempotencia: str | None = None,
    medio_pago: str = MEDIO_PAGO_COBRO,
    imputacion_manual: dict[int, int] | None = None,
    observacion_imputacion: str | None = None,
) -> MovimientoCuenta:
    """Registra el cobro (parcial o total) de la cuenta de un cliente, en efectivo.

    Por defecto el cobro se imputa FIFO. `imputacion_manual` (`obligacion_id -> monto`) es la excepción:
    solo un OWNER, con `observacion_imputacion` obligatoria, repartiendo exactamente el monto entre
    obligaciones pendientes del cliente sin superar el saldo de ninguna.

    Con `clave_idempotencia`, un reintento con el mismo contenido devuelve el cobro
    original sin escribir nada, incluso si la caja se cerró entretanto; con otro
    contenido se rechaza.

    El estado activo/inactivo del cliente no interviene: un cliente inactivo que ya
    tiene deuda puede pagarla (solo se le prohíben las ventas nuevas). Uno inactivo
    sin deuda no puede cobrar porque no cumple `0 < monto <= saldo`.

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un usuario activo (OWNER o CASHIER).
        DatosInvalidosError: si la descripción supera el límite.
        ClaveIdempotenciaReutilizadaError: si la clave ya se usó con otro contenido.
        ClienteNoEncontradoError: si el cliente no existe.
        CajaCerradaError: si no hay una caja abierta.
        CobroInvalidoError: si el medio no es efectivo, no se cumple `0 < monto <= saldo` o el reparto
            manual no es válido.
        PermisoDenegadoError: también si un usuario que no es OWNER pide una imputación manual.
    """
    descripcion = validar_descripcion_movimiento(descripcion)
    if imputacion_manual is not None:
        observacion_imputacion = validar_observacion_imputacion(observacion_imputacion)
    contenido_hash = calcular_hash_cobro(cliente_id, monto_centavos, descripcion, imputacion_manual)

    with obtener_conexion(inmediata=True) as conexion:
        usuario = repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id)
        exigir_rol(usuario, _ROLES_COBRO)
        if imputacion_manual is not None:
            exigir_rol(usuario, _SOLO_OWNER)

        # Antes de exigir caja abierta: un reintento de un cobro ya registrado sigue
        # devolviéndolo aunque la caja se haya cerrado entretanto.
        if clave_idempotencia is not None:
            existente = repositorio_clientes.obtener_cobro_por_clave_en_conexion(conexion, clave_idempotencia)
            if existente is not None:
                cobro_existente, hash_existente = existente
                if hash_existente != contenido_hash:
                    raise ClaveIdempotenciaReutilizadaError(
                        "La clave de idempotencia ya fue usada para registrar un cobro con datos distintos."
                    )
                return cobro_existente

        cliente = repositorio_clientes.obtener_por_id_en_conexion(conexion, cliente_id)
        if cliente is None:
            raise ClienteNoEncontradoError(f"No existe un cliente con id {cliente_id}.")
        if repositorio_caja.obtener_sesion_abierta_en_conexion(conexion) is None:
            raise CajaCerradaError("No hay una caja abierta: abrí la caja antes de registrar cobros.")

        saldo_centavos = repositorio_clientes.obtener_saldo_en_conexion(conexion, cliente_id)
        validar_cobro(monto_centavos, saldo_centavos, medio_pago)
        # El reparto se decide antes de escribir nada; queda persistido y nunca se recalcula después.
        pendientes = repositorio_obligaciones.listar_pendientes_en_conexion(conexion, cliente_id)
        if imputacion_manual is None:
            reparto, tipo_imputacion = imputar_fifo(pendientes, monto_centavos), TIPO_IMPUTACION_FIFO
        else:
            reparto = validar_imputacion_manual(pendientes, imputacion_manual, monto_centavos)
            tipo_imputacion = TIPO_IMPUTACION_MANUAL

        ingreso = repositorio_caja.registrar_movimiento_en_conexion(
            conexion,
            MovimientoCaja(
                tipo="INGRESO",
                monto_centavos=monto_centavos,
                descripcion=f"Cobro de cuenta corriente: {cliente.nombre}",
                origen="COBRO_CUENTA",
            ),
            usuario_id=usuario_id,
        )
        cobro = repositorio_clientes.registrar_cobro_en_conexion(
            conexion,
            cliente_id,
            monto_centavos,
            ingreso.id,
            usuario_id,
            descripcion,
            clave_idempotencia,
            contenido_hash if clave_idempotencia is not None else None,
        )
        for obligacion_id, monto in reparto:
            repositorio_obligaciones.registrar_imputacion_en_conexion(
                conexion, cobro.id, obligacion_id, monto, tipo_imputacion, usuario_id, observacion_imputacion
            )
        repositorio_auditoria.registrar_en_conexion(
            conexion,
            usuario_id,
            "COBRO_CUENTA",
            "CLIENTE",
            cliente_id,
            f"Cobro de ${centavos_a_texto(monto_centavos)} a {cliente.nombre}",
        )
        if imputacion_manual is not None:
            ventas_por_obligacion = {o.id: o.venta_id for o in pendientes}
            detalle = ", ".join(
                f"venta #{ventas_por_obligacion[obligacion_id]} ${centavos_a_texto(monto)}" for obligacion_id, monto in reparto
            )
            repositorio_auditoria.registrar_en_conexion(
                conexion,
                usuario_id,
                "COBRO_IMPUTACION_MANUAL",
                "CLIENTE",
                cliente_id,
                f"Cobro #{cobro.id} imputado a mano (excepción): {detalle}. Motivo: {observacion_imputacion}",
            )

    logger.info(
        "Cobro de cuenta corriente registrado: cliente_id=%s monto_centavos=%s usuario_id=%s",
        cliente_id,
        monto_centavos,
        usuario_id,
    )
    return cobro
