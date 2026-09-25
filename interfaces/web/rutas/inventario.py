"""Rutas del inventario físico (V1.4).

Cliente delgado: solo llama a `services/servicio_inventario` y renderiza plantillas o redirects.
Cero SQL y cero reglas de negocio acá (ni en el JS). OWNER crea, revisa, confirma y cancela;
OWNER y CASHIER cuentan. El conteo es a ciegas: ninguna respuesta de conteo incluye el stock
esperado ni la diferencia (eso solo lo ve el OWNER en el detalle). Los permisos se validan además
en el servicio.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from domain.usuario import Usuario
from excepciones import DatosInvalidosError, InventarioNoAbiertoError, InventarioNoEncontradoError
from interfaces.web.auth import obtener_usuario_actual, requiere_rol
from interfaces.web.esquemas import ConteoEntrada, ConteoSalida
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base, nueva_clave_idempotencia, redireccionar_con_mensaje
from services import servicio_inventario, servicio_stock

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER", "CASHIER"))])
_SOLO_OWNER = Depends(requiere_rol("OWNER"))


def _inventario_abierto_o_error(usuario: Usuario):
    inventario = servicio_inventario.obtener_inventario_abierto(usuario.id)
    if inventario is None:
        raise InventarioNoAbiertoError("No hay un inventario abierto para contar.")
    return inventario


def _parsear_cantidad(texto: str) -> int:
    try:
        return int(texto.strip())
    except ValueError as error:
        raise DatosInvalidosError("La cantidad contada debe ser un número entero.") from error


@router.get("/inventario")
def inicio_inventario(request: Request, usuario_actual: Usuario = Depends(obtener_usuario_actual)):
    if usuario_actual.rol != "OWNER":
        return RedirectResponse("/inventario/conteo", status_code=303)
    contexto = {
        **contexto_base(request),
        "inventarios": servicio_inventario.listar_inventarios(usuario_actual.id),
        "abierto": servicio_inventario.obtener_inventario_abierto(usuario_actual.id),
    }
    return templates.TemplateResponse(request, "inventario/lista.html", contexto)


@router.get("/inventario/nuevo", dependencies=[_SOLO_OWNER])
def formulario_nuevo_inventario(request: Request, usuario_actual: Usuario = Depends(obtener_usuario_actual)):
    abierto = servicio_inventario.obtener_inventario_abierto(usuario_actual.id)
    if abierto is not None:
        return redireccionar_con_mensaje(
            f"/inventario/{abierto.id}", "warning", "Ya hay un inventario abierto: confirmalo o cancelalo primero."
        )
    contexto = {
        **contexto_base(request),
        "productos": servicio_stock.listar_todos(),
        "productos_inactivos_con_stock": [p for p in servicio_stock.listar_inactivos() if p.stock_actual > 0],
    }
    return templates.TemplateResponse(request, "inventario/nuevo.html", contexto)


@router.post("/inventario/nuevo", dependencies=[_SOLO_OWNER])
def crear_inventario(
    alcance: str = Form("todos"),
    producto_id: list[int] = Form([]),
    observaciones: str = Form(""),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    if alcance not in ("todos", "manual"):
        raise DatosInvalidosError("El alcance del inventario debe ser 'todos' o 'manual'.")
    if alcance == "manual" and not producto_id:
        raise DatosInvalidosError("Elegí al menos un producto para el inventario.")
    inventario = servicio_inventario.crear_inventario(
        usuario_actual.id,
        producto_ids=producto_id if alcance == "manual" else None,
        observaciones=observaciones,
    )
    return redireccionar_con_mensaje(
        f"/inventario/{inventario.id}", "success", f"Inventario #{inventario.id} iniciado. Ya se puede contar."
    )


@router.get("/inventario/conteo")
def pantalla_de_conteo(request: Request, usuario_actual: Usuario = Depends(obtener_usuario_actual)):
    inventario = servicio_inventario.obtener_inventario_abierto(usuario_actual.id)
    lineas = servicio_inventario.listar_lineas_para_conteo(inventario.id, usuario_actual.id) if inventario else []
    contexto = {
        **contexto_base(request),
        "inventario": inventario,
        "lineas": lineas,
        "contadas": sum(1 for linea in lineas if linea.contada),
    }
    return templates.TemplateResponse(request, "inventario/conteo.html", contexto)


@router.post("/inventario/conteo/{producto_id}")
def registrar_conteo(
    producto_id: int,
    cantidad: str = Form(""),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    inventario = _inventario_abierto_o_error(usuario_actual)
    servicio_inventario.registrar_conteo(inventario.id, producto_id, _parsear_cantidad(cantidad), usuario_actual.id)
    return redireccionar_con_mensaje("/inventario/conteo", "success", "Conteo registrado.")


@router.post("/api/inventario/conteo/{producto_id}", response_model=ConteoSalida)
def api_registrar_conteo(
    producto_id: int,
    datos: ConteoEntrada,
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
) -> ConteoSalida:
    inventario = _inventario_abierto_o_error(usuario_actual)
    servicio_inventario.registrar_conteo(inventario.id, producto_id, datos.cantidad, usuario_actual.id)
    return ConteoSalida(producto_id=producto_id, cantidad_contada=datos.cantidad)


@router.get("/inventario/{inventario_id}", dependencies=[_SOLO_OWNER])
def ver_inventario(request: Request, inventario_id: int, usuario_actual: Usuario = Depends(obtener_usuario_actual)):
    try:
        detalle = servicio_inventario.obtener_detalle(inventario_id, usuario_actual.id)
    except InventarioNoEncontradoError:
        return redireccionar_con_mensaje("/inventario", "error", "El inventario no existe.")
    lineas = detalle.lineas
    contexto = {
        **contexto_base(request),
        "detalle": detalle,
        "inventario": detalle.inventario,
        "lineas": lineas,
        "contadas": [linea for linea in lineas if linea.contada],
        "desactualizadas": [linea for linea in lineas if linea.desactualizada],
        "clave_idempotencia": nueva_clave_idempotencia(),
    }
    return templates.TemplateResponse(request, "inventario/detalle.html", contexto)


@router.post("/inventario/{inventario_id}/confirmar", dependencies=[_SOLO_OWNER])
def confirmar_inventario(
    inventario_id: int,
    clave_idempotencia: str = Form(""),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    resultado = servicio_inventario.confirmar_inventario(
        inventario_id, usuario_actual.id, clave_idempotencia=clave_idempotencia or None
    )
    mensaje = (
        f"Inventario #{inventario_id} confirmado: {resultado.ajustes_generados} ajuste(s) de stock, "
        f"{resultado.lineas_sin_diferencia} sin diferencia."
    )
    return redireccionar_con_mensaje(f"/inventario/{inventario_id}", "success", mensaje)


@router.post("/inventario/{inventario_id}/cancelar", dependencies=[_SOLO_OWNER])
def cancelar_inventario(inventario_id: int, usuario_actual: Usuario = Depends(obtener_usuario_actual)):
    servicio_inventario.cancelar_inventario(inventario_id, usuario_actual.id)
    return redireccionar_con_mensaje("/inventario", "success", f"Inventario #{inventario_id} cancelado. El stock no se modificó.")
