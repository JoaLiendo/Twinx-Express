"""Rutas de gestión de proveedores (Fase 4A): listado con búsqueda, alta, edición,
baja lógica/física, reactivación y, desde V1.4, ficha con historial y productos vinculados
(alta y baja del vínculo, cambio de principal).

Cliente delgado: solo llama a `services/servicio_proveedores` y
renderiza plantillas o redirects. Cero SQL y cero reglas de negocio
acá. Todo el router es exclusivo de OWNER (ver auditoría de Fase 4:
Cashier no consulta ni gestiona proveedores, mismo criterio que
Precios/Reportes/Empleados), así que a diferencia de `productos.py`
la restricción se declara una sola vez a nivel de router.
"""

from fastapi import APIRouter, Depends, Form, Request

from domain.usuario import Usuario
from excepciones import ProveedorNoEncontradoError
from interfaces.web.auth import obtener_usuario_actual, requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base, redireccionar_con_mensaje
from services import servicio_proveedores, servicio_stock

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER"))])


@router.get("/proveedores")
def listar_proveedores(request: Request, mostrar_inactivos: bool = False, q: str = ""):
    proveedores = servicio_proveedores.buscar_proveedores(q, incluir_inactivos=mostrar_inactivos)
    contexto = {
        **contexto_base(request),
        "proveedores": proveedores,
        "mostrar_inactivos": mostrar_inactivos,
        "q": q,
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
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    proveedor = servicio_proveedores.crear_proveedor(
        nombre=nombre,
        contacto_nombre=contacto_nombre,
        telefono=telefono,
        email=email,
        direccion=direccion,
        notas=notas,
        usuario_id=usuario_actual.id,
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
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    proveedor = servicio_proveedores.actualizar_proveedor(
        proveedor_id=proveedor_id,
        nombre=nombre,
        contacto_nombre=contacto_nombre,
        telefono=telefono,
        email=email,
        direccion=direccion,
        notas=notas,
        usuario_id=usuario_actual.id,
    )
    return redireccionar_con_mensaje(
        "/proveedores", "success", f"Proveedor '{proveedor.nombre}' actualizado correctamente."
    )


@router.post("/proveedores/{proveedor_id}/eliminar")
def eliminar_proveedor(proveedor_id: int, usuario_actual: Usuario = Depends(obtener_usuario_actual)):
    fue_baja_logica = servicio_proveedores.eliminar_proveedor(proveedor_id, usuario_id=usuario_actual.id)
    mensaje = (
        "Proveedor desactivado: tiene compras asociadas, se conservó su historial."
        if fue_baja_logica
        else "Proveedor eliminado correctamente."
    )
    return redireccionar_con_mensaje("/proveedores", "success", mensaje)


@router.post("/proveedores/{proveedor_id}/reactivar")
def reactivar_proveedor(proveedor_id: int, usuario_actual: Usuario = Depends(obtener_usuario_actual)):
    proveedor = servicio_proveedores.reactivar_proveedor(proveedor_id, usuario_id=usuario_actual.id)
    return redireccionar_con_mensaje(
        "/proveedores", "success", f"Proveedor '{proveedor.nombre}' reactivado correctamente."
    )


@router.get("/proveedores/{proveedor_id}")
def ver_proveedor(request: Request, proveedor_id: int):
    try:
        ficha = servicio_proveedores.obtener_ficha(proveedor_id)
    except ProveedorNoEncontradoError:
        return redireccionar_con_mensaje("/proveedores", "error", "El proveedor no existe.")

    ids_vinculados = {item.vinculo.producto_id for item in ficha.productos}
    contexto = {
        **contexto_base(request),
        "ficha": ficha,
        "productos_disponibles": [p for p in servicio_stock.listar_todos() if p.id not in ids_vinculados],
    }
    return templates.TemplateResponse(request, "proveedores/ficha.html", contexto)


@router.post("/proveedores/{proveedor_id}/productos")
def vincular_producto(
    proveedor_id: int,
    producto_id: int = Form(...),
    codigo_proveedor: str = Form(""),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    servicio_proveedores.vincular_producto(
        proveedor_id, producto_id, usuario_actual.id, codigo_proveedor=codigo_proveedor
    )
    return redireccionar_con_mensaje(f"/proveedores/{proveedor_id}", "success", "Producto vinculado al proveedor.")


@router.post("/proveedores/{proveedor_id}/productos/{producto_id}/quitar")
def quitar_vinculo(proveedor_id: int, producto_id: int, usuario_actual: Usuario = Depends(obtener_usuario_actual)):
    servicio_proveedores.quitar_vinculo(proveedor_id, producto_id, usuario_actual.id)
    return redireccionar_con_mensaje(f"/proveedores/{proveedor_id}", "success", "Vínculo eliminado.")


@router.post("/proveedores/{proveedor_id}/productos/{producto_id}/principal")
def establecer_principal(
    proveedor_id: int, producto_id: int, usuario_actual: Usuario = Depends(obtener_usuario_actual)
):
    servicio_proveedores.establecer_principal(proveedor_id, producto_id, usuario_actual.id)
    return redireccionar_con_mensaje(
        f"/proveedores/{proveedor_id}", "success", "Ahora es el proveedor principal de ese producto."
    )
