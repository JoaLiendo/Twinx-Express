"""Casos de uso de gestión de clientes (migración 020).

Alta: OWNER o CASHIER. Edición, desactivación y reactivación: solo OWNER. El
permiso se exige acá además de en la interfaz web, para que no dependa de quién
invoque el caso de uso. Cada escritura y su auditoría forman una única
transacción `BEGIN IMMEDIATE`.

Los nombres pueden repetirse. Un cliente con saldo pendiente no puede
desactivarse; un cliente inactivo no puede asociarse a ninguna venta nueva (ver
`services.servicio_ventas.registrar_venta`).

V1.14-A: tipo de cliente y condición fiscal (independientes) y compradores autorizados de una
EMPRESA (máximo 2 activos; alta y baja solo OWNER, con historial que nunca se reescribe).
"""

import logging

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import clientes as repositorio_clientes
from db.repositorios import compradores_autorizados as repositorio_autorizados
from db.repositorios import usuarios as repositorio_usuarios
from domain.cliente import (
    ESTADOS_LISTADO_CLIENTES,
    MAXIMO_AUTORIZADOS_ACTIVOS,
    TIPO_PERSONA,
    Cliente,
    ClienteConSaldo,
    CompradorAutorizado,
    DeudaTotal,
    EstadoCuenta,
    MovimientoCuenta,
)
from domain.usuario import exigir_rol
from excepciones import (
    ClienteConSaldoError,
    ClienteInactivoError,
    ClienteNoEncontradoError,
    CompradorAutorizadoNoEncontradoError,
    DatosInvalidosError,
    LimiteCompradoresAutorizadosError,
)

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
    tipo_cliente: str = TIPO_PERSONA,
    condicion_fiscal: str | None = None,
    razon_social: str | None = None,
    cuit: str | None = None,
    contacto: str | None = None,
) -> Cliente:
    """Da de alta un cliente activo. Todo cliente nuevo nace clasificado (PERSONA o EMPRESA); la
    condición fiscal de una persona es opcional ("sin declarar").

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un usuario activo.
        DatosInvalidosError: si algún dato viola las reglas de `domain.cliente.Cliente`.
        CuitClienteDuplicadoError: si otro cliente ya tiene ese CUIT.
    """
    cliente = Cliente(
        nombre=nombre,
        telefono=telefono,
        email=email,
        direccion=direccion,
        observaciones=observaciones,
        tipo_cliente=tipo_cliente or None,
        condicion_fiscal=condicion_fiscal or None,
        razon_social=razon_social,
        cuit=cuit,
        contacto=contacto,
    )
    if cliente.tipo_cliente is None:
        raise DatosInvalidosError("Elegí si el cliente es una persona o una empresa.")
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
    tipo_cliente: str | None = None,
    condicion_fiscal: str | None = None,
    razon_social: str | None = None,
    cuit: str | None = None,
    contacto: str | None = None,
) -> Cliente:
    """Edita los datos de un cliente (activo o no); no cambia su estado.

    `tipo_cliente`/`condicion_fiscal` en `None` conservan los que ya tenía: una clasificación nunca
    se borra (un histórico sin clasificar sigue así hasta que el OWNER elija). El resto de los campos
    opcionales se reemplazan tal cual vienen.

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        ClienteNoEncontradoError: si el cliente no existe.
        DatosInvalidosError: si algún dato viola las reglas de `domain.cliente.Cliente`, o se intenta
            dejar de ser EMPRESA con compradores autorizados activos.
        CuitClienteDuplicadoError: si otro cliente ya tiene ese CUIT.
    """
    with obtener_conexion(inmediata=True) as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _ROLES_ADMINISTRACION)
        actual = repositorio_clientes.obtener_por_id_en_conexion(conexion, cliente_id)
        if actual is None:
            raise ClienteNoEncontradoError(f"No existe un cliente con id {cliente_id}.")
        cliente = Cliente(
            id=cliente_id,
            nombre=nombre,
            telefono=telefono,
            email=email,
            direccion=direccion,
            observaciones=observaciones,
            tipo_cliente=tipo_cliente or actual.tipo_cliente,
            condicion_fiscal=condicion_fiscal or actual.condicion_fiscal,
            razon_social=razon_social,
            cuit=cuit,
            contacto=contacto,
        )
        if (
            actual.es_empresa
            and not cliente.es_empresa
            and repositorio_autorizados.contar_activos_en_conexion(conexion, cliente_id) > 0
        ):
            raise DatosInvalidosError("Dá de baja a los compradores autorizados antes de cambiar el tipo de la empresa.")
        actualizado = repositorio_clientes.actualizar_cliente_en_conexion(conexion, cliente)
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


def agregar_comprador_autorizado(
    cliente_id: int,
    nombre: str,
    usuario_id: int,
    documento: str | None = None,
    contacto: str | None = None,
) -> CompradorAutorizado:
    """Abre un período de autorización para comprar a nombre de una EMPRESA activa. Reactivar a alguien
    dado de baja es lo mismo: un período nuevo (el anterior queda en el historial). Nunca se crea un
    autorizado de forma automática (un comprador excepcional de una venta no lo es).

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        ClienteNoEncontradoError / ClienteInactivoError: si la empresa no existe o está inactiva.
        DatosInvalidosError: si el cliente no es una EMPRESA o los datos no son válidos.
        LimiteCompradoresAutorizadosError: si ya tiene el máximo de autorizados activos.
    """
    with obtener_conexion(inmediata=True) as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _ROLES_ADMINISTRACION)
        cliente = repositorio_clientes.obtener_por_id_en_conexion(conexion, cliente_id)
        if cliente is None:
            raise ClienteNoEncontradoError(f"No existe un cliente con id {cliente_id}.")
        if not cliente.es_empresa:
            raise DatosInvalidosError("Solo una empresa puede tener compradores autorizados.")
        if not cliente.activo:
            raise ClienteInactivoError(f"La empresa '{cliente.nombre}' está inactiva.")
        comprador = CompradorAutorizado(cliente_id=cliente_id, nombre=nombre, documento=documento, contacto=contacto)
        if repositorio_autorizados.contar_activos_en_conexion(conexion, cliente_id) >= MAXIMO_AUTORIZADOS_ACTIVOS:
            raise LimiteCompradoresAutorizadosError(
                f"'{cliente.nombre}' ya tiene {MAXIMO_AUTORIZADOS_ACTIVOS} compradores autorizados activos: "
                "dá de baja a uno antes de agregar otro."
            )
        creado = repositorio_autorizados.crear_en_conexion(conexion, comprador, usuario_id)
        repositorio_auditoria.registrar_en_conexion(
            conexion,
            usuario_id,
            "CLIENTE_AUTORIZADO_ALTA",
            "CLIENTE",
            cliente_id,
            f"Comprador autorizado #{creado.id} de {cliente.nombre}: {creado.nombre}",
        )
    logger.info("Comprador autorizado agregado: id=%s cliente_id=%s usuario_id=%s", creado.id, cliente_id, usuario_id)
    return creado


def dar_de_baja_comprador_autorizado(cliente_id: int, comprador_id: int, usuario_id: int) -> CompradorAutorizado:
    """Cierra el período de un comprador autorizado. Dar de baja uno ya dado de baja no hace nada.

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        CompradorAutorizadoNoEncontradoError: si el período no existe o es de otro cliente.
    """
    with obtener_conexion(inmediata=True) as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _ROLES_ADMINISTRACION)
        comprador = repositorio_autorizados.obtener_en_conexion(conexion, comprador_id)
        if comprador is None or comprador.cliente_id != cliente_id:
            raise CompradorAutorizadoNoEncontradoError("El comprador autorizado no existe para este cliente.")
        if not comprador.activo:
            return comprador
        dado_de_baja = repositorio_autorizados.dar_de_baja_en_conexion(conexion, comprador_id, usuario_id)
        repositorio_auditoria.registrar_en_conexion(
            conexion,
            usuario_id,
            "CLIENTE_AUTORIZADO_BAJA",
            "CLIENTE",
            cliente_id,
            f"Baja del comprador autorizado #{comprador_id}: {comprador.nombre}",
        )
    logger.info("Comprador autorizado dado de baja: id=%s cliente_id=%s usuario_id=%s", comprador_id, cliente_id, usuario_id)
    return dado_de_baja


def listar_compradores_autorizados(cliente_id: int) -> list[CompradorAutorizado]:
    """Períodos de autorización de la empresa: activos primero, después el historial."""
    return repositorio_autorizados.listar_por_cliente(cliente_id)


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
