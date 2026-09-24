"""Reposición de stock (V1.2, solo OWNER, solo lectura): productos que llegaron
al mínimo, cantidad sugerida y revisión antes de comprar. No crea compras."""

from fastapi import APIRouter, Depends, Request

from interfaces.web.auth import requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base
from services import servicio_stock

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER"))])


@router.get("/reposicion")
def ver_reposicion(request: Request):
    sugerencias = servicio_stock.listar_reposicion()
    contexto = {
        **contexto_base(request),
        "sugerencias": sugerencias,
        "sin_stock": sum(1 for s in sugerencias if s.stock_actual == 0),
        "costo_total_centavos": sum(s.costo_estimado_centavos for s in sugerencias),
    }
    return templates.TemplateResponse(request, "reposicion.html", contexto)
