"""Casos de uso de registro de ventas.

Orquesta `domain.venta` (reglas de negocio), `domain.producto` (stock)
y los repositorios de ventas/productos. `registrar_venta` es la única
operación que descuenta stock: hace todo (validación de stock, alta
de la venta y de su detalle, descuento de stock) dentro de una misma
transacción de `db.conexion.obtener_conexion`, de forma que ante
cualquier error no quede ningún cambio a medias.

Fase 5A agrega idempotencia opcional (`clave_idempotencia`): protege
contra ventas duplicadas por reintento de red o doble envío del
formulario. La garantía real la da el índice `UNIQUE` de
`ventas.clave_idempotencia` (ver migración 008), no esta función --
acá solo se orquesta qué hacer antes y después de ese `UNIQUE`.
"""

import logging
import sqlite3

from db.conexion import obtener_conexion
from db.repositorios import productos as repositorio_productos
from db.repositorios import ventas as repositorio_ventas
from domain.producto import Producto
from domain.venta import TIPOS_PAGO_VALIDOS, ItemVenta, Venta, VentaConDetalle, calcular_hash_contenido
from excepciones import (
    ClaveIdempotenciaReutilizadaError,
    DatosInvalidosError,
    ErrorBaseDatos,
    ProductoNoEncontradoError,
    StockInsuficienteError,
)

logger = logging.getLogger(__name__)


def _resolver_clave_reutilizada(
    venta_existente: Venta, hash_existente: str, contenido_hash: str
) -> Venta:
    """Decide qué hacer cuando ya existe una venta con la clave de
    idempotencia pedida: si el contenido coincide, es un reintento
    legítimo (se devuelve la venta tal cual); si no, es una clave
    reutilizada con datos distintos (error controlado)."""
    if hash_existente == contenido_hash:
        return venta_existente
    raise ClaveIdempotenciaReutilizadaError(
        "La clave de idempotencia ya fue usada para registrar una venta con datos distintos."
    )


def registrar_venta(items: list[ItemVenta], tipo_pago: str, clave_idempotencia: str | None = None) -> Venta:
    """Registra una venta con sus líneas de detalle y descuenta stock.

    Todo ocurre en una única transacción: primero se valida que haya
    stock suficiente para cada producto (sumando cantidades si un
    mismo producto aparece en más de un ítem), recién después se
    descuenta el stock y se inserta la venta junto con su detalle. Si
    cualquier paso falla, no se persiste ni la venta ni ningún
    descuento de stock: `obtener_conexion` revierte toda la
    transacción.

    Si se pasa `clave_idempotencia` (Fase 5A, obligatoria para la API
    web, opcional para el CLI y llamados internos): antes de tocar
    stock se busca una venta ya registrada con esa clave.
    - Si existe y su contenido (mismos productos+cantidades agregadas,
      mismo `tipo_pago`) coincide, se devuelve esa venta sin volver a
      descontar stock ni insertar nada -- es un reintento legítimo.
    - Si existe con contenido distinto, se levanta
      `ClaveIdempotenciaReutilizadaError` sin tocar nada.
    - Si no existe, sigue el flujo normal, y el `INSERT` final incluye
      la clave. Si dos requests con la misma clave llegan casi al
      mismo tiempo (ninguno ve todavía la fila del otro), el `UNIQUE`
      de la base deja pasar solo al primero que comprometa: el otro
      recibe `sqlite3.IntegrityError` en su `INSERT`, su transacción se
      revierte por completo (incluido el descuento de stock que haya
      alcanzado a hacer), y acá se recupera la venta ganadora con una
      lectura nueva, aplicando el mismo criterio de arriba.

    Args:
        items: líneas de venta (producto_id + cantidad). No puede
            estar vacío.
        tipo_pago: uno de `domain.venta.TIPOS_PAGO_VALIDOS`
            ('EFECTIVO', 'TARJETA', 'TRANSFERENCIA', 'OTRO').
        clave_idempotencia: identificador que el cliente genera una
            vez por intento de cobro y reenvía igual en cualquier
            reintento de ese mismo intento. `None` (el default)
            desactiva la protección -- pensado para el CLI y para
            llamados internos que no la necesitan.

    Raises:
        DatosInvalidosError: si `items` está vacío o `tipo_pago` no es válido.
        ProductoNoEncontradoError: si algún `producto_id` no existe.
        StockInsuficienteError: si algún producto no tiene stock
            suficiente para la cantidad total pedida.
        ClaveIdempotenciaReutilizadaError: si `clave_idempotencia` ya
            se usó antes con un contenido distinto.
    """
    if not items:
        raise DatosInvalidosError("Una venta debe tener al menos un ítem.")
    if tipo_pago not in TIPOS_PAGO_VALIDOS:
        raise DatosInvalidosError(
            f"Tipo de pago inválido: {tipo_pago!r}. Debe ser uno de {sorted(TIPOS_PAGO_VALIDOS)}."
        )

    contenido_hash = calcular_hash_contenido(items, tipo_pago) if clave_idempotencia is not None else None

    cantidad_pedida_por_producto: dict[int, int] = {}
    for item in items:
        cantidad_pedida_por_producto[item.producto_id] = (
            cantidad_pedida_por_producto.get(item.producto_id, 0) + item.cantidad
        )

    try:
        # `inmediata=True`: esta transacción lee stock y, en base a esa
        # lectura, decide si escribe -- sin esto, el `SELECT` de stock
        # corre sin ningún lock que lo proteja y dos ventas concurrentes
        # del mismo producto pueden leer el mismo valor y confirmarse
        # las dos (lost update silencioso, ver auditoría de concurrencia,
        # Stage D). `BEGIN IMMEDIATE` serializa a los dos hilos: el que
        # pierde la carrera vuelve a leer el stock ya actualizado.
        with obtener_conexion(inmediata=True) as conexion:
            if clave_idempotencia is not None:
                existente = repositorio_ventas.obtener_por_clave_idempotencia_en_conexion(
                    conexion, clave_idempotencia
                )
                if existente is not None:
                    venta_existente, hash_existente = existente
                    return _resolver_clave_reutilizada(venta_existente, hash_existente, contenido_hash)

            productos_por_id: dict[int, Producto] = {}
            for producto_id, cantidad_total in cantidad_pedida_por_producto.items():
                producto = repositorio_productos.obtener_por_id_en_conexion(conexion, producto_id)
                if producto is None:
                    raise ProductoNoEncontradoError(f"No existe un producto con id {producto_id}.")
                if producto.stock_actual < cantidad_total:
                    raise StockInsuficienteError(
                        f"Stock insuficiente para '{producto.nombre}': "
                        f"pedido {cantidad_total}, disponible {producto.stock_actual}."
                    )
                productos_por_id[producto_id] = producto

            for producto_id, cantidad_total in cantidad_pedida_por_producto.items():
                producto = productos_por_id[producto_id]
                producto.actualizar_stock(producto.stock_actual - cantidad_total)
                repositorio_productos.actualizar_stock_en_conexion(conexion, producto.id, producto.stock_actual)

            items_con_precio = [
                (item, productos_por_id[item.producto_id].precio_venta_centavos) for item in items
            ]
            # Aritmética entera exacta: sin redondeos ni errores de precisión binaria.
            total_centavos = sum(precio_centavos * item.cantidad for item, precio_centavos in items_con_precio)

            venta = repositorio_ventas.registrar_venta_con_detalle(
                conexion,
                total_centavos,
                tipo_pago,
                items_con_precio,
                clave_idempotencia=clave_idempotencia,
                contenido_hash=contenido_hash,
            )
    except ErrorBaseDatos as error:
        if clave_idempotencia is None or not isinstance(error.__cause__, sqlite3.IntegrityError):
            raise
        # Perdimos la carrera: otra request con la misma clave comprometió
        # su transacción entre nuestro chequeo y nuestro INSERT. A esta
        # altura ya tiene que existir -- se recupera con una lectura nueva.
        existente = repositorio_ventas.obtener_por_clave_idempotencia(clave_idempotencia)
        if existente is None:
            raise
        venta_existente, hash_existente = existente
        return _resolver_clave_reutilizada(venta_existente, hash_existente, contenido_hash)

    logger.info(
        "Venta registrada: id=%s total_centavos=%s tipo_pago=%s", venta.id, venta.total_centavos, venta.tipo_pago
    )
    return venta


def obtener_venta_con_detalle(venta_id: int) -> VentaConDetalle | None:
    """Lectura histórica de una venta ya confirmada, para el ticket
    imprimible de Fase 5D. Delega directo al repositorio: no hay
    ninguna regla de negocio que aplicar acá (a diferencia de
    `registrar_venta`), es una lectura de algo ya confirmado.
    """
    return repositorio_ventas.obtener_venta_con_detalle(venta_id)
