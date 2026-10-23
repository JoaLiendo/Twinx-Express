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
from domain.dinero import texto_a_centavos
from services import servicio_categorias, servicio_configuracion, servicio_liquidacion, servicio_precios

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER"))])


@router.get("/precios")
def ver_precios(request: Request):
    contexto = {
        **contexto_base(request),
        "categorias": servicio_categorias.obtener_arbol().en_orden(solo_activas=True),
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


@router.get("/precios/liquidacion")
def ver_liquidacion(request: Request):
    """V1.12-E: propuestas para lo próximo a vencer. No cambia nada hasta que el OWNER confirma cada una."""
    contexto = {
        **contexto_base(request),
        "candidatos": servicio_liquidacion.listar_candidatos(),
        "parametros": servicio_configuracion.obtener_parametros_liquidacion(),
        "dias_alerta": servicio_configuracion.obtener_dias_alerta_vencimiento(),
    }
    return templates.TemplateResponse(request, "precios_liquidacion.html", contexto)


@router.post("/precios/liquidacion/parametros")
def guardar_parametros_liquidacion(
    descuento_objetivo: str = Form(""),
    margen_minimo: str = Form(""),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    servicio_configuracion.guardar_parametros_liquidacion(descuento_objetivo, margen_minimo, usuario_actual.id)
    return redireccionar_con_mensaje("/precios/liquidacion", "success", "Parámetros de liquidación guardados.")


@router.post("/precios/liquidacion/{producto_id}/aplicar")
def aplicar_precio_liquidacion(
    producto_id: int,
    precio: str = Form(...),
    precio_anterior_centavos: int = Form(...),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    producto = servicio_liquidacion.aplicar_precio(
        producto_id, texto_a_centavos(precio), precio_anterior_centavos, usuario_actual.id
    )
    return redireccionar_con_mensaje(
        "/precios/liquidacion", "success", f"Nuevo precio de «{producto.nombre}» aplicado."
    )
