"""Analítica (V1.17, solo OWNER): resumen ejecutivo, ventas, margen bruto, precios y costos, deuda y
proveedores. Cliente delgado: lee filtros de la URL, llama a `services.servicio_analitica` y renderiza.
Pérdidas y reposición viven en sus pantallas existentes (`/reportes/perdidas`, `/reposicion`), que esta
sección solo enlaza. Nada de lo que hay acá escribe en la base."""

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request

from domain.analitica import GRANULARIDADES
from domain.gasto import ETIQUETAS_CATEGORIA as ETIQUETAS_CATEGORIA_GASTO
from interfaces.web.auth import requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base, entero_opcional, respuesta_csv
from services import servicio_analitica, servicio_categorias, servicio_exportacion_csv, servicio_stock

router = APIRouter(prefix="/analitica", dependencies=[Depends(requiere_rol("OWNER"))])

ETIQUETAS_GRANULARIDAD = {"dia": "Por día", "semana": "Por semana", "mes": "Por mes"}
ETIQUETAS_CRITERIO = {"producto": "Por producto", "categoria": "Por categoría", **ETIQUETAS_GRANULARIDAD}


def _periodo(fecha_desde: str | None, fecha_hasta: str | None) -> dict[str, str | None]:
    return {"fecha_desde": fecha_desde or None, "fecha_hasta": fecha_hasta or None}


def _filtros(producto_id: str, categoria_id: str) -> dict[str, int | None]:
    return {
        "producto_id": entero_opcional(producto_id, "El producto elegido no es válido."),
        "categoria_id": entero_opcional(categoria_id, "La categoría elegida no es válida."),
    }


def _opciones_de_filtro() -> dict:
    return {
        "productos": servicio_stock.listar_todos(),
        "categorias": servicio_categorias.obtener_arbol().en_orden(),
    }


def _query(**valores: object) -> str:
    return urlencode({clave: valor for clave, valor in valores.items() if valor not in (None, "")})


@router.get("")
def ver_resumen(request: Request):
    contexto = {**contexto_base(request), "resumen": servicio_analitica.resumen_ejecutivo(), "seccion": "resumen"}
    return templates.TemplateResponse(request, "analitica/resumen.html", contexto)


@router.get("/ventas")
def ver_ventas(
    request: Request,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    granularidad: str = "",
    producto_id: str = "",
    categoria_id: str = "",
):
    analisis = servicio_analitica.analizar_ventas(
        **_periodo(fecha_desde, fecha_hasta), granularidad=granularidad or None, **_filtros(producto_id, categoria_id)
    )
    contexto = {
        **contexto_base(request),
        **_opciones_de_filtro(),
        "analisis": analisis,
        "granularidades": [(clave, ETIQUETAS_GRANULARIDAD[clave]) for clave in GRANULARIDADES],
        "seccion": "ventas",
    }
    return templates.TemplateResponse(request, "analitica/ventas.html", contexto)


@router.get("/margen")
def ver_margen(
    request: Request,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    criterio: str = "",
    producto_id: str = "",
    categoria_id: str = "",
):
    analisis = servicio_analitica.analizar_margen(
        **_periodo(fecha_desde, fecha_hasta), criterio=criterio or None, **_filtros(producto_id, categoria_id)
    )
    contexto = {
        **contexto_base(request),
        **_opciones_de_filtro(),
        "analisis": analisis,
        "criterios": list(ETIQUETAS_CRITERIO.items()),
        "url_exportar": "/analitica/margen/exportar?" + _query(
            fecha_desde=analisis.fecha_desde, fecha_hasta=analisis.fecha_hasta, criterio=analisis.criterio,
            producto_id=analisis.producto_id, categoria_id=analisis.categoria_id,
        ),
        "seccion": "margen",
    }
    return templates.TemplateResponse(request, "analitica/margen.html", contexto)


@router.get("/margen/exportar")
def exportar_margen(
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    criterio: str = "",
    producto_id: str = "",
    categoria_id: str = "",
):
    """CSV del mismo agregado de la pantalla de margen (mismos filtros y mismo servicio)."""
    contenido = servicio_exportacion_csv.csv_margen(
        **_periodo(fecha_desde, fecha_hasta), criterio=criterio or None, **_filtros(producto_id, categoria_id)
    )
    return respuesta_csv(contenido, "margen_bruto")


@router.get("/precios")
def ver_precios(
    request: Request,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    producto_id: str = "",
    categoria_id: str = "",
    umbral: str = "",
):
    analisis = servicio_analitica.analizar_precios(
        **_periodo(fecha_desde, fecha_hasta), **_filtros(producto_id, categoria_id), umbral=umbral or None
    )
    contexto = {
        **contexto_base(request),
        **_opciones_de_filtro(),
        "analisis": analisis,
        "url_exportar": "/analitica/precios/exportar?" + _query(
            fecha_desde=analisis.fecha_desde, fecha_hasta=analisis.fecha_hasta,
            producto_id=analisis.producto_id, categoria_id=analisis.categoria_id,
        ),
        "seccion": "precios",
    }
    return templates.TemplateResponse(request, "analitica/precios.html", contexto)


@router.get("/precios/exportar")
def exportar_precios(
    fecha_desde: str | None = None, fecha_hasta: str | None = None, producto_id: str = "", categoria_id: str = ""
):
    """CSV de la evolución de precios y costos por producto (mismos filtros y mismo servicio)."""
    contenido = servicio_exportacion_csv.csv_evolucion_precios(
        **_periodo(fecha_desde, fecha_hasta), **_filtros(producto_id, categoria_id)
    )
    return respuesta_csv(contenido, "evolucion_precios")


@router.get("/deuda")
def ver_deuda(request: Request):
    contexto = {**contexto_base(request), "analisis": servicio_analitica.analizar_deuda(), "seccion": "deuda"}
    return templates.TemplateResponse(request, "analitica/deuda.html", contexto)


@router.get("/proveedores")
def ver_proveedores(request: Request, fecha_desde: str | None = None, fecha_hasta: str | None = None):
    analisis = servicio_analitica.analizar_proveedores(**_periodo(fecha_desde, fecha_hasta))
    contexto = {**contexto_base(request), "analisis": analisis, "seccion": "proveedores"}
    return templates.TemplateResponse(request, "analitica/proveedores.html", contexto)


@router.get("/resultado")
def ver_resultado(request: Request, fecha_desde: str | None = None, fecha_hasta: str | None = None, granularidad: str = ""):
    """Resultado operativo estimado (cierre operativo): ventas, margen bruto, gastos y resultado por período."""
    analisis = servicio_analitica.analizar_resultado(**_periodo(fecha_desde, fecha_hasta), granularidad=granularidad or None)
    contexto = {
        **contexto_base(request),
        "analisis": analisis,
        "granularidades": list(ETIQUETAS_GRANULARIDAD.items()),
        "etiquetas_categoria": ETIQUETAS_CATEGORIA_GASTO,
        "url_exportar": "/analitica/resultado/exportar?" + _query(
            fecha_desde=analisis.fecha_desde, fecha_hasta=analisis.fecha_hasta, granularidad=analisis.granularidad
        ),
        "seccion": "resultado",
    }
    return templates.TemplateResponse(request, "analitica/resultado.html", contexto)


@router.get("/resultado/exportar")
def exportar_resultado(fecha_desde: str | None = None, fecha_hasta: str | None = None, granularidad: str = ""):
    contenido = servicio_exportacion_csv.csv_resultado(**_periodo(fecha_desde, fecha_hasta), granularidad=granularidad or None)
    return respuesta_csv(contenido, "resultado_operativo")
