"""Rutas de clientes y cuenta corriente (021): lista con saldo, alta, edición, baja lógica y
reactivación, ficha/estado de cuenta, cobro, compradores autorizados de empresas (V1.14-A) y el
buscador JSON del POS.

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
    CONDICIONES_FISCALES_CLIENTE,
    LONGITUD_MAXIMA_CONTACTO,
    LONGITUD_MAXIMA_DESCRIPCION_MOVIMIENTO,
    LONGITUD_MAXIMA_DIRECCION,
    LONGITUD_MAXIMA_DOCUMENTO,
    LONGITUD_MAXIMA_EMAIL,
    LONGITUD_MAXIMA_NOMBRE,
    LONGITUD_MAXIMA_OBSERVACIONES,
    LONGITUD_MAXIMA_RAZON_SOCIAL,
    LONGITUD_MAXIMA_TELEFONO,
    MAXIMO_AUTORIZADOS_ACTIVOS,
    TIPO_PERSONA,
    TIPOS_CLIENTE,
)
from domain.credito import AMBITO_CLIENTE, MODOS
from domain.dinero import texto_a_centavos
from domain.usuario import Usuario
from excepciones import DatosInvalidosError
from interfaces.web.auth import obtener_usuario_actual, requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import (
    contexto_base,
    limite_credito_del_formulario,
    nueva_clave_idempotencia,
    redireccionar_con_mensaje,
    version_del_formulario,
)
from services import servicio_clientes, servicio_credito, servicio_cuenta_corriente

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
    "razon_social": LONGITUD_MAXIMA_RAZON_SOCIAL,
    "contacto": LONGITUD_MAXIMA_CONTACTO,
}


def _contexto_formulario(request: Request) -> dict:
    return {
        **contexto_base(request),
        "limites": _LIMITES_CAMPOS,
        "tipos_cliente": TIPOS_CLIENTE,
        "condiciones_fiscales": CONDICIONES_FISCALES_CLIENTE,
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
    return templates.TemplateResponse(request, "clientes/nuevo.html", _contexto_formulario(request))


@router.post("/clientes/nuevo")
def crear_cliente(
    nombre: str = Form(...),
    telefono: str = Form(""),
    email: str = Form(""),
    direccion: str = Form(""),
    observaciones: str = Form(""),
    tipo_cliente: str = Form(TIPO_PERSONA),
    condicion_fiscal: str = Form(""),
    razon_social: str = Form(""),
    cuit: str = Form(""),
    contacto: str = Form(""),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    cliente = servicio_clientes.crear_cliente(
        nombre,
        usuario_actual.id,
        telefono=telefono,
        email=email,
        direccion=direccion,
        observaciones=observaciones,
        tipo_cliente=tipo_cliente,
        condicion_fiscal=condicion_fiscal or None,
        razon_social=razon_social,
        cuit=cuit,
        contacto=contacto,
    )
    return redireccionar_con_mensaje(f"/clientes/{cliente.id}", "success", f"Cliente '{cliente.nombre}' creado correctamente.")


@router.get("/clientes/{cliente_id}")
def ficha_cliente(request: Request, cliente_id: int):
    """`/clientes/nuevo` se declara antes a propósito (ver `ventas.py`/`compras.py`)."""
    if servicio_clientes.obtener_cliente(cliente_id) is None:
        return _redirigir_cliente_inexistente()
    contexto = {
        **contexto_base(request),
        "estado_cuenta": servicio_clientes.obtener_estado_de_cuenta(cliente_id),
        "autorizados": servicio_clientes.listar_compradores_autorizados(cliente_id),
        "politica_efectiva": servicio_credito.politica_credito_efectiva(cliente_id),
        "politica_individual": servicio_credito.obtener_politica_individual(cliente_id),
        "modos_credito": MODOS,
        "maximo_autorizados": MAXIMO_AUTORIZADOS_ACTIVOS,
        "limites_autorizado": {
            "nombre": LONGITUD_MAXIMA_NOMBRE,
            "documento": LONGITUD_MAXIMA_DOCUMENTO,
            "contacto": LONGITUD_MAXIMA_CONTACTO,
        },
    }
    return templates.TemplateResponse(request, "clientes/detalle.html", contexto)


@router.get("/clientes/{cliente_id}/editar", dependencies=[_SOLO_OWNER])
def formulario_editar_cliente(request: Request, cliente_id: int):
    cliente = servicio_clientes.obtener_cliente(cliente_id)
    if cliente is None:
        return _redirigir_cliente_inexistente()
    contexto = {**_contexto_formulario(request), "cliente": cliente}
    return templates.TemplateResponse(request, "clientes/editar.html", contexto)


@router.post("/clientes/{cliente_id}/editar", dependencies=[_SOLO_OWNER])
def editar_cliente(
    cliente_id: int,
    nombre: str = Form(...),
    telefono: str = Form(""),
    email: str = Form(""),
    direccion: str = Form(""),
    observaciones: str = Form(""),
    tipo_cliente: str = Form(""),
    condicion_fiscal: str = Form(""),
    razon_social: str = Form(""),
    cuit: str = Form(""),
    contacto: str = Form(""),
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
        tipo_cliente=tipo_cliente or None,
        condicion_fiscal=condicion_fiscal or None,
        razon_social=razon_social,
        cuit=cuit,
        contacto=contacto,
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


@router.post("/clientes/{cliente_id}/credito/politica", dependencies=[_SOLO_OWNER])
def guardar_politica_individual(
    cliente_id: int,
    modo: str = Form(""),
    limite: str = Form(""),
    version: str = Form(""),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    servicio_credito.configurar_politica(
        AMBITO_CLIENTE,
        modo,
        limite_credito_del_formulario(modo, limite),
        usuario_actual.id,
        version_esperada=version_del_formulario(version),
        cliente_id=cliente_id,
    )
    return redireccionar_con_mensaje(f"/clientes/{cliente_id}", "success", "Política de crédito del cliente guardada.")


@router.post("/clientes/{cliente_id}/autorizados", dependencies=[_SOLO_OWNER])
def agregar_comprador_autorizado(
    cliente_id: int,
    nombre: str = Form(""),
    documento: str = Form(""),
    contacto: str = Form(""),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    comprador = servicio_clientes.agregar_comprador_autorizado(
        cliente_id, nombre, usuario_actual.id, documento=documento, contacto=contacto
    )
    return redireccionar_con_mensaje(
        f"/clientes/{cliente_id}", "success", f"'{comprador.nombre}' quedó autorizado para comprar."
    )


@router.post("/clientes/{cliente_id}/autorizados/{comprador_id}/baja", dependencies=[_SOLO_OWNER])
def dar_de_baja_comprador_autorizado(
    cliente_id: int, comprador_id: int, usuario_actual: Usuario = Depends(obtener_usuario_actual)
):
    comprador = servicio_clientes.dar_de_baja_comprador_autorizado(cliente_id, comprador_id, usuario_actual.id)
    return redireccionar_con_mensaje(
        f"/clientes/{cliente_id}", "success", f"'{comprador.nombre}' ya no está autorizado."
    )


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
        "obligaciones_pendientes": servicio_cuenta_corriente.listar_obligaciones_pendientes(cliente_id),
    }
    return templates.TemplateResponse(request, "clientes/cobro.html", contexto)


def _imputacion_manual_del_formulario(obligacion_ids: list[str], montos: list[str]) -> dict[int, int] | None:
    """Reparto manual (V1.14-D): pares `obligación -> monto`; los montos vacíos no cuentan. `None` si no se
    indicó ninguno (cobro FIFO). Un id adulterado es un dato inválido, no un error del servidor."""
    reparto: dict[int, int] = {}
    for obligacion_id, monto in zip(obligacion_ids, montos, strict=False):
        if not monto.strip():
            continue
        try:
            clave = int(obligacion_id)
        except ValueError:
            raise DatosInvalidosError("La imputación manual indica una obligación inválida.") from None
        if clave in reparto:
            raise DatosInvalidosError("La imputación manual repite una obligación.")
        reparto[clave] = texto_a_centavos(monto)
    return reparto or None


@router.post("/clientes/{cliente_id}/cobro")
def registrar_cobro(
    cliente_id: int,
    monto: str = Form(""),
    descripcion: str = Form(""),
    clave_idempotencia: str = Form(""),
    imputacion_obligacion_id: list[str] = Form([]),
    imputacion_monto: list[str] = Form([]),
    observacion_imputacion: str = Form(""),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    """`monto` vacío llega igual al servicio y se rechaza con un mensaje claro (no con un 422 crudo).
    Toda la operación (ingreso de caja + cobro + imputaciones + auditoría) la hace `registrar_cobro`;
    quién cobra es siempre el usuario de la sesión. Un reparto manual solo lo acepta el servicio si quien
    cobra es OWNER: un CASHIER que lo arme a mano recibe un permiso denegado sin que se registre nada.
    Un rechazo vuelve a esta pantalla con un toast, vía el manejador global."""
    servicio_cuenta_corriente.registrar_cobro(
        cliente_id,
        texto_a_centavos(monto),
        usuario_actual.id,
        descripcion=descripcion or None,
        clave_idempotencia=clave_idempotencia or None,
        imputacion_manual=_imputacion_manual_del_formulario(imputacion_obligacion_id, imputacion_monto),
        observacion_imputacion=observacion_imputacion or None,
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
