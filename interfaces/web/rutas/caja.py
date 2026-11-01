"""Rutas del panel de caja: estado actual, historial, apertura, cierre e
ingresos/egresos manuales. Cliente delgado sobre `services.servicio_caja`.
"""

from fastapi import APIRouter, Depends, Form, Request

from domain.caja import clasificar_diferencia
from domain.dinero import centavos_a_texto_localizado, texto_a_centavos
from domain.usuario import Usuario
from interfaces.web.auth import obtener_usuario_actual, requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base, nueva_clave_idempotencia, redireccionar_con_mensaje
from services import servicio_backup, servicio_caja
from services.control_escrituras import control_escrituras

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER", "CASHIER"))])

# El middleware de `interfaces/web/app.py` no cuenta este request como escritura
# de negocio: la registra la propia ruta solo durante el cierre, para que el
# backup posterior no se espere a sí mismo (ver `ControlEscrituras.escritura_de_negocio`).
RUTA_CERRAR_CAJA = "/caja/cerrar"


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
        "movimientos": list(reversed(servicio_caja.listar_movimientos_de_sesion_actual())),
        "alerta_backup": servicio_backup.alerta_de_backup(),
        "clave_ingreso": nueva_clave_idempotencia(),
        "clave_egreso": nueva_clave_idempotencia(),
    }
    return templates.TemplateResponse(request, "caja/panel.html", contexto)


@router.get("/caja/arqueo")
def ver_arqueo(request: Request):
    contexto = {**contexto_base(request), "arqueo": servicio_caja.calcular_arqueo_de_sesion()}
    return templates.TemplateResponse(request, "caja/arqueo.html", contexto)


@router.post("/caja/abrir")
def abrir_caja(
    monto_inicial: str = Form(...),
    descripcion: str = Form(""),
    usuario_actual: Usuario | None = Depends(obtener_usuario_actual),
):
    servicio_caja.abrir_caja(texto_a_centavos(monto_inicial), descripcion or None, usuario_id=usuario_actual.id)
    return redireccionar_con_mensaje("/caja", "success", "Caja abierta correctamente.")


@router.post(RUTA_CERRAR_CAJA)
def cerrar_caja(
    monto_final: str = Form(...),
    descripcion: str = Form(""),
    usuario_actual: Usuario | None = Depends(obtener_usuario_actual),
):
    """Cierra la caja y, ya confirmado el cierre, genera el backup
    `CIERRE_CAJA`. Si el backup falla la caja sigue cerrada: se avisa con un
    toast de advertencia y la alerta queda visible hasta un backup correcto."""
    monto = texto_a_centavos(monto_final)
    with control_escrituras.escritura_de_negocio():
        cierre = servicio_caja.cerrar_caja(monto, descripcion or None, usuario_id=usuario_actual.id)
    mensaje = f"Caja cerrada correctamente. {_mensaje_resultado_cierre(cierre.diferencia_centavos)}"
    resultado = servicio_backup.backup_de_cierre_de_caja(
        servicio_caja.sesion_de_movimiento(cierre.id), control_escrituras, usuario_id=usuario_actual.id
    )
    if resultado.ok:
        return redireccionar_con_mensaje("/caja", "success", f"{mensaje} Backup guardado.")
    return redireccionar_con_mensaje(
        "/caja",
        "warning",
        f"{mensaje} ATENCIÓN: no se pudo guardar el backup automático ({resultado.error}) "
        "Avisá al dueño para que haga un backup manual.",
    )


@router.post("/caja/ingreso")
def registrar_ingreso(
    monto: str = Form(...),
    descripcion: str = Form(...),
    clave_idempotencia: str = Form(""),
    usuario_actual: Usuario | None = Depends(obtener_usuario_actual),
):
    servicio_caja.registrar_ingreso(
        texto_a_centavos(monto),
        descripcion,
        usuario_id=usuario_actual.id,
        clave_idempotencia=clave_idempotencia or None,
    )
    return redireccionar_con_mensaje("/caja", "success", "Ingreso registrado correctamente.")


@router.post("/caja/egreso")
def registrar_egreso(
    monto: str = Form(...),
    descripcion: str = Form(...),
    clave_idempotencia: str = Form(""),
    usuario_actual: Usuario | None = Depends(obtener_usuario_actual),
):
    servicio_caja.registrar_egreso(
        texto_a_centavos(monto),
        descripcion,
        usuario_id=usuario_actual.id,
        clave_idempotencia=clave_idempotencia or None,
    )
    return redireccionar_con_mensaje("/caja", "success", "Egreso registrado correctamente.")
