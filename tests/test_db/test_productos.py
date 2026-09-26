"""Pruebas de integración de db.repositorios.productos para las
funciones agregadas en Fase 4B (actualización de costo dentro de una
transacción compartida). El resto del repositorio ya está cubierto
end-to-end por tests/test_servicio_stock.py."""

import pytest

from db.conexion import obtener_conexion
from db.repositorios import productos as repositorio_productos
from domain.producto import Producto
from excepciones import ProductoNoEncontradoError


def test_actualizar_costo_con_evento_modifica_solo_el_costo_y_devuelve_el_evento(base_datos_temporal):
    producto = repositorio_productos.crear_producto(
        Producto(codigo_barras="7790000000001", nombre="Alfajor", precio_costo_centavos=100, precio_venta_centavos=200)
    )

    with obtener_conexion() as conexion:
        evento_id = repositorio_productos.actualizar_costo_con_evento_en_conexion(conexion, producto.id, 150)
        sin_cambio = repositorio_productos.actualizar_costo_con_evento_en_conexion(conexion, producto.id, 150)

    actualizado = repositorio_productos.obtener_por_id(producto.id)
    assert evento_id is not None and sin_cambio is None  # sin cambio de valor no hay evento
    assert actualizado.precio_costo_centavos == 150
    assert actualizado.precio_venta_centavos == 200  # sin cambios
    assert actualizado.nombre == "Alfajor"  # sin cambios


def test_actualizar_costo_con_evento_de_producto_inexistente_falla(base_datos_temporal):
    with obtener_conexion() as conexion:
        with pytest.raises(ProductoNoEncontradoError):
            repositorio_productos.actualizar_costo_con_evento_en_conexion(conexion, 9999, 150)


class TestObtenerPorIdEnConexionIncluyendoInactivos:
    """Migración 013 (anulación de ventas): restaurar stock no puede
    depender de que el producto siga activo en el catálogo."""

    def test_encuentra_un_producto_activo(self, base_datos_temporal):
        producto = repositorio_productos.crear_producto(
            Producto(codigo_barras="7790000000001", nombre="Alfajor", precio_costo_centavos=100, precio_venta_centavos=200)
        )
        with obtener_conexion() as conexion:
            encontrado = repositorio_productos.obtener_por_id_en_conexion_incluyendo_inactivos(conexion, producto.id)
        assert encontrado is not None
        assert encontrado.id == producto.id

    def test_encuentra_un_producto_inactivo(self, base_datos_temporal):
        producto = repositorio_productos.crear_producto(
            Producto(codigo_barras="7790000000001", nombre="Alfajor", precio_costo_centavos=100, precio_venta_centavos=200)
        )
        with obtener_conexion() as conexion:
            conexion.execute("UPDATE productos SET activo = 0 WHERE id = ?", (producto.id,))

        with obtener_conexion() as conexion:
            encontrado = repositorio_productos.obtener_por_id_en_conexion_incluyendo_inactivos(conexion, producto.id)

        assert encontrado is not None
        assert encontrado.activo is False

    def test_inexistente_devuelve_none(self, base_datos_temporal):
        with obtener_conexion() as conexion:
            assert repositorio_productos.obtener_por_id_en_conexion_incluyendo_inactivos(conexion, 9999) is None

    def test_obtener_por_id_en_conexion_normal_no_cambio_de_comportamiento(self, base_datos_temporal):
        """`obtener_por_id_en_conexion` (sin `_incluyendo_inactivos`)
        sigue exigiendo `activo = 1`, sin ningún cambio -- ver auditoría
        de diseño de anulación de ventas."""
        producto = repositorio_productos.crear_producto(
            Producto(codigo_barras="7790000000001", nombre="Alfajor", precio_costo_centavos=100, precio_venta_centavos=200)
        )
        with obtener_conexion() as conexion:
            conexion.execute("UPDATE productos SET activo = 0 WHERE id = ?", (producto.id,))

        with obtener_conexion() as conexion:
            assert repositorio_productos.obtener_por_id_en_conexion(conexion, producto.id) is None


class TestListarValorizables:
    """Valorización de Inventario: activos e inactivos por igual,
    mientras `stock_actual > 0` -- un producto discontinuado con
    mercadería remanente sigue siendo capital real inmovilizado."""

    def test_excluye_producto_con_stock_cero(self, base_datos_temporal):
        repositorio_productos.crear_producto(
            Producto(
                codigo_barras="7790000000001", nombre="Sin stock",
                precio_costo_centavos=100, precio_venta_centavos=200, stock_actual=0,
            )
        )

        assert repositorio_productos.listar_valorizables() == []

    def test_incluye_producto_activo_con_stock(self, base_datos_temporal):
        producto = repositorio_productos.crear_producto(
            Producto(
                codigo_barras="7790000000001", nombre="Alfajor",
                precio_costo_centavos=100, precio_venta_centavos=200, stock_actual=5,
            )
        )

        resultado = repositorio_productos.listar_valorizables()

        assert [p.id for p in resultado] == [producto.id]

    def test_incluye_producto_inactivo_con_stock(self, base_datos_temporal):
        producto = repositorio_productos.crear_producto(
            Producto(
                codigo_barras="7790000000001", nombre="Discontinuado",
                precio_costo_centavos=100, precio_venta_centavos=200, stock_actual=3,
            )
        )
        with obtener_conexion() as conexion:
            conexion.execute("UPDATE productos SET activo = 0 WHERE id = ?", (producto.id,))

        resultado = repositorio_productos.listar_valorizables()

        assert len(resultado) == 1
        assert resultado[0].id == producto.id
        assert resultado[0].activo is False

    def test_sin_productos_con_stock_devuelve_lista_vacia(self, base_datos_temporal):
        assert repositorio_productos.listar_valorizables() == []
