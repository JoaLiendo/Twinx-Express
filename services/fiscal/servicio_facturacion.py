"""Fiscalización de una venta con CAE (WSFEv1), conciliación de resultados inciertos y recuperación.

No existe una transacción distribuida SQLite + ARCA: el orden de los pasos es la garantía.

1. Snapshot y validación (receptor, clase, IVA, otros impuestos nacionales indirectos, tablas ARCA) sin
   escribir nada: si falta un dato, no se crea ningún comprobante y la venta sigue "registrada". Un
   PENDIENTE se revalida (otros impuestos, con el mismo alcance por clase y receptor) antes de tocar ARCA.
2. Alta PENDIENTE. Un índice único deja un solo comprobante principal vivo por venta (doble POST).
3. Bajo un candado por secuencia (punto de venta + tipo), y si no hay otro comprobante en vuelo en ella:
   `FECompUltimoAutorizado` → número = último de ARCA + 1. SQLite nunca decide el número.
4. ENVIANDO con ese número, persistido ANTES de enviar `FECAESolicitar`.
5. Respuesta A → AUTORIZADO (CAE, vencimiento, observaciones) en una transacción. R → RECHAZADO sin CAE.
   El pedido no llegó a salir → vuelve a PENDIENTE (número liberado). Cualquier otra falla después de
   enviar → INCIERTO, que conserva su número: una consulta negativa no prueba que ARCA no lo procesó, porque
   puede asentarlo más tarde. Se resuelve con `FECompConsultar` o, por decisión del dueño, reenviando ese
   MISMO número: ARCA no autoriza dos veces un número, así que nunca se llega a N+1 con N sin resolver.
"""

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import clientes as repositorio_clientes
from db.repositorios import fiscal as repositorio_fiscal
from db.repositorios import usuarios as repositorio_usuarios
from domain.auditoria import (
    ACCION_FISCAL_AUTORIZADO,
    ACCION_FISCAL_DESCARTADO,
    ACCION_FISCAL_ERROR,
    ACCION_FISCAL_INCIERTO,
    ACCION_FISCAL_NC_AUTORIZADA,
    ACCION_FISCAL_RECHAZADO,
    ACCION_FISCAL_RECONCILIADO,
    ACCION_FISCAL_SOLICITUD,
)
from domain.dinero import centavos_a_texto
from domain.fiscal import comprobante as estados
from domain.fiscal import otros_impuestos, reglas, tablas_arca
from domain.fiscal.comprobante import ComprobanteFiscal, calcular_contenido_hash, resolver_receptor
from domain.fiscal.emisor import PerfilFiscal
from domain.fiscal.iva import calcular_importes
from domain.fiscal.protocolo import ComprobanteAsociado, MensajeARCA, RespuestaAutorizacion, SolicitudAutorizacion
from domain.usuario import exigir_rol
from domain.fiscal.documento import armar_borrador
from excepciones import (
    ComprobanteFiscalNoEncontradoError,
    ConfiguracionFiscalError,
    ErrorBaseDatos,
    ErrorComunicacionARCA,
    ErrorFiscal,
    ErrorWSAA,
    FiscalizacionBloqueadaError,
    FiscalizacionNoDisponibleError,
    OperacionFiscalInvalidaError,
    SecuenciaFiscalBloqueadaError,
    TicketYaVigenteError,
    VentaNoEncontradaError,
)
from services.fiscal import servicio_custodia, servicio_parametros
from services.fiscal.gateway import GatewayARCA, gateway_para

logger = logging.getLogger(__name__)

_ROLES_OPERATIVOS = frozenset({"OWNER", "CASHIER"})
_SOLO_OWNER = frozenset({"OWNER"})
ENTIDAD = "COMPROBANTE_FISCAL"

_candados: dict[tuple, threading.Lock] = {}
_candado_registro = threading.Lock()


def _candado_secuencia(clave: tuple) -> threading.Lock:
    with _candado_registro:
        return _candados.setdefault(clave, threading.Lock())


def _clave_secuencia(comprobante: ComprobanteFiscal) -> tuple:
    return (comprobante.ambiente, comprobante.emisor.cuit, comprobante.punto_venta, comprobante.tipo_comprobante)


def _exigir_usuario(usuario_id: int | None, roles: frozenset[str]) -> None:
    with obtener_conexion() as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id) if usuario_id else None, roles)


def perfil_listo() -> PerfilFiscal:
    perfil = repositorio_fiscal.obtener_perfil()
    if perfil is None:
        raise ConfiguracionFiscalError("La facturación fiscal no está configurada: el dueño debe completar el perfil fiscal.")
    if not perfil.punto_venta_cae_listo:
        raise ConfiguracionFiscalError("El punto de venta no está validado con ARCA en el ambiente vigente.")
    return perfil


def _obtener(comprobante_id: int) -> ComprobanteFiscal:
    comprobante = repositorio_fiscal.obtener_comprobante(comprobante_id)
    if comprobante is None:
        raise ComprobanteFiscalNoEncontradoError(f"No existe el comprobante fiscal {comprobante_id}.")
    return comprobante


def _mensajes_json(mensajes: tuple[MensajeARCA, ...]) -> str | None:
    return json.dumps([m.como_dict() for m in mensajes], ensure_ascii=False) if mensajes else None


def _fecha_iso(aaaammdd: str | None) -> str | None:
    if not aaaammdd:
        return None
    return datetime.strptime(aaaammdd[:8], "%Y%m%d").date().isoformat()


def auditar(conexion, usuario_id: int | None, accion: str, comprobante: ComprobanteFiscal, detalle: str) -> None:
    repositorio_auditoria.registrar_en_conexion(
        conexion, usuario_id, accion, ENTIDAD, comprobante.id,
        f"{comprobante.nombre_tipo} {comprobante.numero_formateado} (venta #{comprobante.venta_id}): {detalle}",
    )


def _cae_valido(codigo: str | None) -> bool:
    return bool(codigo) and len(codigo) == 14 and codigo.isdigit()


# --- 1-2. Preparar el comprobante -------------------------------------------------------------------


@dataclass(frozen=True)
class _Preparado:
    nuevo: repositorio_fiscal.NuevoComprobante


def preparar_principal(perfil: PerfilFiscal, venta_id: int, usuario_id: int | None, punto_venta: int | None = None) -> _Preparado:
    """Snapshot del comprobante principal de la venta (sin escribir nada). `punto_venta` es el de CAE salvo
    que se emita bajo CAEA (ver `servicio_caea`)."""
    with obtener_conexion() as conexion:
        venta = repositorio_fiscal.venta_para_fiscalizar_en_conexion(conexion, venta_id)
        if venta is None:
            raise VentaNoEncontradaError(f"No existe la venta {venta_id}.")
        if venta.estado != "ACTIVA":
            raise OperacionFiscalInvalidaError("Una venta anulada no se fiscaliza.")
        cliente = (
            repositorio_clientes.obtener_por_id_en_conexion(conexion, venta.cliente_id) if venta.cliente_id else None
        )
    hoy = date.today()
    receptor = resolver_receptor(cliente, venta.total_centavos, hoy)
    clase = reglas.resolver_clase(perfil.condicion_iva, receptor.condicion_iva, perfil.habilitacion_clase_a)
    tipo = reglas.tipo_comprobante(clase, tablas_arca.ROL_PRINCIPAL)
    importes = calcular_importes(list(venta.lineas), clase, receptor_consumidor_final=receptor.es_consumidor_final)
    if importes.total_centavos != venta.total_centavos:
        raise FiscalizacionBloqueadaError("Los importes fiscales no reconcilian con el total de la venta.")
    servicio_parametros.validar_codigos(
        perfil.ambiente, clase, tipo, receptor.doc_tipo, receptor.condicion_iva_receptor_id, importes, hoy
    )
    emisor = perfil.snapshot(punto_venta or perfil.punto_venta_cae)
    return _Preparado(
        repositorio_fiscal.NuevoComprobante(
            venta_id=venta_id,
            rol=tablas_arca.ROL_PRINCIPAL,
            comprobante_asociado_id=None,
            ambiente=perfil.ambiente,
            modalidad=estados.MODALIDAD_CAE,
            caea_id=None,
            tipo_comprobante=tipo,
            clase=clase,
            concepto=tablas_arca.CONCEPTO_PRODUCTOS,
            moneda=tablas_arca.MONEDA_PESOS,
            cotizacion=str(tablas_arca.COTIZACION_PESOS),
            emisor=emisor,
            receptor=receptor,
            importes=importes,
            lineas=venta.lineas,
            contenido_hash=calcular_contenido_hash(
                tablas_arca.ROL_PRINCIPAL, None, perfil.ambiente, emisor, tipo, receptor, importes, list(venta.lineas)
            ),
            usuario_id=usuario_id,
            estado=estados.PENDIENTE,
        )
    )


def _alta(nuevo: repositorio_fiscal.NuevoComprobante, buscar_existente) -> ComprobanteFiscal:
    """Inserta el PENDIENTE o, si otra solicitud ganó la carrera, devuelve el que quedó vivo."""
    try:
        with obtener_conexion(inmediata=True) as conexion:
            existente = buscar_existente(conexion)
            if existente is not None:
                return existente
            comprobante_id = repositorio_fiscal.insertar_comprobante_en_conexion(conexion, nuevo)
            comprobante = repositorio_fiscal.obtener_comprobante_en_conexion(conexion, comprobante_id)
            auditar(conexion, nuevo.usuario_id, ACCION_FISCAL_SOLICITUD, comprobante,
                     f"solicitud por ${centavos_a_texto(nuevo.importes.total_centavos)}")
            return comprobante
    except ErrorBaseDatos:
        with obtener_conexion() as conexion:
            existente = buscar_existente(conexion)
        if existente is None:
            raise
        return existente


def fiscalizar_venta(venta_id: int, usuario_id: int) -> ComprobanteFiscal:
    """Fiscaliza la venta con CAE y devuelve su comprobante principal en el estado que haya quedado.

    Idempotente: si la venta ya tiene un comprobante vivo, lo devuelve (autorizado, en envío o incierto)
    sin volver a pedirlo; un PENDIENTE se reintenta.

    Raises:
        FiscalizacionBloqueadaError: faltan datos fiscales (no se crea ningún comprobante).
        ConfiguracionFiscalError: perfil o punto de venta sin configurar/validar.
        FiscalizacionNoDisponibleError: ARCA no respondió antes de enviar (el comprobante queda PENDIENTE).
        SecuenciaFiscalBloqueadaError: otro comprobante de la secuencia sigue incierto.
    """
    _exigir_usuario(usuario_id, _ROLES_OPERATIVOS)
    perfil = perfil_listo()
    with obtener_conexion() as conexion:
        vivo = repositorio_fiscal.principal_vivo_de_venta_en_conexion(conexion, venta_id)
    if vivo is None:
        preparado = preparar_principal(perfil, venta_id, usuario_id)
        vivo = _alta(preparado.nuevo, lambda c: repositorio_fiscal.principal_vivo_de_venta_en_conexion(c, venta_id))
    if vivo.estado == estados.INCIERTO:
        # Primero se le pregunta a ARCA; solo si demuestra que no lo procesó, se vuelve a emitir.
        vivo = conciliar(vivo.id, None)
    if vivo.estado != estados.PENDIENTE:
        return vivo
    return emitir(vivo.id, usuario_id)


# --- 3-5. Emitir -----------------------------------------------------------------------------------


def solicitud_de(comprobante: ComprobanteFiscal, numero: int, fecha: date) -> SolicitudAutorizacion:
    asociados = ()
    if comprobante.comprobante_asociado_id is not None:
        original = _obtener(comprobante.comprobante_asociado_id)
        asociados = (
            ComprobanteAsociado(
                tipo=original.tipo_comprobante,
                punto_venta=original.punto_venta,
                numero=original.numero,
                cuit=original.emisor.cuit,
                fecha=original.fecha_comprobante.replace("-", ""),
            ),
        )
    receptor = comprobante.receptor
    return SolicitudAutorizacion(
        punto_venta=comprobante.punto_venta,
        tipo=comprobante.tipo_comprobante,
        numero=numero,
        fecha=fecha.strftime("%Y%m%d"),
        concepto=comprobante.concepto,
        doc_tipo=receptor.doc_tipo,
        doc_nro=receptor.doc_nro,
        condicion_iva_receptor_id=receptor.condicion_iva_receptor_id,
        importes=comprobante.importes,
        moneda=comprobante.moneda,
        cotizacion=comprobante.cotizacion,
        asociados=asociados,
    )


def hash_solicitud(solicitud: SolicitudAutorizacion) -> str:
    return hashlib.sha256(json.dumps(solicitud.como_dict(), sort_keys=True).encode("utf-8")).hexdigest()


def _cerrar_intento(correlacion: str, resultado: str, inicio: float, detalle: dict | None, conexion=None) -> None:
    duracion = int((time.monotonic() - inicio) * 1000)
    if conexion is not None:
        repositorio_fiscal.cerrar_intento_en_conexion(conexion, correlacion, resultado, duracion, detalle)
        return
    with obtener_conexion() as nueva:
        repositorio_fiscal.cerrar_intento_en_conexion(nueva, correlacion, resultado, duracion, detalle)


def _detalle_error(error: Exception) -> dict:
    return {"error": type(error).__name__, "mensaje": str(error)[:300]}


def emitir(comprobante_id: int, usuario_id: int | None) -> ComprobanteFiscal:
    """Envía un comprobante PENDIENTE a ARCA (ver el orden en el docstring del módulo)."""
    comprobante = _obtener(comprobante_id)
    if comprobante.estado == estados.PENDIENTE:
        _exigir_otros_impuestos_conocidos(comprobante)
    perfil = perfil_listo()
    if comprobante.ambiente != perfil.ambiente:
        raise OperacionFiscalInvalidaError("El comprobante es de otro ambiente fiscal: descartalo y fiscalizá de nuevo.")
    gateway = gateway_para(perfil)
    with _candado_secuencia(_clave_secuencia(comprobante)):
        comprobante = _obtener(comprobante_id)
        if comprobante.estado != estados.PENDIENTE:
            return comprobante
        _resolver_en_vuelo_de_la_secuencia(comprobante, gateway)
        try:
            ultimo = gateway.ultimo_autorizado(comprobante.punto_venta, comprobante.tipo_comprobante)
        except ErrorComunicacionARCA as error:
            raise FiscalizacionNoDisponibleError(f"ARCA no está disponible: {error}") from error
        with obtener_conexion() as conexion:
            ultimo_local = repositorio_fiscal.ultimo_autorizado_local_en_conexion(
                conexion, comprobante.ambiente, comprobante.emisor.cuit, comprobante.punto_venta, comprobante.tipo_comprobante
            )
        if ultimo < ultimo_local:
            with obtener_conexion() as conexion:
                auditar(conexion, usuario_id, ACCION_FISCAL_ERROR, comprobante,
                         f"ARCA informa último {ultimo} y Twinx tiene autorizado el {ultimo_local}: numeración inconsistente")
            raise ErrorFiscal(
                f"Numeración inconsistente: ARCA informa como último el {ultimo} y Twinx ya tiene autorizado el {ultimo_local}"
                " en esta secuencia. No se emite hasta que el técnico lo revise."
            )
        if ultimo > ultimo_local:
            logger.warning("Secuencia %s: ARCA informa %s y Twinx registró hasta %s (hay emisiones externas)",
                           _clave_secuencia(comprobante)[2:], ultimo, ultimo_local)
        try:
            return _enviar(comprobante, ultimo + 1, gateway, usuario_id)
        finally:
            # E5: pase lo que pase (AUTORIZADO, INCIERTO, ENVIANDO), la custodia fuera de `data/` lo sabe.
            servicio_custodia.actualizar_sin_fallar()


def _exigir_otros_impuestos_conocidos(comprobante: ComprobanteFiscal) -> None:
    """Revalida una factura PENDIENTE antes de cualquier llamada a ARCA (no se pide número ni CAE ni se abre
    un intento), con el mismo alcance que al prepararla (clase y receptor del snapshot): la clasificación
    ACTUAL de cada producto debe seguir permitiendo fiscalizar (el dueño pudo cambiarla después de preparar
    el comprobante) y, donde rige la transparencia, su snapshot debe tenerla registrada (una factura
    preparada antes de V1.16-H no la tiene: se descarta y se vuelve a fiscalizar). Las notas de crédito
    replican su factura.

    Raises:
        FiscalizacionBloqueadaError: con el motivo por producto; el comprobante sigue PENDIENTE.
    """
    if comprobante.rol != tablas_arca.ROL_PRINCIPAL:
        return
    consumidor_final = comprobante.receptor.es_consumidor_final
    with obtener_conexion() as conexion:
        actuales = repositorio_fiscal.otros_impuestos_de_productos_en_conexion(
            conexion, [linea.producto_id for linea in comprobante.lineas if linea.producto_id is not None]
        )
    otros_impuestos.exigir_conocidos(
        otros_impuestos.a_exigir(
            (
                (linea.descripcion, actuales.get(linea.producto_id, otros_impuestos.SIN_CLASIFICAR))
                for linea in comprobante.lineas
            ),
            comprobante.clase,
            consumidor_final,
        )
    )
    if otros_impuestos.aplica_transparencia(comprobante.clase, consumidor_final) and any(
        linea.otros_impuestos != otros_impuestos.NO_APLICA for linea in comprobante.lineas
    ):
        raise FiscalizacionBloqueadaError(
            "Esta factura pendiente se preparó sin confirmar los otros impuestos nacionales indirectos de sus"
            " productos: descartala y volvé a fiscalizar la venta."
        )


def _resolver_en_vuelo_de_la_secuencia(comprobante: ComprobanteFiscal, gateway: GatewayARCA) -> None:
    with obtener_conexion() as conexion:
        otro = repositorio_fiscal.en_vuelo_de_secuencia_en_conexion(
            conexion, comprobante.ambiente, comprobante.emisor.cuit, comprobante.punto_venta, comprobante.tipo_comprobante
        )
    if otro is None:
        return
    if otro.estado == estados.ENVIANDO:
        _marcar_incierto(otro.id, "envío interrumpido sin respuesta registrada")
    try:
        resuelto = _conciliar_bajo_candado(otro.id, gateway, None)
    except ErrorFiscal as error:
        raise SecuenciaFiscalBloqueadaError(
            f"El comprobante {otro.numero_formateado} de esta secuencia tiene resultado incierto y no pudo conciliarse"
            f" ({error}). No se emite el siguiente hasta resolverlo."
        ) from error
    if resuelto.estado in estados.ESTADOS_EN_VUELO:
        raise SecuenciaFiscalBloqueadaError(
            f"El comprobante {resuelto.numero_formateado} de esta secuencia sigue con resultado incierto."
        )


def _enviar(comprobante: ComprobanteFiscal, numero: int, gateway: GatewayARCA, usuario_id: int | None) -> ComprobanteFiscal:
    fecha = date.today()
    solicitud = solicitud_de(comprobante, numero, fecha)
    with obtener_conexion(inmediata=True) as conexion:
        repositorio_fiscal.actualizar_estado_en_conexion(
            conexion, comprobante.id, estados.ENVIANDO, numero=numero, fecha_comprobante=fecha.isoformat()
        )
        correlacion = repositorio_fiscal.abrir_intento_en_conexion(
            conexion, comprobante.id, "FECAESolicitar", comprobante.ambiente, numero, hash_solicitud(solicitud)
        )
    inicio = time.monotonic()
    logger.info("FECAESolicitar: ambiente=%s pto=%s tipo=%s nro=%s correlacion=%s",
                comprobante.ambiente, comprobante.punto_venta, comprobante.tipo_comprobante, numero, correlacion)
    try:
        respuesta = gateway.solicitar_cae(solicitud)
    except ErrorComunicacionARCA as error:
        if not error.enviado:
            _volver_a_pendiente(comprobante.id, correlacion, inicio, error)
            raise FiscalizacionNoDisponibleError(f"ARCA no está disponible: {error}") from error
        return _quedo_incierto(comprobante.id, correlacion, inicio, error, gateway)
    except ErrorWSAA as error:
        # ARCA no validó las credenciales: no procesó nada, el número no se consumió.
        _invalidar_ticket_rechazado(error, gateway)
        _volver_a_pendiente(comprobante.id, correlacion, inicio, error)
        raise
    except Exception as error:
        # Respuesta inválida o falla inesperada después de enviar: nunca se asume rechazo.
        logger.error("FECAESolicitar sin resultado interpretable (correlacion=%s): %s", correlacion, type(error).__name__)
        resultado = _quedo_incierto(comprobante.id, correlacion, inicio, error, gateway)
        if not isinstance(error, ErrorFiscal):
            raise
        return resultado
    return _registrar_respuesta(comprobante.id, correlacion, inicio, respuesta, gateway, usuario_id)


def _invalidar_ticket_rechazado(error: ErrorWSAA, gateway: GatewayARCA) -> None:
    """`alreadyAuthenticated` no acusa al TA guardado: borrarlo perdería uno que WSAA todavía tiene por válido."""
    if not isinstance(error, TicketYaVigenteError):
        gateway.invalidar_credenciales()


def _volver_a_pendiente(comprobante_id: int, correlacion: str, inicio: float, error: Exception) -> None:
    with obtener_conexion(inmediata=True) as conexion:
        repositorio_fiscal.actualizar_estado_en_conexion(
            conexion, comprobante_id, estados.PENDIENTE, numero=None, fecha_comprobante=None
        )
        _cerrar_intento(correlacion, "NO_ENVIADO", inicio, _detalle_error(error), conexion)
    logger.warning("FECAESolicitar no enviado (correlacion=%s): %s", correlacion, error)


def _marcar_incierto(comprobante_id: int, motivo: str) -> ComprobanteFiscal:
    with obtener_conexion(inmediata=True) as conexion:
        comprobante = repositorio_fiscal.obtener_comprobante_en_conexion(conexion, comprobante_id)
        if comprobante.estado == estados.ENVIANDO:
            repositorio_fiscal.actualizar_estado_en_conexion(conexion, comprobante_id, estados.INCIERTO)
            auditar(conexion, None, ACCION_FISCAL_INCIERTO, comprobante, motivo)
    return _obtener(comprobante_id)


def _quedo_incierto(comprobante_id: int, correlacion: str, inicio: float, error: Exception, gateway: GatewayARCA) -> ComprobanteFiscal:
    with obtener_conexion() as conexion:
        _cerrar_intento(correlacion, "INCIERTO", inicio, _detalle_error(error), conexion)
    _marcar_incierto(comprobante_id, f"sin respuesta interpretable de ARCA ({type(error).__name__})")
    logger.warning("FECAESolicitar con resultado INCIERTO (correlacion=%s): %s", correlacion, type(error).__name__)
    try:
        return _conciliar_bajo_candado(comprobante_id, gateway, None)
    except ErrorFiscal as error_conciliacion:
        logger.warning("Conciliación inmediata no posible (comprobante %s): %s", comprobante_id, error_conciliacion)
        return _obtener(comprobante_id)


def _registrar_respuesta(
    comprobante_id: int, correlacion: str, inicio: float, respuesta: RespuestaAutorizacion, gateway: GatewayARCA,
    usuario_id: int | None,
) -> ComprobanteFiscal:
    detalle = {
        "resultado": respuesta.resultado,
        "observaciones": [m.como_dict() for m in respuesta.observaciones],
        "errores": [m.como_dict() for m in respuesta.errores],
        "eventos": [m.como_dict() for m in respuesta.eventos],
        "reproceso": respuesta.reproceso,
    }
    if respuesta.resultado == "A" and not (_cae_valido(respuesta.cae) and respuesta.vencimiento):
        # Aprobado sin un CAE utilizable: no se registra una autorización a medias; se concilia con ARCA.
        return _quedo_incierto(comprobante_id, correlacion, inicio,
                               ErrorFiscal("ARCA aprobó sin un CAE válido en la respuesta"), gateway)
    with obtener_conexion(inmediata=True) as conexion:
        comprobante = repositorio_fiscal.obtener_comprobante_en_conexion(conexion, comprobante_id)
        if respuesta.resultado == "A":
            repositorio_fiscal.actualizar_estado_en_conexion(
                conexion, comprobante_id, estados.AUTORIZADO,
                resultado="A",
                codigo_autorizacion=respuesta.cae,
                vencimiento_autorizacion=_fecha_iso(respuesta.vencimiento),
                fecha_proceso=respuesta.fecha_proceso,
                observaciones_json=_mensajes_json(respuesta.observaciones),
                errores_json=_mensajes_json(respuesta.errores),
            )
            accion = ACCION_FISCAL_NC_AUTORIZADA if comprobante.es_nota_credito else ACCION_FISCAL_AUTORIZADO
            texto = f"CAE {respuesta.cae}, vence {_fecha_iso(respuesta.vencimiento)}"
            if respuesta.observaciones:
                texto += f" ({len(respuesta.observaciones)} observación/es)"
        else:
            repositorio_fiscal.actualizar_estado_en_conexion(
                conexion, comprobante_id, estados.RECHAZADO,
                resultado="R",
                fecha_proceso=respuesta.fecha_proceso,
                observaciones_json=_mensajes_json(respuesta.observaciones),
                errores_json=_mensajes_json(respuesta.errores),
            )
            accion = ACCION_FISCAL_RECHAZADO
            primero = (respuesta.errores or respuesta.observaciones or (MensajeARCA(0, "sin detalle"),))[0]
            texto = f"rechazado por ARCA ({primero.codigo}: {primero.mensaje[:120]})"
        _cerrar_intento(correlacion, respuesta.resultado, inicio, detalle, conexion)
        comprobante = repositorio_fiscal.obtener_comprobante_en_conexion(conexion, comprobante_id)
        auditar(conexion, usuario_id, accion, comprobante, texto)
    logger.info("FECAESolicitar resultado=%s comprobante=%s correlacion=%s", respuesta.resultado, comprobante_id, correlacion)
    return _obtener(comprobante_id)


# --- Conciliación ----------------------------------------------------------------------------------


def _coincide_con_arca(comprobante: ComprobanteFiscal, consultado) -> bool:
    return (
        consultado.total_centavos == comprobante.importes.total_centavos
        and consultado.doc_tipo == comprobante.receptor.doc_tipo
        and consultado.doc_nro.lstrip("0") == comprobante.receptor.doc_nro.lstrip("0")
        and consultado.fecha == (comprobante.fecha_comprobante or "").replace("-", "")
    )


def conciliar(comprobante_id: int, usuario_id: int | None) -> ComprobanteFiscal:
    """Resuelve un comprobante INCIERTO preguntándole a ARCA (nunca reenviándolo).

    - ARCA lo tiene con los mismos datos: se reconstruye la autorización con el CAE de ARCA.
    - ARCA tiene ese número con OTROS datos (lo usó otro sistema): el nuestro no se procesó → PENDIENTE.
    - ARCA no lo tiene y su último es menor: todavía no se sabe (puede asentarlo después) → sigue INCIERTO.
    - Respuesta contradictoria o ARCA no disponible: sigue INCIERTO.

    `usuario_id` (OWNER) es la acción manual "Reintentar conciliación"; `None` es la automática.
    """
    if usuario_id is not None:
        _exigir_usuario(usuario_id, _SOLO_OWNER)
    comprobante = _obtener(comprobante_id)
    if comprobante.estado not in estados.ESTADOS_EN_VUELO:
        return comprobante
    perfil = repositorio_fiscal.obtener_perfil()
    if perfil is None or perfil.ambiente != comprobante.ambiente:
        raise OperacionFiscalInvalidaError("El comprobante es de otro ambiente: no puede conciliarse desde el actual.")
    gateway = gateway_para(perfil)
    with _candado_secuencia(_clave_secuencia(comprobante)):
        try:
            return _conciliar_bajo_candado(comprobante_id, gateway, usuario_id)
        finally:
            servicio_custodia.actualizar_sin_fallar()


def _conciliar_bajo_candado(comprobante_id: int, gateway: GatewayARCA, usuario_id: int | None) -> ComprobanteFiscal:
    comprobante = _obtener(comprobante_id)
    if comprobante.estado == estados.ENVIANDO:
        comprobante = _marcar_incierto(comprobante_id, "envío interrumpido sin respuesta registrada")
    if comprobante.estado != estados.INCIERTO:
        return comprobante
    with obtener_conexion() as conexion:
        correlacion = repositorio_fiscal.abrir_intento_en_conexion(
            conexion, comprobante.id, "FECompConsultar", comprobante.ambiente, comprobante.numero
        )
    inicio = time.monotonic()
    try:
        consultado = gateway.consultar(comprobante.punto_venta, comprobante.tipo_comprobante, comprobante.numero)
        ultimo = None if consultado is not None else gateway.ultimo_autorizado(comprobante.punto_venta, comprobante.tipo_comprobante)
    except ErrorFiscal as error:
        _cerrar_intento(correlacion, "ERROR", inicio, _detalle_error(error))
        raise
    with obtener_conexion(inmediata=True) as conexion:
        actual = repositorio_fiscal.obtener_comprobante_en_conexion(conexion, comprobante_id)
        if actual.estado != estados.INCIERTO:
            _cerrar_intento(correlacion, "ERROR", inicio, {"mensaje": "el estado cambió durante la conciliación"}, conexion)
            return actual
        if consultado is not None and _coincide_con_arca(actual, consultado) and _cae_valido(consultado.codigo_autorizacion):
            repositorio_fiscal.actualizar_estado_en_conexion(
                conexion, comprobante_id, estados.AUTORIZADO,
                resultado="A",
                codigo_autorizacion=consultado.codigo_autorizacion,
                vencimiento_autorizacion=_fecha_iso(consultado.vencimiento),
                fecha_proceso=consultado.fecha_proceso,
                observaciones_json=_mensajes_json(consultado.observaciones),
            )
            _cerrar_intento(correlacion, "ENCONTRADO", inicio, {"codigo": consultado.codigo_autorizacion}, conexion)
            auditar(conexion, usuario_id, ACCION_FISCAL_RECONCILIADO, actual,
                     f"ARCA lo tenía autorizado: CAE {consultado.codigo_autorizacion} recuperado")
        elif consultado is not None:
            repositorio_fiscal.actualizar_estado_en_conexion(conexion, comprobante_id, estados.PENDIENTE, numero=None, fecha_comprobante=None)
            _cerrar_intento(correlacion, "ENCONTRADO", inicio, {"mensaje": "número usado por otro comprobante"}, conexion)
            auditar(conexion, usuario_id, ACCION_FISCAL_RECONCILIADO, actual,
                     "ARCA tiene ese número con otros datos: el pedido no se procesó; queda pendiente con número nuevo")
        elif ultimo < actual.numero:
            _cerrar_intento(correlacion, "NO_ENCONTRADO", inicio, {"ultimo": ultimo}, conexion)
            logger.warning("Comprobante %s sigue INCIERTO: ARCA no informa el número %s (último %s)",
                           comprobante_id, actual.numero, ultimo)
        else:
            _cerrar_intento(correlacion, "ERROR", inicio, {"ultimo": ultimo, "mensaje": "ARCA no lo tiene pero su último lo supera"}, conexion)
            auditar(conexion, usuario_id, ACCION_FISCAL_ERROR, actual,
                     f"conciliación contradictoria: ARCA no tiene el número y su último es {ultimo}")
    return _obtener(comprobante_id)


def recuperar_al_iniciar() -> int:
    """Al arrancar, nada puede estar realmente en envío: todo ENVIANDO (el proceso murió a mitad de un
    FECAESolicitar) pasa a INCIERTO. Solo base de datos; la conciliación con ARCA corre después."""
    with obtener_conexion() as conexion:
        en_envio = repositorio_fiscal.ids_por_estados_en_conexion(conexion, (estados.ENVIANDO,))
    for comprobante_id in en_envio:
        _marcar_incierto(comprobante_id, "la aplicación se cerró durante el envío a ARCA")
    if en_envio:
        logger.warning("Recuperación fiscal: %s comprobante(s) en envío pasaron a INCIERTO", len(en_envio))
    return len(en_envio)


def conciliar_inciertos() -> None:
    """Conciliación automática de todos los INCIERTO (arranque y panel). Nunca lanza: lo no resuelto queda
    INCIERTO y el panel lo muestra."""
    with obtener_conexion() as conexion:
        inciertos = repositorio_fiscal.ids_por_estados_en_conexion(conexion, (estados.INCIERTO,))
    for comprobante_id in inciertos:
        try:
            conciliar(comprobante_id, None)
        except ErrorFiscal as error:
            logger.warning("Conciliación automática del comprobante %s no posible: %s", comprobante_id, error)


def descartar(comprobante_id: int, usuario_id: int) -> ComprobanteFiscal:
    """OWNER: descarta un PENDIENTE (nunca enviado o liberado por conciliación) para volver a fiscalizar la
    venta con datos corregidos. No toca ninguno con número en vuelo ni autorizado."""
    _exigir_usuario(usuario_id, _SOLO_OWNER)
    with obtener_conexion(inmediata=True) as conexion:
        comprobante = repositorio_fiscal.obtener_comprobante_en_conexion(conexion, comprobante_id)
        if comprobante is None:
            raise ComprobanteFiscalNoEncontradoError(f"No existe el comprobante fiscal {comprobante_id}.")
        if comprobante.estado != estados.PENDIENTE:
            raise OperacionFiscalInvalidaError("Solo se descarta un comprobante pendiente que no está en ARCA.")
        repositorio_fiscal.actualizar_estado_en_conexion(conexion, comprobante_id, estados.DESCARTADO)
        auditar(conexion, usuario_id, ACCION_FISCAL_DESCARTADO, comprobante, "descartado antes de enviarse")
    return _obtener(comprobante_id)


def reintentar(comprobante_id: int, usuario_id: int) -> ComprobanteFiscal:
    """OWNER: reenvía un PENDIENTE (factura o nota). Un INCIERTO primero se concilia y, si ARCA sigue sin
    informarlo, se reenvía con su MISMO número y fecha, nunca con uno nuevo.

    Raises:
        SecuenciaFiscalBloqueadaError: ese número ya no es el próximo a autorizar: reenviarlo no sirve.
        FiscalizacionNoDisponibleError: el reenvío no llegó a salir (sigue INCIERTO).
    """
    _exigir_usuario(usuario_id, _SOLO_OWNER)
    comprobante = _obtener(comprobante_id)
    if comprobante.estado == estados.INCIERTO:
        comprobante = _resolver_incierto_con_reenvio(comprobante, usuario_id)
    if comprobante.estado != estados.PENDIENTE:
        return comprobante
    return emitir(comprobante_id, usuario_id)


def _resolver_incierto_con_reenvio(comprobante: ComprobanteFiscal, usuario_id: int) -> ComprobanteFiscal:
    perfil = perfil_listo()
    if comprobante.ambiente != perfil.ambiente:
        raise OperacionFiscalInvalidaError("El comprobante es de otro ambiente: no puede reenviarse desde el actual.")
    gateway = gateway_para(perfil)
    with _candado_secuencia(_clave_secuencia(comprobante)):
        comprobante = _conciliar_bajo_candado(comprobante.id, gateway, usuario_id)
        if comprobante.estado != estados.INCIERTO:
            return comprobante
        ultimo = gateway.ultimo_autorizado(comprobante.punto_venta, comprobante.tipo_comprobante)
        if ultimo + 1 != comprobante.numero:
            raise SecuenciaFiscalBloqueadaError(
                f"ARCA informa como último el {ultimo}: el número {comprobante.numero} no es el próximo a autorizar"
                " y no se reenvía. Hay que revisar la secuencia con el técnico."
            )
        return _reenviar_mismo_numero(comprobante, gateway, usuario_id)


def _reenviar_mismo_numero(comprobante: ComprobanteFiscal, gateway: GatewayARCA, usuario_id: int) -> ComprobanteFiscal:
    """Repite el pedido idéntico. Sigue INCIERTO hasta que ARCA lo decida: si el pedido anterior ya se asentó,
    responde 10016 y el CAE se recupera por consulta; nunca hay un segundo comprobante."""
    fecha = date.fromisoformat(comprobante.fecha_comprobante)
    solicitud = solicitud_de(comprobante, comprobante.numero, fecha)
    with obtener_conexion(inmediata=True) as conexion:
        correlacion = repositorio_fiscal.abrir_intento_en_conexion(
            conexion, comprobante.id, "FECAESolicitar", comprobante.ambiente, comprobante.numero, hash_solicitud(solicitud)
        )
    inicio = time.monotonic()
    logger.info("FECAESolicitar (reenvío del mismo número): pto=%s tipo=%s nro=%s correlacion=%s",
                comprobante.punto_venta, comprobante.tipo_comprobante, comprobante.numero, correlacion)
    try:
        respuesta = gateway.solicitar_cae(solicitud)
    except ErrorComunicacionARCA as error:
        if error.enviado:
            return _quedo_incierto(comprobante.id, correlacion, inicio, error, gateway)
        _cerrar_intento(correlacion, "NO_ENVIADO", inicio, _detalle_error(error))
        raise FiscalizacionNoDisponibleError(f"ARCA no está disponible: {error}") from error
    except ErrorWSAA as error:
        _invalidar_ticket_rechazado(error, gateway)
        _cerrar_intento(correlacion, "NO_ENVIADO", inicio, _detalle_error(error))
        raise
    except ErrorFiscal as error:
        return _quedo_incierto(comprobante.id, correlacion, inicio, error, gateway)
    if respuesta.resultado == "R" and any(
        mensaje.codigo == tablas_arca.ERROR_NUMERO_NO_CONSECUTIVO for mensaje in (*respuesta.observaciones, *respuesta.errores)
    ):
        _cerrar_intento(correlacion, "R", inicio, {"observaciones": [m.como_dict() for m in respuesta.observaciones]})
        return _conciliar_bajo_candado(comprobante.id, gateway, usuario_id)
    return _registrar_respuesta(comprobante.id, correlacion, inicio, respuesta, gateway, usuario_id)


def obtener(comprobante_id: int) -> ComprobanteFiscal:
    return _obtener(comprobante_id)


def comprobantes_de_venta(venta_id: int) -> list[ComprobanteFiscal]:
    return repositorio_fiscal.listar_comprobantes_de_venta(venta_id)


def intentos(comprobante_id: int):
    return repositorio_fiscal.listar_intentos(comprobante_id)


# --- Cierre operativo (E3): vista previa sin emitir ----------------------------------------------------


def previsualizar_venta(venta_id: int, usuario_id: int | None):
    """Borrador del comprobante principal que se emitiría para la venta: mismo snapshot que la emisión
    (`preparar_principal`), pero sin crear el comprobante, sin número, sin CAE ni QR. No escribe nada ni
    llama a ARCA. Si la venta no podría fiscalizarse, lanza el mismo error que bloquearía la emisión.

    Raises:
        ConfiguracionFiscalError: sin perfil del emisor.
        FiscalizacionBloqueadaError / DatosInvalidosError / VentaNoEncontradaError: lo que bloquearía emitir.
    """
    perfil = repositorio_fiscal.obtener_perfil()
    if perfil is None:
        raise ConfiguracionFiscalError("Todavía no hay perfil fiscal del emisor: no hay nada que previsualizar.")
    if perfil.punto_venta_cae is None:
        raise ConfiguracionFiscalError("El perfil fiscal no tiene punto de venta: completalo para previsualizar.")
    return armar_borrador(preparar_principal(perfil, venta_id, usuario_id).nuevo)


# --- Cierre operativo (E6): listado informativo de autorizados ---------------------------------------


@dataclass(frozen=True)
class TotalesListado:
    cantidad: int
    total_centavos: int
    neto_centavos: int
    iva_centavos: int
    por_tipo: list[tuple[str, int, int]]  # (nombre del tipo, cantidad, total)
    notas_credito_centavos: int


def listar_autorizados(inicio: str, fin_exclusivo: str) -> list[ComprobanteFiscal]:
    """Comprobantes autorizados (CAE o CAEA) del período, por fecha de comprobante. Solo lectura."""
    return repositorio_fiscal.listar_autorizados_entre(inicio, fin_exclusivo)


def totales_de(comprobantes: list[ComprobanteFiscal]) -> TotalesListado:
    """Totales informativos: suma de importes del snapshot de cada comprobante; las notas de crédito se
    informan aparte (no se netean contra las facturas: eso lo decide el contador)."""
    por_tipo: dict[str, list[int]] = {}
    total = neto = iva = notas = 0
    for c in comprobantes:
        acumulado = por_tipo.setdefault(c.nombre_tipo, [0, 0])
        acumulado[0] += 1
        acumulado[1] += c.importes.total_centavos
        if c.es_nota_credito:
            notas += c.importes.total_centavos
        else:
            total += c.importes.total_centavos
            neto += c.importes.neto_centavos
            iva += c.importes.iva_centavos
    return TotalesListado(
        cantidad=len(comprobantes),
        total_centavos=total,
        neto_centavos=neto,
        iva_centavos=iva,
        por_tipo=[(nombre, cantidad, importe) for nombre, (cantidad, importe) in sorted(por_tipo.items())],
        notas_credito_centavos=notas,
    )
