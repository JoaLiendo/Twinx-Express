"""Exportaciones CSV de los datos operativos (V1.8-B): compras, ventas, deuda de clientes, rotación de stock
y kardex; y (V1.10-D) deuda a proveedores.

Política uniforme, la misma del catálogo de productos (`services.servicio_exportacion`): UTF-8 con BOM
(`utf-8-sig`, para que Excel en Windows respete tildes y ñ), separador coma y quoting del módulo `csv`
(nunca filas armadas concatenando). Los importes salen como texto decimal simple ("150.50", ver
`domain.dinero.centavos_a_texto`), no con el formato localizado de pantalla. Cada exportación devuelve TODO el
conjunto filtrado -- no la página visible --, con los mismos filtros y las mismas consultas que la pantalla de
origen; todo se genera en memoria.

PROTECCIÓN CONTRA INYECCIÓN DE FÓRMULAS: un texto que empiece con `=`, `+`, `-`, `@` (o tabulación / retorno de
carro) lo interpretaría como fórmula Excel/LibreOffice al abrir el archivo. `domain.celdas_seguras.neutralizar_texto`
le antepone un apóstrofo (regla inyectiva: ver ese módulo, que también define la inversa que usa la importación).
Solo se tocan los textos: los números que generamos (`numero`, `dinero`) pasan intactos, incluso negativos.
"""

import csv
import io
from collections.abc import Iterable, Sequence

from domain.celdas_seguras import Numero, neutralizar_texto
from domain.dinero import centavos_a_texto
from domain.reportes_operativos import ProductoRotacion
from services import (
    servicio_compras,
    servicio_deuda_proveedores,
    servicio_movimientos_stock,
    servicio_reportes,
    servicio_ventas,
)


def numero(valor: int | None) -> Numero:
    return Numero("" if valor is None else str(valor))


def dinero(centavos: int | None) -> Numero:
    return Numero("" if centavos is None else centavos_a_texto(centavos))


def _celda(valor: str | None) -> str:
    return valor if isinstance(valor, Numero) else neutralizar_texto(valor)


def generar_csv(encabezados: Sequence[str], filas: Iterable[Sequence[str | None]]) -> bytes:
    """CSV con BOM UTF-8. Los encabezados son fijos del sistema; las celdas de texto se neutralizan."""
    buffer = io.StringIO()
    escritor = csv.writer(buffer)
    escritor.writerow(encabezados)
    for fila in filas:
        escritor.writerow([_celda(valor) for valor in fila])
    return buffer.getvalue().encode("utf-8-sig")


COLUMNAS_COMPRAS = (
    "compra", "fecha", "proveedor", "estado", "codigo_barras", "producto", "cantidad", "costo_unitario",
    "subtotal", "total_compra", "motivo_anulacion", "fecha_anulacion",
)


def csv_compras(
    proveedor_id: int | None, fecha_desde: str | None, fecha_hasta: str | None, estado: str | None,
    producto_id: int | None,
) -> bytes:
    """Compras que cumplen los filtros del listado (todas, sin paginar), una fila por línea. `total_compra` es
    el total de la compra completa y se repite en cada una de sus líneas: no sumarlo entre filas."""
    lineas = servicio_compras.listar_lineas_para_exportar(proveedor_id, fecha_desde, fecha_hasta, estado, producto_id)
    return generar_csv(
        COLUMNAS_COMPRAS,
        (
            (
                numero(linea.compra_id), linea.fecha, linea.proveedor_nombre, linea.estado, linea.codigo_barras,
                linea.producto_nombre, numero(linea.cantidad), dinero(linea.costo_unitario_centavos),
                dinero(linea.subtotal_centavos), dinero(linea.total_centavos), linea.motivo_anulacion,
                linea.fecha_anulacion,
            )
            for linea in lineas
        ),
    )


COLUMNAS_VENTAS = (
    "venta", "fecha", "estado", "usuario", "cliente", "tipo_pago", "codigo_barras", "producto", "cantidad",
    "precio_unitario", "subtotal", "total_venta", "motivo_anulacion", "fecha_anulacion",
)


def csv_ventas(
    fecha_desde: str | None, fecha_hasta: str | None, tipo_pago: str | None, estado: str | None,
    cliente_id: int | None,
) -> bytes:
    """Ventas del historial con sus filtros (mismo rango efectivo que la pantalla; todas, sin paginar), una fila
    por línea. Las anuladas se incluyen si el filtro las incluye, con su estado. `total_venta` se repite en cada
    línea de la venta."""
    lineas = servicio_ventas.listar_lineas_para_exportar(fecha_desde, fecha_hasta, tipo_pago, estado, cliente_id)
    return generar_csv(
        COLUMNAS_VENTAS,
        (
            (
                numero(linea.venta_id), linea.fecha, linea.estado, linea.usuario_nombre, linea.cliente_nombre,
                linea.tipo_pago, linea.codigo_barras, linea.producto_nombre, numero(linea.cantidad),
                dinero(linea.precio_unitario_centavos), dinero(linea.subtotal_centavos), dinero(linea.total_centavos),
                linea.motivo_anulacion, linea.fecha_anulacion,
            )
            for linea in lineas
        ),
    )


COLUMNAS_DEUDA = ("cliente_id", "cliente", "estado", "saldo")


def csv_deuda_clientes() -> bytes:
    """Deuda actual de cuenta corriente: el mismo agregado del reporte (`generar_reporte_deuda`, solo saldos
    positivos, clientes activos o inactivos)."""
    return generar_csv(
        COLUMNAS_DEUDA,
        (
            (numero(fila.cliente_id), fila.cliente_nombre, "ACTIVO" if fila.activo else "INACTIVO", dinero(fila.saldo_centavos))
            for fila in servicio_reportes.generar_reporte_deuda().filas
        ),
    )


COLUMNAS_DEUDA_PROVEEDORES = (
    "proveedor_id", "proveedor", "saldo", "vencida", "proxima", "vigente", "sin_vencimiento", "ultima_compra",
    "ultimo_pago", "criterio",
)


def csv_deuda_proveedores(proveedor_id: int | None, situacion: str | None) -> bytes:
    """El mismo dataset de `/reportes/deuda-proveedores` con sus filtros. `criterio` aclara que el reparto por
    vencimiento es una estimación FIFO; una fila de libro inconsistente sale sin importes FIFO (y sin saldo si
    este tampoco es creíble) y con `criterio` = "Datos inconsistentes", nunca con cifras inventadas."""
    reporte = servicio_deuda_proveedores.generar_reporte_deuda_proveedores(proveedor_id, situacion)
    return generar_csv(
        COLUMNAS_DEUDA_PROVEEDORES,
        (
            (
                numero(fila.proveedor_id), fila.nombre, dinero(fila.saldo_total_centavos), dinero(fila.vencida_centavos),
                dinero(fila.proxima_centavos), dinero(fila.vigente_centavos), dinero(fila.sin_vencimiento_centavos),
                fila.ultima_compra, fila.ultimo_pago,
                servicio_deuda_proveedores.CRITERIO_FIFO if fila.consistente else servicio_deuda_proveedores.MARCA_INCONSISTENTE,
            )
            for fila in reporte.filas
        ),
    )


COLUMNAS_ROTACION = (
    "codigo_barras", "producto", "producto_activo", "stock_actual", "unidades_vendidas", "ultima_venta",
    "dias_desde_ultima_venta", "costo_unitario", "valor_stock", "estado",
)


def _fila_rotacion(fila: ProductoRotacion) -> tuple[str | None, ...]:
    return (
        fila.codigo_barras, fila.nombre, "SI" if fila.activo else "NO", numero(fila.stock_actual),
        numero(fila.unidades_vendidas), fila.fecha_ultima_venta, numero(fila.dias_desde_ultima_venta),
        dinero(fila.costo_unitario_centavos), dinero(fila.valor_stock_centavos), fila.estado,
    )


def csv_rotacion(fecha_desde: str | None, fecha_hasta: str | None) -> bytes:
    """El mismo dataset de `/reportes/rotacion` para el período (mismo rango por defecto y mismas reglas)."""
    reporte = servicio_reportes.generar_reporte_rotacion(fecha_desde, fecha_hasta)
    return generar_csv(COLUMNAS_ROTACION, (_fila_rotacion(fila) for fila in reporte.filas))


COLUMNAS_KARDEX = ("fecha", "tipo", "referencia", "entrada", "salida", "saldo", "descripcion")
TIPO_SALDO_INICIAL = "SALDO_INICIAL_RECONSTRUIDO"


def csv_kardex(producto_id: int, fecha_desde: str | None, fecha_hasta: str | None) -> bytes:
    """El kardex del producto en el período. La primera fila (`SALDO_INICIAL_RECONSTRUIDO`) informa el saldo
    reconstruido al inicio: se calcula desde el stock actual y NO es un dato histórico persistido."""
    kardex = servicio_movimientos_stock.generar_kardex(producto_id, fecha_desde, fecha_hasta)
    inicial = (
        kardex.fecha_desde, TIPO_SALDO_INICIAL, None, None, None, numero(kardex.saldo_inicial),
        "Reconstruido desde el stock actual; no es un dato histórico persistido",
    )
    movimientos = (
        (
            m.fecha, m.tipo, m.referencia, numero(m.entrada) if m.entrada else None,
            numero(m.salida) if m.salida else None, numero(m.saldo), m.descripcion,
        )
        for m in kardex.movimientos
    )
    return generar_csv(COLUMNAS_KARDEX, [inicial, *movimientos])
