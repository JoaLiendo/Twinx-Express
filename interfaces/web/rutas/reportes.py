"""Ruta de Reportes de ventas: filtra por rango de fechas y arma el
resumen del período vía `services.servicio_reportes`.

Cliente delgado: solo lee los parámetros de la URL, llama a
`services/` y renderiza la plantilla. Cero SQL y cero agregación acá
(eso vive en `services.servicio_reportes` -- ver su docstring para las
métricas que este módulo deliberadamente no calcula todavía).

Los reportes operativos (V1.6-C: caja por sesión, compras por proveedor, cuenta corriente y rotación de stock) viven
bajo `/reportes/...`, con la misma protección (solo OWNER) y también solo de lectura.
"""

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request

from excepciones import DatosInvalidosError
from interfaces.web.auth import requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base, respuesta_csv
from services import (
    servicio_deuda_proveedores,
    servicio_exportacion_csv,
    servicio_proveedores,
    servicio_reportes,
    servicio_stock,
)

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER"))])


@router.get("/reportes")
def ver_reportes(request: Request, fecha_desde: str | None = None, fecha_hasta: str | None = None):
    reporte = servicio_reportes.generar_reporte_ventas(fecha_desde, fecha_hasta)
    # Valorización de Inventario: a HOY, deliberadamente sin recibir
    # fecha_desde/fecha_hasta -- no hay ningún parámetro por el que el
    # filtro de período de `reporte` pueda llegar a afectar este cálculo.
    valorizacion = servicio_stock.calcular_valorizacion_inventario()
    contexto = {
        **contexto_base(request),
        "reporte": reporte,
        "valorizacion": valorizacion,
        # Los inputs del filtro reflejan el rango efectivo (incluso
        # cuando no se pidió ninguno explícito y se usó el de
        # por defecto), para que el usuario vea qué período está viendo.
        "fecha_desde": reporte.fecha_desde,
        "fecha_hasta": reporte.fecha_hasta,
    }
    return templates.TemplateResponse(request, "reportes.html", contexto)


def _periodo(fecha_desde: str | None, fecha_hasta: str | None) -> dict[str, str | None]:
    # Un campo vacío del formulario (fecha borrada) equivale a "sin ese límite".
    return {"fecha_desde": fecha_desde or None, "fecha_hasta": fecha_hasta or None}


@router.get("/reportes/caja")
def ver_reporte_caja(request: Request, fecha_desde: str | None = None, fecha_hasta: str | None = None):
    reporte = servicio_reportes.generar_reporte_caja(**_periodo(fecha_desde, fecha_hasta))
    contexto = {**contexto_base(request), "reporte": reporte, "seccion": "caja"}
    return templates.TemplateResponse(request, "reportes/caja.html", contexto)


def _id_proveedor(proveedor_id: str) -> int | None:
    texto_proveedor = proveedor_id.strip()
    try:
        return int(texto_proveedor) if texto_proveedor else None
    except ValueError:
        raise DatosInvalidosError("El proveedor elegido no es válido.") from None


@router.get("/reportes/compras")
def ver_reporte_compras(
    request: Request, fecha_desde: str | None = None, fecha_hasta: str | None = None, proveedor_id: str = ""
):
    id_proveedor = _id_proveedor(proveedor_id)
    reporte = servicio_reportes.generar_reporte_compras(**_periodo(fecha_desde, fecha_hasta), proveedor_id=id_proveedor)
    contexto = {
        **contexto_base(request),
        "reporte": reporte,
        "proveedores": servicio_proveedores.listar_todos(),
        "seccion": "compras",
    }
    return templates.TemplateResponse(request, "reportes/compras.html", contexto)


@router.get("/reportes/cuenta-corriente")
def ver_reporte_cuenta_corriente(request: Request, fecha_desde: str | None = None, fecha_hasta: str | None = None):
    contexto = {
        **contexto_base(request),
        "deuda": servicio_reportes.generar_reporte_deuda(),
        "cobranzas": servicio_reportes.generar_reporte_cobranzas(**_periodo(fecha_desde, fecha_hasta)),
        "seccion": "cuenta-corriente",
    }
    return templates.TemplateResponse(request, "reportes/cuenta_corriente.html", contexto)


@router.get("/reportes/rotacion/exportar")
def exportar_reporte_rotacion(fecha_desde: str | None = None, fecha_hasta: str | None = None):
    """CSV del mismo dataset de la pantalla de rotación (mismo período y reglas)."""
    return respuesta_csv(servicio_exportacion_csv.csv_rotacion(**_periodo(fecha_desde, fecha_hasta)), "rotacion")


@router.get("/reportes/cuenta-corriente/exportar")
def exportar_deuda_de_clientes():
    """CSV de la deuda actual de cuenta corriente (el mismo agregado que el reporte)."""
    return respuesta_csv(servicio_exportacion_csv.csv_deuda_clientes(), "deuda_clientes")


@router.get("/reportes/rotacion")
def ver_reporte_rotacion(request: Request, fecha_desde: str | None = None, fecha_hasta: str | None = None):
    reporte = servicio_reportes.generar_reporte_rotacion(**_periodo(fecha_desde, fecha_hasta))
    contexto = {**contexto_base(request), "reporte": reporte, "seccion": "rotacion"}
    return templates.TemplateResponse(request, "reportes/rotacion.html", contexto)


@router.get("/reportes/deuda-proveedores")
def ver_reporte_deuda_proveedores(request: Request, proveedor_id: str = "", situacion: str = ""):
    reporte = servicio_deuda_proveedores.generar_reporte_deuda_proveedores(_id_proveedor(proveedor_id), situacion)
    filtros = {clave: valor for clave, valor in (("proveedor_id", reporte.proveedor_id), ("situacion", reporte.situacion)) if valor}
    contexto = {
        **contexto_base(request),
        "reporte": reporte,
        "proveedores": servicio_proveedores.listar_todos(),
        "criterio_fifo": servicio_deuda_proveedores.CRITERIO_FIFO,
        "url_exportar": "/reportes/deuda-proveedores/exportar" + (f"?{urlencode(filtros)}" if filtros else ""),
        "seccion": "deuda-proveedores",
    }
    return templates.TemplateResponse(request, "reportes/deuda_proveedores.html", contexto)


@router.get("/reportes/deuda-proveedores/exportar")
def exportar_deuda_proveedores(proveedor_id: str = "", situacion: str = ""):
    """CSV del mismo dataset de la pantalla de deuda a proveedores (mismos filtros y mismo servicio)."""
    return respuesta_csv(
        servicio_exportacion_csv.csv_deuda_proveedores(_id_proveedor(proveedor_id), situacion), "deuda_proveedores"
    )
