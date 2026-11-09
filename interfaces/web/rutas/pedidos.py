"""Pedidos de clientes (migración 051): tomar, listar, ver, cancelar y pasar al POS para venderlos.

Ambos roles toman y venden pedidos (es trabajo de mostrador); un pedido no mueve stock ni dinero. La
conversión reutiliza el punto de venta: `GET /pedidos/{id}/vender` abre el POS con el carrito precargado
y `POST /api/ventas` (con `pedido_id`) registra la venta y deja el pedido convertido en una sola transacción.
"""

import json
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request

from domain.pedido import ESTADOS_PEDIDO, ItemPedido
from domain.usuario import Usuario
from excepciones import DatosInvalidosError, PedidoNoEncontradoError, PedidoNoPendienteError
from interfaces.web.auth import obtener_usuario_actual, requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.rutas.ventas import contexto_pos
from interfaces.web.utilidades import (
    contexto_base,
    entero_opcional,
    nueva_clave_idempotencia,
    pagina_o_primera,
    redireccionar_con_mensaje,
)
from services import servicio_clientes, servicio_pedidos, servicio_stock

router = APIRouter(prefix="/pedidos", dependencies=[Depends(requiere_rol("OWNER", "CASHIER"))])


@router.get("")
def listar_pedidos(request: Request, estado: str = "", q: str = "", pagina: str = "1"):
    filtros = servicio_pedidos.filtros_del_listado(estado, q)
    listado = servicio_pedidos.listar_pedidos(filtros, pagina_o_primera(pagina))
    consulta = urlencode({clave: valor for clave, valor in (("estado", estado), ("q", q.strip())) if valor})
    contexto = {
        **contexto_base(request),
        "listado": listado,
        "estados": ESTADOS_PEDIDO,
        "estado": estado,
        "q": q.strip(),
        "hay_filtro_activo": bool(consulta),
        "consulta_filtros": consulta,
    }
    return templates.TemplateResponse(request, "pedidos/lista.html", contexto)


@router.get("/nuevo")
def formulario_nuevo_pedido(request: Request, cliente_id: str = ""):
    from datetime import date

    contexto = {
        **contexto_base(request),
        "productos": servicio_stock.listar_todos(),
        "clientes": servicio_clientes.listar_clientes(solo_activos=True),
        "cliente_id": entero_opcional(cliente_id, "El cliente elegido no es válido."),
        "clave_idempotencia": nueva_clave_idempotencia(),
        "hoy": date.today().isoformat(),
    }
    return templates.TemplateResponse(request, "pedidos/nuevo.html", contexto)


@router.post("/nuevo")
def crear_pedido(
    cliente_id: str = Form(""),
    contacto_nombre: str = Form(""),
    contacto_telefono: str = Form(""),
    notas: str = Form(""),
    fecha_entrega: str = Form(""),
    producto_id: list[int] = Form([]),
    cantidad: list[int] = Form([]),
    clave_idempotencia: str = Form(""),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    if len(producto_id) != len(cantidad):
        raise DatosInvalidosError("Los datos del formulario de pedido son inconsistentes.")
    pedido = servicio_pedidos.registrar_pedido(
        usuario_id=usuario_actual.id,
        items=[ItemPedido(pid, cant) for pid, cant in zip(producto_id, cantidad)],
        cliente_id=entero_opcional(cliente_id, "El cliente elegido no es válido."),
        contacto_nombre=contacto_nombre,
        contacto_telefono=contacto_telefono,
        notas=notas,
        fecha_entrega=fecha_entrega,
        clave_idempotencia=clave_idempotencia or None,
    )
    return redireccionar_con_mensaje(f"/pedidos/{pedido.id}", "success", f"Pedido #{pedido.id} tomado. El stock se valida al venderlo.")


def _pedido_o_redireccion(pedido_id: int):
    try:
        return servicio_pedidos.obtener_pedido(pedido_id), None
    except PedidoNoEncontradoError:
        return None, redireccionar_con_mensaje("/pedidos", "error", "El pedido no existe.")


@router.get("/{pedido_id}")
def ver_pedido(request: Request, pedido_id: int):
    pedido, redireccion = _pedido_o_redireccion(pedido_id)
    if redireccion:
        return redireccion
    lineas = servicio_pedidos.listar_lineas(pedido_id)
    contexto = {
        **contexto_base(request),
        "pedido": pedido,
        "lineas": lineas,
        "total_vigente_centavos": sum(l.subtotal_vigente_centavos for l in lineas),
        "hay_avisos": any(l.precio_cambio or l.sin_stock_suficiente or not l.producto_activo for l in lineas),
    }
    return templates.TemplateResponse(request, "pedidos/detalle.html", contexto)


@router.get("/{pedido_id}/cancelar")
def formulario_cancelar_pedido(request: Request, pedido_id: int):
    pedido, redireccion = _pedido_o_redireccion(pedido_id)
    if redireccion:
        return redireccion
    if not pedido.pendiente:
        return redireccionar_con_mensaje(f"/pedidos/{pedido_id}", "error", "El pedido ya no está pendiente.")
    return templates.TemplateResponse(request, "pedidos/cancelar.html", {**contexto_base(request), "pedido": pedido})


@router.post("/{pedido_id}/cancelar")
def accion_cancelar_pedido(pedido_id: int, motivo: str = Form(""), usuario_actual: Usuario = Depends(obtener_usuario_actual)):
    servicio_pedidos.cancelar_pedido(pedido_id, usuario_id=usuario_actual.id, motivo=motivo)
    return redireccionar_con_mensaje(f"/pedidos/{pedido_id}", "success", "Pedido cancelado.")


@router.get("/{pedido_id}/vender")
def vender_pedido(request: Request, pedido_id: int):
    """Abre el POS con el carrito precargado con el pedido. No registra nada: la venta se concreta con
    "Cobrar", que vuelve a validar stock y precios y deja el pedido convertido."""
    try:
        precarga = servicio_pedidos.preparar_precarga_pos(pedido_id)
    except PedidoNoEncontradoError:
        return redireccionar_con_mensaje("/pedidos", "error", "El pedido no existe.")
    except PedidoNoPendienteError as error:
        return redireccionar_con_mensaje(f"/pedidos/{pedido_id}", "error", str(error))
    cliente = None
    if precarga.pedido.cliente_id is not None:
        with_saldo = servicio_clientes.listar_clientes_con_saldo(estado="todos")
        cliente = next(
            (
                {"id": c.cliente.id, "nombre": c.cliente.nombre, "telefono": c.cliente.telefono, "saldo_centavos": c.saldo_centavos}
                for c in with_saldo
                if c.cliente.id == precarga.pedido.cliente_id and c.cliente.activo
            ),
            None,
        )
    contexto = contexto_pos(request)
    contexto["pedido_precarga"] = {
        "pedido_id": precarga.pedido.id,
        "items": precarga.items_vendibles,
        "cliente": cliente,
        "avisos": precarga.avisos,
    }
    contexto["pedido_precarga_json"] = json.dumps(contexto["pedido_precarga"], ensure_ascii=False)
    contexto["pedido"] = precarga.pedido
    return templates.TemplateResponse(request, "ventas/pos.html", contexto)
