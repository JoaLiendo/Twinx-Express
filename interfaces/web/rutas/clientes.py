"""Rutas de clientes y cuenta corriente (021): lista con saldo, alta, edición, baja lógica y
reactivación, ficha/estado de cuenta, cobro y el buscador JSON del POS.

Cliente delgado: solo llama a `services.servicio_clientes` y `services.servicio_cuenta_corriente`
y renderiza plantillas o redirects. Cero SQL y cero reglas de negocio acá; ninguna ruta atrapa
`ErrorAplicacion` (lo traduce el manejador global de `interfaces.web.app`).

Permisos: todo el router es OWNER + CASHIER (ambos pueden ver clientes, crearlos y cobrar).
Editar, desactivar y reactivar son solo OWNER (`_SOLO_OWNER`, mismo patrón que `ventas.py`); los
servicios además exigen el rol, así que la regla no depende de esta capa.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse

from domain.cliente import (
    LONGITUD_MAXIMA_DESCRIPCION_MOVIMIENTO,
    LONGITUD_MAXIMA_DIRECCION,
    LONGITUD_MAXIMA_EMAIL,
    LONGITUD_MAXIMA_NOMBRE,
    LONGITUD_MAXIMA_OBSERVACIONES,
    LONGITUD_MAXIMA_TELEFONO,
)
from domain.dinero import texto_a_centavos
from domain.usuario import Usuario
from interfaces.web.auth import obtener_usuario_actual, requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base, nueva_clave_idempotencia, redireccionar_con_mensaje
from services import servicio_clientes, servicio_cuenta_corriente

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER", "CASHIER"))])

_SOLO_OWNER = Depends(requiere_rol("OWNER"))

# Solo para el atributo `maxlength` de los inputs (ayuda de UX): el servidor valida siempre.
_LIMITES_CAMPOS = {
    "nombre": LONGITUD_MAXIMA_NOMBRE,
    "telefono": LONGITUD_MAXIMA_TELEFONO,
    "email": LONGITUD_MAXIMA_EMAIL,
    "direccion": LONGITUD_MAXIMA_DIRECCION,
    "observaciones": LONGITUD_MAXIMA_OBSERVACIONES,
    "descripcion_cobro": LONGITUD_MAXIMA_DESCRIPCION_MOVIMIENTO,
}


def _redirigir_cliente_inexistente():
    return redireccionar_con_mensaje("/clientes", "error", "El cliente no existe.")


@router.get("/clientes")
def listar_clientes(
    request: Request,
    q: str = "",
    estado: Literal["activos", "inactivos", "todos"] = "activos",
):
    contexto = {
        **contexto_base(request),
        "clientes": servicio_clientes.listar_clientes_con_saldo(q or None, estado),
        "deuda": servicio_clientes.obtener_deuda_total(),
        "q": q,
        "estado": estado,
    }
    return templates.TemplateResponse(request, "clientes/lista.html", contexto)


@router.get("/clientes/nuevo")
def formulario_nuevo_cliente(request: Request):
    contexto = {**contexto_base(request), "limites": _LIMITES_CAMPOS}
    return templates.TemplateResponse(request, "clientes/nuevo.html", contexto)


@router.post("/clientes/nuevo")
def crear_cliente(
    nombre: str = Form(...),
    telefono: str = Form(""),
    email: str = Form(""),
    direccion: str = Form(""),
    observaciones: str = Form(""),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    cliente = servicio_clientes.crear_cliente(
        nombre,
        usuario_actual.id,
        telefono=telefono,
        email=email,
        direccion=direccion,
        observaciones=observaciones,
    )
    return redireccionar_con_mensaje(f"/clientes/{cliente.id}", "success", f"Cliente '{cliente.nombre}' creado correctamente.")


@router.get("/clientes/{cliente_id}")
def ficha_cliente(request: Request, cliente_id: int):
    """`/clientes/nuevo` se declara antes a propósito (ver `ventas.py`/`compras.py`)."""
    if servicio_clientes.obtener_cliente(cliente_id) is None:
        return _redirigir_cliente_inexistente()
    contexto = {**contexto_base(request), "estado_cuenta": servicio_clientes.obtener_estado_de_cuenta(cliente_id)}
    return templates.TemplateResponse(request, "clientes/detalle.html", contexto)


@router.get("/clientes/{cliente_id}/editar", dependencies=[_SOLO_OWNER])
def formulario_editar_cliente(request: Request, cliente_id: int):
    cliente = servicio_clientes.obtener_cliente(cliente_id)
    if cliente is None:
        return _redirigir_cliente_inexistente()
    contexto = {**contexto_base(request), "cliente": cliente, "limites": _LIMITES_CAMPOS}
    return templates.TemplateResponse(request, "clientes/editar.html", contexto)


@router.post("/clientes/{cliente_id}/editar", dependencies=[_SOLO_OWNER])
def editar_cliente(
    cliente_id: int,
    nombre: str = Form(...),
    telefono: str = Form(""),
    email: str = Form(""),
    direccion: str = Form(""),
    observaciones: str = Form(""),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    cliente = servicio_clientes.editar_cliente(
        cliente_id,
        nombre,
        usuario_actual.id,
        telefono=telefono,
        email=email,
        direccion=direccion,
        observaciones=observaciones,
    )
    return redireccionar_con_mensaje(f"/clientes/{cliente_id}", "success", f"Cliente '{cliente.nombre}' actualizado correctamente.")


@router.post("/clientes/{cliente_id}/desactivar", dependencies=[_SOLO_OWNER])
def desactivar_cliente(cliente_id: int, usuario_actual: Usuario = Depends(obtener_usuario_actual)):
    cliente = servicio_clientes.desactivar_cliente(cliente_id, usuario_actual.id)
    return redireccionar_con_mensaje(f"/clientes/{cliente_id}", "success", f"Cliente '{cliente.nombre}' desactivado.")


@router.post("/clientes/{cliente_id}/reactivar", dependencies=[_SOLO_OWNER])
def reactivar_cliente(cliente_id: int, usuario_actual: Usuario = Depends(obtener_usuario_actual)):
    cliente = servicio_clientes.reactivar_cliente(cliente_id, usuario_actual.id)
    return redireccionar_con_mensaje(f"/clientes/{cliente_id}", "success", f"Cliente '{cliente.nombre}' reactivado.")


@router.get("/clientes/{cliente_id}/cobro")
def formulario_cobro(request: Request, cliente_id: int):
    """Pantalla dedicada de cobro. La clave de idempotencia nace acá (una por formulario mostrado):
    un doble clic o un reenvío del mismo formulario llega con la misma clave y el servicio lo trata
    como un reintento. Sin deuda no hay nada que cobrar: se vuelve a la ficha."""
    if servicio_clientes.obtener_cliente(cliente_id) is None:
        return _redirigir_cliente_inexistente()
    estado_cuenta = servicio_clientes.obtener_estado_de_cuenta(cliente_id)
    if estado_cuenta.resumen.saldo_centavos <= 0:
        return redireccionar_con_mensaje(f"/clientes/{cliente_id}", "warning", "El cliente no tiene deuda pendiente.")
    contexto = {
        **contexto_base(request),
        "estado_cuenta": estado_cuenta,
        "clave_cobro": nueva_clave_idempotencia(),
        "limites": _LIMITES_CAMPOS,
    }
    return templates.TemplateResponse(request, "clientes/cobro.html", contexto)


@router.post("/clientes/{cliente_id}/cobro")
def registrar_cobro(
    cliente_id: int,
    monto: str = Form(""),
    descripcion: str = Form(""),
    clave_idempotencia: str = Form(""),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    """`monto` vacío llega igual al servicio y se rechaza con un mensaje claro (no con un 422 crudo).
    Toda la operación (ingreso de caja + cobro + auditoría) la hace `registrar_cobro`; quién cobra
    es siempre el usuario de la sesión. Un rechazo (monto inválido, mayor al saldo, caja cerrada...)
    vuelve a esta pantalla con un toast, vía el manejador global."""
    servicio_cuenta_corriente.registrar_cobro(
        cliente_id,
        texto_a_centavos(monto),
        usuario_actual.id,
        descripcion=descripcion or None,
        clave_idempotencia=clave_idempotencia or None,
    )
    return redireccionar_con_mensaje(f"/clientes/{cliente_id}", "success", "Cobro registrado correctamente.")


@router.get("/api/clientes/buscar")
def api_buscar_clientes(q: str = "") -> JSONResponse:
    """Buscador del POS: solo clientes ACTIVOS, por nombre o teléfono, máximo 20 (ver
    `servicio_clientes.buscar_clientes_activos_para_venta`)."""
    resultados = servicio_clientes.buscar_clientes_activos_para_venta(q)
    return JSONResponse(
        content=[
            {
                "id": item.cliente.id,
                "nombre": item.cliente.nombre,
                "telefono": item.cliente.telefono,
                "saldo_centavos": item.saldo_centavos,
            }
            for item in resultados
        ]
    )
