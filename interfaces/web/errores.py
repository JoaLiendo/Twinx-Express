"""Respuesta única para los errores 500/422/503 que no son un aviso de negocio (V1.17).

Solo quien navega recibe una página: `/api/` responde JSON siempre, y un cliente que no pide HTML también,
así el contrato de la API del POS no cambia. Los errores de negocio siguen su camino propio
(`manejar_error_de_aplicacion` en `interfaces.web.app`): esto no los reemplaza.
"""

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from interfaces.web.plantillas import templates

MENSAJE_500 = "Algo salió mal. La operación no pudo completarse."
_MENSAJES_HTML = {
    422: "Hay datos inválidos o incompletos.",
    500: MENSAJE_500,
    503: "El servicio no está disponible temporalmente.",
}


def _quiere_html(request: Request) -> bool:
    return not request.url.path.startswith("/api/") and "text/html" in request.headers.get("accept", "")


def respuesta_error(request: Request, estado: int, mensaje_json: str, detalle: str | None = None) -> Response:
    """`detalle` es un texto fijo y seguro para mostrar debajo del mensaje; nunca el de una excepción.

    La plantilla es independiente a propósito: `base.html` consulta la base (`contexto_base`), que puede ser
    justamente lo que falló.
    """
    if _quiere_html(request):
        contexto = {"codigo": estado, "mensaje": _MENSAJES_HTML[estado], "detalle": detalle}
        return templates.TemplateResponse(request, "error_web.html", contexto, status_code=estado)
    return JSONResponse(status_code=estado, content={"error": mensaje_json})
