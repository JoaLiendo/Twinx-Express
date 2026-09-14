"""Rutas de gestión de proveedores (Fase 4A): listado, alta, edición,
baja lógica/física y reactivación.

Cliente delgado: solo llama a `services/servicio_proveedores` y
renderiza plantillas o redirects. Cero SQL y cero reglas de negocio
acá. Todo el router es exclusivo de OWNER (ver auditoría de Fase 4:
Cashier no consulta ni gestiona proveedores, mismo criterio que
Precios/Reportes/Empleados), así que a diferencia de `productos.py`
la restricción se declara una sola vez a nivel de router.
"""

from fastapi import APIRouter, Depends, Form, Request

from interfaces.web.auth import requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base, redireccionar_con_mensaje
from services import servicio_proveedores

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER"))])


@router.get("/proveedores")
def listar_proveedores(request: Request, mostrar_inactivos: bool = False):
    proveedores = (
        servicio_proveedores.listar_todos()
        if mostrar_inactivos
        else servicio_proveedores.listar_activos()
    )
    contexto = {
        **contexto_base(request),
        "proveedores": proveedores,
        "mostrar_inactivos": mostrar_inactivos,
    }
    return templates.TemplateResponse(request, "proveedores/lista.html", contexto)


@router.get("/proveedores/nuevo")
def formulario_nuevo_proveedor(request: Request):
    return templates.TemplateResponse(request, "proveedores/nuevo.html", contexto_base(request))


@router.post("/proveedores/nuevo")
def crear_proveedor(
    nombre: str = Form(...),
    contacto_nombre: str = Form(""),
    telefono: str = Form(""),
    email: str = Form(""),
    direccion: str = Form(""),
    notas: str = Form(""),
):
    proveedor = servicio_proveedores.crear_proveedor(
        nombre=nombre,
        contacto_nombre=contacto_nombre,
        telefono=telefono,
        email=email,
        direccion=direccion,
        notas=notas,
    )
    return redireccionar_con_mensaje(
        "/proveedores", "success", f"Proveedor '{proveedor.nombre}' creado correctamente."
    )


@router.get("/proveedores/{proveedor_id}/editar")
def formulario_editar_proveedor(request: Request, proveedor_id: int):
    proveedor = servicio_proveedores.obtener_por_id(proveedor_id)
    if proveedor is None:
        return redireccionar_con_mensaje("/proveedores", "error", "El proveedor no existe.")

    contexto = {**contexto_base(request), "proveedor": proveedor}
    return templates.TemplateResponse(request, "proveedores/editar.html", contexto)


@router.post("/proveedores/{proveedor_id}/editar")
def editar_proveedor(
    proveedor_id: int,
    nombre: str = Form(...),
    contacto_nombre: str = Form(""),
    telefono: str = Form(""),
    email: str = Form(""),
    direccion: str = Form(""),
    notas: str = Form(""),
):
    proveedor = servicio_proveedores.actualizar_proveedor(
        proveedor_id=proveedor_id,
        nombre=nombre,
        contacto_nombre=contacto_nombre,
        telefono=telefono,
        email=email,
        direccion=direccion,
        notas=notas,
    )
    return redireccionar_con_mensaje(
        "/proveedores", "success", f"Proveedor '{proveedor.nombre}' actualizado correctamente."
    )


@router.post("/proveedores/{proveedor_id}/eliminar")
def eliminar_proveedor(proveedor_id: int):
    fue_baja_logica = servicio_proveedores.eliminar_proveedor(proveedor_id)
    mensaje = (
        "Proveedor desactivado: tiene compras asociadas, se conservó su historial."
        if fue_baja_logica
        else "Proveedor eliminado correctamente."
    )
    return redireccionar_con_mensaje("/proveedores", "success", mensaje)


@router.post("/proveedores/{proveedor_id}/reactivar")
def reactivar_proveedor(proveedor_id: int):
    proveedor = servicio_proveedores.reactivar_proveedor(proveedor_id)
    return redireccionar_con_mensaje(
        "/proveedores", "success", f"Proveedor '{proveedor.nombre}' reactivado correctamente."
    )
