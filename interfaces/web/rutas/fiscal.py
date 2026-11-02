"""Facturación fiscal ARCA (V1.16): POS, comprobantes imprimibles y panel del dueño.

Cliente delgado: cero SQL, cero SOAP, cero reglas fiscales. Permisos:
- OWNER y CASHIER: fiscalizar una venta, ver e imprimir un comprobante AUTORIZADO.
- Solo OWNER: panel, perfil del emisor, puntos de venta, certificados, IVA y otros impuestos nacionales
  indirectos de productos, conciliar y descartar. Ningún formulario permite elegir el número, el CAE ni el ambiente (producción se habilita
  únicamente por consola del técnico).
"""

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response

from domain.fiscal import otros_impuestos, reglas
from domain.fiscal.comprobante import AMBIENTES, ComprobanteFiscal
from domain.fiscal.documento import armar
from domain.fiscal.emisor import PerfilFiscal
from domain.fiscal.iva import OPCIONES_TRATAMIENTO, TratamientoIva
from domain.usuario import Usuario
from excepciones import DatosInvalidosError
from interfaces.web.auth import obtener_usuario_actual, requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base, redireccionar_con_mensaje
from domain.venta import MOTIVOS_ANULACION_VALIDOS
from services.fiscal import (
    credenciales,
    servicio_caea,
    documentos,
    servicio_conexion,
    servicio_facturacion,
    servicio_notas,
    servicio_panel,
    servicio_perfil,
)
from services.fiscal.gateway import invalidar_tickets

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER", "CASHIER"))])
_SOLO_OWNER = Depends(requiere_rol("OWNER"))
_TAMANO_MAXIMO_ARCHIVO = credenciales.TAMANO_MAXIMO_ARCHIVO


def _json_comprobante(comprobante: ComprobanteFiscal) -> dict:
    return {
        "comprobante_id": comprobante.id,
        "venta_id": comprobante.venta_id,
        "estado": comprobante.estado,
        "etiqueta": comprobante.etiqueta_estado,
        "autorizado": comprobante.autorizado,
        "tipo": comprobante.nombre_tipo,
        "numero": comprobante.numero_formateado if comprobante.numero else None,
        "codigo_autorizacion": comprobante.codigo_autorizacion,
        "errores": [m.get("mensaje") for m in (comprobante.errores + comprobante.observaciones)][:5]
        if comprobante.estado == "RECHAZADO" else [],
    }


@router.post("/api/fiscal/ventas/{venta_id}/fiscalizar")
def api_fiscalizar(venta_id: int, usuario: Usuario = Depends(obtener_usuario_actual)) -> dict:
    return _json_comprobante(servicio_caea.fiscalizar_con_contingencia(venta_id, usuario.id))


@router.post("/ventas/{venta_id}/fiscalizar")
def fiscalizar_desde_detalle(venta_id: int, usuario: Usuario = Depends(obtener_usuario_actual)):
    comprobante = servicio_caea.fiscalizar_con_contingencia(venta_id, usuario.id)
    tipo = "success" if comprobante.autorizado else "warning"
    return redireccionar_con_mensaje(f"/ventas/{venta_id}", tipo, f"{comprobante.nombre_tipo}: {comprobante.etiqueta_estado}.")


def _documento(comprobante_id: int):
    comprobante = servicio_facturacion.obtener(comprobante_id)
    asociado = (
        servicio_facturacion.obtener(comprobante.comprobante_asociado_id) if comprobante.comprobante_asociado_id else None
    )
    return comprobante, armar(comprobante, asociado)


@router.get("/fiscal/comprobantes/{comprobante_id}")
def ver_comprobante(request: Request, comprobante_id: int):
    """Vista imprimible A4. Un comprobante sin autorización no tiene documento fiscal (409)."""
    comprobante, documento = _documento(comprobante_id)
    contexto = {"comprobante": comprobante, "documento": documento, "qr_svg": documentos.qr_svg(documento.qr_url)}
    return templates.TemplateResponse(request, "fiscal/comprobante.html", contexto)


@router.get("/fiscal/comprobantes/{comprobante_id}/ticket")
def ticket_comprobante(request: Request, comprobante_id: int):
    """Formato térmico de 80 mm."""
    comprobante, documento = _documento(comprobante_id)
    contexto = {"comprobante": comprobante, "documento": documento, "qr_svg": documentos.qr_svg(documento.qr_url)}
    return templates.TemplateResponse(request, "fiscal/comprobante_80mm.html", contexto)


@router.get("/fiscal/comprobantes/{comprobante_id}/pdf")
def pdf_comprobante(comprobante_id: int) -> Response:
    comprobante, documento = _documento(comprobante_id)
    nombre = f"{comprobante.nombre_tipo.replace(' ', '_')}_{comprobante.numero_formateado}.pdf"
    return Response(
        documentos.pdf(documento),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


# --- Solo OWNER ------------------------------------------------------------------------------------


@router.get("/fiscal", dependencies=[_SOLO_OWNER])
def panel(request: Request):
    contexto = {**contexto_base(request), "panel": servicio_panel.estado(), "ambientes": AMBIENTES}
    return templates.TemplateResponse(request, "fiscal/panel.html", contexto)


@router.post("/fiscal/probar-conexion", dependencies=[_SOLO_OWNER])
def probar_conexion(usuario: Usuario = Depends(obtener_usuario_actual)):
    estado = servicio_conexion.probar_conexion(usuario.id)
    texto = f"ARCA responde: aplicación {estado.aplicacion}, base {estado.base_datos}, autenticación {estado.autenticacion}."
    return redireccionar_con_mensaje("/fiscal", "success" if estado.ok else "warning", texto)


@router.post("/fiscal/ultimos-autorizados", dependencies=[_SOLO_OWNER])
def ultimos_autorizados(usuario: Usuario = Depends(obtener_usuario_actual)):
    ultimos = servicio_conexion.ultimos_autorizados(usuario.id)
    return redireccionar_con_mensaje("/fiscal", "success", "Último autorizado en ARCA: " + ", ".join(f"{t} n° {n}" for t, n in ultimos.items()))


@router.post("/fiscal/caea/solicitar", dependencies=[_SOLO_OWNER])
def solicitar_caea(usuario: Usuario = Depends(obtener_usuario_actual)):
    obtenidos = servicio_caea.solicitar(usuario.id)
    return redireccionar_con_mensaje("/fiscal", "success", "CAEA: " + ", ".join(f"{c.etiqueta} {c.codigo}" for c in obtenidos))


@router.post("/fiscal/caea/informar", dependencies=[_SOLO_OWNER])
def informar_caea():
    informados = servicio_caea.informar_pendientes()
    sin_movimiento = servicio_caea.informar_sin_movimiento()
    return redireccionar_con_mensaje(
        "/fiscal", "success", f"Informados a ARCA: {informados} comprobante(s) CAEA, {sin_movimiento} período(s) sin movimiento."
    )


@router.post("/fiscal/conciliar-inciertos", dependencies=[_SOLO_OWNER])
def conciliar_inciertos():
    servicio_notas.conciliar_y_aplicar()
    return redireccionar_con_mensaje("/fiscal", "success", "Conciliación con ARCA ejecutada: revisá el estado de los comprobantes.")


@router.post("/fiscal/comprobantes/{comprobante_id}/conciliar", dependencies=[_SOLO_OWNER])
def conciliar(comprobante_id: int, usuario: Usuario = Depends(obtener_usuario_actual)):
    comprobante = servicio_facturacion.conciliar(comprobante_id, usuario.id)
    servicio_notas.aplicar_pendientes()
    return redireccionar_con_mensaje(f"/ventas/{comprobante.venta_id}", "success", f"Conciliado: {comprobante.etiqueta_estado}.")


@router.post("/fiscal/comprobantes/{comprobante_id}/descartar", dependencies=[_SOLO_OWNER])
def descartar(comprobante_id: int, usuario: Usuario = Depends(obtener_usuario_actual)):
    comprobante = servicio_facturacion.descartar(comprobante_id, usuario.id)
    servicio_notas.aplicar_pendientes()
    return redireccionar_con_mensaje(f"/ventas/{comprobante.venta_id}", "success", "Comprobante pendiente descartado.")


@router.post("/fiscal/comprobantes/{comprobante_id}/reintentar", dependencies=[_SOLO_OWNER])
def reintentar(comprobante_id: int, usuario: Usuario = Depends(obtener_usuario_actual)):
    """Reenvía un PENDIENTE (nunca un INCIERTO: ese se concilia). Sirve para facturas y notas de crédito."""
    comprobante = servicio_facturacion.reintentar(comprobante_id, usuario.id)
    servicio_notas.aplicar_pendientes()
    return redireccionar_con_mensaje(f"/ventas/{comprobante.venta_id}", "success", f"{comprobante.nombre_tipo}: {comprobante.etiqueta_estado}.")


@router.get("/fiscal/ventas/{venta_id}/nota-credito", dependencies=[_SOLO_OWNER])
def formulario_nota_credito(request: Request, venta_id: int):
    contexto = {
        **contexto_base(request),
        "venta_id": venta_id,
        "original": next((c for c in servicio_facturacion.comprobantes_de_venta(venta_id) if c.rol == "PRINCIPAL" and c.autorizado), None),
        "motivos": sorted(MOTIVOS_ANULACION_VALIDOS),
    }
    return templates.TemplateResponse(request, "fiscal/nota_credito.html", contexto)


@router.post("/fiscal/ventas/{venta_id}/nota-credito", dependencies=[_SOLO_OWNER])
def emitir_nota_credito(
    venta_id: int,
    motivo: str = Form(...),
    observaciones: str = Form(""),
    usuario: Usuario = Depends(obtener_usuario_actual),
):
    nota = servicio_notas.emitir_nota_credito_total(venta_id, motivo, observaciones.strip() or None, usuario.id)
    tipo = "success" if nota.autorizado else "warning"
    return redireccionar_con_mensaje(f"/ventas/{venta_id}", tipo, f"{nota.nombre_tipo}: {nota.etiqueta_estado}.")


@router.get("/fiscal/configuracion", dependencies=[_SOLO_OWNER])
def formulario_configuracion(request: Request):
    contexto = {
        **contexto_base(request),
        "perfil": servicio_perfil.obtener_perfil(),
        "condiciones": reglas.CONDICIONES_IVA_EMISOR,
        "habilitaciones": reglas.HABILITACIONES_CLASE_A,
        "ambientes": AMBIENTES,
    }
    return templates.TemplateResponse(request, "fiscal/configuracion.html", contexto)


def _entero_opcional(texto: str, campo: str) -> int | None:
    texto = texto.strip()
    if not texto:
        return None
    if not texto.isdigit():
        raise DatosInvalidosError(f"El {campo} debe ser un número.")
    return int(texto)


@router.post("/fiscal/configuracion", dependencies=[_SOLO_OWNER])
def guardar_configuracion(
    cuit: str = Form(""),
    razon_social: str = Form(""),
    condicion_iva: str = Form(""),
    domicilio: str = Form(""),
    ingresos_brutos: str = Form(""),
    inicio_actividades: str = Form(""),
    habilitacion_clase_a: str = Form("NORMAL"),
    punto_venta_cae: str = Form(""),
    punto_venta_caea: str = Form(""),
    contingencia_caea: str = Form(""),
    usuario: Usuario = Depends(obtener_usuario_actual),
):
    perfil = PerfilFiscal(
        cuit=cuit,
        razon_social=razon_social,
        condicion_iva=condicion_iva,
        domicilio=domicilio,
        ingresos_brutos=ingresos_brutos,
        inicio_actividades=inicio_actividades,
        habilitacion_clase_a=habilitacion_clase_a,
        punto_venta_cae=_entero_opcional(punto_venta_cae, "punto de venta"),
        punto_venta_caea=_entero_opcional(punto_venta_caea, "punto de venta CAEA"),
        contingencia_caea=contingencia_caea == "1",
    )
    cambiados = servicio_perfil.guardar_perfil(perfil, usuario.id)
    mensaje = f"Perfil fiscal guardado ({', '.join(cambiados)})." if cambiados else "Sin cambios."
    return redireccionar_con_mensaje("/fiscal/configuracion", "success", mensaje)


@router.post("/fiscal/configuracion/validar-punto-venta", dependencies=[_SOLO_OWNER])
def validar_punto_venta(modalidad: str = Form("CAE"), usuario: Usuario = Depends(obtener_usuario_actual)):
    fallidas = servicio_conexion.validar_punto_venta(usuario.id, caea=modalidad == "CAEA")
    texto = "ARCA confirmó el punto de venta."
    if fallidas:
        return redireccionar_con_mensaje("/fiscal/configuracion", "warning", f"{texto} No se pudieron descargar: {', '.join(fallidas)}.")
    return redireccionar_con_mensaje("/fiscal/configuracion", "success", f"{texto} Tablas de ARCA actualizadas.")


@router.get("/fiscal/certificados", dependencies=[_SOLO_OWNER])
def certificados(request: Request):
    contexto = {
        **contexto_base(request),
        "certificados": servicio_panel.estado().certificados,
        "ambientes": AMBIENTES,
    }
    return templates.TemplateResponse(request, "fiscal/certificados.html", contexto)


async def _leer_limitado(archivo: UploadFile) -> bytes:
    contenido = await archivo.read(_TAMANO_MAXIMO_ARCHIVO + 1)
    if len(contenido) > _TAMANO_MAXIMO_ARCHIVO:
        raise DatosInvalidosError("El archivo es demasiado grande para ser un certificado o una clave.")
    return contenido


@router.post("/fiscal/certificados", dependencies=[_SOLO_OWNER])
async def importar_certificado(
    ambiente: str = Form(...),
    certificado: UploadFile = File(...),
    clave: UploadFile = File(...),
    password: str = Form(""),
    usuario: Usuario = Depends(obtener_usuario_actual),
):
    """La contraseña de la clave solo se usa en esta request y nunca se guarda ni se loguea."""
    if ambiente not in AMBIENTES:
        raise DatosInvalidosError("Ambiente inválido.")
    perfil = servicio_perfil.obtener_perfil()
    info = credenciales.importar(
        ambiente, await _leer_limitado(certificado), await _leer_limitado(clave), password or None,
        perfil.cuit if perfil else None, usuario.id,
    )
    invalidar_tickets(ambiente)
    return redireccionar_con_mensaje("/fiscal/certificados", "success", f"Certificado de {AMBIENTES[ambiente].lower()} cargado: vence {info.vence}.")


@router.get("/fiscal/productos", dependencies=[_SOLO_OWNER])
def productos_iva(request: Request, q: str = "", todos: str = ""):
    contexto = {
        **contexto_base(request),
        "productos": servicio_perfil.listar_productos(not todos, q),
        "opciones": OPCIONES_TRATAMIENTO,
        "opciones_otros": otros_impuestos.CLASIFICACIONES,
        "q": q,
        "todos": bool(todos),
        "sin_clasificar": servicio_perfil.contar_sin_clasificar(),
        "otros_pendientes": servicio_perfil.contar_otros_impuestos_pendientes(),
    }
    return templates.TemplateResponse(request, "fiscal/productos.html", contexto)


def _campos_por_producto(formulario, prefijo: str) -> dict[int, str]:
    """`<prefijo><id>` con valor elegido; los vacíos ("sin cambios") no se tocan."""
    campos = {}
    for nombre, valor in formulario.items():
        if not nombre.startswith(prefijo) or not str(valor).strip():
            continue
        identificador = nombre.removeprefix(prefijo)
        if not identificador.isdigit():
            raise DatosInvalidosError("Producto inválido.")
        campos[int(identificador)] = str(valor).strip()
    return campos


@router.post("/fiscal/productos", dependencies=[_SOLO_OWNER])
async def clasificar_productos(request: Request, usuario: Usuario = Depends(obtener_usuario_actual)):
    """`iva_<id>` clasifica el IVA y `otros_<id>` los otros impuestos nacionales indirectos de ese producto."""
    formulario = await request.form()
    tratamientos = {
        producto_id: TratamientoIva.desde_texto(valor)
        for producto_id, valor in _campos_por_producto(formulario, "iva_").items()
    }
    clasificaciones_otros = _campos_por_producto(formulario, "otros_")
    for valor in clasificaciones_otros.values():
        otros_impuestos.validar_clasificacion(valor)  # todo se valida antes de guardar cualquiera de las dos
    cambiados_iva = servicio_perfil.clasificar_productos(tratamientos, usuario.id) if tratamientos else 0
    cambiados_otros = (
        servicio_perfil.clasificar_otros_impuestos(clasificaciones_otros, usuario.id) if clasificaciones_otros else 0
    )
    volver = "/fiscal/productos" + ("?todos=1" if formulario.get("todos") else "")
    return redireccionar_con_mensaje(
        volver, "success",
        f"Clasificación guardada: IVA en {cambiados_iva} producto(s), otros impuestos nacionales indirectos en"
        f" {cambiados_otros} producto(s).",
    )


def contexto_fiscal_de_venta(venta_id: int, usuario: Usuario | None) -> dict:
    """Datos para la sección fiscal del detalle de una venta (lo usa `rutas.ventas`)."""
    comprobantes = servicio_facturacion.comprobantes_de_venta(venta_id)
    vivo = next(
        (c for c in comprobantes if c.rol == "PRINCIPAL" and c.estado not in ("RECHAZADO", "DESCARTADO")), None
    )
    return {
        "comprobantes_fiscales": comprobantes,
        "cancelaciones_fiscales": servicio_notas.cancelaciones_de_venta(venta_id),
        "nota_credito_viva": any(c.rol == "NOTA_CREDITO" and c.estado not in ("RECHAZADO", "DESCARTADO") for c in comprobantes),
        "principal_fiscal_vivo": vivo,
        "fiscal_activo": servicio_panel.fiscalizacion_activa(),
        "es_owner": usuario is not None and usuario.rol == "OWNER",
    }


