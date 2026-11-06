"""Consulta del registro de auditoría (V1.2, solo OWNER, solo lectura)."""

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request

from domain.auditoria import ACCIONES_VALIDAS
from excepciones import DatosInvalidosError
from interfaces.web.auth import requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base, pagina_o_primera, respuesta_csv_por_lotes
from services import servicio_auditoria, servicio_exportacion_csv, servicio_usuarios

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER"))])


def _filtros(accion: str, usuario_id: str, fecha_desde: str, fecha_hasta: str) -> dict:
    """Filtros de la pantalla, los mismos que usa la exportación."""
    # Los <select>/<input> vacíos de un GET llegan como "": significan "sin filtro".
    # `isdigit` acepta "²", que `int` no parsea; `int` además rechaza más de 4300 dígitos (`ValueError`).
    try:
        filtro_usuario = int(usuario_id) if usuario_id.isdecimal() else None
    except ValueError:
        raise DatosInvalidosError("El usuario elegido no es válido.") from None
    return {
        "accion": accion or None,
        "usuario_id": filtro_usuario,
        "fecha_desde": fecha_desde or None,
        "fecha_hasta": fecha_hasta or None,
    }


@router.get("/auditoria/exportar")
def exportar_auditoria(accion: str = "", usuario_id: str = "", fecha_desde: str = "", fecha_hasta: str = ""):
    """CSV de TODO lo que cumple los filtros de la pantalla (no solo la página visible)."""
    filtros = _filtros(accion, usuario_id, fecha_desde, fecha_hasta)
    return respuesta_csv_por_lotes(servicio_exportacion_csv.csv_auditoria(**filtros), "auditoria")


@router.get("/auditoria")
def ver_auditoria(
    request: Request,
    accion: str = "",
    usuario_id: str = "",
    fecha_desde: str = "",
    fecha_hasta: str = "",
    pagina: str = "1",
):
    filtros = _filtros(accion, usuario_id, fecha_desde, fecha_hasta)
    resultado = servicio_auditoria.listar_pagina(**filtros, pagina=pagina_o_primera(pagina))
    contexto = {
        **contexto_base(request),
        "entradas": resultado.entradas,
        "acciones": sorted(ACCIONES_VALIDAS),
        "usuarios": servicio_usuarios.listar_todos(),
        "filtro": {
            "accion": accion,
            "usuario_id": filtros["usuario_id"],
            "fecha_desde": fecha_desde,
            "fecha_hasta": fecha_hasta,
        },
        "pagina": resultado.pagina,
        "total_paginas": resultado.total_paginas,
        "total_entradas": resultado.total,
        # Los filtros pedidos viajan en los enlaces de página y en el de exportar.
        "consulta_filtros": urlencode({clave: valor for clave, valor in filtros.items() if valor is not None}),
    }
    return templates.TemplateResponse(request, "auditoria.html", contexto)
