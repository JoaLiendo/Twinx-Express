"""Rutas de login/logout (Fase 2E): la única parte de la interfaz web que
escribe la cookie de sesión.

Cliente delgado sobre `services.servicio_auth`: no hashea, no valida
contraseñas, no toca `sesiones`/`usuarios` por SQL directo. El mensaje
de error de login es un único texto fijo, usado por igual para
"usuario inexistente", "contraseña incorrecta" y "usuario inactivo"
(`UsuarioInactivoError` trae su propio mensaje interno más específico,
pero a propósito nunca se muestra: ver `excepciones.UsuarioInactivoError`
y `services.servicio_auth`).
"""

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from excepciones import CredencialesInvalidasError, UsuarioInactivoError
from interfaces.web.auth import COOKIE_SEGURA, NOMBRE_COOKIE_SESION, obtener_usuario_actual
from interfaces.web.plantillas import templates
from services import servicio_auth, servicio_usuarios

router = APIRouter()
logger = logging.getLogger(__name__)

_MENSAJE_ERROR_LOGIN = "Usuario o contraseña incorrectos."


def _next_seguro(valor: str | None) -> str:
    """Solo permite redirigir a una ruta relativa propia de esta app.

    Rechaza cualquier cosa que no empiece con `/` (URLs absolutas a
    otro host) o que empiece con `//` (redirect "relativo al esquema",
    un truco clásico de open-redirect que el navegador interpreta como
    otro host). Sin esto, `next` sería una vía para mandar a alguien
    que recién inició sesión a un sitio externo.
    """
    if valor and valor.startswith("/") and not valor.startswith("//"):
        return valor
    return "/"


@router.get("/login")
def formulario_login(request: Request, next: str = "/"):
    if obtener_usuario_actual(request) is not None:
        return RedirectResponse("/", status_code=303)
    if not servicio_usuarios.hay_algun_owner_activo():
        return RedirectResponse("/configuracion-inicial", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"next": _next_seguro(next)})


@router.post("/login")
def procesar_login(request: Request, nombre_usuario: str = Form(...), password: str = Form(...), next: str = Form("/")):
    destino = _next_seguro(next)
    try:
        sesion = servicio_auth.iniciar_sesion(nombre_usuario, password)
    except UsuarioInactivoError:
        logger.info("Intento de acceso a cuenta desactivada: %s", nombre_usuario)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": _MENSAJE_ERROR_LOGIN, "nombre_usuario": nombre_usuario, "next": destino},
        )
    except CredencialesInvalidasError:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": _MENSAJE_ERROR_LOGIN, "nombre_usuario": nombre_usuario, "next": destino},
        )

    respuesta = RedirectResponse(destino, status_code=303)
    respuesta.set_cookie(
        key=NOMBRE_COOKIE_SESION,
        value=sesion.token,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=servicio_auth.DURACION_SESION_SEGUNDOS,
        secure=COOKIE_SEGURA,
    )
    return respuesta


@router.post("/logout")
def cerrar_sesion_http(request: Request):
    token = request.cookies.get(NOMBRE_COOKIE_SESION)
    if token:
        servicio_auth.cerrar_sesion(token)

    respuesta = RedirectResponse("/login", status_code=303)
    respuesta.delete_cookie(NOMBRE_COOKIE_SESION, path="/")
    return respuesta
