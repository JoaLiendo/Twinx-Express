"""Casos de uso de control de stock.

Orquesta `domain.producto` (reglas de negocio) y
`db.repositorios.productos` (persistencia). Es la única puerta de
entrada que las interfaces (`interfaces/cli`, y a futuro la web)
deberían usar para operar sobre productos: no deben llamar a
`db.repositorios` ni construir `Producto` directamente.
"""

import logging

from db.conexion import obtener_conexion
from db.repositorios import ajustes_stock as repositorio_ajustes_stock
from db.repositorios import productos as repositorio_productos
from domain.ajuste_stock import AjusteStock, AjusteStockConUsuario
from domain.producto import Producto
from excepciones import (
    CategoriaNoEncontradaError,
    ErrorBaseDatos,
    ProductoNoEncontradoError,
    StockInsuficienteError,
)
from services import servicio_categorias, servicio_imagenes

logger = logging.getLogger(__name__)


def _validar_categoria_si_corresponde(categoria_id: int | None) -> None:
    """Si se especifica una categoría, debe existir. Se valida acá (antes
    de tocar la base) en vez de dejar que la FK de `productos.categoria_id`
    falle: así el error es siempre `CategoriaNoEncontradaError`, nunca
    confundible con la violación de UNIQUE de `codigo_barras` que ya
    traduce el repositorio en el mismo bloque `except`."""
    if categoria_id is not None and servicio_categorias.obtener_por_id(categoria_id) is None:
        raise CategoriaNoEncontradaError(f"No existe una categoría con id {categoria_id}.")


def registrar_producto(
    codigo_barras: str,
    nombre: str,
    precio_costo_centavos: int,
    precio_venta_centavos: int,
    stock_actual: int = 0,
    stock_minimo: int = 0,
    categoria_id: int | None = None,
    unidad_medida: str = "UNIDAD",
) -> Producto:
    """Da de alta un nuevo producto.

    Los precios se reciben en centavos (`int`, ver `domain.dinero`).
    La validación de negocio (precios/stock no negativos, campos
    obligatorios no vacíos, `unidad_medida` válida) ocurre al construir
    `Producto`; si algún dato es inválido se propaga
    `DatosInvalidosError` antes de tocar la base de datos. Si el
    código de barras ya existe, se propaga `CodigoBarrasDuplicadoError`.
    Si `categoria_id` no corresponde a ninguna categoría existente, se
    propaga `CategoriaNoEncontradaError`. `categoria_id=None` significa
    "sin categoría", un valor válido.
    """
    _validar_categoria_si_corresponde(categoria_id)
    producto = Producto(
        codigo_barras=codigo_barras,
        nombre=nombre,
        precio_costo_centavos=precio_costo_centavos,
        precio_venta_centavos=precio_venta_centavos,
        stock_actual=stock_actual,
        stock_minimo=stock_minimo,
        categoria_id=categoria_id,
        unidad_medida=unidad_medida,
    )
    producto_creado = repositorio_productos.crear_producto(producto)
    logger.info("Producto registrado: %s (id=%s)", producto_creado.nombre, producto_creado.id)
    return producto_creado


def obtener_por_id(producto_id: int) -> Producto | None:
    """Busca un producto activo por id (pantallas de edición y baja)."""
    return repositorio_productos.obtener_por_id(producto_id)


def buscar_por_codigo_barras(codigo_barras: str) -> Producto | None:
    """Busca un producto por código de barras exacto (lector USB)."""
    return repositorio_productos.buscar_por_codigo_barras(codigo_barras)


def buscar_por_nombre(texto: str) -> list[Producto]:
    """Busca productos por coincidencia parcial de nombre (búsqueda manual)."""
    return repositorio_productos.buscar_por_nombre(texto)


def listar_todos() -> list[Producto]:
    """Devuelve todos los productos (ej. para la grilla del punto de venta)."""
    return repositorio_productos.listar_todos()


def listar_stock_critico() -> list[Producto]:
    """Devuelve los productos cuyo stock llegó al mínimo o está por debajo, para alertas."""
    return repositorio_productos.listar_stock_critico()


def actualizar_producto(
    producto_id: int,
    codigo_barras: str,
    nombre: str,
    precio_costo_centavos: int,
    precio_venta_centavos: int,
    stock_minimo: int,
    categoria_id: int | None = None,
    unidad_medida: str = "UNIDAD",
) -> Producto:
    """Actualiza los datos editables de un producto existente.

    El stock actual no se modifica acá: solo cambia con las ventas
    (ver `services.servicio_ventas.registrar_venta`). Los precios se
    reciben en centavos (`int`, ver `domain.dinero`).

    Raises:
        ProductoNoEncontradoError: si no existe un producto activo con ese id.
        DatosInvalidosError: si algún dato es inválido.
        CodigoBarrasDuplicadoError: si el código de barras ya pertenece a otro producto.
        CategoriaNoEncontradaError: si `categoria_id` no corresponde a ninguna categoría existente.
    """
    producto_actual = repositorio_productos.obtener_por_id(producto_id)
    if producto_actual is None:
        raise ProductoNoEncontradoError(f"No existe un producto con id {producto_id}.")
    _validar_categoria_si_corresponde(categoria_id)

    producto_actualizado = Producto(
        id=producto_id,
        codigo_barras=codigo_barras,
        nombre=nombre,
        precio_costo_centavos=precio_costo_centavos,
        precio_venta_centavos=precio_venta_centavos,
        stock_actual=producto_actual.stock_actual,
        stock_minimo=stock_minimo,
        categoria_id=categoria_id,
        unidad_medida=unidad_medida,
    )
    resultado = repositorio_productos.actualizar_datos(producto_actualizado)
    logger.info("Producto actualizado: %s (id=%s)", resultado.nombre, resultado.id)
    return resultado


def eliminar_producto(producto_id: int) -> bool:
    """Da de baja un producto. Devuelve `True` si la baja fue lógica.

    Si el producto no tiene ventas asociadas se elimina físicamente;
    si tiene, se desactiva (baja lógica) para conservar el historial
    de ventas que lo referencia (ver `db.repositorios.productos.eliminar_producto`).
    La baja lógica conserva la imagen (se sigue viendo si se reactiva);
    la eliminación física borra también el archivo de imagen, si tenía.

    Raises:
        ProductoNoEncontradoError: si no existe un producto activo con ese id.
    """
    producto_antes = repositorio_productos.obtener_por_id(producto_id)
    baja_logica = repositorio_productos.eliminar_producto(producto_id)
    if not baja_logica and producto_antes is not None:
        servicio_imagenes.eliminar_archivo(producto_antes.imagen_archivo)
    logger.info("Producto id=%s dado de baja (baja_logica=%s)", producto_id, baja_logica)
    return baja_logica


def listar_inactivos() -> list[Producto]:
    """Devuelve los productos dados de baja lógica, para poder reactivarlos."""
    return repositorio_productos.listar_inactivos()


def reactivar_producto(producto_id: int) -> Producto:
    """Reactiva un producto dado de baja lógica (`activo` vuelve a 1).

    No modifica `stock_actual` ni las ventas/detalle de venta
    asociadas: la baja lógica solo cambió `activo`, y reactivar solo
    lo revierte.

    Raises:
        ProductoNoEncontradoError: si no existe un producto inactivo
            con ese id (no existe, o ya está activo).
    """
    producto = repositorio_productos.reactivar_producto(producto_id)
    logger.info("Producto reactivado: %s (id=%s)", producto.nombre, producto.id)
    return producto


def asignar_imagen(producto_id: int, contenido: bytes, nombre_original: str) -> Producto:
    """Valida y guarda una imagen, y la asocia a un producto existente.

    Orden seguro para el reemplazo (ver auditoría de Fase 3D): primero
    se valida y se guarda el archivo nuevo; recién si la base se
    actualiza sin error se borra el archivo anterior (si había). Si el
    `UPDATE` de la base falla después de haber guardado el archivo
    nuevo, se borra ese archivo nuevo para no dejarlo huérfano -- el
    producto sigue apuntando a su imagen anterior, intacta.

    Raises:
        ProductoNoEncontradoError: si no existe un producto con ese id.
        ArchivoImagenInvalidoError: formato, tamaño o contenido inválido
            (ver `services.servicio_imagenes.validar_y_guardar`).
    """
    producto_actual = repositorio_productos.obtener_por_id(producto_id)
    if producto_actual is None:
        raise ProductoNoEncontradoError(f"No existe un producto con id {producto_id}.")

    nombre_archivo_nuevo = servicio_imagenes.validar_y_guardar(producto_id, contenido, nombre_original)
    try:
        resultado = repositorio_productos.actualizar_imagen(producto_id, nombre_archivo_nuevo)
    except (ErrorBaseDatos, ProductoNoEncontradoError):
        servicio_imagenes.eliminar_archivo(nombre_archivo_nuevo)
        raise

    if producto_actual.imagen_archivo:
        servicio_imagenes.eliminar_archivo(producto_actual.imagen_archivo)

    logger.info("Imagen asignada al producto id=%s", producto_id)
    return resultado


def ajustar_stock(
    producto_id: int,
    delta: int,
    motivo: str,
    usuario_id: int,
    observaciones: str | None = None,
) -> AjusteStock:
    """Corrige `stock_actual` fuera de una venta o una compra (merma,
    rotura, vencimiento, pérdida, robo o una diferencia de recuento),
    dejando un registro histórico permanente del ajuste.

    `delta` ya viene con signo resuelto (positivo suma, negativo resta)
    -- la traducción desde "Sumar"/"Restar" + cantidad es responsabilidad
    de la interfaz. Usa el mismo patrón de concurrencia que
    `services.servicio_ventas.registrar_venta`: la lectura del stock
    vigente y la escritura del nuevo valor ocurren dentro de una única
    transacción con `BEGIN IMMEDIATE`, para que dos ajustes concurrentes
    sobre el mismo producto no generen un lost update.

    Raises:
        ProductoNoEncontradoError: si no existe un producto activo con ese id.
        StockInsuficienteError: si el ajuste dejaría el stock en negativo.
        DatosInvalidosError: si `motivo`/`delta`/`observaciones` son inválidos
            (ver `domain.ajuste_stock.AjusteStock`).
    """
    with obtener_conexion(inmediata=True) as conexion:
        producto = repositorio_productos.obtener_por_id_en_conexion(conexion, producto_id)
        if producto is None:
            raise ProductoNoEncontradoError(f"No existe un producto con id {producto_id}.")

        stock_anterior = producto.stock_actual
        nuevo_stock = stock_anterior + delta
        if nuevo_stock < 0:
            raise StockInsuficienteError(
                f"El ajuste dejaría el stock de '{producto.nombre}' en negativo: "
                f"{stock_anterior} + ({delta}) = {nuevo_stock}."
            )

        producto.actualizar_stock(nuevo_stock)
        repositorio_productos.actualizar_stock_en_conexion(conexion, producto_id, producto.stock_actual)

        ajuste = AjusteStock(
            producto_id=producto_id,
            usuario_id=usuario_id,
            motivo=motivo,
            delta=delta,
            stock_anterior=stock_anterior,
            stock_resultante=nuevo_stock,
            observaciones=observaciones,
        )
        ajuste_creado = repositorio_ajustes_stock.registrar_ajuste_en_conexion(conexion, ajuste)

    logger.info(
        "Ajuste de stock registrado: producto_id=%s motivo=%s delta=%s usuario_id=%s",
        producto_id, motivo, delta, usuario_id,
    )
    return ajuste_creado


def listar_ajustes(producto_id: int) -> list[AjusteStockConUsuario]:
    """Historial de ajustes manuales de stock de un producto, más
    reciente primero (ver `services.servicio_stock.ajustar_stock`)."""
    return repositorio_ajustes_stock.listar_por_producto(producto_id)


def quitar_imagen(producto_id: int) -> Producto:
    """Quita la imagen de un producto (si tenía) y borra el archivo.

    No falla si el producto no tenía imagen asignada.

    Raises:
        ProductoNoEncontradoError: si no existe un producto con ese id.
    """
    producto_actual = repositorio_productos.obtener_por_id(producto_id)
    if producto_actual is None:
        raise ProductoNoEncontradoError(f"No existe un producto con id {producto_id}.")

    resultado = repositorio_productos.actualizar_imagen(producto_id, None)
    if producto_actual.imagen_archivo:
        servicio_imagenes.eliminar_archivo(producto_actual.imagen_archivo)

    logger.info("Imagen quitada del producto id=%s", producto_id)
    return resultado
