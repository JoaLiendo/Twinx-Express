"""Panel principal: estado de caja y alertas de stock de un vistazo.

Cliente delgado: solo llama a `services/` y renderiza la plantilla.
"""

from fastapi import APIRouter, Depends, Request

from interfaces.web.auth import requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base
from services import servicio_caja, servicio_stock

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER", "CASHIER"))])


@router.get("/")
def ver_dashboard(request: Request):
    productos_criticos = servicio_stock.listar_stock_critico()
    arqueo = servicio_caja.calcular_arqueo_del_dia()
    movimientos_recientes = list(reversed(servicio_caja.listar_movimientos()))[:5]
    contexto = {
        **contexto_base(request),
        "productos_criticos": productos_criticos,
        "arqueo": arqueo,
        "movimientos_recientes": movimientos_recientes,
    }
    return templates.TemplateResponse(request, "dashboard.html", contexto)
