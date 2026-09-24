"""Utilidades compartidas por los routers de la interfaz web."""

from urllib.parse import quote
from uuid import uuid4

from fastapi import Request
from starlette.responses import RedirectResponse

from interfaces.web.auth import obtener_usuario_actual
from interfaces.web.navegacion import navegacion_visible_para


def nueva_clave_idempotencia() -> str:
    """Clave única para un formulario que mueve dinero o stock (V1.1).

    Se genera al mostrar el formulario (GET) y viaja en un campo oculto: un
    doble clic o un reenvío del mismo formulario llega con la misma clave y
    el servicio lo trata como un reintento, nunca como una operación nueva.
    """
    return uuid4().hex


def redireccionar_con_mensaje(url: str, tipo: str, mensaje: str) -> RedirectResponse:
    """Redirige a `url` adjuntando un mensaje para mostrar como toast.

    `tipo` es 'success', 'error' o 'warning' (ver `base.html`, que lee
    estos query params al cargar la página, muestra el toast
    correspondiente y limpia la URL).
    """
    separador = "&" if "?" in url else "?"
    return RedirectResponse(url=f"{url}{separador}tipo={tipo}&msg={quote(mensaje)}", status_code=303)


def contexto_base(request: Request) -> dict:
    """Datos comunes a toda página: usuario autenticado, badge de caja
    del header y navegación (ya filtrada por el rol de ese usuario).

    Resuelve el usuario una sola vez por request (`obtener_usuario_actual`
    solo lee la cookie de sesión y no lanza: ver `interfaces.web.auth`).
    `interfaces.web.rutas.autenticacion` expone `/login` y
    `/configuracion-inicial` y es quien fija esa cookie: en la
    aplicación real `usuario_actual` es `None` solo mientras no haya
    una sesión iniciada, y la navegación queda filtrada por su rol
    (ver `navegacion_visible_para`).
    """
    from services import servicio_caja

    usuario_actual = obtener_usuario_actual(request)
    rol_actual = usuario_actual.rol if usuario_actual is not None else None
    return {
        "caja_abierta": servicio_caja.consultar_estado(),
        "nav_items": navegacion_visible_para(rol_actual),
        "usuario_actual": usuario_actual,
    }
