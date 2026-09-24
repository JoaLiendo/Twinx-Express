"""Rutas de gestión de usuarios/empleados (Etapa B.2 del MVP): listado,
alta, edición de datos, cambio de rol, activar/desactivar y reset de
contraseña. Además, el autocambio de la propia contraseña, disponible
para cualquier usuario autenticado (OWNER o CASHIER).

Cliente delgado: solo llama a `services.servicio_usuarios` y renderiza
plantillas o redirects. Cero SQL y cero reglas de negocio acá -- todas
las reglas (inmutabilidad de `nombre_usuario`, baja únicamente lógica,
y "nunca 0 OWNER activos") viven en `services/servicio_usuarios.py` y
se exponen acá como excepciones de `excepciones.py`, que el manejador
global de `interfaces.web.app` ya traduce a un toast de error sobre la
página de origen (mismo mecanismo que usan `productos.py`/`proveedores.py`:
ninguna ruta de esta app atrapa `ErrorAplicacion` a mano).

Dos routers separados a propósito:
- `router`: administración de usuarios, exclusiva de OWNER.
- `router_cuenta`: autocambio de la propia contraseña, para cualquier
  usuario autenticado -- no puede vivir bajo `router` porque ese tiene
  `requiere_rol("OWNER")` a nivel de router entero.
"""

from fastapi import APIRouter, Depends, Form, Request

from interfaces.web.auth import obtener_usuario_actual, requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base, redireccionar_con_mensaje
from services import servicio_usuarios

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER"))])
router_cuenta = APIRouter(dependencies=[Depends(requiere_rol("OWNER", "CASHIER"))])


@router.get("/empleados")
def listar_empleados(request: Request):
    contexto = {**contexto_base(request), "usuarios": servicio_usuarios.listar_todos()}
    return templates.TemplateResponse(request, "empleados/lista.html", contexto)


@router.get("/empleados/nuevo")
def formulario_nuevo_empleado(request: Request):
    return templates.TemplateResponse(request, "empleados/nuevo.html", contexto_base(request))


@router.post("/empleados/nuevo")
def crear_empleado(
    nombre_usuario: str = Form(...),
    nombre_completo: str = Form(...),
    password: str = Form(...),
    rol: str = Form(...),
    usuario_actual=Depends(obtener_usuario_actual),
):
    usuario = servicio_usuarios.crear_usuario(
        nombre_usuario=nombre_usuario,
        nombre_completo=nombre_completo,
        password=password,
        rol=rol,
        actor_id=usuario_actual.id,
    )
    return redireccionar_con_mensaje(
        "/empleados", "success", f"Usuario '{usuario.nombre_usuario}' creado correctamente."
    )


@router.get("/empleados/{usuario_id}/editar")
def formulario_editar_empleado(request: Request, usuario_id: int):
    usuario = servicio_usuarios.obtener_por_id(usuario_id)
    if usuario is None:
        return redireccionar_con_mensaje("/empleados", "error", "El usuario no existe.")

    contexto = {**contexto_base(request), "usuario": usuario}
    return templates.TemplateResponse(request, "empleados/editar.html", contexto)


@router.post("/empleados/{usuario_id}/editar")
def editar_empleado(usuario_id: int, nombre_completo: str = Form(...)):
    usuario = servicio_usuarios.editar_datos(usuario_id, nombre_completo)
    return redireccionar_con_mensaje(
        "/empleados", "success", f"Datos de '{usuario.nombre_usuario}' actualizados correctamente."
    )


@router.post("/empleados/{usuario_id}/rol")
def cambiar_rol_empleado(usuario_id: int, rol: str = Form(...), usuario_actual=Depends(obtener_usuario_actual)):
    usuario = servicio_usuarios.cambiar_rol(usuario_id, rol, actor_id=usuario_actual.id)
    return redireccionar_con_mensaje(
        "/empleados", "success", f"Rol de '{usuario.nombre_usuario}' actualizado a {usuario.rol}."
    )


@router.post("/empleados/{usuario_id}/activar")
def activar_empleado(usuario_id: int, usuario_actual=Depends(obtener_usuario_actual)):
    usuario = servicio_usuarios.activar(usuario_id, actor_id=usuario_actual.id)
    return redireccionar_con_mensaje(
        "/empleados", "success", f"Usuario '{usuario.nombre_usuario}' activado correctamente."
    )


@router.post("/empleados/{usuario_id}/desactivar")
def desactivar_empleado(usuario_id: int, usuario_actual=Depends(obtener_usuario_actual)):
    usuario = servicio_usuarios.desactivar(usuario_id, actor_id=usuario_actual.id)
    return redireccionar_con_mensaje(
        "/empleados", "success", f"Usuario '{usuario.nombre_usuario}' desactivado correctamente."
    )


@router.post("/empleados/{usuario_id}/resetear-password")
def resetear_password_empleado(
    usuario_id: int, password_nueva: str = Form(...), usuario_actual=Depends(obtener_usuario_actual)
):
    usuario = servicio_usuarios.resetear_password(usuario_id, password_nueva, actor_id=usuario_actual.id)
    return redireccionar_con_mensaje(
        "/empleados", "success", f"Contraseña de '{usuario.nombre_usuario}' actualizada correctamente."
    )


@router_cuenta.get("/mi-cuenta/password")
def formulario_cambiar_password_propia(request: Request):
    return templates.TemplateResponse(request, "mi_cuenta_password.html", contexto_base(request))


@router_cuenta.post("/mi-cuenta/password")
def cambiar_password_propia(
    request: Request,
    password_actual: str = Form(...),
    password_nueva: str = Form(...),
    usuario_actual=Depends(obtener_usuario_actual),
):
    servicio_usuarios.cambiar_password_propia(usuario_actual.id, password_actual, password_nueva)
    return redireccionar_con_mensaje(
        "/mi-cuenta/password", "success", "Tu contraseña se actualizó correctamente."
    )
