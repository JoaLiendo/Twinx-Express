"""Casos de uso de control de stock.

Orquesta `domain.producto` (reglas de negocio) y
`db.repositorios.productos` (persistencia). Es la única puerta de
entrada que las interfaces (`interfaces/cli`, y a futuro la web)
deberían usar para operar sobre productos: no deben llamar a
`db.repositorios` ni construir `Producto` directamente.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

from db.conexion import ConexionBD, obtener_conexion
from db.repositorios import ajustes_stock as repositorio_ajustes_stock
from db.repositorios import etiquetas as repositorio_etiquetas
from db.repositorios import producto_proveedor as repositorio_producto_proveedor
from db.repositorios import productos as repositorio_productos
from db.repositorios import usuarios as repositorio_usuarios
from domain.ajuste_stock import (
    MOTIVO_STOCK_INICIAL,
    AjusteStock,
    AjusteStockConUsuario,
    validar_motivo_manual,
)
from domain.dinero import MAXIMO_ENTERO
from domain.partida import ORIGEN_AJUSTE, ORIGEN_RECUENTO, ORIGEN_STOCK_INICIAL
from domain.producto import Producto
from domain.usuario import exigir_rol
from excepciones import (
    MENSAJE_FORMULARIO_REENVIADO_CON_OTROS_DATOS,
    AjusteNoEncontradoError,
    AjusteNoReversibleError,
    AjusteYaRevertidoError,
    CategoriaNoEncontradaError,
    ClaveIdempotenciaReutilizadaError,
    DatosInvalidosError,
    ErrorBaseDatos,
    ProductoNoEncontradoError,
    StockInsuficienteError,
)
from db.repositorios import auditoria as repositorio_auditoria
from services import servicio_categorias, servicio_etiquetas, servicio_imagenes, servicio_partidas

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
    etiqueta_ids: Iterable[int] = (),
) -> Producto:
    """Da de alta un nuevo producto, con sus etiquetas (V1.11-E) en la misma transacción.

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
    etiquetas = servicio_etiquetas.validar_ids(etiqueta_ids)
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
        repositorio_etiquetas.reemplazar_de_producto_en_conexion(conexion, producto_creado.id, etiquetas)
        registrar_stock_inicial_en_conexion(
            conexion, producto_creado, usuario_id, producto_creado.precio_costo_centavos
        )
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


def registrar_stock_inicial_en_conexion(
    conexion: ConexionBD,
    producto: Producto,
    usuario_id: int | None,
    costo_unitario_centavos: int | None,
) -> AjusteStock | None:
    """Registra el stock de alta de un producto recién creado como movimiento `STOCK_INICIAL`
    (V1.11-C), dentro de la transacción del alta: se confirma o se revierte junto con ella.

    No escribe `productos.stock_actual` (el alta ya lo insertó con ese valor, sin pasar por un
    UPDATE): solo deja el movimiento causal con anterior 0 y resultante = stock de alta. Sin stock
    (0) no registra nada -- un movimiento de cero no explica nada. `usuario_id` y
    `costo_unitario_centavos` se guardan tal como llegan, `None` incluido: nunca se inventa un
    responsable ni un costo (ver `domain.ajuste_stock.MOTIVO_STOCK_INICIAL` y migración 029).
    """
    if producto.stock_actual <= 0:
        return None
    ajuste = AjusteStock(
        producto_id=producto.id,
        usuario_id=usuario_id,
        motivo=MOTIVO_STOCK_INICIAL,
        delta=producto.stock_actual,
        stock_anterior=0,
        stock_resultante=producto.stock_actual,
        costo_unitario_centavos=costo_unitario_centavos,
    )
    ajuste_creado = repositorio_ajustes_stock.registrar_ajuste_en_conexion(conexion, ajuste)
    # V1.12: el stock de alta es una partida propia, sin vencimiento conocido y con el mismo costo del ajuste.
    servicio_partidas.registrar_ingreso_en_conexion(
        conexion, producto.id, ORIGEN_STOCK_INICIAL, producto.stock_actual,
        ajuste_id=ajuste_creado.id, costo_unitario_centavos=costo_unitario_centavos,
    )
    servicio_partidas.verificar_conciliacion_en_conexion(conexion, [producto.id])
    return ajuste_creado


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


@dataclass(frozen=True)
class GrupoReposicion:
    """Las sugerencias de reposición de un mismo proveedor principal (`proveedor_id=None`: los
    productos sin proveedor principal). Conserva el orden de urgencia de `listar_reposicion`."""

    proveedor_id: int | None
    proveedor_nombre: str | None
    proveedor_activo: bool
    sugerencias: list[SugerenciaReposicion]


@dataclass(frozen=True)
class LineaListaReposicion:
    """Un producto de una lista de reposición ya elegida, con la cantidad a comprar y su costo sugerido."""

    producto_id: int
    codigo_barras: str
    nombre: str
    cantidad: int
    costo_unitario_centavos: int

    @property
    def subtotal_centavos(self) -> int:
        return self.cantidad * self.costo_unitario_centavos


@dataclass(frozen=True)
class ListaReposicion:
    """Productos elegidos de un grupo de reposición, para precargar una compra o imprimir. No es una
    orden de compra: no se guarda ni tiene número ni estado."""

    proveedor_id: int | None
    proveedor_nombre: str | None
    proveedor_activo: bool
    lineas: list[LineaListaReposicion]

    @property
    def total_centavos(self) -> int:
        return sum(linea.subtotal_centavos for linea in self.lineas)


def agrupar_reposicion_por_proveedor() -> list[GrupoReposicion]:
    """`listar_reposicion` agrupada por proveedor principal: primero los proveedores por nombre y al
    final el grupo sin proveedor principal. Solo lectura; no cambia la regla de reposición."""
    por_proveedor: dict[int | None, list[SugerenciaReposicion]] = {}
    for sugerencia in listar_reposicion():
        por_proveedor.setdefault(sugerencia.proveedor_id, []).append(sugerencia)
    grupos = [
        GrupoReposicion(
            proveedor_id=proveedor_id,
            proveedor_nombre=sugerencias[0].proveedor_nombre,
            proveedor_activo=sugerencias[0].proveedor_activo,
            sugerencias=sugerencias,
        )
        for proveedor_id, sugerencias in por_proveedor.items()
    ]
    return sorted(grupos, key=lambda grupo: (grupo.proveedor_id is None, (grupo.proveedor_nombre or "").casefold()))


def preparar_lista_reposicion(proveedor_id: int | None, cantidades: dict[int, int]) -> ListaReposicion:
    """Arma la lista de reposición de un grupo con las cantidades elegidas (producto_id -> cantidad).

    Los costos salen siempre de `listar_reposicion` (último costo con el proveedor principal o, si no
    hay, el costo vigente): nunca de lo que envía el cliente. No escribe nada.

    Raises:
        DatosInvalidosError: si no se eligió ningún producto, una cantidad no es un entero entre 1 y
            `MAXIMO_ENTERO`, el grupo ya no existe, o un producto no pertenece a ese grupo (otro
            proveedor principal) o ya no necesita reposición.
    """
    if not cantidades:
        raise DatosInvalidosError("Elegí al menos un producto para reponer.")
    grupo = next((g for g in agrupar_reposicion_por_proveedor() if g.proveedor_id == proveedor_id), None)
    if grupo is None:
        raise DatosInvalidosError("Ese proveedor ya no tiene productos para reponer.")
    por_producto = {sugerencia.producto_id: sugerencia for sugerencia in grupo.sugerencias}
    for producto_id, cantidad in cantidades.items():
        if producto_id not in por_producto:
            raise DatosInvalidosError("Un producto elegido no corresponde a este proveedor o ya no necesita reposición.")
        if not 1 <= cantidad <= MAXIMO_ENTERO:
            raise DatosInvalidosError("La cantidad a comprar debe ser un entero mayor a cero.")
    return ListaReposicion(
        proveedor_id=grupo.proveedor_id,
        proveedor_nombre=grupo.proveedor_nombre,
        proveedor_activo=grupo.proveedor_activo,
        lineas=[
            LineaListaReposicion(
                producto_id=sugerencia.producto_id,
                codigo_barras=sugerencia.codigo_barras,
                nombre=sugerencia.nombre,
                cantidad=cantidades[sugerencia.producto_id],
                costo_unitario_centavos=sugerencia.costo_unitario_centavos,
            )
            for sugerencia in grupo.sugerencias
            if sugerencia.producto_id in cantidades
        ],
    )


def preparar_precarga_compra(proveedor_id: int | None, cantidades: dict[int, int]) -> ListaReposicion:
    """Como `preparar_lista_reposicion`, para precargar el formulario de compra: además exige que el
    proveedor principal esté activo (una compra a un proveedor inactivo no se puede registrar, y el
    atajo no debe dejar reasignar sus productos a otro). La lista imprimible sí admite ese caso.

    Raises:
        DatosInvalidosError: lo mismo que `preparar_lista_reposicion`, o si el proveedor está inactivo.
    """
    lista = preparar_lista_reposicion(proveedor_id, cantidades)
    if not lista.proveedor_activo:
        raise DatosInvalidosError("El proveedor principal está inactivo: no se puede precargar una compra a su nombre.")
    return lista


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
    etiqueta_ids: Iterable[int] | None = None,
) -> Producto:
    """Actualiza los datos editables de un producto existente.

    `etiqueta_ids` (V1.11-E) deja al producto exactamente con esas etiquetas, en la misma
    transacción y auditado; `None` no las toca.

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
    etiquetas = servicio_etiquetas.validar_ids(etiqueta_ids) if etiqueta_ids is not None else None

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
    with obtener_conexion(inmediata=True) as conexion:
        resultado = repositorio_productos.actualizar_datos_en_conexion(conexion, producto_actualizado, usuario_id)
        etiquetas_cambiaron = etiquetas is not None and repositorio_etiquetas.reemplazar_de_producto_en_conexion(
            conexion, producto_id, etiquetas
        )
        if etiquetas_cambiaron and usuario_id is not None:
            repositorio_auditoria.registrar_en_conexion(
                conexion,
                usuario_id,
                "PRODUCTO_EDITADO",
                "PRODUCTO",
                producto_id,
                f"{resultado.nombre}: se modificaron las etiquetas",
            )
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


FILTROS_DE_STOCK = {
    "critico": lambda producto: producto.tiene_stock_critico,
    "sin_stock": lambda producto: producto.stock_actual == 0,
    "con_stock": lambda producto: producto.stock_actual > 0,
}


def listar_para_administracion(
    texto: str = "",
    categoria_id: int | None = None,
    estado_stock: str = "",
    inactivos: bool = False,
    etiqueta_id: int | None = None,
) -> list[Producto]:
    """Listado administrativo de productos (V1.11-E), con filtros combinables.

    `texto` busca en el nombre (sin distinguir mayúsculas) o en el código de barras;
    `categoria_id` incluye sus subcategorías; `estado_stock` es una clave de
    `FILTROS_DE_STOCK` (otra cosa no filtra); `inactivos` lista los dados de baja en vez de los
    activos. El catálogo de un kiosco es chico: se filtra en memoria sobre un único listado en
    vez de sumar una consulta por cada combinación de filtros.
    """
    productos = listar_inactivos() if inactivos else listar_todos()
    texto = texto.strip()
    if texto:
        buscado = texto.casefold()
        productos = [p for p in productos if buscado in p.nombre.casefold() or texto in p.codigo_barras]
    if categoria_id is not None:
        incluidas = servicio_categorias.obtener_arbol().ids_con_descendientes(categoria_id)
        productos = [p for p in productos if p.categoria_id in incluidas]
    filtro_stock = FILTROS_DE_STOCK.get(estado_stock)
    if filtro_stock is not None:
        productos = [p for p in productos if filtro_stock(p)]
    if etiqueta_id is not None:
        etiquetados = servicio_etiquetas.ids_por_producto()
        productos = [p for p in productos if etiqueta_id in etiquetados.get(p.id, [])]
    return productos


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


_COSTO_VIGENTE_DEL_PRODUCTO = object()
"""Sentinel de `aplicar_ajuste_en_conexion`: distingue "no se pasó costo, usar el vigente del
producto" (default) de "se pasó explícitamente `None`" (una reversa de un ajuste legacy sin
snapshot, que no debe inventar un costo -- ver `revertir_ajuste`)."""


def aplicar_ajuste_en_conexion(
    conexion: ConexionBD,
    producto: Producto,
    delta: int,
    motivo: str,
    usuario_id: int,
    observaciones: str | None = None,
    clave_idempotencia: str | None = None,
    inventario_id: int | None = None,
    reversa_de_id: int | None = None,
    costo_unitario_centavos: int | None = _COSTO_VIGENTE_DEL_PRODUCTO,
    partida_id: int | None = None,
) -> AjusteStock:
    """Núcleo del ajuste de stock, dentro de la transacción `BEGIN IMMEDIATE` de quien invoca.

    Recibe el producto ya resuelto (así el ajuste manual exige un producto activo y el inventario
    físico -- y una reversa -- pueden ajustar también uno inactivo con stock). Valida que el stock
    no quede negativo, escribe el nuevo stock por el único punto autorizado
    (`actualizar_stock_en_conexion`), registra el ajuste con `stock_anterior`/`stock_resultante` y
    deja la auditoría `AJUSTE_STOCK`, todo en la transacción del llamador. `inventario_id` marca el
    ajuste como RECUENTO de un inventario; `reversa_de_id` lo marca como REVERSA de otro ajuste
    (ver `revertir_ajuste`).

    Congela `producto.precio_costo_centavos` (el costo vigente en este mismo instante, dentro de la
    transacción) como snapshot del ajuste -- salvo que `costo_unitario_centavos` se pase
    explícitamente (una reversa referencia el snapshot del ajuste original, no inventa uno nuevo
    con el costo actual, que puede haber cambiado desde entonces).

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

    costo = (
        producto.precio_costo_centavos
        if costo_unitario_centavos is _COSTO_VIGENTE_DEL_PRODUCTO
        else costo_unitario_centavos
    )
    ajuste = AjusteStock(
        producto_id=producto.id,
        usuario_id=usuario_id,
        motivo=motivo,
        delta=delta,
        stock_anterior=stock_anterior,
        stock_resultante=nuevo_stock,
        observaciones=observaciones,
        inventario_id=inventario_id,
        costo_unitario_centavos=costo,
        reversa_de_id=reversa_de_id,
    )
    ajuste_creado = repositorio_ajustes_stock.registrar_ajuste_en_conexion(
        conexion, ajuste, clave_idempotencia=clave_idempotencia
    )
    # V1.12: una unidad que aparece en un recuento no trae ningún dato conocido (ni vencimiento ni costo);
    # la que devuelve una reversa de un ajuste anterior a V1.12 conserva el costo histórico del original.
    servicio_partidas.aplicar_ajuste_en_conexion(
        conexion,
        producto.id,
        delta,
        ajuste_creado.id,
        ORIGEN_RECUENTO if motivo == "RECUENTO" else ORIGEN_AJUSTE,
        reversa_de_id=reversa_de_id,
        costo_unitario_centavos=None if motivo == "RECUENTO" else costo,
        partida_elegida_id=partida_id,
    )
    servicio_partidas.verificar_conciliacion_en_conexion(conexion, [producto.id])
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

    `motivo` (V1.11-A) debe ser uno de `domain.ajuste_stock.MOTIVOS_MANUALES_PERMITIDOS`:
    ni un motivo legacy (`MERMA`/`PERDIDA`/`OTRO`) ni `RECUENTO` (ese lo genera únicamente
    `services.servicio_inventario.confirmar_inventario`, vía `aplicar_ajuste_en_conexion`
    directamente, sin pasar por esta función). Al ser todos de pérdida, `delta` debe ser negativo.

    Raises:
        ProductoNoEncontradoError: si no existe un producto activo con ese id.
        StockInsuficienteError: si el ajuste dejaría el stock en negativo.
        DatosInvalidosError: si `motivo`/`delta`/`observaciones` son inválidos
            (ver `domain.ajuste_stock.AjusteStock` y `domain.ajuste_stock.validar_motivo_manual`).
    """
    validar_motivo_manual(motivo, delta)
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


_SOLO_OWNER = frozenset({"OWNER"})


def revertir_ajuste(
    ajuste_id: int,
    observaciones: str,
    usuario_id: int,
    clave_idempotencia: str | None = None,
    producto_id_esperado: int | None = None,
) -> AjusteStock:
    """Revierte un ajuste manual erróneo con un ajuste NUEVO, vinculado al original por
    `reversa_de_id` (V1.11-B): el original nunca se edita ni se borra, es histórico e inmutable.

    Aplica exactamente `-delta` del original (restaura el stock que ese ajuste movió) y referencia
    su `costo_unitario_centavos` (sin inventar un costo nuevo, ver `aplicar_ajuste_en_conexion`).
    Exclusivo de OWNER, validado acá (no solo en la ruta): es una operación irreversible que además
    desbloquea volver a ajustar el producto si el error lo había dejado en un stock incorrecto.

    `producto_id_esperado` (opcional): si se pasa, exige que el ajuste pertenezca a ese producto
    -- lo usa la ruta web, cuyo `producto_id` viene de la URL (`/productos/{producto_id}/ajustes/
    {ajuste_id}/revertir`) y no debe poder revertir el ajuste de OTRO producto aunque la URL
    combine ids inconsistentes. No se confía en que el formulario se haya cargado desde el GET que
    sí valida esto: el POST puede construirse directamente, así que la validación ocurre acá,
    dentro de la misma transacción, antes de tocar nada.

    `clave_idempotencia`: mismo patrón que `ajustar_stock` -- un reenvío con la misma clave (doble
    clic, doble submit) devuelve la reversa ya registrada sin generar una segunda.

    Usa el mismo producto del ajuste original incluso si está inactivo (`...incluyendo_inactivos`,
    igual criterio que el RECUENTO de inventario): revertir no depende de que el producto siga
    activo.

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        AjusteNoEncontradoError: si no existe un ajuste con `ajuste_id`, o si existe pero no
            pertenece a `producto_id_esperado`.
        AjusteNoReversibleError: si el ajuste es un RECUENTO de inventario, o ya es una reversa.
        AjusteYaRevertidoError: si el ajuste ya tiene una reversa.
        StockInsuficienteError: si revertirlo dejaría el stock en negativo.
        DatosInvalidosError: si `observaciones` está vacío (toda reversa exige su justificación,
            ver `domain.ajuste_stock.MOTIVOS_QUE_REQUIEREN_OBSERVACIONES`).
        ClaveIdempotenciaReutilizadaError: si la clave ya se usó para revertir otro ajuste.
    """
    with obtener_conexion(inmediata=True) as conexion:
        usuario = repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id)
        exigir_rol(usuario, _SOLO_OWNER)

        if clave_idempotencia is not None:
            existente = repositorio_ajustes_stock.obtener_por_clave_idempotencia_en_conexion(
                conexion, clave_idempotencia
            )
            if existente is not None:
                if (existente.reversa_de_id, existente.observaciones) != (ajuste_id, observaciones):
                    raise ClaveIdempotenciaReutilizadaError(MENSAJE_FORMULARIO_REENVIADO_CON_OTROS_DATOS)
                return existente

        original = repositorio_ajustes_stock.obtener_por_id_en_conexion(conexion, ajuste_id)
        if original is None:
            raise AjusteNoEncontradoError(f"No existe un ajuste de stock con id {ajuste_id}.")
        if producto_id_esperado is not None and original.producto_id != producto_id_esperado:
            # Mismo error que "no existe": desde la URL (producto_id + ajuste_id) no hay ningún
            # ajuste válido que revertir, sea porque el producto no existe o porque el ajuste es
            # de otro producto -- en ningún caso corresponde tocar el stock de nada.
            raise AjusteNoEncontradoError(
                f"El ajuste {ajuste_id} no corresponde al producto {producto_id_esperado}."
            )
        if original.motivo == "RECUENTO":
            raise AjusteNoReversibleError(
                "Un ajuste de inventario (RECUENTO) no se revierte desde este flujo: "
                "corregilo con un nuevo inventario físico."
            )
        if original.motivo == MOTIVO_STOCK_INICIAL:
            raise AjusteNoReversibleError(
                "El stock inicial de un producto no se revierte: si se cargó mal, "
                "corregilo con un inventario físico."
            )
        if original.reversa_de_id is not None:
            raise AjusteNoReversibleError("No se puede revertir una reversa.")
        if repositorio_ajustes_stock.obtener_reversa_de_en_conexion(conexion, ajuste_id) is not None:
            raise AjusteYaRevertidoError(f"El ajuste {ajuste_id} ya fue revertido anteriormente.")

        producto = repositorio_productos.obtener_por_id_en_conexion_incluyendo_inactivos(
            conexion, original.producto_id
        )
        if producto is None:
            raise ProductoNoEncontradoError(f"No existe un producto con id {original.producto_id}.")

        reversa = aplicar_ajuste_en_conexion(
            conexion,
            producto,
            -original.delta,
            "REVERSA",
            usuario_id,
            observaciones=observaciones,
            clave_idempotencia=clave_idempotencia,
            reversa_de_id=original.id,
            costo_unitario_centavos=original.costo_unitario_centavos,
        )

    logger.info(
        "Ajuste de stock revertido: ajuste_id=%s reversa_id=%s usuario_id=%s", ajuste_id, reversa.id, usuario_id
    )
    return reversa


def listar_ajustes(producto_id: int) -> list[AjusteStockConUsuario]:
    """Historial de ajustes manuales de stock de un producto, más
    reciente primero (ver `services.servicio_stock.ajustar_stock`)."""
    return repositorio_ajustes_stock.listar_por_producto(producto_id)


def obtener_ajuste(ajuste_id: int) -> AjusteStock | None:
    """El ajuste con ese id, o `None` (pantalla de confirmación de `revertir_ajuste`)."""
    return repositorio_ajustes_stock.obtener_por_id(ajuste_id)


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
