"""Ayuda y soporte (V1.18): manual integrado para todos los usuarios y asistencia técnica local (solo
OWNER): versión, ubicación de los datos, estado de la base y de los backups, últimos avisos del registro y
exportación de un diagnóstico sanitizado. Sin servicios externos."""

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from interfaces.web.auth import requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base
from services import servicio_soporte

router_ayuda = APIRouter(dependencies=[Depends(requiere_rol("OWNER", "CASHIER"))])
router = APIRouter(dependencies=[Depends(requiere_rol("OWNER"))])


@router_ayuda.get("/ayuda")
def ver_ayuda(request: Request):
    return templates.TemplateResponse(request, "ayuda.html", contexto_base(request))


@router.get("/soporte")
def ver_soporte(request: Request):
    contexto = {**contexto_base(request), "info": servicio_soporte.obtener_informacion()}
    return templates.TemplateResponse(request, "soporte.html", contexto)


@router.get("/soporte/diagnostico")
def descargar_diagnostico():
    """ZIP de diagnóstico para enviar al soporte: información técnica sanitizada, nunca la base ni secretos."""
    contenido = servicio_soporte.exportar_diagnostico()
    nombre = servicio_soporte.nombre_de_diagnostico()
    return Response(
        content=contenido,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )
