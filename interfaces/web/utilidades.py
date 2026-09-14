"""Utilidades compartidas por los routers de la interfaz web."""

from urllib.parse import quote

from fastapi import Request
from starlette.responses import RedirectResponse

from interfaces.web.auth import obtener_usuario_actual
from interfaces.web.navegacion import navegacion_visible_para


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
    Fase 2D: todavía no hay ningún login que fije esa cookie, así que en
    la aplicación real `usuario_actual` da `None` y la navegación se ve
    completa (ver `navegacion_visible_para`) — no cambia nada de lo que
    ya se ve hoy.
    """
    from services import servicio_caja

    usuario_actual = obtener_usuario_actual(request)
    rol_actual = usuario_actual.rol if usuario_actual is not None else None
    return {
        "caja_abierta": servicio_caja.consultar_estado(),
        "nav_items": navegacion_visible_para(rol_actual),
        "usuario_actual": usuario_actual,
    }
