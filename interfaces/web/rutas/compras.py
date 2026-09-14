"""Rutas de ingreso de mercadería / compras (Fase 4B): listado, alta y
detalle. Sin edición ni eliminación -- las compras son inmutables en
esta fase (ver `services.servicio_compras`).

Cliente delgado: solo llama a `services/` y renderiza plantillas o
redirects. Cero SQL y cero reglas de negocio acá. Todo el router es
exclusivo de OWNER (mismo criterio que `proveedores.py`: Cashier no
consulta ni registra compras).
"""

from fastapi import APIRouter, Depends, Form, Request

from domain.compra import ItemCompra
from domain.dinero import texto_a_centavos
from domain.usuario import Usuario
from excepciones import DatosInvalidosError
from interfaces.web.auth import obtener_usuario_actual, requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base, redireccionar_con_mensaje
from services import servicio_compras, servicio_proveedores, servicio_stock

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER"))])


@router.get("/compras")
def listar_compras(
    request: Request,
    proveedor_id: int | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
):
    contexto = {
        **contexto_base(request),
        "compras": servicio_compras.listar_resumen(proveedor_id, fecha_desde, fecha_hasta),
        # Incluye proveedores dados de baja: puede haber compras históricas
        # de un proveedor ya inactivo, y el filtro tiene que poder elegirlo.
        "proveedores": servicio_proveedores.listar_todos(),
        "proveedor_id": proveedor_id,
        "fecha_desde": fecha_desde or "",
        "fecha_hasta": fecha_hasta or "",
        "hay_filtro_activo": bool(proveedor_id or fecha_desde or fecha_hasta),
    }
    return templates.TemplateResponse(request, "compras/lista.html", contexto)


@router.get("/compras/nueva")
def formulario_nueva_compra(request: Request):
    contexto = {
        **contexto_base(request),
        "proveedores": servicio_proveedores.listar_activos(),
        "productos": servicio_stock.listar_todos(),
    }
    return templates.TemplateResponse(request, "compras/nueva.html", contexto)


@router.post("/compras/nueva")
def crear_compra(
    proveedor_id: int = Form(...),
    observaciones: str = Form(""),
    producto_id: list[int] = Form(...),
    cantidad: list[int] = Form(...),
    costo_unitario: list[str] = Form(...),
    usuario_actual: Usuario | None = Depends(obtener_usuario_actual),
):
    if not (len(producto_id) == len(cantidad) == len(costo_unitario)):
        raise DatosInvalidosError("Los datos del formulario de compra son inconsistentes.")

    items = [
        ItemCompra(pid, cant, texto_a_centavos(costo))
        for pid, cant, costo in zip(producto_id, cantidad, costo_unitario)
    ]
    compra = servicio_compras.registrar_compra(
        proveedor_id=proveedor_id,
        usuario_id=usuario_actual.id,
        items=items,
        observaciones=observaciones or None,
    )
    return redireccionar_con_mensaje(
        f"/compras/{compra.id}", "success", f"Compra #{compra.id} registrada correctamente."
    )


@router.get("/compras/{compra_id}")
def ver_compra(request: Request, compra_id: int):
    compra = servicio_compras.obtener_resumen_por_id(compra_id)
    if compra is None:
        return redireccionar_con_mensaje("/compras", "error", "La compra no existe.")

    contexto = {
        **contexto_base(request),
        "compra": compra,
        "detalle": servicio_compras.listar_detalle_con_producto(compra_id),
    }
    return templates.TemplateResponse(request, "compras/detalle.html", contexto)
