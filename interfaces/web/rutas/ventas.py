"""Ruta del panel de ventas (POS): grilla de productos + carrito dinámico.

El carrito vive en el navegador (`static/js/pos.js`); esta ruta solo
sirve la página y expone la API JSON que confirma la venta a través de
`services.servicio_ventas`. Cliente delgado: cero SQL, cero reglas de
negocio (la validación de stock/tipo de pago ocurre en domain/services,
igual que en la CLI).
"""

from fastapi import APIRouter, Depends, HTTPException, Request

from domain.usuario import Usuario
from domain.venta import TIPOS_PAGO_VALIDOS, ItemVenta
from interfaces.web.auth import obtener_usuario_actual, requiere_rol
from interfaces.web.esquemas import VentaEntrada, VentaSalida
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base, redireccionar_con_mensaje
from services import servicio_stock, servicio_ventas

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER", "CASHIER"))])


@router.get("/ventas")
def panel_ventas(request: Request, q: str = ""):
    productos = servicio_stock.buscar_por_nombre(q) if q else servicio_stock.listar_todos()
    contexto = {
        **contexto_base(request),
        "productos": productos,
        "q": q,
        "tipos_pago": sorted(TIPOS_PAGO_VALIDOS),
    }
    return templates.TemplateResponse(request, "ventas/pos.html", contexto)


@router.post("/api/ventas", response_model=VentaSalida)
def api_registrar_venta(
    datos: VentaEntrada,
    usuario_actual: Usuario | None = Depends(obtener_usuario_actual),
) -> VentaSalida:
    items = [ItemVenta(producto_id=item.producto_id, cantidad=item.cantidad) for item in datos.items]
    venta = servicio_ventas.registrar_venta(
        items, datos.tipo_pago, clave_idempotencia=datos.clave_idempotencia, usuario_id=usuario_actual.id
    )
    return VentaSalida(
        id=venta.id, fecha=venta.fecha, total_centavos=venta.total_centavos, tipo_pago=venta.tipo_pago
    )


@router.get("/ventas/historial")
def historial_ventas(
    request: Request,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    tipo_pago: str | None = None,
):
    fecha_desde_efectiva, fecha_hasta_efectiva, ventas = servicio_ventas.listar_historial(
        fecha_desde, fecha_hasta, tipo_pago
    )
    contexto = {
        **contexto_base(request),
        "ventas": ventas,
        "fecha_desde": fecha_desde_efectiva,
        "fecha_hasta": fecha_hasta_efectiva,
        "tipo_pago": tipo_pago or "",
        "tipos_pago": sorted(TIPOS_PAGO_VALIDOS),
    }
    return templates.TemplateResponse(request, "ventas/historial.html", contexto)


@router.get("/ventas/{venta_id}")
def detalle_venta(request: Request, venta_id: int):
    """`/ventas/historial` (arriba) se declara antes que esta ruta a
    propósito: si estuviera después, una request a `/ventas/historial`
    intentaría bindear `venta_id="historial"` contra este parámetro
    `int` y fallaría, en vez de servir el listado (mismo motivo por el
    que `compras.py` declara `/compras/nueva` antes de
    `/compras/{compra_id}`)."""
    resumen = servicio_ventas.obtener_resumen_por_id(venta_id)
    if resumen is None:
        return redireccionar_con_mensaje("/ventas/historial", "error", "La venta no existe.")

    detalle = servicio_ventas.obtener_venta_con_detalle(venta_id)
    contexto = {**contexto_base(request), "resumen": resumen, "lineas": detalle.lineas}
    return templates.TemplateResponse(request, "ventas/detalle.html", contexto)


@router.get("/ventas/{venta_id}/ticket")
def ticket_venta(request: Request, venta_id: int):
    """Vista de solo lectura del ticket de una venta ya confirmada
    (Fase 5D), pensada para imprimirse con Ctrl+P. Todos los datos
    económicos salen de `servicio_ventas.obtener_venta_con_detalle`
    (que a su vez lee de `ventas`/`detalle_venta`, nunca del carrito
    del cliente): `venta_id` es el único dato que viene del pedido, y
    solo se usa para buscar -- nunca para calcular nada.

    No persiste monto recibido ni vuelto (Fase 5C no los guarda) por
    diseño: el ticket muestra únicamente el total y el medio de pago.
    """
    venta_con_detalle = servicio_ventas.obtener_venta_con_detalle(venta_id)
    if venta_con_detalle is None:
        raise HTTPException(status_code=404, detail="La venta no existe.")
    contexto = {"venta": venta_con_detalle.venta, "lineas": venta_con_detalle.lineas}
    return templates.TemplateResponse(request, "ventas/ticket.html", contexto)
