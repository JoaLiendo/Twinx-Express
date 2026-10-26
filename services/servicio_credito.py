"""Crédito a clientes (V1.14): políticas por capas, estado financiero, cierres, strikes y autorizaciones.

`politica_credito_efectiva` y `situacion_en_conexion` son los ÚNICOS puntos que resuelven cuánto crédito
tiene un cliente y si puede usarlo: rutas, plantillas y la venta los consultan acá, nunca recalculan las
reglas por su cuenta. Configurar capas, autorizar a un DEMORADO y reabrir un crédito cerrado es solo
OWNER, con auditoría.
"""

import logging
from datetime import date

from db.conexion import ConexionBD, obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import clientes as repositorio_clientes
from db.repositorios import estado_credito as repositorio_estado
from db.repositorios import obligaciones as repositorio_obligaciones
from db.repositorios import politicas_credito as repositorio_politicas
from db.repositorios import usuarios as repositorio_usuarios
from domain.cliente import Cliente
from domain.credito import (
    AMBITO_CLIENTE,
    AMBITO_GENERAL,
    ESTADO_CRITICA,
    ESTADO_DEMORADO,
    ETIQUETAS_ORIGEN,
    MODO_LIMITE,
    MODOS,
    STRIKES_PARA_VETO,
    AutorizacionCredito,
    EpisodioCredito,
    PoliticaCredito,
    PoliticaEfectiva,
    SituacionCredito,
    inicio_de_critica,
    resolver_politica,
    validar_autorizacion,
    validar_observacion_reapertura,
    validar_politica,
)
from domain.dinero import centavos_a_texto
from domain.usuario import exigir_rol
from excepciones import (
    ClienteNoEncontradoError,
    CreditoNoDisponibleError,
    DatosInvalidosError,
    PoliticaCreditoDesactualizadaError,
    ReaperturaCreditoInvalidaError,
)

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


def hoy() -> date:
    """Día de referencia para medir antigüedades. Un único punto (los tests lo fijan para recorrer los días)."""
    return date.today()


def _leer_situacion_en_conexion(conexion: ConexionBD, cliente: Cliente, dia: date) -> SituacionCredito:
    pendientes = repositorio_obligaciones.listar_pendientes_en_conexion(conexion, cliente.id)
    return SituacionCredito(
        politica=politica_efectiva_en_conexion(conexion, cliente),
        saldo_centavos=repositorio_clientes.obtener_saldo_en_conexion(conexion, cliente.id),
        hoy=dia,
        fecha_obligacion_mas_antigua=pendientes[0].fecha_origen if pendientes else None,
        episodio_abierto=repositorio_estado.obtener_episodio_abierto_en_conexion(conexion, cliente.id),
        strikes=repositorio_estado.contar_strikes_en_conexion(conexion, cliente.id),
        autorizacion=repositorio_estado.obtener_ultima_autorizacion_en_conexion(conexion, cliente.id),
    )


def _eventos_pendientes(situacion: SituacionCredito, dia: date) -> bool:
    """¿Hay un cierre o un strike que ya corresponde y todavía no se registró?"""
    if situacion.estado != ESTADO_CRITICA:
        return False
    episodio = situacion.episodio_abierto
    return episodio is None or (episodio.fecha_strike is None and dia >= episodio.fecha_limite_strike)


def situacion_en_conexion(
    conexion: ConexionBD, cliente: Cliente, usuario_id: int | None = None
) -> SituacionCredito:
    """Situación del cliente HOY, registrando antes (en la transacción recibida, que debe ser de escritura si
    hay algo que registrar) el cierre y el strike que ya correspondan. Idempotente: un cierre solo se abre si
    no hay uno abierto y un episodio suma su strike una única vez, sin importar cuántas veces se evalúe.

    No hay proceso en segundo plano: esto corre al abrir la ficha, vender o cobrar. Las fechas registradas
    son las teóricas (origen + 60 para el cierre, inicio + 30 para el strike), no la de la detección.
    """
    dia = hoy()
    situacion = _leer_situacion_en_conexion(conexion, cliente, dia)
    if not _eventos_pendientes(situacion, dia):
        return situacion
    if situacion.episodio_abierto is None:
        mas_antigua = repositorio_obligaciones.listar_pendientes_en_conexion(conexion, cliente.id)[0]
        inicio = inicio_de_critica(mas_antigua.fecha_origen)
        repositorio_estado.abrir_episodio_en_conexion(conexion, cliente.id, mas_antigua.id, inicio)
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, "CREDITO_CIERRE", "CLIENTE", cliente.id,
            f"Crédito de {cliente.nombre} cerrado por deuda crítica desde el {inicio.isoformat()}"
            f" (venta #{mas_antigua.venta_id})",
        )
        situacion = _leer_situacion_en_conexion(conexion, cliente, dia)
    episodio = situacion.episodio_abierto
    if episodio.fecha_strike is None and dia >= episodio.fecha_limite_strike:
        if repositorio_estado.registrar_strike_en_conexion(conexion, episodio.id, episodio.fecha_limite_strike):
            strikes = repositorio_estado.contar_strikes_en_conexion(conexion, cliente.id)
            repositorio_auditoria.registrar_en_conexion(
                conexion, usuario_id, "CREDITO_STRIKE", "CLIENTE", cliente.id,
                f"Strike {strikes} de {cliente.nombre}: siguió en deuda crítica 30 días después del cierre"
                f" ({episodio.fecha_limite_strike.isoformat()})",
            )
            if strikes == STRIKES_PARA_VETO:
                repositorio_auditoria.registrar_en_conexion(
                    conexion, usuario_id, "CREDITO_VETO", "CLIENTE", cliente.id,
                    f"Crédito de {cliente.nombre} vetado para siempre: {STRIKES_PARA_VETO} strikes",
                )
            logger.warning("Strike de crédito registrado: cliente_id=%s strikes=%s", cliente.id, strikes)
        situacion = _leer_situacion_en_conexion(conexion, cliente, dia)
    return situacion


def sincronizar_estado(cliente_id: int, usuario_id: int | None = None) -> None:
    """Registra, en su propia transacción, el cierre o el strike que ya correspondan al cliente. La venta a
    cuenta y el cobro la llaman ANTES de su transacción: si después la operación se rechaza, la historia del
    crédito igual queda registrada. Un cliente inexistente no hace nada (lo informa la operación)."""
    with obtener_conexion() as conexion:
        cliente = repositorio_clientes.obtener_por_id_en_conexion(conexion, cliente_id)
        if cliente is None or not _eventos_pendientes(_leer_situacion_en_conexion(conexion, cliente, hoy()), hoy()):
            return
    with obtener_conexion(inmediata=True) as conexion:
        situacion_en_conexion(conexion, repositorio_clientes.obtener_por_id_en_conexion(conexion, cliente_id), usuario_id)


def situacion_crediticia(cliente_id: int, usuario_id: int | None = None) -> SituacionCredito:
    """Saldo, límite efectivo, disponible, estado financiero, cierre, strikes y autorización del cliente
    (lo que muestran la ficha y el POS), con los eventos pendientes ya registrados.

    Raises:
        ClienteNoEncontradoError: si el cliente no existe.
    """
    sincronizar_estado(cliente_id, usuario_id)
    with obtener_conexion() as conexion:
        cliente = repositorio_clientes.obtener_por_id_en_conexion(conexion, cliente_id)
        if cliente is None:
            raise ClienteNoEncontradoError(f"No existe un cliente con id {cliente_id}.")
        return _leer_situacion_en_conexion(conexion, cliente, hoy())


def exigir_credito_para_venta_en_conexion(
    conexion: ConexionBD, cliente: Cliente, credito_centavos: int, usuario_id: int | None = None
) -> None:
    """Rechaza financiar `credito_centavos` si la situación del cliente no lo permite: veto, crédito cerrado,
    CRÍTICA, DEMORADO sin autorización vigente, sin política o por encima del disponible. La llama la venta
    dentro de su `BEGIN IMMEDIATE`: dos ventas simultáneas no pueden consumir el mismo disponible.

    Raises:
        CreditoNoDisponibleError: con el motivo.
    """
    motivo = situacion_en_conexion(conexion, cliente, usuario_id).motivo_rechazo(credito_centavos)
    if motivo is not None:
        raise CreditoNoDisponibleError(motivo)


def autorizar_demorado(cliente_id: int, compromiso: str, fecha_limite: date, usuario_id: int) -> AutorizacionCredito:
    """OWNER: autoriza por un tiempo acotado a seguir vendiendo a cuenta a un cliente DEMORADO, con el
    compromiso de pago que asumió. No desbloquea nada más: no sirve en CRÍTICA, con el crédito cerrado o
    vetado, y vencida no vale.

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        ClienteNoEncontradoError: si el cliente no existe.
        DatosInvalidosError: si el cliente no está DEMORADO, falta el compromiso o la fecha no es válida.
    """
    with obtener_conexion(inmediata=True) as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _SOLO_OWNER)
        cliente = repositorio_clientes.obtener_por_id_en_conexion(conexion, cliente_id)
        if cliente is None:
            raise ClienteNoEncontradoError(f"No existe un cliente con id {cliente_id}.")
        situacion = situacion_en_conexion(conexion, cliente, usuario_id)
        if situacion.estado != ESTADO_DEMORADO or situacion.vetado or situacion.credito_cerrado:
            raise DatosInvalidosError("Solo se autoriza temporalmente a un cliente DEMORADO con el crédito abierto.")
        compromiso = validar_autorizacion(compromiso, fecha_limite, situacion.hoy)
        autorizacion = repositorio_estado.registrar_autorizacion_en_conexion(
            conexion, cliente_id, compromiso, fecha_limite, usuario_id
        )
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, "CREDITO_AUTORIZACION_DEMORADO", "CLIENTE", cliente_id,
            f"Autorización temporal a {cliente.nombre} hasta el {fecha_limite.isoformat()}. Compromiso: {compromiso}",
        )
    logger.info("Autorización temporal de crédito: cliente_id=%s hasta=%s usuario_id=%s", cliente_id, fecha_limite, usuario_id)
    return autorizacion


def reabrir_credito(cliente_id: int, observacion: str, usuario_id: int) -> EpisodioCredito:
    """OWNER: reabre explícitamente el crédito cerrado por un episodio crítico. Pagar nunca lo reabre solo.

    Raises:
        DatosInvalidosError: si falta la observación.
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        ClienteNoEncontradoError: si el cliente no existe.
        ReaperturaCreditoInvalidaError: si está vetado (3 strikes), no tiene el crédito cerrado (o otro OWNER
            lo reabrió recién) o sigue en deuda crítica.
    """
    observacion = validar_observacion_reapertura(observacion)
    with obtener_conexion(inmediata=True) as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _SOLO_OWNER)
        cliente = repositorio_clientes.obtener_por_id_en_conexion(conexion, cliente_id)
        if cliente is None:
            raise ClienteNoEncontradoError(f"No existe un cliente con id {cliente_id}.")
        situacion = situacion_en_conexion(conexion, cliente, usuario_id)
        if situacion.vetado:
            raise ReaperturaCreditoInvalidaError(
                f"El crédito de '{cliente.nombre}' está vetado para siempre (3 strikes): no puede reabrirse."
            )
        if situacion.episodio_abierto is None:
            raise ReaperturaCreditoInvalidaError(f"'{cliente.nombre}' no tiene el crédito cerrado.")
        if situacion.estado == ESTADO_CRITICA:
            raise ReaperturaCreditoInvalidaError(
                f"'{cliente.nombre}' sigue en deuda crítica: primero tiene que pagar lo más antiguo."
            )
        episodio = situacion.episodio_abierto
        if not repositorio_estado.reabrir_en_conexion(conexion, episodio.id, usuario_id, observacion):
            raise ReaperturaCreditoInvalidaError(f"'{cliente.nombre}' no tiene el crédito cerrado.")
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, "CREDITO_REAPERTURA", "CLIENTE", cliente_id,
            f"Crédito de {cliente.nombre} reabierto (cierre del {episodio.fecha_inicio}). Motivo: {observacion}",
        )
    logger.info("Crédito reabierto: cliente_id=%s episodio_id=%s usuario_id=%s", cliente_id, episodio.id, usuario_id)
    return episodio


def listar_episodios(cliente_id: int) -> list[EpisodioCredito]:
    """Historial de cierres, strikes y reaperturas del cliente, el más reciente primero."""
    return repositorio_estado.listar_episodios(cliente_id)


def listar_autorizaciones(cliente_id: int) -> list[AutorizacionCredito]:
    """Autorizaciones temporales otorgadas al cliente, la más reciente primero."""
    return repositorio_estado.listar_autorizaciones(cliente_id)


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
