"""Crédito a clientes (V1.14): políticas por capas.

`politica_credito_efectiva` es el ÚNICO punto que resuelve cuánto crédito tiene un cliente: rutas,
plantillas y la venta lo consultan acá, nunca recalculan las capas por su cuenta. Configurar cualquier
capa es solo OWNER, con control de versión (dos OWNER editando a la vez no se pisan) y auditoría.
"""

import logging

from db.conexion import ConexionBD, obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import clientes as repositorio_clientes
from db.repositorios import politicas_credito as repositorio_politicas
from db.repositorios import usuarios as repositorio_usuarios
from domain.cliente import Cliente
from domain.credito import (
    AMBITO_CLIENTE,
    AMBITO_GENERAL,
    ETIQUETAS_ORIGEN,
    MODO_LIMITE,
    MODOS,
    PoliticaCredito,
    PoliticaEfectiva,
    SituacionCredito,
    resolver_politica,
    validar_politica,
)
from domain.dinero import centavos_a_texto
from domain.usuario import exigir_rol
from excepciones import ClienteNoEncontradoError, CreditoNoDisponibleError, PoliticaCreditoDesactualizadaError

logger = logging.getLogger(__name__)

_SOLO_OWNER = frozenset({"OWNER"})


def _describir(politica: PoliticaCredito) -> str:
    if politica.modo == MODO_LIMITE:
        return f"límite ${centavos_a_texto(politica.limite_centavos)}"
    return MODOS[politica.modo].lower()


def politica_efectiva_en_conexion(conexion: ConexionBD, cliente: Cliente) -> PoliticaEfectiva:
    """Ver `politica_credito_efectiva`; dentro de una transacción ya abierta."""
    general = repositorio_politicas.obtener_en_conexion(conexion, AMBITO_GENERAL)
    por_tipo = (
        repositorio_politicas.obtener_en_conexion(conexion, cliente.tipo_cliente) if cliente.tipo_cliente else None
    )
    individual = repositorio_politicas.obtener_en_conexion(conexion, AMBITO_CLIENTE, cliente.id)
    return resolver_politica(general, por_tipo, individual)


def situacion_en_conexion(conexion: ConexionBD, cliente: Cliente) -> SituacionCredito:
    """Política efectiva + deuda actual del cliente, dentro de una transacción ya abierta."""
    return SituacionCredito(
        politica=politica_efectiva_en_conexion(conexion, cliente),
        saldo_centavos=repositorio_clientes.obtener_saldo_en_conexion(conexion, cliente.id),
    )


def situacion_crediticia(cliente_id: int) -> SituacionCredito:
    """Saldo, límite efectivo y crédito disponible del cliente (lo que muestran la ficha y el POS).

    Raises:
        ClienteNoEncontradoError: si el cliente no existe.
    """
    with obtener_conexion() as conexion:
        cliente = repositorio_clientes.obtener_por_id_en_conexion(conexion, cliente_id)
        if cliente is None:
            raise ClienteNoEncontradoError(f"No existe un cliente con id {cliente_id}.")
        return situacion_en_conexion(conexion, cliente)


def exigir_credito_para_venta_en_conexion(conexion: ConexionBD, cliente: Cliente, credito_centavos: int) -> None:
    """Rechaza financiar `credito_centavos` si la situación del cliente no lo permite. La llama la venta
    dentro de su `BEGIN IMMEDIATE`: dos ventas simultáneas no pueden consumir el mismo disponible.

    Raises:
        CreditoNoDisponibleError: con el motivo (sin política, sin crédito o límite superado).
    """
    motivo = situacion_en_conexion(conexion, cliente).motivo_rechazo(credito_centavos)
    if motivo is not None:
        raise CreditoNoDisponibleError(motivo)


def politica_credito_efectiva(cliente_id: int) -> PoliticaEfectiva:
    """Política de crédito que rige para el cliente (GENERAL -> TIPO -> INDIVIDUAL). Un cliente histórico
    sin clasificar solo toma la general o la individual.

    Raises:
        ClienteNoEncontradoError: si el cliente no existe.
    """
    with obtener_conexion() as conexion:
        cliente = repositorio_clientes.obtener_por_id_en_conexion(conexion, cliente_id)
        if cliente is None:
            raise ClienteNoEncontradoError(f"No existe un cliente con id {cliente_id}.")
        return politica_efectiva_en_conexion(conexion, cliente)


def listar_politicas_globales() -> list[PoliticaCredito]:
    """Capas GENERAL, PERSONA y EMPRESA (las no configuradas como `HEREDAR`, versión 0)."""
    return repositorio_politicas.listar_globales()


def obtener_politica_individual(cliente_id: int) -> PoliticaCredito:
    """Excepción individual del cliente (`HEREDAR`, versión 0, si no tiene)."""
    return repositorio_politicas.obtener_individual(cliente_id)


def configurar_politica(
    ambito: str,
    modo: str,
    limite_centavos: int | None,
    usuario_id: int,
    *,
    version_esperada: int,
    cliente_id: int | None = None,
) -> PoliticaCredito:
    """Configura una capa de crédito (GENERAL, PERSONA, EMPRESA o la individual de `cliente_id`).

    `version_esperada` es la versión que el OWNER vio al abrir el formulario (0 si la capa nunca se
    configuró): si otro OWNER la cambió entretanto, se rechaza sin pisar nada. Bajar un límite por
    debajo de la deuda actual está permitido: no toca la deuda, solo deja sin crédito disponible.

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        DatosInvalidosError: si la capa no es válida (ver `domain.credito.validar_politica`).
        ClienteNoEncontradoError: si la capa es individual y el cliente no existe.
        PoliticaCreditoDesactualizadaError: si la capa cambió desde `version_esperada`.
    """
    validar_politica(ambito, modo, limite_centavos, cliente_id)
    with obtener_conexion(inmediata=True) as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _SOLO_OWNER)
        cliente = None
        if cliente_id is not None:
            cliente = repositorio_clientes.obtener_por_id_en_conexion(conexion, cliente_id)
            if cliente is None:
                raise ClienteNoEncontradoError(f"No existe un cliente con id {cliente_id}.")
        anterior = repositorio_politicas.obtener_en_conexion(conexion, ambito, cliente_id)
        if anterior.version != version_esperada:
            raise PoliticaCreditoDesactualizadaError(
                "Otro usuario cambió esta política mientras la editabas: revisá el valor actual y volvé a guardar."
            )
        nueva = repositorio_politicas.guardar_en_conexion(conexion, ambito, modo, limite_centavos, cliente_id, usuario_id)
        destino = f"de {cliente.nombre}" if cliente is not None else ETIQUETAS_ORIGEN[ambito].lower()
        repositorio_auditoria.registrar_en_conexion(
            conexion,
            usuario_id,
            "POLITICA_CREDITO_CAMBIADA",
            "CLIENTE" if cliente is not None else "CONFIGURACION",
            cliente_id,
            f"Crédito {destino}: {_describir(anterior)} -> {_describir(nueva)}",
        )
    logger.info(
        "Política de crédito configurada: ambito=%s cliente_id=%s modo=%s limite=%s usuario_id=%s",
        ambito, cliente_id, modo, limite_centavos, usuario_id,
    )
    return nueva
