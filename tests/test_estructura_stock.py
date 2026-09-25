"""Guardia estructural del stock (V1.4): el inventario físico depende de que `productos.stock_actual`
se escriba únicamente por un punto autorizado (`actualizar_stock_en_conexion`), porque el trigger de
`version_stock` y la regla "el inventario no sobrescribe movimientos posteriores" se apoyan en eso.

Si este test falla es porque apareció un escritor paralelo de stock: hay que hacerlo pasar por el
punto autorizado (y cubrir su efecto sobre el inventario), no relajar el test."""

import inspect
import itertools
import re
from pathlib import Path

from services import servicio_compras, servicio_stock, servicio_ventas

RAIZ = Path(__file__).resolve().parent.parent
CARPETAS_PRODUCCION = ("db", "domain", "services", "interfaces")

# Sentencias que escriben `productos`: UPDATE ... SET (hasta el WHERE/cierre) y los reemplazos de fila.
_UPDATE_PRODUCTOS = re.compile(r"UPDATE\s+productos\s+SET(?P<set>.*?)(?:WHERE|RETURNING|\"\"\"|\"\s*,|\"\s*\))", re.S | re.I)
_REEMPLAZO_PRODUCTOS = re.compile(r"(INSERT\s+OR\s+REPLACE\s+INTO|REPLACE\s+INTO)\s+productos", re.I)
_ESCRIBE_STOCK = re.compile(r"\bstock_actual\s*=")


def _archivos_de_produccion():
    for carpeta in CARPETAS_PRODUCCION:
        for archivo in sorted((RAIZ / carpeta).rglob("*.py")):
            yield archivo


def test_un_unico_update_de_stock_actual_en_todo_el_codigo_de_produccion():
    escritores = []
    for archivo in _archivos_de_produccion():
        for coincidencia in _UPDATE_PRODUCTOS.finditer(archivo.read_text(encoding="utf-8")):
            if _ESCRIBE_STOCK.search(coincidencia.group("set")):
                escritores.append(archivo.relative_to(RAIZ).as_posix())

    assert escritores == ["db/repositorios/productos.py"]


def test_el_detector_reconoce_un_escritor_paralelo_de_stock():
    """El detector no es vacuo: reconoce un UPDATE de stock aunque venga en otra forma de SQL."""
    for sql in (
        "UPDATE productos SET stock_actual = ? WHERE id = ?",
        "UPDATE productos\n   SET nombre = ?, stock_actual = stock_actual - 1\n WHERE id = ?",
        'conexion.execute("UPDATE productos SET stock_actual = 0")',
    ):
        coincidencia = _UPDATE_PRODUCTOS.search(sql)
        assert coincidencia and _ESCRIBE_STOCK.search(coincidencia.group("set")), sql
    seguro = _UPDATE_PRODUCTOS.search("UPDATE productos SET nombre = ?, stock_minimo = ? WHERE id = ?")
    assert seguro and not _ESCRIBE_STOCK.search(seguro.group("set"))


def test_no_hay_reemplazos_de_filas_de_productos():
    for archivo in _archivos_de_produccion():
        assert not _REEMPLAZO_PRODUCTOS.search(archivo.read_text(encoding="utf-8")), archivo


def test_el_update_con_columna_dinamica_solo_admite_columnas_de_precio():
    from db.repositorios import productos as repositorio_productos

    assert set(repositorio_productos._COLUMNA_DE_PRECIO.values()) == {
        "precio_costo_centavos",
        "precio_venta_centavos",
    }


def test_los_cuatro_escritores_de_stock_pasan_por_el_punto_autorizado():
    for funcion in (
        servicio_ventas.registrar_venta,
        servicio_ventas.anular_venta,
        servicio_compras.registrar_compra,
        servicio_stock.aplicar_ajuste_en_conexion,
    ):
        assert "actualizar_stock_en_conexion" in inspect.getsource(funcion), funcion.__name__


def test_el_ajuste_manual_y_el_inventario_comparten_el_nucleo_de_ajuste():
    assert "aplicar_ajuste_en_conexion" in inspect.getsource(servicio_stock.ajustar_stock)
    from services import servicio_inventario

    codigo = inspect.getsource(servicio_inventario.confirmar_inventario)
    assert "servicio_stock.aplicar_ajuste_en_conexion" in codigo
    assert not _ESCRIBE_STOCK.search(codigo) and "actualizar_stock" not in codigo


def test_ningun_servicio_ni_interfaz_ejecuta_sql_sobre_stock():
    for carpeta in ("services", "interfaces"):
        for archivo in sorted((RAIZ / carpeta).rglob("*.py")):
            assert not _UPDATE_PRODUCTOS.search(archivo.read_text(encoding="utf-8")), archivo


def test_los_servicios_de_v14_no_importan_sqlite3():
    """CLAUDE.md §3: solo `db/` importa `sqlite3`. Los servicios anotan la conexión con `ConexionBD`."""
    for nombre in ("servicio_inventario", "servicio_proveedores", "servicio_stock"):
        texto = (RAIZ / "services" / f"{nombre}.py").read_text(encoding="utf-8")
        assert not re.search(r"^\s*(import sqlite3|from sqlite3)", texto, re.M), nombre


def test_en_tiempo_de_ejecucion_todo_cambio_de_stock_sale_de_la_sentencia_autorizada(
    base_datos_temporal, caja_abierta, monkeypatch
):
    """Prueba dinámica (no textual): se traza el SQL REAL de un recorrido que toca stock de todas las formas
    conocidas (alta, venta, anulación, compra, ajuste, recuento, edición, precios, baja, importación) y se
    verifica que la única sentencia que asigna `stock_actual` es la autorizada, y que cada una movió la
    versión. Cubre SQL armado dinámicamente, que un detector textual no vería."""
    import sqlite3

    from domain.compra import ItemCompra
    from domain.venta import ItemVenta
    from services import servicio_compras, servicio_importacion, servicio_inventario, servicio_proveedores, servicio_ventas
    from tests.utilidades_clientes import consultar, crear_owner, crear_producto

    sentencias: list[str] = []
    conectar_original = sqlite3.connect

    def conectar_con_traza(*args, **kwargs):
        conexion = conectar_original(*args, **kwargs)
        conexion.set_trace_callback(sentencias.append)
        return conexion

    owner = crear_owner()
    producto = crear_producto("7790000000001", stock=50)
    otro = crear_producto("7790000000002", stock=5)
    monkeypatch.setattr(sqlite3, "connect", conectar_con_traza)

    venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 2)], "EFECTIVO", usuario_id=owner.id)
    servicio_ventas.anular_venta(venta.id, "ERROR_CARGA", None, owner.id)
    proveedor = servicio_proveedores.crear_proveedor("P")
    servicio_compras.registrar_compra(proveedor.id, owner.id, [ItemCompra(producto.id, 3, 10)])
    servicio_stock.ajustar_stock(producto.id, -1, "MERMA", owner.id)
    inventario = servicio_inventario.crear_inventario(owner.id, [producto.id])
    servicio_inventario.registrar_conteo(inventario.id, producto.id, 40, owner.id)
    servicio_inventario.confirmar_inventario(inventario.id, owner.id)
    servicio_stock.actualizar_producto(producto.id, producto.codigo_barras, "Otro nombre", 20, 300, 2)
    servicio_stock.eliminar_producto(otro.id, usuario_id=owner.id)
    contenido = (
        "codigo_barras,nombre,precio_costo,precio_venta,stock_actual,stock_minimo\n"
        f"{producto.codigo_barras},Renombrado,1.00,2.00,999,1\n"
        "7790000000077,Nuevo,1.00,2.00,7,1\n"
    ).encode("utf-8")
    servicio_importacion.procesar_archivo(
        "productos.csv", contenido, modo=servicio_importacion.MODO_CREAR_Y_ACTUALIZAR, usuario_id=owner.id
    )

    escrituras_de_stock = [
        s for s in sentencias if re.search(r"UPDATE\s+productos\s+SET[^;]*\bstock_actual\s*=", s, re.I | re.S)
        or re.search(r"DO\s+UPDATE\s+SET[^;]*\bstock_actual\s*=", s, re.I | re.S)
        or re.search(r"REPLACE\s+INTO\s+productos", s, re.I)
    ]
    autorizada = re.compile(r"^\s*UPDATE productos\s+SET stock_actual = \d+,\s+fecha_actualizacion = datetime\(", re.S)
    assert escrituras_de_stock, "el recorrido no ejerció ninguna escritura de stock (el test sería vacuo)"
    assert all(autorizada.match(s) for s in escrituras_de_stock), [s for s in escrituras_de_stock if not autorizada.match(s)]
    # venta, anulación, compra, ajuste manual y RECUENTO: cinco escrituras autorizadas, cinco versiones.
    # SQLite repite en la traza la sentencia que dispara el trigger: se colapsan repeticiones consecutivas.
    escrituras_de_stock = [sentencia for sentencia, _ in itertools.groupby(escrituras_de_stock)]
    assert len(escrituras_de_stock) == 5
    assert consultar(base_datos_temporal, "SELECT version_stock FROM productos WHERE id = ?", (producto.id,))[0][0] == 5
    assert consultar(base_datos_temporal, "SELECT stock_actual FROM productos WHERE id = ?", (producto.id,))[0][0] == 40
