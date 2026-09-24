"""Configuración de los datos comerciales del ticket (V1.2, solo OWNER)."""

from fastapi import APIRouter, Depends, Form, Request

from domain.comercio import LIMITES, DatosComercio
from domain.usuario import Usuario
from interfaces.web.auth import obtener_usuario_actual, requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base, redireccionar_con_mensaje
from services import servicio_configuracion

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER"))])


@router.get("/configuracion")
def ver_configuracion(request: Request):
    contexto = {
        **contexto_base(request),
        "comercio": servicio_configuracion.obtener_datos_comercio(),
        "limites": LIMITES,
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
