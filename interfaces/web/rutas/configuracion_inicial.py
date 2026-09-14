"""Ruta de configuración inicial: permite crear el primer usuario
OWNER en una instalación limpia (ver auditoría de Stage D). Sin esto,
la app queda inutilizable: no hay forma de autenticarse si no existe
ningún usuario.

No requiere autenticación -- por definición, nadie puede autenticarse
todavía. La única protección es que ambas rutas se autodeshabilitan en
cuanto existe un OWNER activo; `services.servicio_usuarios.crear_primer_owner`
hace esa comprobación de forma atómica a nivel de base de datos, no
solo con un chequeo previo acá (ver auditoría de concurrencia).
"""

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from excepciones import DatosInvalidosError, NombreUsuarioDuplicadoError
from interfaces.web.plantillas import templates
from services import servicio_usuarios

router = APIRouter()


@router.get("/configuracion-inicial")
def formulario_configuracion_inicial(request: Request):
    if servicio_usuarios.hay_algun_owner_activo():
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "configuracion_inicial.html", {})


@router.post("/configuracion-inicial")
def crear_owner_inicial(
    request: Request,
    # `Form("")` en vez de `Form(...)`: si se declaran "requeridos",
    # FastAPI trata un valor vacío enviado por el cliente como si el
    # campo faltara por completo y responde 422 automáticamente antes
    # de que este handler llegue a ejecutarse (verificado de forma
    # aislada, es un comportamiento de FastAPI, no de esta ruta) --
    # eso saltearía por completo el manejo de error de más abajo. Con
    # el default en `""`, un valor vacío sí llega hasta acá y lo
    # atrapa la validación de dominio normalmente.
    nombre_usuario: str = Form(""),
    nombre_completo: str = Form(""),
    password: str = Form(""),
):
    """Un error de validación o de nombre duplicado se atrapa acá y se
    re-renderiza el mismo formulario con un mensaje claro -- mismo
    patrón que `autenticacion.py::procesar_login`, necesario porque
    esta página es standalone (no extiende `base.html`) y el
    manejador global de `ErrorAplicacion` solo sabe mostrar un toast
    ahí. Nunca se conserva la contraseña ingresada, solo los campos no
    sensibles."""
    try:
        # Se ignora deliberadamente si devolvió el usuario creado o
        # `None` (otro request ya creó el OWNER mientras tanto): en
        # ambos casos el destino es el mismo, `/login` ya normal --
        # ese caso no es un error de validación, no debe mostrarse acá.
        servicio_usuarios.crear_primer_owner(nombre_usuario, nombre_completo, password)
    except (DatosInvalidosError, NombreUsuarioDuplicadoError) as error:
        return templates.TemplateResponse(
            request,
            "configuracion_inicial.html",
            {"error": str(error), "nombre_usuario": nombre_usuario, "nombre_completo": nombre_completo},
        )
    return RedirectResponse("/login", status_code=303)
