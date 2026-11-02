"""Notas de crédito: cancelación fiscal de una venta facturada (V1.16-F). Solo OWNER.

Una NC es otro comprobante fiscal (su propio número y CAE) que referencia la factura original sin
modificarla. V1.16 emite NC totales: cancelan la venta completa, con el mismo efecto comercial que una
anulación (stock y partidas restaurados, venta ANULADA, sin movimiento de caja) y las mismas reglas: venta de
la caja abierta y no a cuenta corriente. Esas reglas se verifican ANTES de pedir la NC a ARCA.

Orden seguro (ver migración 043): intención + NC PENDIENTE → autorización ARCA → efecto comercial y
cancelación APLICADA en una única transacción local. Ningún fallo de red puede producir una doble
devolución: el efecto local solo ocurre con la NC autorizada y la transición PENDIENTE → APLICADA es única.
No se emiten notas de débito: un kiosco no las necesita en V1.16.
"""

import logging
from datetime import date

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import fiscal as repositorio_fiscal
from db.repositorios import usuarios as repositorio_usuarios
from domain.auditoria import ACCION_FISCAL_ERROR, ACCION_FISCAL_SOLICITUD
from domain.fiscal import comprobante as estados
from domain.fiscal import reglas, tablas_arca
from domain.fiscal.comprobante import ComprobanteFiscal, calcular_contenido_hash
from domain.usuario import exigir_rol
from domain.venta import validar_motivo_anulacion
from excepciones import ErrorAplicacion, ErrorBaseDatos, OperacionFiscalInvalidaError
from services import servicio_ventas
from services.fiscal import servicio_caea, servicio_facturacion, servicio_parametros
from services.fiscal.gateway import gateway_para

logger = logging.getLogger(__name__)

_SOLO_OWNER = frozenset({"OWNER"})


def _exigir_owner(usuario_id: int) -> None:
    with obtener_conexion() as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _SOLO_OWNER)


def emitir_nota_credito_total(venta_id: int, motivo: str, observaciones: str | None, usuario_id: int) -> ComprobanteFiscal:
    """Cancela fiscalmente la venta con una NC total y, si ARCA la autoriza, aplica la anulación comercial.

    Devuelve la NC en el estado que haya quedado (autorizada, pendiente, incierta o rechazada).

    Raises:
        OperacionFiscalInvalidaError: la factura no está autorizada o ya tiene una NC viva.
        Las de `servicio_ventas.validar_anulable_en_conexion` si la venta no puede cancelarse.
        FiscalizacionNoDisponibleError: ARCA no respondió (la NC y la cancelación quedan pendientes).
    """
    _exigir_owner(usuario_id)
    validar_motivo_anulacion(motivo, observaciones)
    perfil = servicio_facturacion.perfil_listo()
    with obtener_conexion() as conexion:
        original = repositorio_fiscal.principal_vivo_de_venta_en_conexion(conexion, venta_id)
    if original is None or not original.autorizado:
        raise OperacionFiscalInvalidaError("Solo se emite una nota de crédito sobre una factura autorizada por ARCA.")
    if original.ambiente != perfil.ambiente:
        raise OperacionFiscalInvalidaError("La factura es de otro ambiente fiscal.")
    tipo = reglas.tipo_comprobante(original.clase, tablas_arca.ROL_NOTA_CREDITO)
    emisor = perfil.snapshot(perfil.punto_venta_cae)
    servicio_parametros.validar_codigos(
        perfil.ambiente, original.clase, tipo, original.receptor.doc_tipo, original.receptor.condicion_iva_receptor_id,
        original.importes, date.today(),
    )
    nuevo = repositorio_fiscal.NuevoComprobante(
        venta_id=venta_id,
        rol=tablas_arca.ROL_NOTA_CREDITO,
        comprobante_asociado_id=original.id,
        ambiente=perfil.ambiente,
        modalidad=estados.MODALIDAD_CAE,
        caea_id=None,
        tipo_comprobante=tipo,
        clase=original.clase,
        concepto=original.concepto,
        moneda=original.moneda,
        cotizacion=original.cotizacion,
        emisor=emisor,
        receptor=original.receptor,
        importes=original.importes,
        lineas=original.lineas,
        contenido_hash=calcular_contenido_hash(
            tablas_arca.ROL_NOTA_CREDITO, original.id, perfil.ambiente, emisor, tipo, original.receptor,
            original.importes, list(original.lineas),
        ),
        usuario_id=usuario_id,
        estado=estados.PENDIENTE,
    )
    try:
        with obtener_conexion(inmediata=True) as conexion:
            servicio_ventas.validar_anulable_en_conexion(conexion, venta_id)
            if repositorio_fiscal.nota_viva_de_en_conexion(conexion, original.id, tablas_arca.ROL_NOTA_CREDITO):
                raise OperacionFiscalInvalidaError("La factura ya tiene una nota de crédito en curso o autorizada.")
            nota_id = repositorio_fiscal.insertar_comprobante_en_conexion(conexion, nuevo)
            repositorio_fiscal.insertar_cancelacion_en_conexion(
                conexion, venta_id, original.id, nota_id, motivo, observaciones, usuario_id
            )
            nota = repositorio_fiscal.obtener_comprobante_en_conexion(conexion, nota_id)
            repositorio_auditoria.registrar_en_conexion(
                conexion, usuario_id, ACCION_FISCAL_SOLICITUD, servicio_facturacion.ENTIDAD, nota_id,
                f"Nota de crédito por {original.nombre_tipo} {original.numero_formateado} (venta #{venta_id}): {motivo}",
            )
    except ErrorBaseDatos as error:
        raise OperacionFiscalInvalidaError("La factura ya tiene una nota de crédito en curso.") from error
    try:
        nota = servicio_facturacion.emitir(nota.id, usuario_id)
    finally:
        aplicar_pendientes()
    return servicio_facturacion.obtener(nota.id)


def aplicar_pendientes() -> None:
    """Resuelve las cancelaciones PENDIENTE según su NC: autorizada → efecto comercial (una vez);
    rechazada o descartada → RECHAZADA; todavía en curso → sigue pendiente. Nunca lanza."""
    with obtener_conexion() as conexion:
        pendientes = repositorio_fiscal.cancelaciones_por_estado_en_conexion(conexion, ("PENDIENTE",))
    for cancelacion in pendientes:
        try:
            _aplicar(cancelacion.id)
        except ErrorAplicacion as error:
            logger.error("No se pudo resolver la cancelación fiscal %s: %s", cancelacion.id, error)


def _aplicar(cancelacion_id: int) -> None:
    with obtener_conexion(inmediata=True) as conexion:
        cancelacion = repositorio_fiscal.obtener_cancelacion_en_conexion(conexion, cancelacion_id)
        if cancelacion.estado != "PENDIENTE":
            return
        nota = repositorio_fiscal.obtener_comprobante_en_conexion(conexion, cancelacion.nota_credito_id)
        if nota.estado in estados.ESTADOS_TERMINALES_SIN_AUTORIZACION:
            repositorio_fiscal.actualizar_cancelacion_en_conexion(conexion, cancelacion_id, "RECHAZADA", f"NC {nota.etiqueta_estado.lower()}")
            return
        if not nota.autorizado:
            return
    try:
        with obtener_conexion(inmediata=True) as conexion:
            servicio_ventas.validar_anulable_en_conexion(conexion, cancelacion.venta_id)
            servicio_ventas.anular_en_conexion(
                conexion, cancelacion.venta_id, cancelacion.motivo, cancelacion.observaciones, cancelacion.usuario_id
            )
            repositorio_fiscal.actualizar_cancelacion_en_conexion(
                conexion, cancelacion_id, "APLICADA", f"NC {nota.numero_formateado} autorizada: venta anulada"
            )
        logger.info("Cancelación fiscal %s aplicada: venta %s anulada por NC %s", cancelacion_id, cancelacion.venta_id, nota.id)
    except ErrorAplicacion as error:
        # La NC está autorizada pero el efecto comercial ya no puede aplicarse (p. ej. la caja se cerró
        # mientras la NC estaba incierta): queda a la vista del dueño, nunca sobre un cierre en silencio.
        with obtener_conexion(inmediata=True) as conexion:
            repositorio_fiscal.actualizar_cancelacion_en_conexion(conexion, cancelacion_id, "SIN_APLICAR", str(error)[:300])
            repositorio_auditoria.registrar_en_conexion(
                conexion, cancelacion.usuario_id, ACCION_FISCAL_ERROR, servicio_facturacion.ENTIDAD, nota.id,
                f"NC {nota.numero_formateado} autorizada sin poder anular la venta #{cancelacion.venta_id}: {str(error)[:150]}",
            )


def conciliar_y_aplicar() -> None:
    """Conciliación automática (arranque y panel): primero los inciertos, después las cancelaciones, el
    mantenimiento CAEA (pedir el código del período, informar lo emitido) y el refresco de tablas de ARCA.
    Nunca lanza (corre en un hilo de arranque): cada paso que falla se registra y no impide los siguientes."""

    def refrescar_tablas() -> None:
        perfil = repositorio_fiscal.obtener_perfil()
        if perfil is not None and perfil.punto_venta_cae_listo:
            servicio_parametros.refrescar_si_vencida(gateway_para(perfil))

    for paso in (servicio_facturacion.conciliar_inciertos, aplicar_pendientes, servicio_caea.mantenimiento_automatico,
                 refrescar_tablas):
        try:
            paso()
        except ErrorAplicacion as error:
            logger.warning("Mantenimiento fiscal automático incompleto (%s): %s", paso.__name__, error)


def cancelaciones_de_venta(venta_id: int):
    return repositorio_fiscal.cancelaciones_de_venta(venta_id)
