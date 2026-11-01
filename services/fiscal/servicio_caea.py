"""Contingencia CAEA (V1.16-G): solicitar el código anticipado, emitir con él cuando ARCA no responde,
informar lo emitido y los puntos de venta sin movimiento.

Cuándo se emite bajo CAEA: SOLO si la autorización con CAE falló sin llegar a enviarse
(`FiscalizacionNoDisponibleError`), la contingencia está habilitada, el punto de venta CAEA está validado y
hay un CAEA vigente para hoy. Nunca ante un resultado incierto: podría duplicar la factura de la venta.

No es una "factura offline" inventada: el código es el que ARCA otorgó para la quincena y cada comprobante
queda pendiente de informar (alerta persistente) hasta que ARCA lo acepte por `FECAEARegInformativo`.
"""

import dataclasses
import logging
import time
from datetime import date, datetime

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import caea as repositorio_caea
from db.repositorios import fiscal as repositorio_fiscal
from db.repositorios import usuarios as repositorio_usuarios
from domain.auditoria import (
    ACCION_FISCAL_CAEA_EMITIDO,
    ACCION_FISCAL_CAEA_INFORMADO,
    ACCION_FISCAL_CAEA_OBTENIDO,
    ACCION_FISCAL_DESCARTADO,
    ACCION_FISCAL_ERROR,
)
from domain.fiscal import caea as dominio_caea
from domain.fiscal import comprobante as estados
from domain.fiscal import reglas, tablas_arca
from domain.fiscal.caea import CAEA
from domain.fiscal.comprobante import ComprobanteFiscal
from domain.fiscal.emisor import PerfilFiscal
from domain.usuario import exigir_rol
from excepciones import (
    ConfiguracionFiscalError,
    ErrorAplicacion,
    ErrorFiscal,
    FiscalizacionNoDisponibleError,
)
from services.fiscal import servicio_facturacion
from services.fiscal.arca.wsfe import ErrorWSFE
from services.fiscal.gateway import GatewayARCA, gateway_para

logger = logging.getLogger(__name__)

_SOLO_OWNER = frozenset({"OWNER"})
ESTADOS_POR_INFORMAR = (estados.CAEA_PENDIENTE_INFORMAR, estados.CAEA_INFORME_RECHAZADO)


def _perfil_con_caea() -> PerfilFiscal:
    perfil = repositorio_fiscal.obtener_perfil()
    if perfil is None or not perfil.punto_venta_caea_listo:
        raise ConfiguracionFiscalError("Configurá y validá con ARCA el punto de venta CAEA.")
    return perfil


def _fecha_iso(aaaammdd: str) -> str:
    return datetime.strptime(aaaammdd[:8], "%Y%m%d").date().isoformat()


def _tipos_bajo_caea(perfil: PerfilFiscal) -> list[int]:
    clases = ("C",) if perfil.condicion_iva != "RESPONSABLE_INSCRIPTO" else ("A", "B")
    return [reglas.tipo_comprobante(clase, tablas_arca.ROL_PRINCIPAL) for clase in clases]


def sincronizar_numeracion(perfil: PerfilFiscal, gateway: GatewayARCA) -> None:
    """Toma de ARCA el último autorizado de cada tipo en el punto de venta CAEA (base de la numeración local)."""
    for tipo in _tipos_bajo_caea(perfil):
        ultimo = gateway.ultimo_autorizado(perfil.punto_venta_caea, tipo)
        with obtener_conexion() as conexion:
            repositorio_caea.sincronizar_numeracion_en_conexion(
                conexion, perfil.ambiente, perfil.cuit, perfil.punto_venta_caea, tipo, ultimo
            )


def solicitar(usuario_id: int | None, hoy: date | None = None) -> list[CAEA]:
    """Obtiene (o recupera, si ya se había pedido) el CAEA del período vigente y, en los 5 días previos, el del
    próximo; sincroniza la numeración del punto de venta CAEA. `usuario_id=None` es el pedido automático."""
    if usuario_id is not None:
        with obtener_conexion() as conexion:
            exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _SOLO_OWNER)
    perfil = _perfil_con_caea()
    gateway = gateway_para(perfil)
    obtenidos = []
    for periodo, orden in dominio_caea.solicitables(hoy or date.today()):
        existente = repositorio_caea.del_periodo(perfil.ambiente, perfil.cuit, periodo, orden)
        if existente is not None:
            obtenidos.append(existente)
            continue
        try:
            otorgado = gateway.solicitar_caea(periodo, orden)
        except ErrorWSFE:
            # Ya se había pedido (o ARCA no lo entrega de nuevo): se recupera el existente.
            otorgado = gateway.consultar_caea(periodo, orden)
            if otorgado is None:
                raise
        if (otorgado.periodo, otorgado.orden) != (periodo, orden):
            raise ErrorFiscal("ARCA devolvió un CAEA de otro período.")
        with obtener_conexion(inmediata=True) as conexion:
            registrado = repositorio_caea.guardar_en_conexion(
                conexion, perfil.ambiente, perfil.cuit, otorgado.codigo, periodo, orden,
                _fecha_iso(otorgado.vigente_desde), _fecha_iso(otorgado.vigente_hasta), _fecha_iso(otorgado.tope_informe),
                otorgado.fecha_proceso, [m.como_dict() for m in otorgado.observaciones],
            )

            repositorio_auditoria.registrar_en_conexion(
                conexion, usuario_id, ACCION_FISCAL_CAEA_OBTENIDO, "CAEA", registrado.id,
                f"CAEA {registrado.codigo} para {registrado.etiqueta}, informar hasta {registrado.tope_informe}",
            )
        obtenidos.append(registrado)
        logger.info("CAEA obtenido: período=%s orden=%s ambiente=%s", periodo, orden, perfil.ambiente)
    sincronizar_numeracion(perfil, gateway)
    return obtenidos


def contingencia_disponible(perfil: PerfilFiscal | None, hoy: date | None = None) -> CAEA | None:
    """El CAEA con el que se emitiría hoy, o `None` si la contingencia no puede usarse."""
    if perfil is None or not perfil.contingencia_caea or not perfil.punto_venta_caea_listo:
        return None
    with obtener_conexion() as conexion:
        return repositorio_caea.vigente_en_conexion(conexion, perfil.ambiente, perfil.cuit, (hoy or date.today()).isoformat())


def emitir_bajo_caea(venta_id: int, usuario_id: int) -> ComprobanteFiscal:
    """Emite el comprobante de la venta bajo el CAEA vigente (ver condiciones en el docstring del módulo).

    Raises:
        FiscalizacionNoDisponibleError: si la contingencia no está disponible (sin CAEA, sin punto de venta
            CAEA validado o deshabilitada): Twinx no emite ninguna factura y la venta sigue registrada.
    """
    perfil = repositorio_fiscal.obtener_perfil()
    vigente = contingencia_disponible(perfil)
    if vigente is None:
        raise FiscalizacionNoDisponibleError(
            "Fiscalización no disponible: ARCA no responde y no hay un CAEA vigente con contingencia habilitada."
            " La venta quedó registrada; la factura queda pendiente."
        )
    preparado = servicio_facturacion.preparar_principal(perfil, venta_id, usuario_id, perfil.punto_venta_caea)
    ahora = datetime.now()
    hoy = ahora.date()

    with obtener_conexion(inmediata=True) as conexion:
        vivo = repositorio_fiscal.principal_vivo_de_venta_en_conexion(conexion, venta_id)
        if vivo is not None:
            if vivo.estado != estados.PENDIENTE or vivo.numero is not None:
                return vivo  # ya tiene una factura autorizada, en curso o incierta: nunca una segunda
            repositorio_fiscal.actualizar_estado_en_conexion(conexion, vivo.id, estados.DESCARTADO)
            servicio_facturacion.auditar(conexion, usuario_id, ACCION_FISCAL_DESCARTADO, vivo,
                                         "reemplazado por emisión bajo CAEA (ARCA no respondía; nunca se envió)")
        numero = repositorio_caea.proximo_numero_en_conexion(
            conexion, perfil.ambiente, perfil.cuit, perfil.punto_venta_caea, preparado.nuevo.tipo_comprobante
        )
        if numero is None:
            raise FiscalizacionNoDisponibleError(
                "La numeración del punto de venta CAEA no se sincronizó con ARCA: no se emite bajo CAEA."
            )
        nuevo = dataclasses.replace(
            preparado.nuevo,
            modalidad=estados.MODALIDAD_CAEA,
            caea_id=vigente.id,
            estado=estados.CAEA_PENDIENTE_INFORMAR,
            numero=numero,
            fecha_comprobante=hoy.isoformat(),
            fecha_hora_generacion=ahora.strftime("%Y-%m-%d %H:%M:%S"),
            codigo_autorizacion=vigente.codigo,
            vencimiento_autorizacion=vigente.vigente_hasta,
        )
        comprobante_id = repositorio_fiscal.insertar_comprobante_en_conexion(conexion, nuevo)
        comprobante = repositorio_fiscal.obtener_comprobante_en_conexion(conexion, comprobante_id)
        servicio_facturacion.auditar(conexion, usuario_id, ACCION_FISCAL_CAEA_EMITIDO, comprobante,
                                     f"emitido bajo CAEA {vigente.codigo}; informar a ARCA hasta {vigente.tope_informe}")
    logger.warning("Comprobante %s emitido bajo CAEA (contingencia)", comprobante_id)
    return servicio_facturacion.obtener(comprobante_id)


def fiscalizar_con_contingencia(venta_id: int, usuario_id: int) -> ComprobanteFiscal:
    """Punto de entrada del POS: CAE y, solo si ARCA no respondió antes de enviar, CAEA."""
    try:
        return servicio_facturacion.fiscalizar_venta(venta_id, usuario_id)
    except FiscalizacionNoDisponibleError as error:
        if contingencia_disponible(repositorio_fiscal.obtener_perfil()) is None:
            raise FiscalizacionNoDisponibleError(
                f"Fiscalización no disponible ({error}). La venta quedó registrada y su factura pendiente; sin CAEA"
                " vigente Twinx no emite comprobante fiscal (ver contingencias en el panel fiscal)."
            ) from error
        return emitir_bajo_caea(venta_id, usuario_id)


def informar_pendientes() -> int:
    """Informa a ARCA (FECAEARegInformativo) cada comprobante emitido bajo CAEA, en orden de numeración.
    Devuelve cuántos quedaron informados. Si ARCA no responde, se detiene: lo pendiente sigue pendiente."""
    perfil = repositorio_fiscal.obtener_perfil()
    if perfil is None:
        return 0
    gateway = gateway_para(perfil)
    pendientes = sorted(
        (c for c in repositorio_fiscal.listar_por_estados(ESTADOS_POR_INFORMAR, 500) if c.ambiente == perfil.ambiente),
        key=lambda c: (c.punto_venta, c.tipo_comprobante, c.numero),
    )
    informados = 0
    for comprobante in pendientes:
        caea_texto = comprobante.codigo_autorizacion
        generacion = datetime.strptime(comprobante.fecha_hora_generacion, "%Y-%m-%d %H:%M:%S").strftime("%Y%m%d%H%M%S")
        solicitud = dataclasses.replace(
            servicio_facturacion.solicitud_de(comprobante, comprobante.numero, date.fromisoformat(comprobante.fecha_comprobante)),
            caea=caea_texto,
            fecha_hora_generacion=generacion,
        )
        with obtener_conexion() as conexion:
            correlacion = repositorio_fiscal.abrir_intento_en_conexion(
                conexion, comprobante.id, "FECAEARegInformativo", comprobante.ambiente, comprobante.numero,
                servicio_facturacion.hash_solicitud(solicitud),
            )
        inicio = time.monotonic()
        try:
            respuesta = gateway.informar_caea(solicitud)
            ya_estaba = None
            if respuesta.resultado == "R":
                consultado = gateway.consultar(comprobante.punto_venta, comprobante.tipo_comprobante, comprobante.numero)
                ya_estaba = consultado is not None and consultado.codigo_autorizacion == caea_texto
        except ErrorAplicacion as error:
            with obtener_conexion() as conexion:
                repositorio_fiscal.cerrar_intento_en_conexion(
                    conexion, correlacion, "ERROR", int((time.monotonic() - inicio) * 1000), {"mensaje": str(error)[:300]}
                )
            logger.warning("No se pudo informar el CAEA del comprobante %s: %s", comprobante.id, error)
            break
        aceptado = respuesta.resultado == "A" or ya_estaba

        with obtener_conexion(inmediata=True) as conexion:
            repositorio_fiscal.cerrar_intento_en_conexion(
                conexion, correlacion, respuesta.resultado, int((time.monotonic() - inicio) * 1000),
                {"observaciones": [m.como_dict() for m in respuesta.observaciones],
                 "errores": [m.como_dict() for m in respuesta.errores], "ya_informado": bool(ya_estaba)},
            )
            nuevo_estado = estados.CAEA_INFORMADO if aceptado else estados.CAEA_INFORME_RECHAZADO
            repositorio_fiscal.actualizar_estado_en_conexion(conexion, comprobante.id, nuevo_estado)
            if aceptado:
                servicio_facturacion.auditar(conexion, None, ACCION_FISCAL_CAEA_INFORMADO, comprobante, "informado a ARCA")
            else:
                primero = (respuesta.errores or respuesta.observaciones)
                detalle = f"{primero[0].codigo}: {primero[0].mensaje[:120]}" if primero else "sin detalle"
                repositorio_auditoria.registrar_en_conexion(
                    conexion, None, ACCION_FISCAL_ERROR, servicio_facturacion.ENTIDAD, comprobante.id,
                    f"Informe CAEA rechazado de {comprobante.numero_formateado}: {detalle}",
                )
        informados += int(bool(aceptado))
    return informados


def informar_sin_movimiento(hoy: date | None = None) -> int:
    """Informa cada CAEA con período cerrado cuyo punto de venta CAEA no tuvo comprobantes."""
    perfil = repositorio_fiscal.obtener_perfil()
    if perfil is None or perfil.punto_venta_caea is None:
        return 0
    gateway = gateway_para(perfil)
    informados = 0
    for caea in repositorio_caea.vencidos_sin_cerrar(perfil.ambiente, perfil.cuit, (hoy or date.today()).isoformat()):
        punto = perfil.punto_venta_caea
        if repositorio_caea.tiene_comprobantes(caea.id, punto) or repositorio_caea.sin_movimiento_informado(caea.id, punto):
            continue
        if gateway.consultar_sin_movimiento(punto, caea.codigo):
            respuesta_resultado, fecha, detalle = "A", None, {"mensaje": "ARCA ya lo tenía informado"}
        else:
            respuesta = gateway.informar_sin_movimiento(punto, caea.codigo)
            respuesta_resultado, fecha = respuesta.resultado, respuesta.fecha_proceso
            detalle = {"errores": [m.como_dict() for m in respuesta.errores]}
        with obtener_conexion() as conexion:
            repositorio_caea.registrar_sin_movimiento_en_conexion(conexion, caea.id, punto, respuesta_resultado, fecha, detalle)
        informados += int(respuesta_resultado == "A")
    return informados


def mantenimiento_automatico() -> None:
    """Arranque y panel: pedir el CAEA del período (si la contingencia está habilitada), informar lo emitido y
    los puntos de venta sin movimiento. Nunca lanza: lo no resuelto queda en el panel."""
    perfil = repositorio_fiscal.obtener_perfil()
    if perfil is None or perfil.punto_venta_caea is None:
        return
    pasos = [informar_pendientes, informar_sin_movimiento]
    if perfil.contingencia_caea and perfil.punto_venta_caea_listo:
        pasos.insert(0, lambda: solicitar(None))
    for paso in pasos:
        try:
            paso()
        except ErrorAplicacion as error:
            logger.warning("Mantenimiento CAEA no completado: %s", error)


def estado(perfil: PerfilFiscal | None, hoy: date | None = None) -> dict:
    """Resumen para el panel y las alertas."""
    hoy = hoy or date.today()
    if perfil is None:
        return {"vigente": None, "recientes": [], "por_informar": [], "dias_minimos": None}
    por_informar = [c for c in repositorio_fiscal.listar_por_estados(ESTADOS_POR_INFORMAR, 500) if c.ambiente == perfil.ambiente]
    recientes = repositorio_caea.listar(perfil.ambiente, perfil.cuit)
    topes = {c.id: c for c in recientes}
    dias = [topes[c.caea_id].dias_para_informar(hoy) for c in por_informar if c.caea_id in topes]
    return {
        "vigente": contingencia_disponible(perfil, hoy),
        "recientes": recientes,
        "por_informar": por_informar,
        "dias_minimos": min(dias) if dias else None,
    }


