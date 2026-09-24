"""Actualización masiva de precios (V1.2, solo OWNER).

Flujo en dos pasos, sin escribir nada hasta confirmar: `/precios` (elegir
criterio y alcance) -> vista previa con el precio actual y el nuevo de cada
producto -> `/precios/aplicar` (confirmación, todo o nada). El precio nuevo
lo recalcula siempre el servidor.
"""

from fastapi import APIRouter, Depends, Form, Request

from domain.precios_masivos import ALCANCE_TODOS, ESTADO_OK, CriterioActualizacion, crear_criterio
from domain.usuario import Usuario
from excepciones import DatosInvalidosError
from interfaces.web.auth import obtener_usuario_actual, requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base, nueva_clave_idempotencia, redireccionar_con_mensaje
from services import servicio_categorias, servicio_precios

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER"))])


@router.get("/precios")
def ver_precios(request: Request):
    contexto = {
        **contexto_base(request),
        "categorias": servicio_categorias.listar_activas(),
        "lotes": servicio_precios.listar_lotes_recientes(),
    }
    return templates.TemplateResponse(request, "precios.html", contexto)


@router.get("/precios/vista-previa")
def vista_previa(
    request: Request,
    tipo: str = "PORCENTAJE",
    direccion: str = "AUMENTAR",
    valor: str = "",
    redondeo: str = "NINGUNO",
    alcance: str = ALCANCE_TODOS,
):
    criterio = crear_criterio(tipo, direccion, valor, redondeo)
    propuestas = servicio_precios.proponer_actualizacion(criterio, alcance)
    contexto = {
        **contexto_base(request),
        "criterio": criterio,
        "alcance": alcance,
        "propuestas": propuestas,
        "aplicables": sum(1 for p in propuestas if p.estado == ESTADO_OK),
        "clave_idempotencia": nueva_clave_idempotencia(),
    }
    return templates.TemplateResponse(request, "precios_vista_previa.html", contexto)


@router.post("/precios/aplicar")
def aplicar(
    tipo: str = Form(...),
    direccion: str = Form(...),
    valor: str = Form(...),
    redondeo: str = Form("NINGUNO"),
    alcance: str = Form(...),
    seleccion: list[str] = Form(default=[]),
    clave_idempotencia: str = Form(""),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    # Todo se interpreta acá como texto y se valida en dominio/servicio: un valor
    # manipulado (no numérico, gigante, HTML) es un error controlado, nunca un 422/500.
    try:
        valor_entero = int(valor)
    except ValueError:
        raise DatosInvalidosError("El valor de la actualización no es válido.") from None
    precios_esperados: dict[int, int] = {}
    for item in seleccion:
        try:
            producto_id, precio = item.split(":")
            precios_esperados[int(producto_id)] = int(precio)
        except ValueError:
            raise DatosInvalidosError("La selección de productos no es válida.") from None

    lote = servicio_precios.aplicar_actualizacion(
        usuario_id=usuario_actual.id,
        criterio=CriterioActualizacion(tipo=tipo, direccion=direccion, valor=valor_entero, redondeo=redondeo),
        alcance=alcance,
        precios_esperados=precios_esperados,
        clave_idempotencia=clave_idempotencia or None,
    )
    return redireccionar_con_mensaje(
        "/precios", "success", f"Precios actualizados: {lote.cantidad_productos} producto(s) (lote #{lote.id})."
    )
