"""Rutas de gestión de productos: listado/búsqueda, alta, y una consulta
JSON por código de barras que usa el POS (ver `templates/ventas/pos.html`
y `static/js/pos.js`) para agregar al carrito con el lector USB.

Cliente delgado: solo llama a `services/servicio_stock` y renderiza
plantillas o JSON. Cero SQL y cero reglas de negocio acá.
"""

from datetime import date

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from domain.ajuste_stock import MOTIVOS_AJUSTE_VALIDOS
from domain.dinero import texto_a_centavos
from domain.usuario import Usuario
from excepciones import ArchivoImagenInvalidoError, DatosInvalidosError
from interfaces.web.auth import obtener_usuario_actual, requiere_rol
from interfaces.web.plantillas import templates
from interfaces.web.utilidades import contexto_base, nueva_clave_idempotencia, redireccionar_con_mensaje
from services import servicio_categorias, servicio_exportacion, servicio_importacion, servicio_stock

router = APIRouter()

# Stock es de consulta para CASHIER (ver matriz de roles); alta, edición,
# eliminación e importación quedan exclusivas de OWNER. Por eso la
# restricción se declara por ruta y no a nivel de router, a diferencia de
# dashboard/ventas/caja (donde todo el router comparte el mismo acceso).
_CONSULTA = Depends(requiere_rol("OWNER", "CASHIER"))
_SOLO_OWNER = Depends(requiere_rol("OWNER"))


@router.get("/productos", dependencies=[_CONSULTA])
def listar_productos(
    request: Request,
    q: str = "",
    solo_criticos: bool = False,
    mostrar_inactivos: bool = False,
    categoria_id: int | None = None,
    usuario_actual: Usuario | None = Depends(obtener_usuario_actual),
):
    # Ver productos dados de baja es un caso de uso exclusivo de OWNER
    # (reactivarlos ya lo es, vía _SOLO_OWNER); si un CASHIER fuerza el
    # parámetro a mano, el flag simplemente se ignora y ve el listado
    # normal, en vez de devolver un error sobre una ruta que sí tiene permitida.
    mostrar_inactivos = mostrar_inactivos and usuario_actual is not None and usuario_actual.rol == "OWNER"

    if mostrar_inactivos:
        productos = servicio_stock.listar_inactivos()
        total_criticos = len(servicio_stock.listar_stock_critico())
    elif solo_criticos:
        productos = servicio_stock.listar_stock_critico()
        total_criticos = len(productos)
    elif q:
        productos = servicio_stock.buscar_por_nombre(q)
        total_criticos = len(servicio_stock.listar_stock_critico())
    else:
        productos = servicio_stock.listar_todos()
        total_criticos = len(servicio_stock.listar_stock_critico())

    # Filtro adicional por categoría: se combina con cualquiera de los
    # listados de arriba (búsqueda, stock crítico, inactivos). El catálogo
    # de un kiosco es chico, así que filtrar en Python alcanza sin sumar
    # una consulta SQL por cada combinación posible de filtros.
    if categoria_id:
        productos = [producto for producto in productos if producto.categoria_id == categoria_id]

    # Para mostrar el nombre de categoría en cada tarjeta hace falta el
    # historial completo (incluye categorías ya dadas de baja, para no
    # mostrar "Categoría eliminada" en productos viejos); para el <select>
    # del filtro solo tiene sentido ofrecer las categorías activas.
    categorias_todas = servicio_categorias.listar_todas()

    contexto = {
        **contexto_base(request),
        "productos": productos,
        "q": q,
        "solo_criticos": solo_criticos,
        "mostrar_inactivos": mostrar_inactivos,
        "categoria_id": categoria_id,
        "categorias": [categoria for categoria in categorias_todas if categoria.activa],
        "categorias_por_id": {categoria.id: categoria.nombre for categoria in categorias_todas},
        "total_criticos": total_criticos,
    }
    return templates.TemplateResponse(request, "productos/lista.html", contexto)


@router.get("/productos/categorias", dependencies=[_SOLO_OWNER])
def listar_categorias(request: Request):
    contexto = {**contexto_base(request), "categorias": servicio_categorias.listar_todas()}
    return templates.TemplateResponse(request, "productos/categorias.html", contexto)


@router.post("/productos/categorias", dependencies=[_SOLO_OWNER])
def crear_categoria(nombre: str = Form(...)):
    categoria = servicio_categorias.crear_categoria(nombre)
    return redireccionar_con_mensaje(
        "/productos/categorias", "success", f"Categoría '{categoria.nombre}' creada correctamente."
    )


@router.post("/productos/categorias/{categoria_id}/eliminar", dependencies=[_SOLO_OWNER])
def eliminar_categoria(categoria_id: int):
    fue_baja_logica = servicio_categorias.eliminar_categoria(categoria_id)
    mensaje = (
        "Categoría desactivada: hay productos asociados, se conservó la relación."
        if fue_baja_logica
        else "Categoría eliminada correctamente."
    )
    return redireccionar_con_mensaje("/productos/categorias", "success", mensaje)


@router.get("/productos/categorias/{categoria_id}/editar", dependencies=[_SOLO_OWNER])
def formulario_editar_categoria(request: Request, categoria_id: int):
    categoria = servicio_categorias.obtener_por_id(categoria_id)
    if categoria is None:
        return redireccionar_con_mensaje("/productos/categorias", "error", "La categoría no existe.")

    contexto = {**contexto_base(request), "categoria": categoria}
    return templates.TemplateResponse(request, "productos/categoria_editar.html", contexto)


@router.post("/productos/categorias/{categoria_id}/editar", dependencies=[_SOLO_OWNER])
def editar_categoria(categoria_id: int, nombre: str = Form(...)):
    categoria = servicio_categorias.actualizar_categoria(categoria_id, nombre)
    return redireccionar_con_mensaje(
        "/productos/categorias", "success", f"Categoría '{categoria.nombre}' actualizada correctamente."
    )


@router.post("/productos/categorias/{categoria_id}/reactivar", dependencies=[_SOLO_OWNER])
def reactivar_categoria(categoria_id: int):
    categoria = servicio_categorias.reactivar_categoria(categoria_id)
    return redireccionar_con_mensaje(
        "/productos/categorias", "success", f"Categoría '{categoria.nombre}' reactivada correctamente."
    )


@router.get("/productos/nuevo", dependencies=[_SOLO_OWNER])
def formulario_nuevo_producto(request: Request):
    contexto = {**contexto_base(request), "categorias": servicio_categorias.listar_activas()}
    return templates.TemplateResponse(request, "productos/nuevo.html", contexto)


@router.post("/productos/nuevo", dependencies=[_SOLO_OWNER])
async def crear_producto(
    codigo_barras: str = Form(...),
    nombre: str = Form(...),
    precio_costo: str = Form(...),
    precio_venta: str = Form(...),
    stock_actual: int = Form(0),
    stock_minimo: int = Form(0),
    categoria_id: str = Form(""),
    unidad_medida: str = Form("UNIDAD"),
    imagen: UploadFile | None = File(None),
):
    producto = servicio_stock.registrar_producto(
        codigo_barras=codigo_barras,
        nombre=nombre,
        precio_costo_centavos=texto_a_centavos(precio_costo),
        precio_venta_centavos=texto_a_centavos(precio_venta),
        stock_actual=stock_actual,
        stock_minimo=stock_minimo,
        categoria_id=int(categoria_id) if categoria_id else None,
        unidad_medida=unidad_medida,
    )

    # El producto ya se creó y tiene id antes de tocar la imagen (ver
    # auditoría de Fase 3D): si la imagen falla, el producto igual queda
    # creado -- se informa con un mensaje distinto para no dar a entender
    # que la creación completa falló.
    if imagen is not None and imagen.filename:
        contenido = await imagen.read()
        try:
            servicio_stock.asignar_imagen(producto.id, contenido, imagen.filename)
        except ArchivoImagenInvalidoError as error:
            return redireccionar_con_mensaje(
                "/productos", "warning", f"Producto '{producto.nombre}' creado, pero la imagen no se guardó: {error}"
            )

    return redireccionar_con_mensaje(
        "/productos", "success", f"Producto '{producto.nombre}' creado correctamente."
    )


@router.get("/productos/importar", dependencies=[_SOLO_OWNER])
def formulario_importar_productos(request: Request):
    return templates.TemplateResponse(request, "productos/importar.html", contexto_base(request))


@router.post("/productos/importar", dependencies=[_SOLO_OWNER])
async def importar_productos(archivo: UploadFile = File(...)):
    contenido = await archivo.read()
    resultado = servicio_importacion.procesar_archivo(archivo.filename or "", contenido)

    detalle_errores = ""
    if resultado.errores:
        primeros = "; ".join(resultado.errores[:5])
        sufijo = " (y más...)" if len(resultado.errores) > 5 else ""
        detalle_errores = f" Errores: {primeros}{sufijo}"

    mensaje = (
        f"Importación completa: {resultado.importados} producto(s) importado(s), "
        f"{resultado.duplicados} duplicado(s) omitido(s), {len(resultado.errores)} con error(es)."
        f"{detalle_errores}"
    )
    if resultado.errores:
        tipo = "warning" if resultado.importados > 0 else "error"
    else:
        tipo = "success"
    return redireccionar_con_mensaje("/productos", tipo, mensaje)


@router.get("/productos/exportar/csv", dependencies=[_SOLO_OWNER])
def exportar_productos_csv():
    contenido = servicio_exportacion.generar_csv_productos()
    return Response(
        content=contenido,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="productos_{date.today().isoformat()}.csv"'},
    )


@router.get("/productos/exportar/xlsx", dependencies=[_SOLO_OWNER])
def exportar_productos_xlsx():
    contenido = servicio_exportacion.generar_xlsx_productos()
    return Response(
        content=contenido,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="productos_{date.today().isoformat()}.xlsx"'},
    )


@router.get("/productos/{producto_id}/editar", dependencies=[_SOLO_OWNER])
def formulario_editar_producto(request: Request, producto_id: int):
    producto = servicio_stock.obtener_por_id(producto_id)
    if producto is None:
        return redireccionar_con_mensaje("/productos", "error", "El producto no existe.")

    categorias = servicio_categorias.listar_activas()
    # Si el producto ya tiene asignada una categoría que después se dio de
    # baja, se agrega igual a las opciones -- si no, el <select> caería en
    # "Sin categoría" y guardar el formulario sin tocarlo se la borraría
    # sin que nadie lo haya pedido.
    if producto.categoria_id is not None and not any(c.id == producto.categoria_id for c in categorias):
        categoria_actual = servicio_categorias.obtener_por_id(producto.categoria_id)
        if categoria_actual is not None:
            categorias = categorias + [categoria_actual]

    contexto = {**contexto_base(request), "producto": producto, "categorias": categorias}
    return templates.TemplateResponse(request, "productos/editar.html", contexto)


@router.post("/productos/{producto_id}/editar", dependencies=[_SOLO_OWNER])
async def editar_producto(
    producto_id: int,
    codigo_barras: str = Form(...),
    nombre: str = Form(...),
    precio_costo: str = Form(...),
    precio_venta: str = Form(...),
    stock_minimo: int = Form(0),
    categoria_id: str = Form(""),
    unidad_medida: str = Form("UNIDAD"),
    quitar_imagen: bool = Form(False),
    imagen: UploadFile | None = File(None),
):
    producto = servicio_stock.actualizar_producto(
        producto_id=producto_id,
        codigo_barras=codigo_barras,
        nombre=nombre,
        precio_costo_centavos=texto_a_centavos(precio_costo),
        precio_venta_centavos=texto_a_centavos(precio_venta),
        stock_minimo=stock_minimo,
        categoria_id=int(categoria_id) if categoria_id else None,
        unidad_medida=unidad_medida,
    )

    # Una imagen nueva tiene prioridad sobre el checkbox de "quitar" (si
    # subieron una, evidentemente quieren que el producto tenga imagen).
    if imagen is not None and imagen.filename:
        contenido = await imagen.read()
        try:
            servicio_stock.asignar_imagen(producto_id, contenido, imagen.filename)
        except ArchivoImagenInvalidoError as error:
            return redireccionar_con_mensaje(
                "/productos",
                "warning",
                f"Producto '{producto.nombre}' actualizado, pero la imagen no se guardó: {error}",
            )
    elif quitar_imagen:
        servicio_stock.quitar_imagen(producto_id)

    return redireccionar_con_mensaje(
        "/productos", "success", f"Producto '{producto.nombre}' actualizado correctamente."
    )


@router.post("/productos/{producto_id}/eliminar", dependencies=[_SOLO_OWNER])
def eliminar_producto(producto_id: int):
    fue_baja_logica = servicio_stock.eliminar_producto(producto_id)
    mensaje = (
        "Producto desactivado: tiene ventas asociadas, se conservó su historial."
        if fue_baja_logica
        else "Producto eliminado correctamente."
    )
    return redireccionar_con_mensaje("/productos", "success", mensaje)


@router.post("/productos/{producto_id}/reactivar", dependencies=[_SOLO_OWNER])
def reactivar_producto(producto_id: int):
    producto = servicio_stock.reactivar_producto(producto_id)
    return redireccionar_con_mensaje(
        "/productos", "success", f"Producto '{producto.nombre}' reactivado correctamente."
    )


@router.get("/productos/{producto_id}/ajustar", dependencies=[_SOLO_OWNER])
def formulario_ajustar_stock(request: Request, producto_id: int):
    producto = servicio_stock.obtener_por_id(producto_id)
    if producto is None:
        return redireccionar_con_mensaje("/productos", "error", "El producto no existe.")

    contexto = {
        **contexto_base(request),
        "producto": producto,
        "motivos": sorted(MOTIVOS_AJUSTE_VALIDOS),
        "clave_idempotencia": nueva_clave_idempotencia(),
    }
    return templates.TemplateResponse(request, "productos/ajustar.html", contexto)


@router.post("/productos/{producto_id}/ajustar", dependencies=[_SOLO_OWNER])
def ajustar_stock(
    producto_id: int,
    motivo: str = Form(...),
    direccion: str = Form(...),
    cantidad: int = Form(...),
    observaciones: str = Form(""),
    clave_idempotencia: str = Form(""),
    usuario_actual: Usuario = Depends(obtener_usuario_actual),
):
    # El usuario no ingresa el signo: la interfaz solo ofrece "sumar" o
    # "restar" más una cantidad positiva. No confiar en JavaScript para
    # esto ni para el resto de las reglas (cantidad > 0, OTRO con
    # observaciones): se revalida acá aunque el HTML ya las sugiera.
    if cantidad <= 0:
        raise DatosInvalidosError("La cantidad del ajuste debe ser mayor a cero.")
    if direccion not in ("sumar", "restar"):
        raise DatosInvalidosError("La dirección del ajuste no es válida.")
    delta = cantidad if direccion == "sumar" else -cantidad

    servicio_stock.ajustar_stock(
        producto_id,
        delta=delta,
        motivo=motivo,
        usuario_id=usuario_actual.id,
        observaciones=observaciones.strip() or None,
        clave_idempotencia=clave_idempotencia or None,
    )
    return redireccionar_con_mensaje(
        f"/productos/{producto_id}/editar", "success", "Ajuste de stock registrado correctamente."
    )


@router.get("/productos/{producto_id}/ajustes", dependencies=[_SOLO_OWNER])
def listar_ajustes_de_producto(request: Request, producto_id: int):
    producto = servicio_stock.obtener_por_id(producto_id)
    if producto is None:
        return redireccionar_con_mensaje("/productos", "error", "El producto no existe.")

    ajustes = servicio_stock.listar_ajustes(producto_id)
    contexto = {**contexto_base(request), "producto": producto, "ajustes": ajustes}
    return templates.TemplateResponse(request, "productos/ajustes.html", contexto)


@router.get("/api/productos/buscar-codigo/{codigo_barras}", dependencies=[_CONSULTA])
def api_buscar_por_codigo(codigo_barras: str) -> JSONResponse:
    """Usada por el lector de código de barras en el POS (fetch desde pos.js)."""
    producto = servicio_stock.buscar_por_codigo_barras(codigo_barras)
    if producto is None:
        return JSONResponse(
            status_code=404, content={"error": "No se encontró ningún producto con ese código."}
        )
    return JSONResponse(
        content={
            "id": producto.id,
            "codigo_barras": producto.codigo_barras,
            "nombre": producto.nombre,
            "precio_venta_centavos": producto.precio_venta_centavos,
            "stock_actual": producto.stock_actual,
        }
    )
