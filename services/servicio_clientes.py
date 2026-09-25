"""Casos de uso de gestión de clientes (migración 020).

Alta: OWNER o CASHIER. Edición, desactivación y reactivación: solo OWNER. El
permiso se exige acá además de en la interfaz web, para que no dependa de quién
invoque el caso de uso. Cada escritura y su auditoría forman una única
transacción `BEGIN IMMEDIATE`.

Los nombres pueden repetirse. Un cliente con saldo pendiente no puede
desactivarse; un cliente inactivo no puede asociarse a ninguna venta nueva (ver
`services.servicio_ventas.registrar_venta`).
"""

import logging

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import clientes as repositorio_clientes
from db.repositorios import usuarios as repositorio_usuarios
from domain.cliente import (
    ESTADOS_LISTADO_CLIENTES,
    Cliente,
    ClienteConSaldo,
    DeudaTotal,
    EstadoCuenta,
    MovimientoCuenta,
)
from domain.usuario import exigir_rol
from excepciones import ClienteConSaldoError, ClienteNoEncontradoError, DatosInvalidosError

logger = logging.getLogger(__name__)

_ROLES_ALTA = frozenset({"OWNER", "CASHIER"})
# Tope de resultados del buscador de clientes del POS.
LIMITE_BUSQUEDA_VENTA = 20
_ROLES_ADMINISTRACION = frozenset({"OWNER"})


def crear_cliente(
    nombre: str,
    usuario_id: int,
    telefono: str | None = None,
    email: str | None = None,
    direccion: str | None = None,
    observaciones: str | None = None,
) -> Cliente:
    """Da de alta un cliente activo.

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un usuario activo.
        DatosInvalidosError: si algún dato viola las reglas de `domain.cliente.Cliente`.
    """
    cliente = Cliente(
        nombre=nombre, telefono=telefono, email=email, direccion=direccion, observaciones=observaciones
    )
    with obtener_conexion(inmediata=True) as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _ROLES_ALTA)
        creado = repositorio_clientes.crear_cliente_en_conexion(conexion, cliente)
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, "CLIENTE_CREADO", "CLIENTE", creado.id, f"Cliente creado: {creado.nombre}"
        )
    logger.info("Cliente creado: id=%s usuario_id=%s", creado.id, usuario_id)
    return creado


def editar_cliente(
    cliente_id: int,
    nombre: str,
    usuario_id: int,
    telefono: str | None = None,
    email: str | None = None,
    direccion: str | None = None,
    observaciones: str | None = None,
) -> Cliente:
    """Edita los datos de un cliente (activo o no); no cambia su estado.

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        ClienteNoEncontradoError: si el cliente no existe.
        DatosInvalidosError: si algún dato viola las reglas de `domain.cliente.Cliente`.
    """
    cliente = Cliente(
        id=cliente_id,
        nombre=nombre,
        telefono=telefono,
        email=email,
        direccion=direccion,
        observaciones=observaciones,
    )
    with obtener_conexion(inmediata=True) as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _ROLES_ADMINISTRACION)
        actualizado = repositorio_clientes.actualizar_cliente_en_conexion(conexion, cliente)
        if actualizado is None:
            raise ClienteNoEncontradoError(f"No existe un cliente con id {cliente_id}.")
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, "CLIENTE_EDITADO", "CLIENTE", cliente_id, f"Cliente editado: {actualizado.nombre}"
        )
    logger.info("Cliente editado: id=%s usuario_id=%s", cliente_id, usuario_id)
    return actualizado


def desactivar_cliente(cliente_id: int, usuario_id: int) -> Cliente:
    """Desactiva un cliente (baja lógica). Desactivar uno ya inactivo no hace nada.

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        ClienteNoEncontradoError: si el cliente no existe.
        ClienteConSaldoError: si su cuenta corriente tiene saldo pendiente.
    """
    with obtener_conexion(inmediata=True) as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _ROLES_ADMINISTRACION)
        cliente = repositorio_clientes.obtener_por_id_en_conexion(conexion, cliente_id)
        if cliente is None:
            raise ClienteNoEncontradoError(f"No existe un cliente con id {cliente_id}.")
        if not cliente.activo:
            return cliente
        if repositorio_clientes.obtener_saldo_en_conexion(conexion, cliente_id) > 0:
            raise ClienteConSaldoError(
                f"No se puede desactivar a '{cliente.nombre}': tiene saldo pendiente en su cuenta corriente."
            )
        desactivado = repositorio_clientes.actualizar_activo_en_conexion(conexion, cliente_id, False)
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, "CLIENTE_DESACTIVADO", "CLIENTE", cliente_id, f"Cliente desactivado: {cliente.nombre}"
        )
    logger.info("Cliente desactivado: id=%s usuario_id=%s", cliente_id, usuario_id)
    return desactivado


def reactivar_cliente(cliente_id: int, usuario_id: int) -> Cliente:
    """Reactiva un cliente. Reactivar uno ya activo no hace nada.

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        ClienteNoEncontradoError: si el cliente no existe.
    """
    with obtener_conexion(inmediata=True) as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _ROLES_ADMINISTRACION)
        cliente = repositorio_clientes.obtener_por_id_en_conexion(conexion, cliente_id)
        if cliente is None:
            raise ClienteNoEncontradoError(f"No existe un cliente con id {cliente_id}.")
        if cliente.activo:
            return cliente
        reactivado = repositorio_clientes.actualizar_activo_en_conexion(conexion, cliente_id, True)
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, "CLIENTE_REACTIVADO", "CLIENTE", cliente_id, f"Cliente reactivado: {cliente.nombre}"
        )
    logger.info("Cliente reactivado: id=%s usuario_id=%s", cliente_id, usuario_id)
    return reactivado


def obtener_cliente(cliente_id: int) -> Cliente | None:
    """Cliente por id (activo o no), o `None` si no existe."""
    return repositorio_clientes.obtener_por_id(cliente_id)


def listar_clientes(solo_activos: bool = False) -> list[Cliente]:
    """Clientes ordenados por nombre."""
    return repositorio_clientes.listar(solo_activos)


def obtener_saldo(cliente_id: int) -> int:
    """Saldo de la cuenta corriente del cliente, en centavos.

    Raises:
        ClienteNoEncontradoError: si el cliente no existe.
    """
    if repositorio_clientes.obtener_por_id(cliente_id) is None:
        raise ClienteNoEncontradoError(f"No existe un cliente con id {cliente_id}.")
    return repositorio_clientes.obtener_saldo(cliente_id)


def listar_movimientos_cuenta(cliente_id: int) -> list[MovimientoCuenta]:
    """Libro de cuenta corriente del cliente, en orden cronológico.

    Raises:
        ClienteNoEncontradoError: si el cliente no existe.
    """
    if repositorio_clientes.obtener_por_id(cliente_id) is None:
        raise ClienteNoEncontradoError(f"No existe un cliente con id {cliente_id}.")
    return repositorio_clientes.listar_movimientos_cuenta(cliente_id)


# --- lecturas para la interfaz web (021) ---------------------------------------------------------


def listar_clientes_con_saldo(texto: str | None = None, estado: str = "activos") -> list[ClienteConSaldo]:
    """Clientes con su saldo, ordenados por nombre. `texto` filtra por nombre o teléfono; `estado`
    es `activos` (por defecto), `inactivos` o `todos`.

    Raises:
        DatosInvalidosError: si `estado` no es uno de los tres valores.
    """
    if estado not in ESTADOS_LISTADO_CLIENTES:
        raise DatosInvalidosError(
            f"Estado de listado inválido: {estado!r}. Debe ser uno de {sorted(ESTADOS_LISTADO_CLIENTES)}."
        )
    return repositorio_clientes.listar_con_saldo(texto, estado)


def buscar_clientes_activos_para_venta(texto: str | None = None, limite: int = LIMITE_BUSQUEDA_VENTA) -> list[ClienteConSaldo]:
    """Buscador del POS: solo clientes ACTIVOS (los únicos que pueden recibir ventas nuevas), por
    nombre o teléfono, como máximo `limite` resultados."""
    return repositorio_clientes.listar_con_saldo(texto, "activos", min(limite, LIMITE_BUSQUEDA_VENTA))


def obtener_deuda_total() -> DeudaTotal:
    """Deuda total de la cuenta corriente (suma de saldos positivos, clientes activos o no) y cuántos
    clientes deben, agregada en SQL: no depende de ningún filtro ni página del listado."""
    return repositorio_clientes.obtener_deuda_total()


def obtener_estado_de_cuenta(cliente_id: int) -> EstadoCuenta:
    """Ficha del cliente: datos, saldo, total de cargos, total de cobros y movimientos (el más
    reciente primero), todo leído en una sola transacción.

    Raises:
        ClienteNoEncontradoError: si el cliente no existe.
    """
    with obtener_conexion() as conexion:
        cliente = repositorio_clientes.obtener_por_id_en_conexion(conexion, cliente_id)
        if cliente is None:
            raise ClienteNoEncontradoError(f"No existe un cliente con id {cliente_id}.")
        return EstadoCuenta(
            cliente=cliente,
            resumen=repositorio_clientes.obtener_resumen_cuenta_en_conexion(conexion, cliente_id),
            movimientos=repositorio_clientes.listar_movimientos_cuenta_recientes_en_conexion(conexion, cliente_id),
        )
