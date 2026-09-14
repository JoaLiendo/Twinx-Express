"""Rutas del panel de caja: estado actual, historial, apertura, cierre e
ingresos/egresos manuales. Cliente delgado sobre `services.servicio_caja`.
"""

from fastapi import APIRouter, Depends, Form, Request

from domain.dinero import texto_a_centavos
from interfaces.web.auth import requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base, redireccionar_con_mensaje
from services import servicio_caja

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER", "CASHIER"))])


@router.get("/caja")
def panel_caja(request: Request):
    contexto = {
        **contexto_base(request),
        "movimientos": list(reversed(servicio_caja.listar_movimientos())),
    }
    return templates.TemplateResponse(request, "caja/panel.html", contexto)


@router.get("/caja/arqueo")
def ver_arqueo(request: Request):
    contexto = {**contexto_base(request), "arqueo": servicio_caja.calcular_arqueo_del_dia()}
    return templates.TemplateResponse(request, "caja/arqueo.html", contexto)


@router.post("/caja/abrir")
def abrir_caja(monto_inicial: str = Form(...), descripcion: str = Form("")):
    servicio_caja.abrir_caja(texto_a_centavos(monto_inicial), descripcion or None)
    return redireccionar_con_mensaje("/caja", "success", "Caja abierta correctamente.")


@router.post("/caja/cerrar")
def cerrar_caja(monto_final: str = Form(...), descripcion: str = Form("")):
    servicio_caja.cerrar_caja(texto_a_centavos(monto_final), descripcion or None)
    return redireccionar_con_mensaje("/caja", "success", "Caja cerrada correctamente.")


@router.post("/caja/ingreso")
def registrar_ingreso(monto: str = Form(...), descripcion: str = Form(...)):
    servicio_caja.registrar_ingreso(texto_a_centavos(monto), descripcion)
    return redireccionar_con_mensaje("/caja", "success", "Ingreso registrado correctamente.")


@router.post("/caja/egreso")
def registrar_egreso(monto: str = Form(...), descripcion: str = Form(...)):
    servicio_caja.registrar_egreso(texto_a_centavos(monto), descripcion)
    return redireccionar_con_mensaje("/caja", "success", "Egreso registrado correctamente.")
