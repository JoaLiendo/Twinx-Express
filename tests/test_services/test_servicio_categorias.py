"""Pruebas de integración de services.servicio_categorias contra una base
de datos SQLite real (temporal y aislada, ver tests/conftest.py)."""

import pytest

from excepciones import CategoriaNoEncontradaError, DatosInvalidosError, NombreCategoriaDuplicadoError
from services import servicio_categorias, servicio_stock


def test_crear_categoria_la_persiste_y_asigna_id(base_datos_temporal):
    categoria = servicio_categorias.crear_categoria("Bebidas")

    assert categoria.id is not None
    assert categoria.nombre == "Bebidas"


def test_crear_categoria_duplicada_falla(base_datos_temporal):
    servicio_categorias.crear_categoria("Bebidas")

    with pytest.raises(NombreCategoriaDuplicadoError):
        servicio_categorias.crear_categoria("Bebidas")


def test_crear_categoria_con_nombre_vacio_falla(base_datos_temporal):
    with pytest.raises(DatosInvalidosError):
        servicio_categorias.crear_categoria("   ")


def test_listar_activas(base_datos_temporal):
    servicio_categorias.crear_categoria("Bebidas")
    servicio_categorias.crear_categoria("Golosinas")

    nombres = [c.nombre for c in servicio_categorias.listar_activas()]

    assert nombres == ["Bebidas", "Golosinas"]


def test_obtener_por_id_y_por_nombre(base_datos_temporal):
    creada = servicio_categorias.crear_categoria("Bebidas")

    assert servicio_categorias.obtener_por_id(creada.id).nombre == "Bebidas"
    assert servicio_categorias.obtener_por_nombre("Bebidas").id == creada.id
    assert servicio_categorias.obtener_por_id(9999) is None
    assert servicio_categorias.obtener_por_nombre("no-existe") is None


def test_eliminar_categoria_sin_uso_la_borra_fisicamente(base_datos_temporal):
    categoria = servicio_categorias.crear_categoria("Bebidas")

    fue_baja_logica = servicio_categorias.eliminar_categoria(categoria.id)

    assert fue_baja_logica is False
    assert servicio_categorias.obtener_por_id(categoria.id) is None


def test_eliminar_categoria_utilizada_por_un_producto_no_la_borra_fisicamente(base_datos_temporal):
    categoria = servicio_categorias.crear_categoria("Bebidas")
    servicio_stock.registrar_producto(
        "7790000000001", "Gaseosa", 100, 200, categoria_id=categoria.id
    )

    fue_baja_logica = servicio_categorias.eliminar_categoria(categoria.id)

    assert fue_baja_logica is True
    encontrada = servicio_categorias.obtener_por_id(categoria.id)
    assert encontrada is not None  # sigue existiendo, solo inactiva
    assert encontrada.activa is False


def test_eliminar_categoria_inexistente_falla(base_datos_temporal):
    with pytest.raises(CategoriaNoEncontradaError):
        servicio_categorias.eliminar_categoria(9999)


def test_reactivar_categoria(base_datos_temporal):
    categoria = servicio_categorias.crear_categoria("Bebidas")
    servicio_stock.registrar_producto("7790000000001", "Gaseosa", 100, 200, categoria_id=categoria.id)
    servicio_categorias.eliminar_categoria(categoria.id)

    reactivada = servicio_categorias.reactivar_categoria(categoria.id)

    assert reactivada.activa is True


def test_reactivar_categoria_no_modifica_el_producto_asociado(base_datos_temporal):
    categoria = servicio_categorias.crear_categoria("Bebidas")
    producto = servicio_stock.registrar_producto(
        "7790000000001", "Gaseosa", 100, 200, categoria_id=categoria.id
    )
    servicio_categorias.eliminar_categoria(categoria.id)

    servicio_categorias.reactivar_categoria(categoria.id)

    assert servicio_stock.obtener_por_id(producto.id).categoria_id == categoria.id


def test_categoria_reactivada_vuelve_a_ser_opcion_valida_para_productos(base_datos_temporal):
    categoria = servicio_categorias.crear_categoria("Bebidas")
    servicio_stock.registrar_producto("7790000000001", "Gaseosa", 100, 200, categoria_id=categoria.id)
    servicio_categorias.eliminar_categoria(categoria.id)  # con producto asociado: baja lógica
    assert categoria.nombre not in [c.nombre for c in servicio_categorias.listar_activas()]

    servicio_categorias.reactivar_categoria(categoria.id)

    assert categoria.nombre in [c.nombre for c in servicio_categorias.listar_activas()]


def test_actualizar_categoria_edita_el_nombre_y_conserva_el_id(base_datos_temporal):
    categoria = servicio_categorias.crear_categoria("Bebidas")

    actualizada = servicio_categorias.actualizar_categoria(categoria.id, "Bebidas frías")

    assert actualizada.id == categoria.id
    assert actualizada.nombre == "Bebidas frías"


def test_actualizar_categoria_con_nombre_duplicado_falla(base_datos_temporal):
    servicio_categorias.crear_categoria("Bebidas")
    otra = servicio_categorias.crear_categoria("Golosinas")

    with pytest.raises(NombreCategoriaDuplicadoError):
        servicio_categorias.actualizar_categoria(otra.id, "Bebidas")


def test_actualizar_categoria_con_nombre_vacio_falla(base_datos_temporal):
    categoria = servicio_categorias.crear_categoria("Bebidas")

    with pytest.raises(DatosInvalidosError):
        servicio_categorias.actualizar_categoria(categoria.id, "   ")


def test_actualizar_categoria_inexistente_falla(base_datos_temporal):
    with pytest.raises(CategoriaNoEncontradaError):
        servicio_categorias.actualizar_categoria(9999, "Cualquiera")


def test_actualizar_categoria_no_rompe_relacion_con_producto_asociado(base_datos_temporal):
    categoria = servicio_categorias.crear_categoria("Bebidas")
    producto = servicio_stock.registrar_producto(
        "7790000000001", "Gaseosa", 100, 200, categoria_id=categoria.id
    )

    servicio_categorias.actualizar_categoria(categoria.id, "Bebidas frías")

    assert servicio_stock.obtener_por_id(producto.id).categoria_id == categoria.id
