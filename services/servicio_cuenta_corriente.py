"""Cobros de cuenta corriente (migración 020).

Un cobro es un pago en efectivo del cliente contra su saldo. Genera, en una
única transacción `BEGIN IMMEDIATE`:

    idempotencia -> cliente -> caja abierta -> saldo -> INGRESO de caja
    (origen COBRO_CUENTA) -> COBRO en el libro -> auditoría COBRO_CUENTA

Si cualquier paso falla no queda nada: ni el ingreso, ni el cobro, ni la
auditoría, ni cambios de saldo o de arqueo. El ingreso de caja se audita solo
como `COBRO_CUENTA` (no también como `CAJA_INGRESO`).
"""

import logging

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import caja as repositorio_caja
from db.repositorios import clientes as repositorio_clientes
from db.repositorios import usuarios as repositorio_usuarios
from domain.caja import MovimientoCaja
from domain.cliente import (
    MEDIO_PAGO_COBRO,
    MovimientoCuenta,
    calcular_hash_cobro,
    validar_cobro,
    validar_descripcion_movimiento,
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


def registrar_cobro(
    cliente_id: int,
    monto_centavos: int,
    usuario_id: int,
    descripcion: str | None = None,
    clave_idempotencia: str | None = None,
    medio_pago: str = MEDIO_PAGO_COBRO,
) -> MovimientoCuenta:
    """Registra el cobro (parcial o total) de la cuenta de un cliente, en efectivo.

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
        CobroInvalidoError: si el medio no es efectivo o no se cumple `0 < monto <= saldo`.
    """
    descripcion = validar_descripcion_movimiento(descripcion)
    contenido_hash = calcular_hash_cobro(cliente_id, monto_centavos, descripcion)

    with obtener_conexion(inmediata=True) as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _ROLES_COBRO)

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
        repositorio_auditoria.registrar_en_conexion(
            conexion,
            usuario_id,
            "COBRO_CUENTA",
            "CLIENTE",
            cliente_id,
            f"Cobro de ${centavos_a_texto(monto_centavos)} a {cliente.nombre}",
        )

    logger.info(
        "Cobro de cuenta corriente registrado: cliente_id=%s monto_centavos=%s usuario_id=%s",
        cliente_id,
        monto_centavos,
        usuario_id,
    )
    return cobro
