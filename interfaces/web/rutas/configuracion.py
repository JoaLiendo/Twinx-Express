"""Configuración (solo OWNER): datos comerciales del ticket (V1.2), aviso de vencimientos (V1.12-D),
políticas de crédito general y por tipo de cliente (V1.14-B) y motor del asistente (V1.18)."""

from fastapi import APIRouter, Depends, Form, Request

from domain.comercio import LIMITES, DatosComercio
from domain.credito import AMBITOS_GLOBALES, MODOS
from domain.usuario import Usuario
from excepciones import DatosInvalidosError, MotorIAError
from interfaces.web.auth import obtener_usuario_actual, requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import (
    contexto_base,
    limite_credito_del_formulario,
    redireccionar_con_mensaje,
    version_del_formulario,
)
from services import servicio_configuracion, servicio_credito
from services.asistente import configuracion as configuracion_asistente
from services.asistente import motor as motor_asistente

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER"))])


@router.get("/configuracion")
def ver_configuracion(request: Request):
    contexto = {
        **contexto_base(request),
        "comercio": servicio_configuracion.obtener_datos_comercio(),
        "limites": LIMITES,
        "dias_alerta": servicio_configuracion.obtener_dias_alerta_vencimiento(),
        "politicas_credito": servicio_credito.listar_politicas_globales(),
        "modos_credito": MODOS,
        "asistente": configuracion_asistente.obtener(),
        "asistente_limites": {
            "timeout": (configuracion_asistente.TIMEOUT_MINIMO, configuracion_asistente.TIMEOUT_MAXIMO),
            "num_ctx": (configuracion_asistente.NUM_CTX_MINIMO, configuracion_asistente.NUM_CTX_MAXIMO),
        },
    }
    return templates.TemplateResponse(request, "configuracion.html", contexto)


@router.post("/configuracion")
def guardar_configuracion(
    nombre: str = Form(""),
    direccion: str = Form(""),
    telefono: str = Form(""),
    pie: str = Form(""),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    cambiados = servicio_configuracion.guardar_datos_comercio(
        DatosComercio(nombre=nombre, direccion=direccion, telefono=telefono, pie=pie),
        usuario_id=usuario_actual.id,
    )
    mensaje = (
        f"Datos del ticket guardados ({', '.join(cambiados)})." if cambiados else "No hubo cambios que guardar."
    )
    return redireccionar_con_mensaje("/configuracion", "success", mensaje)


@router.post("/configuracion/credito")
def guardar_politica_credito(
    ambito: str = Form(""),
    modo: str = Form(""),
    limite: str = Form(""),
    version: str = Form(""),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    if ambito not in AMBITOS_GLOBALES:
        raise DatosInvalidosError("Política de crédito inválida.")
    servicio_credito.configurar_politica(
        ambito,
        modo,
        limite_credito_del_formulario(modo, limite),
        usuario_actual.id,
        version_esperada=version_del_formulario(version),
    )
    return redireccionar_con_mensaje("/configuracion", "success", "Política de crédito guardada.")


@router.post("/configuracion/vencimientos")
def guardar_vencimientos(dias_alerta: str = Form(""), usuario_actual: Usuario = Depends(obtener_usuario_actual)):
    dias = servicio_configuracion.guardar_dias_alerta_vencimiento(dias_alerta, usuario_actual.id)
    return redireccionar_con_mensaje(
        "/configuracion", "success", f"Aviso de vencimiento: {dias} día{'s' if dias != 1 else ''} antes."
    )


@router.post("/configuracion/asistente")
def guardar_asistente(
    motor: str = Form(""),
    url: str = Form(""),
    modelo: str = Form(""),
    timeout_segundos: str = Form(""),
    num_ctx: str = Form(""),
    accion: str = Form("guardar"),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    """Guarda el motor del asistente o, con , solo comprueba la conexión sin guardar nada.
    Una configuración inválida (URL remota, modelo mal escrito, límites fuera de rango) vuelve como toast."""
    try:
        configuracion = configuracion_asistente.validar(motor, url, modelo, timeout_segundos, num_ctx)
    except MotorIAError as error:
        return redireccionar_con_mensaje("/configuracion", "error", str(error))
    if accion == "probar":
        estado = motor_asistente.probar_motor(configuracion)
        if not configuracion.usa_ia:
            return redireccionar_con_mensaje("/configuracion", "success", "Sin motor de IA: el asistente usa la ayuda integrada.")
        tipo = "success" if estado.disponible else "warning"
        return redireccionar_con_mensaje("/configuracion", tipo, f"{estado.etiqueta}. {estado.detalle}")
    configuracion_asistente.guardar(configuracion, usuario_actual.id)
    motor_asistente.probar_motor(configuracion)  # refresca el estado que ve el panel
    detalle = f"IA local {configuracion.modelo} en {configuracion.url}" if configuracion.usa_ia else "ayuda integrada, sin motor de IA"
    return redireccionar_con_mensaje("/configuracion", "success", f"Asistente guardado: {detalle}.")
