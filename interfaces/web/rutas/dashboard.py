"""Panel principal (V1.11-E): cuatro accesos operativos y el estado del día de un vistazo.

OWNER y CASHIER ven contenido distinto: el CASHIER nunca recibe costos, márgenes ni valorizaciones
(ni siquiera en el contexto de la plantilla); el OWNER ve además indicadores que ya existen en
Reportes (stock valorizado a costo y pérdidas del período por defecto), sin analítica nueva.

Cliente delgado: solo llama a `services/` y renderiza la plantilla.
"""

from fastapi import APIRouter, Depends, Request

from domain.usuario import Usuario
from interfaces.web.auth import obtener_usuario_actual, requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base
from services import (
    servicio_backup,
    servicio_caja,
    servicio_configuracion,
    servicio_partidas,
    servicio_reportes,
    servicio_stock,
)
from services.fiscal import servicio_panel

MOVIMIENTOS_RECIENTES = 5

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER", "CASHIER"))])


@router.get("/")
def ver_dashboard(request: Request, usuario_actual: Usuario = Depends(obtener_usuario_actual)):
    contexto = {
        **contexto_base(request),
        "productos_criticos": servicio_stock.listar_stock_critico(),
        "arqueo": servicio_caja.calcular_arqueo_de_sesion(),
        "movimientos_recientes": servicio_caja.listar_movimientos_recientes(MOVIMIENTOS_RECIENTES),
        "vencimientos": list(
            servicio_partidas.resumenes_de_vencimiento(servicio_configuracion.obtener_dias_alerta_vencimiento()).values()
        ),
    }
    if usuario_actual.rol == "OWNER":
        contexto["valorizacion"] = servicio_stock.calcular_valorizacion_inventario()
        contexto["perdidas"] = servicio_reportes.generar_reporte_perdidas()
        # V1.16: lo fiscal que no puede esperar (inciertos, certificado, CAEA sin informar).
        contexto["alertas_fiscales"] = servicio_panel.alerta_fiscal()
        # RC4: la misma alerta persistente de backup fallido que muestran Backup y Caja.
        contexto["alerta_backup"] = servicio_backup.alerta_de_backup()
    return templates.TemplateResponse(request, "dashboard.html", contexto)
