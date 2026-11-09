"""Exportaciones CSV de los datos operativos (V1.8-B): compras, ventas, deuda de clientes, rotación de stock
y kardex; y (V1.10-D) deuda a proveedores.

Política uniforme, la misma del catálogo de productos (`services.servicio_exportacion`): UTF-8 con BOM
(`utf-8-sig`, para que Excel en Windows respete tildes y ñ), separador coma y quoting del módulo `csv`
(nunca filas armadas concatenando). Los importes salen como texto decimal simple ("150.50", ver
`domain.dinero.centavos_a_texto`), no con el formato localizado de pantalla. Cada exportación devuelve TODO el
conjunto filtrado -- no la página visible --, con los mismos filtros y las mismas consultas que la pantalla de
origen. Se genera en memoria, salvo la auditoría (V1.17), que crece sin tope y sale de a lotes.

PROTECCIÓN CONTRA INYECCIÓN DE FÓRMULAS: un texto que empiece con `=`, `+`, `-`, `@` (o tabulación / retorno de
carro) lo interpretaría como fórmula Excel/LibreOffice al abrir el archivo. `domain.celdas_seguras.neutralizar_texto`
le antepone un apóstrofo (regla inyectiva: ver ese módulo, que también define la inversa que usa la importación).
Solo se tocan los textos: los números que generamos (`numero`, `dinero`) pasan intactos, incluso negativos.
"""

import csv
import io
from collections.abc import Iterable, Iterator, Sequence
from itertools import chain

from domain.auditoria import EntradaAuditoria
from domain.celdas_seguras import Numero, neutralizar_texto
from domain.dinero import centavos_a_texto
from domain.movimiento_stock import etiqueta_de_tipo
from domain.reportes_operativos import FilaPerdida, ProductoRotacion
from services import (
    servicio_analitica,
    servicio_auditoria,
    servicio_compras,
    servicio_deuda_proveedores,
    servicio_gastos,
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


def _renglones(filas: Iterable[Sequence[str]]) -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer).writerows(filas)
    return buffer.getvalue().encode("utf-8")


def generar_csv_por_lotes(
    encabezados: Sequence[str], lotes: Iterable[Iterable[Sequence[str | None]]]
) -> Iterator[bytes]:
    """Misma política que `generar_csv` (BOM una sola vez, celdas neutralizadas), un bloque por lote."""
    yield "﻿".encode() + _renglones([encabezados])
    for lote in lotes:
        yield _renglones([_celda(valor) for valor in fila] for fila in lote)


COLUMNAS_COMPRAS = (
    "compra", "fecha", "proveedor", "estado", "codigo_barras", "producto", "cantidad", "costo_unitario",
    "subtotal", "total_compra", "motivo_anulacion", "fecha_anulacion", "comprobante_tipo", "comprobante_numero",
    "comprobante_fecha",
)


def csv_compras(
    proveedor_id: int | None, fecha_desde: str | None, fecha_hasta: str | None, estado: str | None,
    producto_id: int | None, referencia: str | None = None,
) -> bytes:
    """Compras que cumplen los filtros del listado (todas, sin paginar), una fila por línea. `total_compra` es
    el total de la compra completa y se repite en cada una de sus líneas: no sumarlo entre filas."""
    lineas = servicio_compras.listar_lineas_para_exportar(proveedor_id, fecha_desde, fecha_hasta, estado, producto_id, referencia)
    return generar_csv(
        COLUMNAS_COMPRAS,
        (
            (
                numero(linea.compra_id), linea.fecha, linea.proveedor_nombre, linea.estado, linea.codigo_barras,
                linea.producto_nombre, numero(linea.cantidad), dinero(linea.costo_unitario_centavos),
                dinero(linea.subtotal_centavos), dinero(linea.total_centavos), linea.motivo_anulacion,
                linea.fecha_anulacion, linea.comprobante_tipo, linea.comprobante_numero, linea.comprobante_fecha,
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
            m.fecha, etiqueta_de_tipo(m.tipo), m.referencia, numero(m.entrada) if m.entrada else None,
            numero(m.salida) if m.salida else None, numero(m.saldo), m.descripcion,
        )
        for m in kardex.movimientos
    )
    return generar_csv(COLUMNAS_KARDEX, [inicial, *movimientos])


COLUMNAS_PERDIDAS = (
    "fecha", "codigo_barras", "producto", "motivo", "cantidad", "costo_unitario", "perdida_a_costo",
    "costo_historico", "responsable", "observaciones", "estado", "revertido_por", "fecha_reversa",
)


def _fila_perdida(fila: FilaPerdida) -> tuple[str | None, ...]:
    return (
        fila.fecha, fila.codigo_barras, fila.producto_nombre, fila.motivo, numero(fila.cantidad),
        dinero(fila.costo_unitario_centavos), dinero(fila.perdida_centavos),
        "CONOCIDO" if fila.costo_conocido else "DESCONOCIDO", fila.usuario_nombre_completo, fila.observaciones,
        "REVERTIDA" if fila.revertida else "VIGENTE", fila.reversa_usuario_nombre_completo, fila.reversa_fecha,
    )


def csv_perdidas(
    fecha_desde: str | None,
    fecha_hasta: str | None,
    producto_id: int | None,
    motivo: str | None,
    categoria_id: int | None = None,
) -> bytes:
    """El mismo dataset de `/reportes/perdidas` con sus filtros. Una fila sin costo histórico sale con
    `costo_unitario`/`perdida_a_costo` vacíos y `costo_historico` = DESCONOCIDO: nunca con un costo inventado."""
    reporte = servicio_reportes.generar_reporte_perdidas(fecha_desde, fecha_hasta, producto_id, motivo, categoria_id)
    return generar_csv(COLUMNAS_PERDIDAS, (_fila_perdida(fila) for fila in reporte.filas))


# --- Analítica (V1.17) --------------------------------------------------------------------------------

COLUMNAS_MARGEN = (
    "grupo", "unidades", "facturado", "costo_referencia", "margen_referencia", "margen_porcentual",
    "unidades_sin_costo_referencia", "costo_real_conocido", "unidades_costo_real", "unidades_sin_costo_real",
    "diferencia_real_vs_referencia",
)


def _porcentaje(valor: float | None) -> Numero:
    return Numero("" if valor is None else f"{valor:.2f}")


def csv_margen(
    fecha_desde: str | None,
    fecha_hasta: str | None,
    criterio: str | None,
    producto_id: int | None,
    categoria_id: int | None,
) -> bytes:
    """El mismo agregado de `/analitica/margen` (mismos filtros y mismo servicio). Un grupo sin costo conocido
    sale con margen vacío, nunca con 0."""
    analisis = servicio_analitica.analizar_margen(fecha_desde, fecha_hasta, criterio, producto_id, categoria_id)
    filas = (
        (
            grupo.etiqueta, numero(grupo.unidades), dinero(grupo.facturado_centavos),
            dinero(grupo.costo_ref_centavos if grupo.unidades_con_costo_ref else None),
            dinero(grupo.margen_ref_centavos), _porcentaje(grupo.margen_ref_porcentual),
            numero(grupo.unidades_sin_costo_ref),
            dinero(grupo.costo_real_centavos if grupo.unidades_costo_real else None),
            numero(grupo.unidades_costo_real), numero(grupo.unidades_sin_costo_real),
            dinero(grupo.diferencia_real_vs_ref_centavos),
        )
        for grupo in analisis.grupos
    )
    return generar_csv(COLUMNAS_MARGEN, filas)


COLUMNAS_EVOLUCION_PRECIOS = (
    "producto_id", "producto", "categoria", "campo", "cambios", "inicial", "final", "variacion", "variacion_porcentual",
    "vigente",
)


def csv_evolucion_precios(
    fecha_desde: str | None, fecha_hasta: str | None, producto_id: int | None, categoria_id: int | None
) -> bytes:
    """La evolución por producto de `/analitica/precios` (mismos filtros y mismo servicio)."""
    analisis = servicio_analitica.analizar_precios(fecha_desde, fecha_hasta, producto_id, categoria_id)
    filas = (
        (
            numero(e.producto_id), e.producto_nombre, e.categoria_nombre, e.campo, numero(e.cambios),
            dinero(e.inicial_centavos), dinero(e.final_centavos), dinero(e.variacion_centavos),
            _porcentaje(e.variacion_porcentual), dinero(e.vigente_centavos),
        )
        for e in analisis.evoluciones
    )
    return generar_csv(COLUMNAS_EVOLUCION_PRECIOS, filas)


COLUMNAS_AUDITORIA = ("id", "fecha", "usuario_id", "usuario", "accion", "entidad", "entidad_id", "resumen")


def _fila_auditoria(entrada: EntradaAuditoria) -> tuple[str | None, ...]:
    return (
        numero(entrada.id), entrada.fecha, numero(entrada.usuario_id), entrada.usuario_nombre_completo,
        entrada.accion, entrada.entidad, numero(entrada.entidad_id), entrada.resumen,
    )


def csv_auditoria(
    accion: str | None, usuario_id: int | None, fecha_desde: str | None, fecha_hasta: str | None
) -> Iterator[bytes]:
    """Las columnas propias de `auditoria` con el nombre del usuario resuelto: el mismo filtro de la pantalla,
    sin su paginación. La tabla no guarda secretos (ver `domain.auditoria`).

    El primer lote se trae acá y no al recorrer el resultado: un filtro inválido o una base no disponible
    tienen que ser un error normal, antes de que la descarga ya haya empezado con un 200.
    """
    lotes = servicio_auditoria.lotes_para_exportar(accion, usuario_id, fecha_desde, fecha_hasta)
    primero = next(lotes, [])
    return generar_csv_por_lotes(COLUMNAS_AUDITORIA, ([_fila_auditoria(e) for e in lote] for lote in chain([primero], lotes)))


COLUMNAS_GASTOS = (
    "gasto", "fecha", "estado", "categoria", "descripcion", "importe", "medio_pago", "referencia", "responsable",
    "observaciones", "usuario", "egreso_caja", "motivo_anulacion", "fecha_anulacion", "reintegro_caja",
)


def csv_gastos(filtros) -> bytes:
    """Gastos operativos que cumplen los filtros del listado (sin paginar). Los anulados salen con su estado y
    motivo: la suma del período debe hacerse solo sobre los VIGENTES."""
    gastos = servicio_gastos.listar_para_exportar(filtros)
    return generar_csv(
        COLUMNAS_GASTOS,
        (
            (
                numero(g.id), g.fecha, g.estado, g.categoria_etiqueta, g.descripcion, dinero(g.importe_centavos),
                g.medio_pago_etiqueta, g.referencia, g.responsable, g.observaciones, g.usuario_nombre,
                numero(g.caja_movimiento_id), g.motivo_anulacion, g.anulado_en, numero(g.caja_movimiento_reintegro_id),
            )
            for g in gastos
        ),
    )


COLUMNAS_RESULTADO = (
    "periodo", "unidades", "facturado", "anulaciones", "anulado", "costo_conocido", "unidades_sin_costo",
    "margen_bruto", "costo_real", "gastos_operativos", "resultado_operativo_estimado", "resultado_porcentual",
)


def csv_resultado(fecha_desde: str | None, fecha_hasta: str | None, granularidad: str | None) -> bytes:
    """El mismo agregado de `/analitica/resultado` (mismo servicio). Última fila: el total del período. Un
    período sin costo conocido sale con margen y resultado vacíos, nunca con 0."""
    analisis = servicio_analitica.analizar_resultado(fecha_desde, fecha_hasta, granularidad)

    def fila(p):
        return (
            p.etiqueta, numero(p.margen.unidades), dinero(p.facturado_centavos), numero(p.anulaciones_cantidad),
            dinero(p.anulaciones_centavos), dinero(p.costo_conocido_centavos if p.margen.unidades_con_costo_ref else None),
            numero(p.unidades_sin_costo), dinero(p.margen_bruto_centavos),
            dinero(p.margen.costo_real_centavos if p.margen.unidades_costo_real else None), dinero(p.gastos_centavos),
            dinero(p.resultado_operativo_centavos), _porcentaje(p.resultado_operativo_porcentual),
        )

    return generar_csv(COLUMNAS_RESULTADO, chain((fila(p) for p in analisis.periodos), [fila(analisis.total)]))


COLUMNAS_CLIENTES = (
    "cliente_id", "cliente", "activo", "ventas", "total_vendido", "ticket_promedio", "unidades", "primera_venta",
    "ultima_venta", "dias_con_compras", "compra_cada_dias", "deuda_actual", "cobros", "cobrado",
)


def csv_clientes(fecha_desde: str | None, fecha_hasta: str | None) -> bytes:
    """El mismo agregado de `/reportes/clientes` (ventas activas con cliente identificado)."""
    reporte = servicio_reportes.generar_reporte_clientes(fecha_desde, fecha_hasta)
    dias = reporte.dias_del_periodo
    return generar_csv(
        COLUMNAS_CLIENTES,
        (
            (
                numero(f.cliente_id), f.cliente_nombre, "si" if f.activo else "no", numero(f.cantidad_ventas),
                dinero(f.total_centavos), dinero(f.ticket_promedio_centavos), numero(f.unidades), f.primera_venta,
                f.ultima_venta, numero(f.dias_con_compras),
                Numero(str(f.compras_cada_dias(dias))) if f.compras_cada_dias(dias) is not None else None,
                dinero(f.saldo_centavos), numero(f.cantidad_cobros), dinero(f.cobrado_centavos),
            )
            for f in reporte.filas
        ),
    )
