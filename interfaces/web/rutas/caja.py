"""Rutas del panel de caja: estado actual, historial, apertura, cierre e
ingresos/egresos manuales. Cliente delgado sobre `services.servicio_caja`.
"""

from fastapi import APIRouter, Depends, Form, Request

from domain.caja import clasificar_diferencia
from domain.dinero import centavos_a_texto_localizado, texto_a_centavos
from domain.usuario import Usuario
from interfaces.web.auth import obtener_usuario_actual, requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base, redireccionar_con_mensaje
from services import servicio_caja

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER", "CASHIER"))])


def _mensaje_resultado_cierre(diferencia_centavos: int) -> str:
    """Texto del resultado del cierre para el toast (migración 011).
    `cerrar_caja` siempre devuelve una diferencia calculada -- nunca
    `None` -- así que acá no hace falta contemplar ese caso."""
    resultado = clasificar_diferencia(diferencia_centavos)
    if resultado == "CUADRADA":
        return "Caja cuadrada."
    monto = centavos_a_texto_localizado(abs(diferencia_centavos))
    signo = "+" if resultado == "SOBRANTE" else "-"
    etiqueta = "Sobrante" if resultado == "SOBRANTE" else "Faltante"
    return f"{etiqueta}: {signo}${monto}."


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
def abrir_caja(
    monto_inicial: str = Form(...),
    descripcion: str = Form(""),
    usuario_actual: Usuario | None = Depends(obtener_usuario_actual),
):
    servicio_caja.abrir_caja(texto_a_centavos(monto_inicial), descripcion or None, usuario_id=usuario_actual.id)
    return redireccionar_con_mensaje("/caja", "success", "Caja abierta correctamente.")


@router.post("/caja/cerrar")
def cerrar_caja(
    monto_final: str = Form(...),
    descripcion: str = Form(""),
    usuario_actual: Usuario | None = Depends(obtener_usuario_actual),
):
    cierre = servicio_caja.cerrar_caja(texto_a_centavos(monto_final), descripcion or None, usuario_id=usuario_actual.id)
    return redireccionar_con_mensaje("/caja", "success", f"Caja cerrada correctamente. {_mensaje_resultado_cierre(cierre.diferencia_centavos)}")


@router.post("/caja/ingreso")
def registrar_ingreso(
    monto: str = Form(...),
    descripcion: str = Form(...),
    usuario_actual: Usuario | None = Depends(obtener_usuario_actual),
):
    servicio_caja.registrar_ingreso(texto_a_centavos(monto), descripcion, usuario_id=usuario_actual.id)
    return redireccionar_con_mensaje("/caja", "success", "Ingreso registrado correctamente.")


@router.post("/caja/egreso")
def registrar_egreso(
    monto: str = Form(...),
    descripcion: str = Form(...),
    usuario_actual: Usuario | None = Depends(obtener_usuario_actual),
):
    servicio_caja.registrar_egreso(texto_a_centavos(monto), descripcion, usuario_id=usuario_actual.id)
    return redireccionar_con_mensaje("/caja", "success", "Egreso registrado correctamente.")
