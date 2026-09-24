"""Entidad Venta y su detalle, y reglas de negocio asociadas.

`ItemVenta` representa una línea del carrito antes de confirmarse la
venta (qué producto, qué cantidad); la resolución de precios y el
descuento de stock ocurren en `services.servicio_ventas.registrar_venta`.
`Venta` representa una venta ya confirmada y persistida.
"""

import hashlib
import json
from dataclasses import dataclass

from excepciones import DatosInvalidosError

TIPOS_PAGO_VALIDOS = frozenset({"EFECTIVO", "TARJETA", "TRANSFERENCIA", "OTRO"})

# Migración 020: la venta a cuenta no es un medio de cobro inmediato ni el
# predeterminado, por eso vive aparte de `TIPOS_PAGO_VALIDOS` (que arma las
# opciones del POS): exige un cliente activo y genera un CARGO en su cuenta.
TIPO_PAGO_CUENTA_CORRIENTE = "CUENTA_CORRIENTE"
TIPOS_PAGO_ACEPTADOS = TIPOS_PAGO_VALIDOS | {TIPO_PAGO_CUENTA_CORRIENTE}

ESTADOS_VENTA_VALIDOS = frozenset({"ACTIVA", "ANULADA"})

MOTIVOS_ANULACION_VALIDOS = frozenset(
    {"ERROR_CARGA", "ARREPENTIMIENTO_CLIENTE", "PRODUCTO_INCORRECTO", "OTRO"}
)

# `OTRO` es el único motivo que no describe por sí mismo qué pasó -- mismo
# criterio que ya usa `domain.ajuste_stock.MOTIVOS_QUE_REQUIEREN_OBSERVACIONES`.
MOTIVOS_ANULACION_QUE_REQUIEREN_OBSERVACIONES = frozenset({"OTRO"})


@dataclass
class ItemVenta:
    """Una línea de venta: producto pedido y cantidad."""

    producto_id: int
    cantidad: int

    def __post_init__(self) -> None:
        if self.cantidad <= 0:
            raise DatosInvalidosError("La cantidad de un ítem de venta debe ser mayor a cero.")


def calcular_hash_contenido(items: list[ItemVenta], tipo_pago: str, cliente_id: int | None = None) -> str:
    """Hash canónico y determinista del contenido lógico de una venta
    (Fase 5A: idempotencia de `services.servicio_ventas.registrar_venta`).

    No incluye ningún precio: el precio nunca es parte de la autoridad
    de la venta (siempre lo resuelve el servidor desde `productos` al
    confirmar), así que no tiene sentido que participe en detectar si
    dos requests representan "el mismo pedido".

    Las líneas se agregan por `producto_id` (dos líneas del mismo
    producto que sumadas dan la misma cantidad total producen el mismo
    hash que una sola línea con esa cantidad -- mismo criterio que ya
    usa `registrar_venta` para sumar cantidades repetidas) y se
    ordenan por `producto_id`, así el resultado no depende del orden en
    que el cliente haya serializado las líneas.

    `cliente_id` (migración 020) solo entra al hash cuando la venta tiene
    cliente, así el hash de una venta sin cliente es idéntico al de siempre.
    """
    cantidad_por_producto: dict[int, int] = {}
    for item in items:
        cantidad_por_producto[item.producto_id] = cantidad_por_producto.get(item.producto_id, 0) + item.cantidad

    contenido_canonico = {
        "tipo_pago": tipo_pago,
        "items": sorted(cantidad_por_producto.items()),
    }
    if cliente_id is not None:
        contenido_canonico["cliente_id"] = cliente_id
    texto_canonico = json.dumps(contenido_canonico, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(texto_canonico.encode("utf-8")).hexdigest()


def validar_motivo_anulacion(motivo: str, observaciones: str | None) -> None:
    """Valida el motivo y las observaciones de una anulación de venta
    (ver `services.servicio_ventas.anular_venta`), antes de tocar stock
    o caja.

    No es un `__post_init__` de un dataclass propio (a diferencia de
    `domain.ajuste_stock.AjusteStock`) porque anular no crea una entidad
    nueva: transiciona el `estado` de una `Venta` ya persistida, así que
    no hay ninguna instancia nueva que validar al construirse.
    """
    if motivo not in MOTIVOS_ANULACION_VALIDOS:
        raise DatosInvalidosError(
            f"Motivo de anulación inválido: {motivo!r}. Debe ser uno de {sorted(MOTIVOS_ANULACION_VALIDOS)}."
        )
    if motivo in MOTIVOS_ANULACION_QUE_REQUIEREN_OBSERVACIONES and not (
        observaciones and observaciones.strip()
    ):
        raise DatosInvalidosError(
            f"Las anulaciones de motivo {motivo} requieren una observación que las justifique."
        )


@dataclass
class Venta:
    """Una venta ya confirmada y persistida, con su total y forma de pago.

    `total_centavos` está en centavos (`int`): la suma de los
    subtotales de sus líneas, calculada con aritmética entera exacta
    (ver `services.servicio_ventas.registrar_venta`).

    `estado` (migración 013) es `'ACTIVA'` por defecto: una venta recién
    registrada siempre lo es. `'ANULADA'` la deja marcada, pero nunca le
    borra ni le modifica ningún otro campo -- ver
    `services.servicio_ventas.anular_venta`.
    """

    id: int
    fecha: str
    total_centavos: int
    tipo_pago: str
    estado: str = "ACTIVA"


@dataclass
class LineaVenta:
    """Una línea ya vendida, con el nombre del producto para mostrarla
    (Fase 5D: ticket imprimible).

    A diferencia de `ItemVenta` (la entrada antes de confirmar la
    venta, sin precio ni nombre), esto representa un `detalle_venta`
    ya persistido: `precio_unitario_centavos`/`subtotal_centavos`
    quedaron congelados al momento de la venta y nunca se recalculan
    para mostrar un ticket. `producto_nombre` es el nombre *actual*
    del producto (vía JOIN, ver `db.repositorios.ventas`), no uno
    historizado -- un producto renombrado después de la venta va a
    aparecer con el nombre nuevo en un ticket reimpreso.
    """

    producto_nombre: str
    cantidad: int
    precio_unitario_centavos: int
    subtotal_centavos: int


@dataclass
class VentaConDetalle:
    """Una venta ya persistida junto con sus líneas -- lo que necesita
    un ticket/recibo (Fase 5D). `lineas` mantiene el mismo orden en que
    se insertaron (ver `db.repositorios.ventas.obtener_venta_con_detalle`)."""

    venta: Venta
    lineas: list[LineaVenta]


@dataclass
class ProductoMasVendido:
    """Un producto y su desempeño de ventas dentro de un período (módulo
    de Reportes): unidades vendidas, facturación, costo y margen que generó.

    No es una entidad con reglas de negocio propias -- es una
    composición de solo lectura armada con un único `JOIN` + `GROUP BY`
    (ver `db.repositorios.ventas.listar_productos_mas_vendidos_en_rango`),
    mismo criterio que `domain.compra.ResumenCompra`.

    `costo_total_centavos`/`margen_bruto_centavos` (Reportes V2) quedan en
    `None` -- nunca en `0` -- cuando `unidades_con_costo_conocido == 0`:
    ninguna línea de este producto en el período tiene
    `detalle_venta.costo_unitario_centavos` (ventas anteriores al commit
    que agregó ese campo). Cuando `unidades_con_costo_conocido` es menor
    a `unidades_vendidas`, ambos valores están calculados solo sobre la
    porción con costo conocido -- nunca se inventa el costo faltante ni
    se trata como cero (ver `services.servicio_reportes` para el
    tratamiento completo de este caso).
    """

    producto_id: int
    producto_nombre: str
    unidades_vendidas: int
    total_vendido_centavos: int
    unidades_con_costo_conocido: int = 0
    costo_total_centavos: int | None = None
    margen_bruto_centavos: int | None = None


@dataclass
class ResumenVenta:
    """Una venta con su vendedor y cantidad de líneas ya resueltos
    (Historial de Ventas): proyección de solo lectura armada con un
    único `JOIN` + `GROUP BY` (ver
    `db.repositorios.ventas.listar_resumen`/`obtener_resumen_por_id`),
    mismo criterio que `domain.compra.ResumenCompra`.

    `vendedor_nombre` es `None` -- nunca una cadena vacía ni un nombre
    inventado -- cuando `ventas.usuario_id` es `NULL` (ventas anteriores
    a esa migración, o registradas desde el CLI, que no autentica a
    nadie).

    No incluye costo ni margen: esa información es responsabilidad
    exclusiva de Reportes (ver `services.servicio_reportes`), nunca del
    Historial, que es una herramienta operativa, no un segundo módulo
    de reportes.

    No reemplaza a `Venta` ni a `VentaConDetalle`: el Historial usa
    `ResumenVenta` para el listado y la cabecera del detalle, y sigue
    reutilizando `VentaConDetalle` (vía
    `db.repositorios.ventas.obtener_venta_con_detalle`, sin cambios) para
    las líneas del detalle.

    `estado`/`motivo_anulacion`/`observaciones_anulacion`/
    `anulado_por_nombre`/`fecha_anulacion` (migración 013) resuelven la
    auditoría de una anulación en la misma cabecera, sin una segunda
    consulta: `listar_resumen`/`obtener_resumen_por_id` **incluyen**
    ventas `ANULADA` a propósito (Historial y Detalle son herramientas
    de auditoría, no un cálculo analítico -- ver
    `services.servicio_ventas.anular_venta`). Los cuatro campos de
    anulación quedan en `None` para una venta `ACTIVA`, nunca en una
    cadena vacía.
    """

    id: int
    fecha: str
    total_centavos: int
    tipo_pago: str
    vendedor_nombre: str | None
    cantidad_lineas: int
    estado: str = "ACTIVA"
    motivo_anulacion: str | None = None
    observaciones_anulacion: str | None = None
    anulado_por_nombre: str | None = None
    fecha_anulacion: str | None = None
    # Migración 020 / 021: cliente de la venta (`None` en ventas históricas y en las que no tienen
    # cliente). `cliente_nombre` es el nombre ACTUAL del cliente, no un snapshot, igual criterio
    # que el nombre del producto en el ticket. Van al final y con default para no romper a nadie
    # que construya un `ResumenVenta` por posición.
    cliente_id: int | None = None
    cliente_nombre: str | None = None
