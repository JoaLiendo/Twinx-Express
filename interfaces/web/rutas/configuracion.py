"""Configuración (solo OWNER): datos comerciales del ticket (V1.2), aviso de vencimientos (V1.12-D) y
políticas de crédito general y por tipo de cliente (V1.14-B)."""

from fastapi import APIRouter, Depends, Form, Request

from domain.comercio import LIMITES, DatosComercio
from domain.credito import AMBITOS_GLOBALES, MODOS
from domain.usuario import Usuario
from excepciones import DatosInvalidosError
from interfaces.web.auth import obtener_usuario_actual, requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import (
    contexto_base,
    limite_credito_del_formulario,
    redireccionar_con_mensaje,
    version_del_formulario,
)
from services import servicio_configuracion, servicio_credito

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
