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

from domain.dinero import texto_a_centavos
from domain.evaluacion_proveedor import CRITERIOS
from domain.proveedor import CONDICIONES_FISCALES
from domain.reclamo_proveedor import ESTADOS_RECLAMO, TIPOS_RECLAMO
from domain.usuario import Usuario
from excepciones import DatosInvalidosError, ProveedorNoEncontradoError, ReclamoNoEncontradoError
from interfaces.web.auth import obtener_usuario_actual, requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import (
    contexto_base,
    entero_opcional,
    nueva_clave_idempotencia,
    redireccionar_con_mensaje,
)
from services import (
    servicio_caja,
    servicio_compras,
    servicio_deuda_proveedores,
    servicio_evaluaciones_proveedor,
    servicio_pagos_proveedor,
    servicio_proveedores,
    servicio_reclamos_proveedor,
    servicio_stock,
)

router = APIRouter(dependencies=[Depends(requiere_rol("OWNER"))])


@router.get("/proveedores")
def listar_proveedores(request: Request, mostrar_inactivos: bool = False, q: str = "", categoria_id: str = ""):
    id_categoria = entero_opcional(categoria_id, "La categoría elegida no es válida.")
    contexto = {
        **contexto_base(request),
        "resumenes": servicio_proveedores.listar_resumen(q, mostrar_inactivos, id_categoria),
        "mostrar_inactivos": mostrar_inactivos,
        "q": q,
        "categoria_id": id_categoria,
        "opciones_categorias": servicio_proveedores.opciones_de_clasificacion(),
    }
    return templates.TemplateResponse(request, "proveedores/lista.html", contexto)


def _contexto_formulario(request: Request, categorias_elegidas: set[int]) -> dict:
    return {
        **contexto_base(request),
        "opciones_categorias": servicio_proveedores.opciones_de_clasificacion(categorias_elegidas),
        "categorias_elegidas": categorias_elegidas,
        "condiciones_fiscales": CONDICIONES_FISCALES,
    }


def _datos_fiscales(
    razon_social: str, cuit: str, condicion_fiscal: str, ingresos_brutos: str, convenio_multilateral: str
) -> servicio_proveedores.DatosFiscales:
    return servicio_proveedores.DatosFiscales(
        razon_social=razon_social,
        cuit=cuit,
        condicion_fiscal=condicion_fiscal,
        ingresos_brutos=ingresos_brutos,
        convenio_multilateral=bool(convenio_multilateral),
    )


@router.get("/proveedores/nuevo")
def formulario_nuevo_proveedor(request: Request):
    return templates.TemplateResponse(request, "proveedores/nuevo.html", _contexto_formulario(request, set()))


@router.post("/proveedores/nuevo")
def crear_proveedor(
    nombre: str = Form(...),
    contacto_nombre: str = Form(""),
    telefono: str = Form(""),
    email: str = Form(""),
    direccion: str = Form(""),
    notas: str = Form(""),
    razon_social: str = Form(""),
    cuit: str = Form(""),
    condicion_fiscal: str = Form(""),
    ingresos_brutos: str = Form(""),
    convenio_multilateral: str = Form(""),
    categoria_id: list[int] = Form([]),
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
        fiscales=_datos_fiscales(razon_social, cuit, condicion_fiscal, ingresos_brutos, convenio_multilateral),
        categoria_ids=categoria_id,
    )
    return redireccionar_con_mensaje(
        "/proveedores", "success", f"Proveedor '{proveedor.nombre}' creado correctamente."
    )


@router.get("/proveedores/{proveedor_id}/editar")
def formulario_editar_proveedor(request: Request, proveedor_id: int):
    proveedor = servicio_proveedores.obtener_por_id(proveedor_id)
    if proveedor is None:
        return redireccionar_con_mensaje("/proveedores", "error", "El proveedor no existe.")

    contexto = {
        **_contexto_formulario(request, servicio_proveedores.ids_categorias(proveedor_id)),
        "proveedor": proveedor,
    }
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
    razon_social: str = Form(""),
    cuit: str = Form(""),
    condicion_fiscal: str = Form(""),
    ingresos_brutos: str = Form(""),
    convenio_multilateral: str = Form(""),
    categoria_id: list[int] = Form([]),
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
        fiscales=_datos_fiscales(razon_social, cuit, condicion_fiscal, ingresos_brutos, convenio_multilateral),
        categoria_ids=categoria_id,
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
        "clave_idempotencia_pago": nueva_clave_idempotencia(),
        "clave_idempotencia_evaluacion": nueva_clave_idempotencia(),
        "criterio_fifo": servicio_deuda_proveedores.CRITERIO_FIFO,
        "criterios_evaluacion": CRITERIOS,
    }
    return templates.TemplateResponse(request, "proveedores/ficha.html", contexto)


@router.get("/proveedores/{proveedor_id}/pagos/nuevo")
def formulario_pago(request: Request, proveedor_id: int):
    try:
        ficha = servicio_proveedores.obtener_ficha(proveedor_id)
    except ProveedorNoEncontradoError:
        return redireccionar_con_mensaje("/proveedores", "error", "El proveedor no existe.")

    contexto = {
        **contexto_base(request),
        "proveedor": ficha.proveedor,
        "saldo": ficha.saldo_centavos,
        "caja_abierta": servicio_caja.consultar_estado(),
        "clave_idempotencia_pago": nueva_clave_idempotencia(),
    }
    return templates.TemplateResponse(request, "proveedores/pago.html", contexto)


@router.post("/proveedores/{proveedor_id}/pagos")
def registrar_pago(
    proveedor_id: int,
    monto: str = Form(...),
    medio_pago: str = Form(...),
    observacion: str = Form(""),
    clave_idempotencia: str = Form(""),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    servicio_pagos_proveedor.registrar_pago(
        proveedor_id=proveedor_id,
        monto_centavos=texto_a_centavos(monto),
        medio_pago=medio_pago,
        usuario_id=usuario_actual.id,
        observacion=observacion or None,
        clave_idempotencia=clave_idempotencia or None,
    )
    return redireccionar_con_mensaje(f"/proveedores/{proveedor_id}", "success", "Pago registrado correctamente.")


@router.post("/proveedores/{proveedor_id}/evaluaciones")
def registrar_evaluacion(
    proveedor_id: int,
    estrellas: str = Form(""),
    comentario: str = Form(""),
    precio: str = Form(""),
    calidad: str = Form(""),
    entrega: str = Form(""),
    cumplimiento: str = Form(""),
    condiciones_pago: str = Form(""),
    atencion: str = Form(""),
    clave_idempotencia: str = Form(""),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    general = entero_opcional(estrellas, "La calificación general debe ser un número de 1 a 5.")
    if general is None:
        raise DatosInvalidosError("Elegí la calificación general (1 a 5 estrellas).")
    textos = {
        "PRECIO": precio, "CALIDAD": calidad, "ENTREGA": entrega, "CUMPLIMIENTO": cumplimiento,
        "CONDICIONES_PAGO": condiciones_pago, "ATENCION": atencion,
    }
    criterios = {
        criterio: entero_opcional(texto, f"El criterio {CRITERIOS[criterio]} debe ser un número de 1 a 5.")
        for criterio, texto in textos.items()
    }
    servicio_evaluaciones_proveedor.registrar_evaluacion(
        proveedor_id, usuario_actual.id, general, comentario, criterios, clave_idempotencia or None
    )
    return redireccionar_con_mensaje(f"/proveedores/{proveedor_id}", "success", "Evaluación registrada.")


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


# --- reclamos e incidentes (V1.13-C) ---------------------------------------------------------------


@router.get("/proveedores/reclamos/nuevo")
def formulario_reclamo(request: Request, proveedor_id: str = "", ajuste_id: str = ""):
    """Alta de un reclamo desde la ficha (`proveedor_id`) o desde un ajuste FALLA_FABRICA (`ajuste_id`).
    Desde un ajuste, el proveedor queda fijo solo si se conoce de verdad; si no, lo elige el OWNER."""
    id_proveedor = entero_opcional(proveedor_id, "El proveedor elegido no es válido.")
    id_ajuste = entero_opcional(ajuste_id, "El ajuste elegido no es válido.")
    reclamable = servicio_reclamos_proveedor.ajuste_reclamable(id_ajuste) if id_ajuste is not None else None
    if reclamable is not None and reclamable.reclamo_existente_id is not None:
        reclamo = servicio_reclamos_proveedor.obtener_por_id(reclamable.reclamo_existente_id)
        return redireccionar_con_mensaje(
            f"/proveedores/{reclamo.proveedor_id}/reclamos/{reclamo.id}", "warning", "Ese ajuste ya tiene este reclamo."
        )
    proveedor_fijo = None
    if reclamable is not None and reclamable.origen is not None:
        id_proveedor = reclamable.origen.proveedor_id
        proveedor_fijo = servicio_proveedores.obtener_incluyendo_inactivos(id_proveedor)
    elif reclamable is None and id_proveedor is not None:
        proveedor_fijo = servicio_proveedores.obtener_incluyendo_inactivos(id_proveedor)
    compras = []
    if proveedor_fijo is not None and (reclamable is None or reclamable.origen.compra_id is None):
        compras = servicio_compras.listar_pagina(proveedor_id=proveedor_fijo.id).compras
    contexto = {
        **contexto_base(request),
        "proveedor_fijo": proveedor_fijo,
        "proveedor_preseleccionado": id_proveedor,
        "proveedores": servicio_proveedores.listar_todos() if proveedor_fijo is None else [],
        "reclamable": reclamable,
        "compras": compras,
        "productos": servicio_stock.listar_todos() + servicio_stock.listar_inactivos() if reclamable is None else [],
        "tipos": TIPOS_RECLAMO,
        "clave_idempotencia": nueva_clave_idempotencia(),
    }
    return templates.TemplateResponse(request, "proveedores/reclamo_nuevo.html", contexto)


@router.post("/proveedores/reclamos/nuevo")
def crear_reclamo(
    proveedor_id: str = Form(""),
    tipo: str = Form(""),
    descripcion: str = Form(""),
    producto_id: str = Form(""),
    compra_id: str = Form(""),
    ajuste_id: str = Form(""),
    cantidad: str = Form(""),
    clave_idempotencia: str = Form(""),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    id_proveedor = entero_opcional(proveedor_id, "El proveedor elegido no es válido.")
    if id_proveedor is None:
        raise DatosInvalidosError("Elegí el proveedor al que se le reclama.")
    reclamo = servicio_reclamos_proveedor.crear_reclamo(
        id_proveedor,
        usuario_actual.id,
        tipo,
        descripcion,
        producto_id=entero_opcional(producto_id, "El producto elegido no es válido."),
        compra_id=entero_opcional(compra_id, "La compra elegida no es válida."),
        ajuste_id=entero_opcional(ajuste_id, "El ajuste elegido no es válido."),
        cantidad=entero_opcional(cantidad, "La cantidad debe ser un número entero."),
        clave_idempotencia=clave_idempotencia or None,
    )
    return redireccionar_con_mensaje(
        f"/proveedores/{reclamo.proveedor_id}/reclamos/{reclamo.id}", "success", f"Reclamo #{reclamo.id} registrado."
    )


@router.get("/proveedores/{proveedor_id}/reclamos/{reclamo_id}")
def ver_reclamo(request: Request, proveedor_id: int, reclamo_id: int):
    try:
        reclamo = servicio_reclamos_proveedor.obtener(reclamo_id, proveedor_id)
    except ReclamoNoEncontradoError:
        return redireccionar_con_mensaje(f"/proveedores/{proveedor_id}", "error", "El reclamo no existe.")
    contexto = {
        **contexto_base(request),
        "reclamo": reclamo,
        "estados": ESTADOS_RECLAMO,
        "clave_idempotencia": nueva_clave_idempotencia(),
    }
    return templates.TemplateResponse(request, "proveedores/reclamo_detalle.html", contexto)


@router.post("/proveedores/{proveedor_id}/reclamos/{reclamo_id}/estado")
def cambiar_estado_reclamo(
    proveedor_id: int,
    reclamo_id: int,
    estado_nuevo: str = Form(""),
    estado_actual: str = Form(""),
    observacion: str = Form(""),
    clave_idempotencia: str = Form(""),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    servicio_reclamos_proveedor.obtener(reclamo_id, proveedor_id)
    servicio_reclamos_proveedor.cambiar_estado(
        reclamo_id, usuario_actual.id, estado_nuevo, observacion, estado_actual, clave_idempotencia or None
    )
    return redireccionar_con_mensaje(
        f"/proveedores/{proveedor_id}/reclamos/{reclamo_id}", "success", "Estado del reclamo actualizado."
    )
