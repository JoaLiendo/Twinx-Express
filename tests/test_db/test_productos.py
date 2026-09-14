"""Pruebas de integración de db.repositorios.productos para las
funciones agregadas en Fase 4B (actualización de costo dentro de una
transacción compartida). El resto del repositorio ya está cubierto
end-to-end por tests/test_servicio_stock.py."""

import pytest

from db.conexion import obtener_conexion
from db.repositorios import productos as repositorio_productos
from domain.producto import Producto
from excepciones import ProductoNoEncontradoError


def test_actualizar_costo_en_conexion_modifica_solo_el_costo(base_datos_temporal):
    producto = repositorio_productos.crear_producto(
        Producto(codigo_barras="7790000000001", nombre="Alfajor", precio_costo_centavos=100, precio_venta_centavos=200)
    )

    with obtener_conexion() as conexion:
        actualizado = repositorio_productos.actualizar_costo_en_conexion(conexion, producto.id, 150)

    assert actualizado.precio_costo_centavos == 150
    assert actualizado.precio_venta_centavos == 200  # sin cambios
    assert actualizado.nombre == "Alfajor"  # sin cambios


def test_actualizar_costo_en_conexion_de_producto_inexistente_falla(base_datos_temporal):
    with obtener_conexion() as conexion:
        with pytest.raises(ProductoNoEncontradoError):
            repositorio_productos.actualizar_costo_en_conexion(conexion, 9999, 150)
