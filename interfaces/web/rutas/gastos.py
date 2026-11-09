"""Gastos operativos (migración 048): listado con filtros, alta, detalle, corrección descriptiva, anulación y
exportación CSV. Solo OWNER: el CASHIER no ve gastos (son información del resultado del negocio)."""

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request

from domain.dinero import texto_a_centavos
from domain.gasto import CATEGORIAS_GASTO, MEDIO_EFECTIVO_CAJA, MEDIOS_PAGO
from domain.usuario import Usuario
from excepciones import GastoNoEncontradoError
from interfaces.web.auth import obtener_usuario_actual, requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import (
    contexto_base,
    nueva_clave_idempotencia,
    pagina_o_primera,
    redireccionar_con_mensaje,
    respuesta_csv,
)
from services import servicio_exportacion_csv, servicio_gastos
from services.servicio_gastos import ESTADOS_FILTRO

router = APIRouter(prefix="/gastos", dependencies=[Depends(requiere_rol("OWNER"))])

_ETIQUETAS_ESTADO = {"VIGENTE": "Vigentes", "ANULADO": "Anulados"}


def _filtros(fecha_desde: str, fecha_hasta: str, categoria: str, medio_pago: str, estado: str, q: str) -> dict:
    return {
        "fecha_desde": fecha_desde,
        "fecha_hasta": fecha_hasta,
        "categoria": categoria,
        "medio_pago": medio_pago,
        "estado": estado,
        "q": q.strip()[:100],
    }


def _opciones() -> dict:
    return {
        "categorias": CATEGORIAS_GASTO,
        "medios_pago": MEDIOS_PAGO,
        "medio_efectivo_caja": MEDIO_EFECTIVO_CAJA,
        "estados": [(clave, _ETIQUETAS_ESTADO[clave]) for clave in ESTADOS_FILTRO],
    }


@router.get("")
def listar_gastos(
    request: Request,
    fecha_desde: str = "",
    fecha_hasta: str = "",
    categoria: str = "",
    medio_pago: str = "",
    estado: str = "",
    q: str = "",
    pagina: str = "1",
):
    valores = _filtros(fecha_desde, fecha_hasta, categoria, medio_pago, estado, q)
    filtros = servicio_gastos.filtros_del_listado(
        valores["fecha_desde"], valores["fecha_hasta"], valores["categoria"], valores["medio_pago"], valores["estado"], valores["q"]
    )
    listado = servicio_gastos.listar_gastos(filtros, pagina_o_primera(pagina))
    consulta = urlencode({clave: valor for clave, valor in valores.items() if valor})
    contexto = {
        **contexto_base(request),
        **_opciones(),
        "listado": listado,
        "filtros": valores,
        "hay_filtro_activo": bool(consulta),
        "consulta_filtros": consulta,
    }
    return templates.TemplateResponse(request, "gastos/lista.html", contexto)


@router.get("/exportar")
def exportar_gastos(
    fecha_desde: str = "", fecha_hasta: str = "", categoria: str = "", medio_pago: str = "", estado: str = "", q: str = ""
):
    """CSV con los mismos filtros del listado (sin paginar)."""
    filtros = servicio_gastos.filtros_del_listado(fecha_desde, fecha_hasta, categoria, medio_pago, estado, q)
    return respuesta_csv(servicio_exportacion_csv.csv_gastos(filtros), "gastos")


@router.get("/nuevo")
def formulario_nuevo_gasto(request: Request):
    from datetime import date

    contexto = {
        **contexto_base(request),
        **_opciones(),
        "clave_idempotencia": nueva_clave_idempotencia(),
        "hoy": date.today().isoformat(),
    }
    return templates.TemplateResponse(request, "gastos/nuevo.html", contexto)


@router.post("/nuevo")
def crear_gasto(
    fecha: str = Form(""),
    categoria: str = Form(""),
    descripcion: str = Form(""),
    importe: str = Form(""),
    medio_pago: str = Form(""),
    referencia: str = Form(""),
    responsable: str = Form(""),
    observaciones: str = Form(""),
    clave_idempotencia: str = Form(""),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    gasto = servicio_gastos.registrar_gasto(
        usuario_id=usuario_actual.id,
        fecha=fecha,
        categoria=categoria,
        descripcion=descripcion,
        importe_centavos=texto_a_centavos(importe),
        medio_pago=medio_pago,
        referencia=referencia,
        responsable=responsable,
        observaciones=observaciones,
        clave_idempotencia=clave_idempotencia or None,
    )
    return redireccionar_con_mensaje(f"/gastos/{gasto.id}", "success", f"Gasto #{gasto.id} registrado correctamente.")


def _gasto_o_redireccion(gasto_id: int):
    try:
        return servicio_gastos.obtener_gasto(gasto_id), None
    except GastoNoEncontradoError:
        return None, redireccionar_con_mensaje("/gastos", "error", "El gasto no existe.")


@router.get("/{gasto_id}")
def ver_gasto(request: Request, gasto_id: int):
    gasto, redireccion = _gasto_o_redireccion(gasto_id)
    if redireccion:
        return redireccion
    return templates.TemplateResponse(request, "gastos/detalle.html", {**contexto_base(request), "gasto": gasto})


@router.get("/{gasto_id}/editar")
def formulario_editar_gasto(request: Request, gasto_id: int):
    gasto, redireccion = _gasto_o_redireccion(gasto_id)
    if redireccion:
        return redireccion
    if not gasto.vigente:
        return redireccionar_con_mensaje(f"/gastos/{gasto_id}", "error", "Un gasto anulado no se modifica.")
    return templates.TemplateResponse(request, "gastos/editar.html", {**contexto_base(request), **_opciones(), "gasto": gasto})


@router.post("/{gasto_id}/editar")
def accion_editar_gasto(
    gasto_id: int,
    categoria: str = Form(""),
    descripcion: str = Form(""),
    referencia: str = Form(""),
    responsable: str = Form(""),
    observaciones: str = Form(""),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    servicio_gastos.modificar_gasto(
        gasto_id,
        usuario_id=usuario_actual.id,
        categoria=categoria,
        descripcion=descripcion,
        referencia=referencia,
        responsable=responsable,
        observaciones=observaciones,
    )
    return redireccionar_con_mensaje(f"/gastos/{gasto_id}", "success", "Gasto actualizado correctamente.")


@router.get("/{gasto_id}/anular")
def formulario_anular_gasto(request: Request, gasto_id: int):
    gasto, redireccion = _gasto_o_redireccion(gasto_id)
    if redireccion:
        return redireccion
    if not gasto.vigente:
        return redireccionar_con_mensaje(f"/gastos/{gasto_id}", "error", "El gasto ya fue anulado.")
    return templates.TemplateResponse(request, "gastos/anular.html", {**contexto_base(request), "gasto": gasto})


@router.post("/{gasto_id}/anular")
def accion_anular_gasto(
    gasto_id: int,
    motivo: str = Form(""),
    reintegro_a_caja: str = Form(""),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    """Quién anula es siempre el usuario autenticado, nunca un dato del formulario."""
    reintegro = {"SI": True, "NO": False}.get(reintegro_a_caja)
    servicio_gastos.anular_gasto(gasto_id, usuario_id=usuario_actual.id, motivo=motivo, reintegro_a_caja=reintegro)
    return redireccionar_con_mensaje(f"/gastos/{gasto_id}", "success", "Gasto anulado correctamente.")
