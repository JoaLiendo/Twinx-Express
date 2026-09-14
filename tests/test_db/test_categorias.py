"""Pruebas de integración de db.repositorios.categorias contra una base de
datos SQLite real (temporal y aislada, ver tests/conftest.py)."""

import pytest

from db.repositorios import categorias as repositorio_categorias
from db.repositorios import productos as repositorio_productos
from domain.categoria import Categoria
from domain.producto import Producto
from excepciones import CategoriaNoEncontradaError, NombreCategoriaDuplicadoError


def test_crear_categoria_persiste_y_asigna_id(base_datos_temporal):
    creada = repositorio_categorias.crear_categoria(Categoria(nombre="Bebidas"))

    assert creada.id is not None
    assert creada.nombre == "Bebidas"
    assert creada.activa is True
    assert creada.fecha_creacion is not None


def test_nombre_duplicado_lanza_error(base_datos_temporal):
    repositorio_categorias.crear_categoria(Categoria(nombre="Bebidas"))

    with pytest.raises(NombreCategoriaDuplicadoError):
        repositorio_categorias.crear_categoria(Categoria(nombre="Bebidas"))


def test_obtener_por_id_devuelve_categoria_existente(base_datos_temporal):
    creada = repositorio_categorias.crear_categoria(Categoria(nombre="Bebidas"))

    encontrada = repositorio_categorias.obtener_por_id(creada.id)

    assert encontrada is not None
    assert encontrada.nombre == "Bebidas"


def test_obtener_por_id_inexistente_devuelve_none(base_datos_temporal):
    assert repositorio_categorias.obtener_por_id(9999) is None


def test_obtener_por_nombre_devuelve_categoria_existente(base_datos_temporal):
    repositorio_categorias.crear_categoria(Categoria(nombre="Bebidas"))

    encontrada = repositorio_categorias.obtener_por_nombre("Bebidas")

    assert encontrada is not None


def test_obtener_por_nombre_inexistente_devuelve_none(base_datos_temporal):
    assert repositorio_categorias.obtener_por_nombre("no-existe") is None


def test_listar_activas_no_incluye_las_dadas_de_baja(base_datos_temporal):
    activa = repositorio_categorias.crear_categoria(Categoria(nombre="Bebidas"))
    repositorio_categorias.crear_categoria(Categoria(nombre="Golosinas"))
    repositorio_categorias.eliminar_categoria(activa.id)  # sin productos, se borra físicamente
    inactiva = repositorio_categorias.crear_categoria(Categoria(nombre="Limpieza"))
    repositorio_categorias.crear_categoria(Categoria(nombre="X"))  # placeholder para forzar baja lógica más abajo

    nombres = [c.nombre for c in repositorio_categorias.listar_activas()]
    assert "Bebidas" not in nombres  # fue borrada físicamente


def test_listar_todas_incluye_activas_e_inactivas(base_datos_temporal):
    repositorio_categorias.crear_categoria(Categoria(nombre="Bebidas"))
    inactiva = repositorio_categorias.crear_categoria(Categoria(nombre="Golosinas"))

    # Forzamos baja lógica asociándole un producto.
    producto = repositorio_productos.crear_producto(
        Producto(
            codigo_barras="7790000000001",
            nombre="Chicle",
            precio_costo_centavos=10,
            precio_venta_centavos=20,
            categoria_id=inactiva.id,
        )
    )
    repositorio_categorias.eliminar_categoria(inactiva.id)

    todas = {c.nombre: c.activa for c in repositorio_categorias.listar_todas()}
    assert todas == {"Bebidas": True, "Golosinas": False}
    assert repositorio_productos.obtener_por_id(producto.id).categoria_id == inactiva.id


def test_eliminar_categoria_sin_productos_la_borra_fisicamente(base_datos_temporal):
    categoria = repositorio_categorias.crear_categoria(Categoria(nombre="Bebidas"))

    fue_baja_logica = repositorio_categorias.eliminar_categoria(categoria.id)

    assert fue_baja_logica is False
    assert repositorio_categorias.obtener_por_id(categoria.id) is None


def test_eliminar_categoria_utilizada_por_productos_hace_baja_logica(base_datos_temporal):
    categoria = repositorio_categorias.crear_categoria(Categoria(nombre="Bebidas"))
    producto = repositorio_productos.crear_producto(
        Producto(
            codigo_barras="7790000000001",
            nombre="Gaseosa",
            precio_costo_centavos=100,
            precio_venta_centavos=200,
            categoria_id=categoria.id,
        )
    )

    fue_baja_logica = repositorio_categorias.eliminar_categoria(categoria.id)

    assert fue_baja_logica is True
    encontrada = repositorio_categorias.obtener_por_id(categoria.id)
    assert encontrada is not None
    assert encontrada.activa is False
    # El producto sigue apuntando a la categoría (no se rompió la relación).
    assert repositorio_productos.obtener_por_id(producto.id).categoria_id == categoria.id


def test_eliminar_categoria_inexistente_falla(base_datos_temporal):
    with pytest.raises(CategoriaNoEncontradaError):
        repositorio_categorias.eliminar_categoria(9999)


def test_reactivar_categoria_dada_de_baja(base_datos_temporal):
    categoria = repositorio_categorias.crear_categoria(Categoria(nombre="Bebidas"))
    repositorio_productos.crear_producto(
        Producto(
            codigo_barras="7790000000001",
            nombre="Gaseosa",
            precio_costo_centavos=100,
            precio_venta_centavos=200,
            categoria_id=categoria.id,
        )
    )
    repositorio_categorias.eliminar_categoria(categoria.id)

    reactivada = repositorio_categorias.reactivar_categoria(categoria.id)

    assert reactivada.activa is True


def test_reactivar_categoria_ya_activa_falla(base_datos_temporal):
    categoria = repositorio_categorias.crear_categoria(Categoria(nombre="Bebidas"))

    with pytest.raises(CategoriaNoEncontradaError):
        repositorio_categorias.reactivar_categoria(categoria.id)


def test_actualizar_nombre_persiste_y_conserva_el_id(base_datos_temporal):
    categoria = repositorio_categorias.crear_categoria(Categoria(nombre="Bebidas"))

    actualizada = repositorio_categorias.actualizar_nombre(categoria.id, "Bebidas frías")

    assert actualizada.id == categoria.id
    assert actualizada.nombre == "Bebidas frías"
    recargada = repositorio_categorias.obtener_por_id(categoria.id)
    assert recargada.nombre == "Bebidas frías"


def test_actualizar_nombre_con_duplicado_falla(base_datos_temporal):
    repositorio_categorias.crear_categoria(Categoria(nombre="Bebidas"))
    otra = repositorio_categorias.crear_categoria(Categoria(nombre="Golosinas"))

    with pytest.raises(NombreCategoriaDuplicadoError):
        repositorio_categorias.actualizar_nombre(otra.id, "Bebidas")

    # No se aplicó ningún cambio parcial.
    assert repositorio_categorias.obtener_por_id(otra.id).nombre == "Golosinas"


def test_actualizar_nombre_inexistente_falla(base_datos_temporal):
    with pytest.raises(CategoriaNoEncontradaError):
        repositorio_categorias.actualizar_nombre(9999, "Cualquiera")


def test_actualizar_nombre_no_rompe_la_relacion_con_productos(base_datos_temporal):
    categoria = repositorio_categorias.crear_categoria(Categoria(nombre="Bebidas"))
    producto = repositorio_productos.crear_producto(
        Producto(
            codigo_barras="7790000000001",
            nombre="Gaseosa",
            precio_costo_centavos=100,
            precio_venta_centavos=200,
            categoria_id=categoria.id,
        )
    )

    repositorio_categorias.actualizar_nombre(categoria.id, "Bebidas frías")

    assert repositorio_productos.obtener_por_id(producto.id).categoria_id == categoria.id
