"""Pruebas de integración de services.servicio_importacion contra una base
de datos SQLite real (temporal y aislada, ver tests/conftest.py)."""

import io

import pytest
from openpyxl import Workbook

from excepciones import ArchivoImportacionInvalidoError
from services import servicio_categorias, servicio_importacion, servicio_stock


def _csv_valido() -> bytes:
    contenido = (
        "codigo_barras,nombre,precio_costo,precio_venta,stock_actual,stock_minimo\n"
        "7790000000001,Alfajor,100.00,200.00,10,2\n"
        "7790000000002,Gaseosa,200,350,5,1\n"
    )
    return contenido.encode("utf-8")


def _xlsx_bytes(filas: list[tuple]) -> bytes:
    libro = Workbook()
    hoja = libro.active
    hoja.append(
        ["codigo_barras", "nombre", "precio_costo", "precio_venta", "stock_actual", "stock_minimo"]
    )
    for fila in filas:
        hoja.append(list(fila))
    buffer = io.BytesIO()
    libro.save(buffer)
    return buffer.getvalue()


def test_importar_csv_valido_crea_todos_los_productos(base_datos_temporal):
    resultado = servicio_importacion.procesar_archivo("productos.csv", _csv_valido())

    assert resultado.total_filas == 2
    assert resultado.importados == 2
    assert resultado.duplicados == 0
    assert resultado.errores == []

    assert servicio_stock.buscar_por_codigo_barras("7790000000001").nombre == "Alfajor"
    assert servicio_stock.buscar_por_codigo_barras("7790000000002").nombre == "Gaseosa"


def test_importar_csv_omite_codigos_de_barras_duplicados(base_datos_temporal):
    servicio_stock.registrar_producto("7790000000001", "Ya existía", 50, 90)

    resultado = servicio_importacion.procesar_archivo("productos.csv", _csv_valido())

    assert resultado.importados == 1
    assert resultado.duplicados == 1
    # No se sobrescribió el producto existente.
    assert servicio_stock.buscar_por_codigo_barras("7790000000001").nombre == "Ya existía"


def test_importar_csv_con_fila_invalida_no_aborta_el_resto(base_datos_temporal):
    contenido = (
        "codigo_barras,nombre,precio_costo,precio_venta,stock_actual,stock_minimo\n"
        "7790000000001,Alfajor,100,200,10,2\n"
        "7790000000002,,100,200,10,2\n"  # nombre vacío -> inválido
        "7790000000003,Gaseosa,-50,200,10,2\n"  # precio negativo -> inválido
    ).encode("utf-8")

    resultado = servicio_importacion.procesar_archivo("productos.csv", contenido, todo_o_nada=False)

    assert resultado.total_filas == 3
    assert resultado.importados == 1
    assert len(resultado.errores) == 2
    assert servicio_stock.buscar_por_codigo_barras("7790000000001") is not None
    assert servicio_stock.buscar_por_codigo_barras("7790000000002") is None
    assert servicio_stock.buscar_por_codigo_barras("7790000000003") is None


def test_importar_csv_sin_columnas_obligatorias_falla(base_datos_temporal):
    contenido = "codigo,descripcion\n123,Algo\n".encode("utf-8")

    with pytest.raises(ArchivoImportacionInvalidoError):
        servicio_importacion.procesar_archivo("productos.csv", contenido)


def test_importar_extension_no_soportada_falla(base_datos_temporal):
    with pytest.raises(ArchivoImportacionInvalidoError):
        servicio_importacion.procesar_archivo("productos.txt", b"cualquier cosa")


def test_importar_csv_sin_columnas_de_categoria_y_unidad_sigue_funcionando(base_datos_temporal):
    """El archivo "viejo" (Fase 1/2), sin las columnas nuevas, tiene que
    seguir importando exactamente igual que antes de Fase 3C."""
    resultado = servicio_importacion.procesar_archivo("productos.csv", _csv_valido())

    assert resultado.importados == 2
    producto = servicio_stock.buscar_por_codigo_barras("7790000000001")
    assert producto.categoria_id is None
    assert producto.unidad_medida == "UNIDAD"


def test_importar_csv_con_categoria_valida_la_asigna(base_datos_temporal):
    categoria = servicio_categorias.crear_categoria("Bebidas")
    contenido = (
        "codigo_barras,nombre,precio_costo,precio_venta,stock_actual,stock_minimo,categoria,unidad_medida\n"
        "7790000000001,Gaseosa,100,200,10,2,Bebidas,LITRO\n"
    ).encode("utf-8")

    resultado = servicio_importacion.procesar_archivo("productos.csv", contenido)

    assert resultado.importados == 1
    assert resultado.errores == []
    producto = servicio_stock.buscar_por_codigo_barras("7790000000001")
    assert producto.categoria_id == categoria.id
    assert producto.unidad_medida == "LITRO"


def test_importar_csv_con_categoria_inexistente_es_fila_invalida(base_datos_temporal):
    contenido = (
        "codigo_barras,nombre,precio_costo,precio_venta,stock_actual,stock_minimo,categoria\n"
        "7790000000001,Gaseosa,100,200,10,2,NoExiste\n"
        "7790000000002,Alfajor,100,200,10,2,\n"
    ).encode("utf-8")

    resultado = servicio_importacion.procesar_archivo("productos.csv", contenido, todo_o_nada=False)

    assert resultado.importados == 1  # la fila sin categoría sí se importa
    assert len(resultado.errores) == 1
    assert servicio_stock.buscar_por_codigo_barras("7790000000001") is None
    assert servicio_stock.buscar_por_codigo_barras("7790000000002") is not None


def test_importar_csv_con_columna_categoria_vacia_deja_sin_categoria(base_datos_temporal):
    contenido = (
        "codigo_barras,nombre,precio_costo,precio_venta,stock_actual,stock_minimo,categoria\n"
        "7790000000001,Gaseosa,100,200,10,2,\n"
    ).encode("utf-8")

    resultado = servicio_importacion.procesar_archivo("productos.csv", contenido)

    assert resultado.importados == 1
    assert servicio_stock.buscar_por_codigo_barras("7790000000001").categoria_id is None


def test_importar_csv_con_unidad_valida_la_asigna(base_datos_temporal):
    contenido = (
        "codigo_barras,nombre,precio_costo,precio_venta,stock_actual,stock_minimo,unidad_medida\n"
        "7790000000001,Queso,100,200,10,2,kg\n"
    ).encode("utf-8")

    resultado = servicio_importacion.procesar_archivo("productos.csv", contenido)

    assert resultado.importados == 1
    assert servicio_stock.buscar_por_codigo_barras("7790000000001").unidad_medida == "KG"


def test_importar_csv_con_unidad_invalida_es_fila_invalida(base_datos_temporal):
    contenido = (
        "codigo_barras,nombre,precio_costo,precio_venta,stock_actual,stock_minimo,unidad_medida\n"
        "7790000000001,Queso,100,200,10,2,TONELADA\n"
    ).encode("utf-8")

    resultado = servicio_importacion.procesar_archivo("productos.csv", contenido)

    assert resultado.importados == 0
    assert len(resultado.errores) == 1
    assert servicio_stock.buscar_por_codigo_barras("7790000000001") is None


def test_importar_csv_con_unidad_vacia_usa_unidad_por_defecto(base_datos_temporal):
    contenido = (
        "codigo_barras,nombre,precio_costo,precio_venta,stock_actual,stock_minimo,unidad_medida\n"
        "7790000000001,Alfajor,100,200,10,2,\n"
    ).encode("utf-8")

    resultado = servicio_importacion.procesar_archivo("productos.csv", contenido)

    assert resultado.importados == 1
    assert servicio_stock.buscar_por_codigo_barras("7790000000001").unidad_medida == "UNIDAD"


def test_importar_xlsx_valido_crea_productos(base_datos_temporal):
    contenido = _xlsx_bytes(
        [
            ("7790000000001", "Alfajor", 100, 200, 10, 2),
            ("7790000000002", "Gaseosa", 200, 350, 5, 1),
        ]
    )

    resultado = servicio_importacion.procesar_archivo("productos.xlsx", contenido)

    assert resultado.importados == 2
    assert resultado.duplicados == 0
    assert resultado.errores == []
    assert servicio_stock.buscar_por_codigo_barras("7790000000002").precio_venta_centavos == 35000
