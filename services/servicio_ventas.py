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
from datetime import date, timedelta

from db.conexion import obtener_conexion
from db.repositorios import caja as repositorio_caja
from db.repositorios import productos as repositorio_productos
from db.repositorios import ventas as repositorio_ventas
from domain.producto import Producto
from domain.venta import (
    TIPOS_PAGO_VALIDOS,
    ItemVenta,
    ResumenVenta,
    Venta,
    VentaConDetalle,
    calcular_hash_contenido,
    validar_motivo_anulacion,
)
from excepciones import (
    ClaveIdempotenciaReutilizadaError,
    DatosInvalidosError,
    ErrorBaseDatos,
    ProductoNoEncontradoError,
    StockInsuficienteError,
    VentaDeCajaCerradaError,
    VentaNoEncontradaError,
    VentaYaAnuladaError,
)

logger = logging.getLogger(__name__)

# Historial de Ventas: ventana por defecto cuando no se pide un rango
# explícito. Más amplia que la de Reportes (7 días, pensada para un
# vistazo analítico rápido) porque acá el caso de uso operativo típico
# es "buscar una venta de hace un par de semanas", no un resumen del día.
DIAS_RANGO_POR_DEFECTO_HISTORIAL = 30


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


def registrar_venta(
    items: list[ItemVenta],
    tipo_pago: str,
    clave_idempotencia: str | None = None,
    usuario_id: int | None = None,
) -> Venta:
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
        usuario_id: id del usuario autenticado que registra la venta
            (migración 009). `None` (el default) para el CLI, que no
            autentica a nadie -- nunca se inventa un usuario.

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

            # Costo histórico (migración 009): mismo `Producto` ya leído
            # arriba dentro de esta misma transacción -- sin ninguna
            # consulta adicional, así el costo guardado corresponde
            # exactamente al producto vendido en este momento, no al
            # costo que pueda tener más adelante.
            costos_unitarios_por_producto_id = {
                producto_id: producto.precio_costo_centavos for producto_id, producto in productos_por_id.items()
            }

            venta = repositorio_ventas.registrar_venta_con_detalle(
                conexion,
                total_centavos,
                tipo_pago,
                items_con_precio,
                clave_idempotencia=clave_idempotencia,
                contenido_hash=contenido_hash,
                usuario_id=usuario_id,
                costos_unitarios_por_producto_id=costos_unitarios_por_producto_id,
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


def _rango_por_defecto_historial() -> tuple[str, str]:
    hoy = date.today()
    desde = hoy - timedelta(days=DIAS_RANGO_POR_DEFECTO_HISTORIAL - 1)
    return desde.isoformat(), hoy.isoformat()


def listar_historial(
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    tipo_pago: str | None = None,
) -> tuple[str, str, list[ResumenVenta]]:
    """Historial de Ventas del período pedido: devuelve
    `(fecha_desde_efectiva, fecha_hasta_efectiva, ventas)`.

    Si no se pasa alguno de los dos límites, usa el rango por defecto
    (últimos `DIAS_RANGO_POR_DEFECTO_HISTORIAL` días) para ambos -- un
    rango a medio especificar (solo desde, o solo hasta) sería ambiguo,
    así que se trata igual que "no se pidió ningún rango" en vez de
    adivinar el límite que falta (mismo criterio que
    `services.servicio_reportes.generar_reporte_ventas`).
    """
    if fecha_desde is None or fecha_hasta is None:
        fecha_desde, fecha_hasta = _rango_por_defecto_historial()

    ventas = repositorio_ventas.listar_resumen(fecha_desde, fecha_hasta, tipo_pago)
    return fecha_desde, fecha_hasta, ventas


def obtener_resumen_por_id(venta_id: int) -> ResumenVenta | None:
    """Cabecera resuelta (vendedor + cantidad de líneas) de una venta
    para la pantalla de detalle del Historial. Delega directo al
    repositorio: no hay ninguna regla de negocio que aplicar acá, es
    una lectura de algo ya confirmado (mismo criterio que
    `obtener_venta_con_detalle`).
    """
    return repositorio_ventas.obtener_resumen_por_id(venta_id)


def anular_venta(
    venta_id: int,
    motivo: str,
    observaciones: str | None,
    usuario_id: int,
) -> Venta:
    """Anula una venta `ACTIVA`: restaura el stock de cada línea y marca
    la venta como `ANULADA` con su auditoría, todo en una única
    transacción (ver auditoría de diseño de anulación de ventas).

    Es exclusivamente una corrección de registro + stock -- **no** hay
    reembolso financiero modelado ni se genera ningún movimiento de
    caja: `EFECTIVO` del día se corrige solo porque
    `db.repositorios.ventas.listar_ventas_del_dia` excluye `ANULADA`
    (ver `services.servicio_caja.calcular_arqueo_del_dia`);
    `TARJETA`/`TRANSFERENCIA`/`OTRO` nunca movieron caja, así que
    anularlas tampoco la toca.

    Solo se puede anular una venta `ACTIVA` que pertenezca a la sesión
    de caja actualmente abierta -- `venta.fecha >= fecha_apertura_vigente`,
    con `fecha_apertura_vigente` resuelta dentro de esta misma
    transacción (`repositorio_caja.obtener_fecha_ultima_apertura_en_conexion`).
    El modelo no tiene `caja_id` en `ventas`; esta es la regla mínima
    verificable con los datos existentes. Una venta de una sesión ya
    cerrada, o sin ninguna caja abierta ahora mismo, se rechaza: el MVP
    no modifica cierres históricos.

    Raises:
        DatosInvalidosError: si `motivo`/`observaciones` son inválidos
            (ver `domain.venta.validar_motivo_anulacion`).
        VentaNoEncontradaError: si no existe una venta con ese id.
        VentaYaAnuladaError: si la venta ya estaba `ANULADA`.
        VentaDeCajaCerradaError: si no hay caja abierta, o la venta
            pertenece a una sesión ya cerrada.
        ProductoNoEncontradoError: si algún `producto_id` de la venta no
            existe (no debería poder pasar: `detalle_venta.producto_id`
            es `ON DELETE RESTRICT`).
    """
    validar_motivo_anulacion(motivo, observaciones)

    with obtener_conexion(inmediata=True) as conexion:
        venta = repositorio_ventas.obtener_por_id_en_conexion(conexion, venta_id)
        if venta is None:
            raise VentaNoEncontradaError(f"No existe una venta con id {venta_id}.")
        if venta.estado != "ACTIVA":
            raise VentaYaAnuladaError(f"La venta {venta_id} ya fue anulada anteriormente.")

        fecha_apertura_vigente = repositorio_caja.obtener_fecha_ultima_apertura_en_conexion(conexion)
        if fecha_apertura_vigente is None:
            raise VentaDeCajaCerradaError(
                "No hay ninguna caja abierta en este momento: no se puede anular la venta."
            )
        if venta.fecha < fecha_apertura_vigente:
            raise VentaDeCajaCerradaError(
                f"La venta {venta_id} pertenece a una sesión de caja ya cerrada: "
                "anularla implicaría modificar un cierre histórico, fuera de alcance."
            )

        items = repositorio_ventas.listar_items_en_conexion(conexion, venta_id)
        cantidad_por_producto: dict[int, int] = {}
        for item in items:
            cantidad_por_producto[item.producto_id] = (
                cantidad_por_producto.get(item.producto_id, 0) + item.cantidad
            )

        for producto_id, cantidad in cantidad_por_producto.items():
            producto = repositorio_productos.obtener_por_id_en_conexion_incluyendo_inactivos(
                conexion, producto_id
            )
            if producto is None:
                raise ProductoNoEncontradoError(f"No existe un producto con id {producto_id}.")
            producto.actualizar_stock(producto.stock_actual + cantidad)
            repositorio_productos.actualizar_stock_en_conexion(conexion, producto.id, producto.stock_actual)

        venta_anulada = repositorio_ventas.anular_venta_en_conexion(
            conexion, venta_id, motivo=motivo, observaciones=observaciones, usuario_id=usuario_id
        )

    logger.info(
        "Venta anulada: id=%s motivo=%s usuario_id=%s", venta_id, motivo, usuario_id
    )
    return venta_anulada
