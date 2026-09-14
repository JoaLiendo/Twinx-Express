"""Cascarón visual de Precios (Fase 1 de rediseño): sin lógica de negocio.

Cliente delgado: solo renderiza la plantilla. La gestión de listas de
precios queda para una fase posterior (ver CLAUDE.md y el plan de Fase 1).
"""

from fastapi import APIRouter, Depends, Request

from interfaces.web.auth import requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER"))])


@router.get("/precios")
def ver_precios(request: Request):
    return templates.TemplateResponse(request, "precios.html", contexto_base(request))
