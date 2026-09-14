"""Cascarón visual de Reportes (Fase 1 de rediseño): sin lógica de negocio.

Cliente delgado: solo renderiza la plantilla. Los reportes reales
(ventas por período, productos más vendidos, etc.) quedan para una
fase posterior (ver CLAUDE.md y el plan de Fase 1).
"""

from fastapi import APIRouter, Depends, Request

from interfaces.web.auth import requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER"))])


@router.get("/reportes")
def ver_reportes(request: Request):
    return templates.TemplateResponse(request, "reportes.html", contexto_base(request))
