"""Cascarón visual de Pedidos (Fase 1 de rediseño): sin lógica de negocio.

Cliente delgado: solo renderiza la plantilla. La funcionalidad real
(alta de pedidos, seguimiento, vínculo con stock) queda para una fase
posterior (ver CLAUDE.md y el plan de Fase 1).
"""

from fastapi import APIRouter, Depends, Request

from interfaces.web.auth import requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER"))])


@router.get("/pedidos")
def ver_pedidos(request: Request):
    return templates.TemplateResponse(request, "pedidos.html", contexto_base(request))
