"""Casos de uso de exportación del catálogo de productos a CSV o Excel.

Contraparte de lectura de `services.servicio_importacion`: genera un
archivo con exactamente las mismas columnas que espera la importación
(`servicio_importacion.COLUMNAS_REQUERIDAS` + las dos opcionales,
`categoria`/`unidad_medida`), para que el ciclo exportar → editar en
una planilla → volver a importar funcione sin tener que adaptar el
archivo a mano.

Solo exporta productos **activos** (`servicio_stock.listar_todos()` ya
filtra `activo=1`, mismo criterio que la grilla del POS): un producto
dado de baja no tiene sentido reimportarlo como si fuera de alta.

Los precios se exportan con `domain.dinero.centavos_a_texto` (texto
decimal simple, ej. "150.50"), nunca con el formato localizado de
pantalla (`centavos_a_texto_localizado`, que usa "." de miles y ","
decimal): ese formato no es el que espera `domain.dinero.texto_a_centavos`
al reimportar (ver su docstring en `interfaces/web/plantillas.py`).

Todo se genera en memoria (`io.StringIO`/`io.BytesIO`), sin escribir
ningún archivo temporal en disco: la ruta HTTP devuelve los bytes
directamente en la respuesta.
"""

import csv
import io

from openpyxl import Workbook

from domain.dinero import centavos_a_texto
from domain.producto import Producto
from services import servicio_categorias, servicio_stock
from services.servicio_importacion import COLUMNAS_REQUERIDAS

# Mismas dos columnas opcionales que ya acepta la importación (ver
# `servicio_importacion._importar_fila`): no están en `COLUMNAS_REQUERIDAS`
# porque son opcionales al importar, pero sí se incluyen siempre al
# exportar -- un archivo exportado nunca depende de que quien lo abra
# sepa qué columnas agregar a mano para poder reimportarlo.
COLUMNAS_OPCIONALES = ("categoria", "unidad_medida")
COLUMNAS_EXPORTACION = COLUMNAS_REQUERIDAS + COLUMNAS_OPCIONALES


def _fila_exportable(producto: Producto, categorias_por_id: dict[int, str]) -> list[str]:
    """Una fila de exportación, en el mismo orden que `COLUMNAS_EXPORTACION`.

    Todos los valores se devuelven como texto, incluso `stock_actual`/
    `stock_minimo` (que no son dinero): un único camino para CSV y XLSX,
    y evita que openpyxl guarde un precio como número de punto flotante
    en la celda -- la razón por la que `domain.dinero` existe en primer
    lugar (ver su docstring). Al reimportar da igual: `texto_a_centavos`
    e `int(float(...))` ya parsean texto, no un tipo numérico nativo.
    """
    nombre_categoria = categorias_por_id.get(producto.categoria_id, "") if producto.categoria_id else ""
    return [
        producto.codigo_barras,
        producto.nombre,
        centavos_a_texto(producto.precio_costo_centavos),
        centavos_a_texto(producto.precio_venta_centavos),
        str(producto.stock_actual),
        str(producto.stock_minimo),
        nombre_categoria,
        producto.unidad_medida,
    ]


def _productos_y_categorias() -> tuple[list[Producto], dict[int, str]]:
    """Productos activos + mapa id->nombre de categoría (incluye
    categorías dadas de baja: un producto puede seguir referenciando una
    que ya no está activa, mismo criterio que ya usa
    `interfaces.web.rutas.productos.listar_productos` para no mostrar
    "categoría eliminada" en productos viejos)."""
    productos = servicio_stock.listar_todos()
    categorias_por_id = {categoria.id: categoria.nombre for categoria in servicio_categorias.listar_todas()}
    return productos, categorias_por_id


def generar_csv_productos() -> bytes:
    """Catálogo de productos activos como CSV, listo para descargar.

    Codificado con BOM UTF-8 (`utf-8-sig`): así Excel en Windows
    reconoce la codificación y muestra bien nombres con tildes/ñ, y
    `servicio_importacion._extraer_filas_csv` decodifica ese mismo BOM
    sin problema (ya usa `utf-8-sig` para leer, ver su código).
    """
    productos, categorias_por_id = _productos_y_categorias()

    buffer_texto = io.StringIO()
    escritor = csv.writer(buffer_texto)
    escritor.writerow(COLUMNAS_EXPORTACION)
    for producto in productos:
        escritor.writerow(_fila_exportable(producto, categorias_por_id))

    return buffer_texto.getvalue().encode("utf-8-sig")


def generar_xlsx_productos() -> bytes:
    """Catálogo de productos activos como XLSX, listo para descargar."""
    productos, categorias_por_id = _productos_y_categorias()

    libro = Workbook()
    hoja = libro.active
    hoja.append(list(COLUMNAS_EXPORTACION))
    for producto in productos:
        hoja.append(_fila_exportable(producto, categorias_por_id))

    buffer_binario = io.BytesIO()
    libro.save(buffer_binario)
    return buffer_binario.getvalue()
