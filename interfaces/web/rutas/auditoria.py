"""Consulta del registro de auditoría (V1.2, solo OWNER, solo lectura)."""

from fastapi import APIRouter, Depends, Request

from domain.auditoria import ACCIONES_VALIDAS
from excepciones import DatosInvalidosError
from interfaces.web.auth import requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base
from services import servicio_auditoria, servicio_usuarios

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER"))])


@router.get("/auditoria")
def ver_auditoria(
    request: Request,
    accion: str = "",
    usuario_id: str = "",
    fecha_desde: str = "",
    fecha_hasta: str = "",
):
    # Los <select>/<input> vacíos de un GET llegan como "": significan "sin filtro".
    # `isdigit` acepta "²", que `int` no parsea; `int` además rechaza más de 4300 dígitos (`ValueError`).
    try:
        filtro_usuario = int(usuario_id) if usuario_id.isdecimal() else None
    except ValueError:
        raise DatosInvalidosError("El usuario elegido no es válido.") from None
    contexto = {
        **contexto_base(request),
        "entradas": servicio_auditoria.listar(
            accion or None, filtro_usuario, fecha_desde or None, fecha_hasta or None
        ),
        "acciones": sorted(ACCIONES_VALIDAS),
        "usuarios": servicio_usuarios.listar_todos(),
        "filtro": {
            "accion": accion,
            "usuario_id": filtro_usuario,
            "fecha_desde": fecha_desde,
            "fecha_hasta": fecha_hasta,
        },
    }
    return templates.TemplateResponse(request, "auditoria.html", contexto)
