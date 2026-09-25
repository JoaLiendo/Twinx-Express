"""Casos de uso de control de stock.

Orquesta `domain.producto` (reglas de negocio) y
`db.repositorios.productos` (persistencia). Es la única puerta de
entrada que las interfaces (`interfaces/cli`, y a futuro la web)
deberían usar para operar sobre productos: no deben llamar a
`db.repositorios` ni construir `Producto` directamente.
"""

import logging
from dataclasses import dataclass, field

from db.conexion import ConexionBD, obtener_conexion
from db.repositorios import ajustes_stock as repositorio_ajustes_stock
from db.repositorios import producto_proveedor as repositorio_producto_proveedor
from db.repositorios import productos as repositorio_productos
from domain.ajuste_stock import AjusteStock, AjusteStockConUsuario
from domain.producto import Producto
from excepciones import (
    MENSAJE_FORMULARIO_REENVIADO_CON_OTROS_DATOS,
    CategoriaNoEncontradaError,
    ClaveIdempotenciaReutilizadaError,
    ErrorBaseDatos,
    ProductoNoEncontradoError,
    StockInsuficienteError,
)
from db.repositorios import auditoria as repositorio_auditoria
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
    usuario_id: int | None = None,
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
    with obtener_conexion() as conexion:
        producto_creado = repositorio_productos.crear_producto_en_conexion(conexion, producto)
        if usuario_id is not None:
            repositorio_auditoria.registrar_en_conexion(
                conexion,
                usuario_id,
                "PRODUCTO_CREADO",
                "PRODUCTO",
                producto_creado.id,
                f"{producto_creado.nombre} ({producto_creado.codigo_barras})",
            )
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


@dataclass(frozen=True)
class SugerenciaReposicion:
    """Un producto por debajo de su stock mínimo y cuánto se sugiere comprar.

    `cantidad_sugerida` = `stock_minimo - stock_actual` (lo que falta para llegar
    al mínimo; siempre positiva porque el producto está estrictamente por debajo).
    `costo_unitario_centavos` es el último costo de compra al proveedor principal del
    producto (`costo_es_del_proveedor`) o, si no tiene principal o nunca se le compró, el
    costo vigente del producto. Los datos del proveedor son solo informativos.
    """

    producto_id: int
    codigo_barras: str
    nombre: str
    stock_actual: int
    stock_minimo: int
    cantidad_sugerida: int
    costo_unitario_centavos: int
    proveedor_id: int | None = None
    proveedor_nombre: str | None = None
    proveedor_activo: bool = True
    costo_es_del_proveedor: bool = False

    @property
    def costo_estimado_centavos(self) -> int:
        return self.cantidad_sugerida * self.costo_unitario_centavos


def listar_reposicion() -> list[SugerenciaReposicion]:
    """Productos que necesitan reposición (stock mínimo > 0 y stock actual
    estrictamente por debajo del mínimo, incluido el stock 0) con la cantidad
    sugerida `stock_minimo - stock_actual`, su proveedor principal y el último costo con
    él (respaldo: costo vigente del producto). Solo lectura: no crea compras ni
    modifica stock."""
    principales = repositorio_producto_proveedor.listar_principales_con_costo()
    sugerencias = []
    for producto in repositorio_productos.listar_para_reposicion():
        principal = principales.get(producto.id)
        ultimo_costo = principal.ultimo_costo_centavos if principal is not None else None
        sugerencias.append(
            SugerenciaReposicion(
                producto_id=producto.id,
                codigo_barras=producto.codigo_barras,
                nombre=producto.nombre,
                stock_actual=producto.stock_actual,
                stock_minimo=producto.stock_minimo,
                cantidad_sugerida=producto.stock_minimo - producto.stock_actual,
                costo_unitario_centavos=ultimo_costo if ultimo_costo is not None else producto.precio_costo_centavos,
                proveedor_id=principal.proveedor_id if principal is not None else None,
                proveedor_nombre=principal.proveedor_nombre if principal is not None else None,
                proveedor_activo=principal.proveedor_activo if principal is not None else True,
                costo_es_del_proveedor=ultimo_costo is not None,
            )
        )
    return sugerencias


@dataclass
class LineaValorizacion:
    """Un producto y su aporte al valor total del inventario (Valorización
    de Inventario). `valor_centavos` es `stock_actual * costo_unitario_centavos`,
    aritmética entera exacta -- nunca redondeo ni `float`."""

    producto_id: int
    codigo_barras: str
    nombre: str
    stock_actual: int
    costo_unitario_centavos: int
    valor_centavos: int


@dataclass
class ValorizacionInventario:
    """Estado del inventario a HOY (Valorización de Inventario): nunca
    depende de ningún rango de fechas -- `calcular_valorizacion_inventario`
    no toma parámetros, así que ningún filtro de período de Reportes
    puede llegar a afectar este cálculo.

    `cantidad_productos_costo_cero` es informativo: esos productos ya
    están incluidos en `valor_total_centavos`, aportando $0 -- nunca
    excluidos.
    """

    unidades_totales: int
    valor_total_centavos: int
    cantidad_productos_valorizados: int
    cantidad_productos_costo_cero: int
    detalle: list[LineaValorizacion] = field(default_factory=list)


def calcular_valorizacion_inventario() -> ValorizacionInventario:
    """Valorización del inventario a costo actual (`stock_actual *
    precio_costo_centavos`), a HOY.

    Incluye productos activos e inactivos por igual, mientras tengan
    `stock_actual > 0` (ver `db.repositorios.productos.listar_valorizables`).
    No es una metodología FIFO ni de costo promedio ponderado -- usa el
    costo vigente de cada producto, la única información de costo que
    el sistema mantiene actualizada (ver auditoría de diseño).
    """
    productos = repositorio_productos.listar_valorizables()

    detalle = sorted(
        (
            LineaValorizacion(
                producto_id=producto.id,
                codigo_barras=producto.codigo_barras,
                nombre=producto.nombre,
                stock_actual=producto.stock_actual,
                costo_unitario_centavos=producto.precio_costo_centavos,
                valor_centavos=producto.stock_actual * producto.precio_costo_centavos,
            )
            for producto in productos
        ),
        key=lambda linea: linea.valor_centavos,
        reverse=True,
    )

    return ValorizacionInventario(
        unidades_totales=sum(producto.stock_actual for producto in productos),
        valor_total_centavos=sum(linea.valor_centavos for linea in detalle),
        cantidad_productos_valorizados=len(productos),
        cantidad_productos_costo_cero=sum(1 for producto in productos if producto.precio_costo_centavos == 0),
        detalle=detalle,
    )


def actualizar_producto(
    producto_id: int,
    codigo_barras: str,
    nombre: str,
    precio_costo_centavos: int,
    precio_venta_centavos: int,
    stock_minimo: int,
    categoria_id: int | None = None,
    unidad_medida: str = "UNIDAD",
    usuario_id: int | None = None,
) -> Producto:
    """Actualiza los datos editables de un producto existente.

    El stock actual no se modifica acá: solo cambia con las ventas
    (ver `services.servicio_ventas.registrar_venta`). Los precios se
    reciben en centavos (`int`, ver `domain.dinero`).

    Si cambia el precio de venta o el de costo, el cambio queda en el
    historial de precios con `usuario_id` (V1.2).

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
    resultado = repositorio_productos.actualizar_datos(producto_actualizado, usuario_id=usuario_id)
    logger.info("Producto actualizado: %s (id=%s)", resultado.nombre, resultado.id)
    return resultado


def eliminar_producto(producto_id: int, usuario_id: int | None = None) -> bool:
    """Da de baja un producto. Devuelve `True` si la baja fue lógica.

    Si el producto no tiene ningún registro asociado se elimina físicamente; si
    tiene ventas, compras, ajustes de stock o cambios de precio, se desactiva
    (baja lógica) para conservar ese historial (ver
    `db.repositorios.productos.eliminar_producto_en_conexion`). La baja lógica
    conserva la imagen (se sigue viendo si se reactiva); la eliminación física
    borra también el archivo de imagen, si tenía.

    La baja y su registro de auditoría (con `usuario_id`) ocurren en la misma
    transacción: si la auditoría falla, la baja se revierte.

    Raises:
        ProductoNoEncontradoError: si no existe un producto activo con ese id.
    """
    with obtener_conexion(inmediata=True) as conexion:
        producto_antes = repositorio_productos.obtener_por_id_en_conexion(conexion, producto_id)
        motivos = repositorio_productos.eliminar_producto_en_conexion(conexion, producto_id)
        if motivos is None or producto_antes is None:
            raise ProductoNoEncontradoError(f"No existe un producto activo con id {producto_id}.")
        if usuario_id is not None:
            detalle = f"baja lógica (tiene {', '.join(motivos)})" if motivos else "eliminado"
            repositorio_auditoria.registrar_en_conexion(
                conexion, usuario_id, "PRODUCTO_BAJA", "PRODUCTO", producto_id, f"{producto_antes.nombre}: {detalle}"
            )
    baja_logica = bool(motivos)
    if not baja_logica:
        servicio_imagenes.eliminar_archivo(producto_antes.imagen_archivo)
    logger.info("Producto id=%s dado de baja (baja_logica=%s)", producto_id, baja_logica)
    return baja_logica


def motivos_de_conservacion(producto_id: int) -> list[str]:
    """Qué registros asociados (ventas, compras, ajustes de stock, cambios de
    precio) tiene un producto y por qué una baja se conserva como baja lógica."""
    return repositorio_productos.listar_motivos_de_conservacion(producto_id)


def listar_inactivos() -> list[Producto]:
    """Devuelve los productos dados de baja lógica, para poder reactivarlos."""
    return repositorio_productos.listar_inactivos()


def reactivar_producto(producto_id: int, usuario_id: int | None = None) -> Producto:
    """Reactiva un producto dado de baja lógica (`activo` vuelve a 1).

    No modifica `stock_actual` ni las ventas/detalle de venta
    asociadas: la baja lógica solo cambió `activo`, y reactivar solo
    lo revierte.

    Raises:
        ProductoNoEncontradoError: si no existe un producto inactivo
            con ese id (no existe, o ya está activo).
    """
    with obtener_conexion(inmediata=True) as conexion:
        producto = repositorio_productos.reactivar_producto_en_conexion(conexion, producto_id)
        if usuario_id is not None:
            repositorio_auditoria.registrar_en_conexion(
                conexion, usuario_id, "PRODUCTO_REACTIVADO", "PRODUCTO", producto.id, producto.nombre
            )
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


def aplicar_ajuste_en_conexion(
    conexion: ConexionBD,
    producto: Producto,
    delta: int,
    motivo: str,
    usuario_id: int,
    observaciones: str | None = None,
    clave_idempotencia: str | None = None,
    inventario_id: int | None = None,
) -> AjusteStock:
    """Núcleo del ajuste de stock, dentro de la transacción `BEGIN IMMEDIATE` de quien invoca.

    Recibe el producto ya resuelto (así el ajuste manual exige un producto activo y el inventario
    físico puede ajustar también uno inactivo con stock). Valida que el stock no quede negativo,
    escribe el nuevo stock por el único punto autorizado (`actualizar_stock_en_conexion`), registra
    el ajuste con `stock_anterior`/`stock_resultante` y deja la auditoría `AJUSTE_STOCK`, todo en
    la transacción del llamador. `inventario_id` marca el ajuste como RECUENTO de un inventario.

    Raises:
        StockInsuficienteError: si el ajuste dejaría el stock en negativo.
        DatosInvalidosError: si `motivo`/`delta`/`observaciones` son inválidos.
    """
    stock_anterior = producto.stock_actual
    nuevo_stock = stock_anterior + delta
    if nuevo_stock < 0:
        raise StockInsuficienteError(
            f"El ajuste dejaría el stock de '{producto.nombre}' en negativo: "
            f"{stock_anterior} + ({delta}) = {nuevo_stock}."
        )

    producto.actualizar_stock(nuevo_stock)
    repositorio_productos.actualizar_stock_en_conexion(conexion, producto.id, producto.stock_actual)

    ajuste = AjusteStock(
        producto_id=producto.id,
        usuario_id=usuario_id,
        motivo=motivo,
        delta=delta,
        stock_anterior=stock_anterior,
        stock_resultante=nuevo_stock,
        observaciones=observaciones,
        inventario_id=inventario_id,
    )
    ajuste_creado = repositorio_ajustes_stock.registrar_ajuste_en_conexion(
        conexion, ajuste, clave_idempotencia=clave_idempotencia
    )
    repositorio_auditoria.registrar_en_conexion(
        conexion,
        usuario_id,
        "AJUSTE_STOCK",
        "PRODUCTO",
        producto.id,
        f"{producto.nombre}: {motivo} {delta:+d} (stock {stock_anterior} → {nuevo_stock})",
    )
    return ajuste_creado


def ajustar_stock(
    producto_id: int,
    delta: int,
    motivo: str,
    usuario_id: int,
    observaciones: str | None = None,
    clave_idempotencia: str | None = None,
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

    `clave_idempotencia` (V1.1): un reenvío con la misma clave (doble clic,
    doble submit) devuelve el ajuste ya registrado sin volver a aplicar el
    delta. Se resuelve dentro de la misma transacción, antes de validar el
    stock, así el reintento de un ajuste ya aplicado nunca falla por
    "stock insuficiente".

    Raises:
        ProductoNoEncontradoError: si no existe un producto activo con ese id.
        StockInsuficienteError: si el ajuste dejaría el stock en negativo.
        DatosInvalidosError: si `motivo`/`delta`/`observaciones` son inválidos
            (ver `domain.ajuste_stock.AjusteStock`).
    """
    with obtener_conexion(inmediata=True) as conexion:
        if clave_idempotencia is not None:
            existente = repositorio_ajustes_stock.obtener_por_clave_idempotencia_en_conexion(
                conexion, clave_idempotencia
            )
            if existente is not None:
                if (existente.producto_id, existente.delta, existente.motivo, existente.observaciones) != (
                    producto_id,
                    delta,
                    motivo,
                    observaciones,
                ):
                    raise ClaveIdempotenciaReutilizadaError(MENSAJE_FORMULARIO_REENVIADO_CON_OTROS_DATOS)
                return existente

        producto = repositorio_productos.obtener_por_id_en_conexion(conexion, producto_id)
        if producto is None:
            raise ProductoNoEncontradoError(f"No existe un producto con id {producto_id}.")

        ajuste_creado = aplicar_ajuste_en_conexion(
            conexion, producto, delta, motivo, usuario_id, observaciones, clave_idempotencia
        )

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
