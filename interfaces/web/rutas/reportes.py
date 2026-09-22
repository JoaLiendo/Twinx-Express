"""Ruta de Reportes de ventas: filtra por rango de fechas y arma el
resumen del período vía `services.servicio_reportes`.

Cliente delgado: solo lee los parámetros de la URL, llama a
`services/` y renderiza la plantilla. Cero SQL y cero agregación acá
(eso vive en `services.servicio_reportes` -- ver su docstring para las
métricas que este módulo deliberadamente no calcula todavía).
"""

from fastapi import APIRouter, Depends, Request

from interfaces.web.auth import requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base
from services import servicio_reportes

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER"))])


@router.get("/reportes")
def ver_reportes(request: Request, fecha_desde: str | None = None, fecha_hasta: str | None = None):
    reporte = servicio_reportes.generar_reporte_ventas(fecha_desde, fecha_hasta)
    contexto = {
        **contexto_base(request),
        "reporte": reporte,
        # Los inputs del filtro reflejan el rango efectivo (incluso
        # cuando no se pidió ninguno explícito y se usó el de
        # por defecto), para que el usuario vea qué período está viendo.
        "fecha_desde": reporte.fecha_desde,
        "fecha_hasta": reporte.fecha_hasta,
    }
    return templates.TemplateResponse(request, "reportes.html", contexto)
