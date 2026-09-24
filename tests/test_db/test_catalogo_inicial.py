"""Tests de `db/repositorios/catalogo_inicial.py`: siembra transaccional,
una sola vez y solo en una instalación nueva (marcador `PRAGMA user_version`)."""

from contextlib import contextmanager

import pytest

import db.repositorios.catalogo_inicial as modulo_repositorio
from db.conexion import obtener_conexion
from db.repositorios import categorias as repositorio_categorias
from db.repositorios import catalogo_inicial as repositorio
from db.repositorios import productos as repositorio_productos
from db.repositorios import usuarios as repositorio_usuarios
from domain.categoria import Categoria
from domain.producto import Producto
from domain.usuario import Usuario
from excepciones import ErrorBaseDatos, ErrorCatalogoInicial


def _catalogo():
    categorias = [Categoria(nombre="Bebidas"), Categoria(nombre="Snacks")]
    productos = [
        (Producto("TX-T-001", "Uno", 0, 0), "Bebidas"),
        (Producto("TX-T-002", "Dos", 0, 0), "Bebidas"),
        (Producto("TX-T-003", "Tres", 0, 0), "Snacks"),
    ]
    return categorias, productos


def _estado():
    """`(categorías, productos, usuarios, user_version)` actuales."""
    with obtener_conexion() as conexion:
        return (
            conexion.execute("SELECT COUNT(*) FROM categorias").fetchone()[0],
            conexion.execute("SELECT COUNT(*) FROM productos").fetchone()[0],
            conexion.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0],
            conexion.execute("PRAGMA user_version").fetchone()[0],
        )


def test_base_nueva_es_instalacion_nueva(base_datos_temporal):
    assert repositorio.es_instalacion_nueva() is True


def test_base_nueva_siembra_categorias_productos_y_marcador(base_datos_temporal):
    categorias, productos = _catalogo()

    assert repositorio.sembrar_catalogo(categorias, productos, 1) is True

    assert _estado() == (2, 3, 0, 1)
    assert repositorio.es_instalacion_nueva() is False


def test_los_productos_quedan_con_su_categoria_y_valores_iniciales(base_datos_temporal):
    categorias, productos = _catalogo()
    repositorio.sembrar_catalogo(categorias, productos, 1)

    with obtener_conexion() as conexion:
        filas = conexion.execute(
            """
            SELECT p.codigo_barras, c.nombre AS categoria, p.precio_costo_centavos, p.precio_venta_centavos,
                   p.stock_actual, p.stock_minimo, p.unidad_medida, p.activo, p.imagen_archivo
            FROM productos p JOIN categorias c ON c.id = p.categoria_id ORDER BY p.codigo_barras
            """
        ).fetchall()

    assert [(f["codigo_barras"], f["categoria"]) for f in filas] == [
        ("TX-T-001", "Bebidas"),
        ("TX-T-002", "Bebidas"),
        ("TX-T-003", "Snacks"),
    ]
    for fila in filas:
        assert (fila["precio_costo_centavos"], fila["precio_venta_centavos"]) == (0, 0)
        assert (fila["stock_actual"], fila["stock_minimo"]) == (0, 0)
        assert fila["unidad_medida"] == "UNIDAD"
        assert fila["activo"] == 1
        assert fila["imagen_archivo"] is None


def test_con_usuarios_existentes_no_siembra(base_datos_temporal):
    repositorio_usuarios.crear_usuario(
        Usuario(nombre_usuario="ana", nombre_completo="Ana", password_hash="hash", rol="OWNER")
    )
    categorias, productos = _catalogo()

    assert repositorio.es_instalacion_nueva() is False
    assert repositorio.sembrar_catalogo(categorias, productos, 1) is False
    assert _estado() == (0, 0, 1, 0)


def test_con_productos_existentes_no_siembra(base_datos_temporal):
    repositorio_productos.crear_producto(Producto("7790000000001", "Propio", 100, 200))
    categorias, productos = _catalogo()

    assert repositorio.sembrar_catalogo(categorias, productos, 1) is False
    assert _estado() == (0, 1, 0, 0)


def test_con_categorias_existentes_no_siembra(base_datos_temporal):
    repositorio_categorias.crear_categoria(Categoria(nombre="Propia"))
    categorias, productos = _catalogo()

    assert repositorio.sembrar_catalogo(categorias, productos, 1) is False
    assert _estado() == (1, 0, 0, 0)


def test_con_user_version_distinto_de_cero_no_siembra(base_datos_temporal):
    with obtener_conexion() as conexion:
        conexion.execute("PRAGMA user_version = 3")
    categorias, productos = _catalogo()

    assert repositorio.es_instalacion_nueva() is False
    assert repositorio.sembrar_catalogo(categorias, productos, 1) is False
    assert _estado() == (0, 0, 0, 3)


def test_segunda_ejecucion_es_un_no_op(base_datos_temporal):
    categorias, productos = _catalogo()
    assert repositorio.sembrar_catalogo(categorias, productos, 1) is True

    assert repositorio.sembrar_catalogo(categorias, productos, 1) is False

    assert _estado() == (2, 3, 0, 1)


def test_error_durante_la_insercion_revierte_todo_incluido_el_marcador(base_datos_temporal):
    categorias, productos = _catalogo()
    productos.append((Producto("TX-T-001", "Repetido", 0, 0), "Snacks"))  # UNIQUE en codigo_barras

    with pytest.raises(ErrorBaseDatos):
        repositorio.sembrar_catalogo(categorias, productos, 1)

    assert _estado() == (0, 0, 0, 0)


def test_categoria_inexistente_revierte_todo(base_datos_temporal):
    categorias, productos = _catalogo()
    productos.append((Producto("TX-T-004", "Huérfano", 0, 0), "No existe"))

    with pytest.raises(ErrorCatalogoInicial):
        repositorio.sembrar_catalogo(categorias, productos, 1)

    assert _estado() == (0, 0, 0, 0)


def test_el_marcador_solo_se_confirma_con_commit(base_datos_temporal, monkeypatch):
    """Si algo falla DESPUÉS de todas las inserciones y del PRAGMA, pero
    antes del COMMIT, no queda nada (ni categorías, ni productos, ni marcador)."""
    conexion_real = modulo_repositorio.obtener_conexion

    @contextmanager
    def conexion_que_falla_antes_del_commit(*, inmediata=False):
        with conexion_real(inmediata=inmediata) as conexion:
            yield conexion
            raise ErrorBaseDatos("fallo simulado antes del commit")

    monkeypatch.setattr(modulo_repositorio, "obtener_conexion", conexion_que_falla_antes_del_commit)
    categorias, productos = _catalogo()

    with pytest.raises(ErrorBaseDatos):
        repositorio.sembrar_catalogo(categorias, productos, 1)

    assert _estado() == (0, 0, 0, 0)
