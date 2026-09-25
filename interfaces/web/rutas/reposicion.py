"""Reposición de stock (solo OWNER): productos que llegaron al mínimo, agrupados por proveedor
principal, con cantidad sugerida editable. Desde un grupo se puede precargar el formulario de
compra o imprimir la lista; ninguna de las dos cosas guarda nada: la compra solo se registra al
confirmarla con `POST /compras/nueva` (`servicio_compras.registrar_compra`)."""

from datetime import date

from fastapi import APIRouter, Depends, Request

from excepciones import DatosInvalidosError
from interfaces.web.auth import requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.rutas.compras import respuesta_formulario_compra
from interfaces.web.utilidades import contexto_base
from services import servicio_stock

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER"))])


@router.get("/reposicion")
def ver_reposicion(request: Request):
    grupos = servicio_stock.agrupar_reposicion_por_proveedor()
    sugerencias = [sugerencia for grupo in grupos for sugerencia in grupo.sugerencias]
    contexto = {
        **contexto_base(request),
        "grupos": grupos,
        "sugerencias": sugerencias,
        "sin_stock": sum(1 for s in sugerencias if s.stock_actual == 0),
        "costo_total_centavos": sum(s.costo_estimado_centavos for s in sugerencias),
    }
    return templates.TemplateResponse(request, "reposicion.html", contexto)


def _entero(texto: str, mensaje: str) -> int:
    try:
        return int(texto.strip())
    except ValueError:
        raise DatosInvalidosError(mensaje) from None


def _seleccion(request: Request) -> tuple[int | None, dict[int, int]]:
    """Interpreta la selección de un grupo (`proveedor_id` opcional, `p=<producto_id>` por cada producto
    tildado y `cantidad_<producto_id>`). Nada de esto es de confianza: lo valida el servicio."""
    parametros = request.query_params
    texto_proveedor = parametros.get("proveedor_id", "").strip()
    proveedor_id = _entero(texto_proveedor, "El proveedor elegido no es válido.") if texto_proveedor else None
    cantidades = {}
    for texto_producto in parametros.getlist("p"):
        producto_id = _entero(texto_producto, "Un producto elegido no es válido.")
        cantidades[producto_id] = _entero(
            parametros.get(f"cantidad_{producto_id}", ""), "La cantidad a comprar debe ser un entero mayor a cero."
        )
    return proveedor_id, cantidades


@router.get("/reposicion/comprar")
def precargar_compra(request: Request):
    return respuesta_formulario_compra(request, servicio_stock.preparar_precarga_compra(*_seleccion(request)))


@router.get("/reposicion/imprimir")
def imprimir_reposicion(request: Request):
    contexto = {"lista": servicio_stock.preparar_lista_reposicion(*_seleccion(request)), "fecha": date.today().strftime("%d/%m/%Y")}
    return templates.TemplateResponse(request, "reposicion_imprimir.html", contexto)
