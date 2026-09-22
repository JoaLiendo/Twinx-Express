"""Pruebas de integración de db.repositorios.ajustes_stock: persistencia
y lectura del historial de ajustes manuales de stock."""

from db.conexion import obtener_conexion
from db.repositorios import ajustes_stock as repositorio_ajustes
from db.repositorios import productos as repositorio_productos
from db.repositorios import usuarios as repositorio_usuarios
from domain.ajuste_stock import AjusteStock
from domain.producto import Producto
from domain.usuario import Usuario


def _crear_producto(codigo="7790000000001"):
    return repositorio_productos.crear_producto(
        Producto(codigo_barras=codigo, nombre="Alfajor", precio_costo_centavos=100, precio_venta_centavos=200, stock_actual=10)
    )


def _crear_usuario(nombre_usuario="duenio", rol="OWNER"):
    return repositorio_usuarios.crear_usuario(
        Usuario(nombre_usuario=nombre_usuario, nombre_completo="Test", password_hash="hash", rol=rol)
    )


def test_registrar_ajuste_en_conexion_persiste_los_datos(base_datos_temporal):
    producto = _crear_producto()
    usuario = _crear_usuario()
    ajuste = AjusteStock(
        producto_id=producto.id,
        usuario_id=usuario.id,
        motivo="MERMA",
        delta=-3,
        stock_anterior=10,
        stock_resultante=7,
        observaciones=None,
    )

    with obtener_conexion() as conexion:
        resultado = repositorio_ajustes.registrar_ajuste_en_conexion(conexion, ajuste)

    assert resultado.id is not None
    assert resultado.producto_id == producto.id
    assert resultado.usuario_id == usuario.id
    assert resultado.motivo == "MERMA"
    assert resultado.delta == -3
    assert resultado.stock_anterior == 10
    assert resultado.stock_resultante == 7


def test_registrar_ajuste_no_calcula_nada_persiste_tal_cual(base_datos_temporal):
    """El repositorio no recalcula delta/stock/motivo -- persiste
    exactamente lo que ya viene resuelto en el `AjusteStock`."""
    producto = _crear_producto()
    usuario = _crear_usuario()
    ajuste = AjusteStock(
        producto_id=producto.id,
        usuario_id=usuario.id,
        motivo="RECUENTO",
        delta=5,
        stock_anterior=10,
        stock_resultante=15,
    )

    with obtener_conexion() as conexion:
        repositorio_ajustes.registrar_ajuste_en_conexion(conexion, ajuste)

    with obtener_conexion() as conexion:
        fila = conexion.execute("SELECT * FROM ajustes_stock").fetchone()
    assert fila["delta"] == 5
    assert fila["stock_anterior"] == 10
    assert fila["stock_resultante"] == 15
    assert fila["motivo"] == "RECUENTO"


def test_listar_por_producto_devuelve_el_historial(base_datos_temporal):
    producto = _crear_producto()
    usuario = _crear_usuario()
    with obtener_conexion() as conexion:
        repositorio_ajustes.registrar_ajuste_en_conexion(
            conexion,
            AjusteStock(
                producto_id=producto.id, usuario_id=usuario.id, motivo="MERMA",
                delta=-2, stock_anterior=10, stock_resultante=8,
            ),
        )

    resultado = repositorio_ajustes.listar_por_producto(producto.id)

    assert len(resultado) == 1
    assert resultado[0].motivo == "MERMA"


def test_listar_por_producto_incluye_nombre_de_usuario(base_datos_temporal):
    producto = _crear_producto()
    usuario = _crear_usuario(nombre_usuario="duenio", rol="OWNER")
    with obtener_conexion() as conexion:
        repositorio_ajustes.registrar_ajuste_en_conexion(
            conexion,
            AjusteStock(
                producto_id=producto.id, usuario_id=usuario.id, motivo="ROTURA",
                delta=-1, stock_anterior=10, stock_resultante=9,
            ),
        )

    resultado = repositorio_ajustes.listar_por_producto(producto.id)

    assert resultado[0].usuario_nombre_completo == "Test"


def test_listar_por_producto_usuario_desactivado_sigue_apareciendo(base_datos_temporal):
    producto = _crear_producto()
    usuario = _crear_usuario()
    with obtener_conexion() as conexion:
        repositorio_ajustes.registrar_ajuste_en_conexion(
            conexion,
            AjusteStock(
                producto_id=producto.id, usuario_id=usuario.id, motivo="MERMA",
                delta=-1, stock_anterior=10, stock_resultante=9,
            ),
        )

    repositorio_usuarios.actualizar_activo(usuario.id, False)

    resultado = repositorio_ajustes.listar_por_producto(producto.id)

    assert len(resultado) == 1
    assert resultado[0].usuario_nombre_completo == "Test"


def test_listar_por_producto_orden_mas_reciente_primero(base_datos_temporal):
    producto = _crear_producto()
    usuario = _crear_usuario()
    with obtener_conexion() as conexion:
        primero = repositorio_ajustes.registrar_ajuste_en_conexion(
            conexion,
            AjusteStock(
                producto_id=producto.id, usuario_id=usuario.id, motivo="MERMA",
                delta=-1, stock_anterior=10, stock_resultante=9,
            ),
        )
    with obtener_conexion() as conexion:
        segundo = repositorio_ajustes.registrar_ajuste_en_conexion(
            conexion,
            AjusteStock(
                producto_id=producto.id, usuario_id=usuario.id, motivo="ROTURA",
                delta=-1, stock_anterior=9, stock_resultante=8,
            ),
        )

    resultado = repositorio_ajustes.listar_por_producto(producto.id)

    assert [ajuste.id for ajuste in resultado] == [segundo.id, primero.id]


def test_listar_por_producto_sin_ajustes_devuelve_lista_vacia(base_datos_temporal):
    producto = _crear_producto()

    assert repositorio_ajustes.listar_por_producto(producto.id) == []


def test_listar_por_producto_no_mezcla_ajustes_de_otro_producto(base_datos_temporal):
    p1 = _crear_producto("7790000000001")
    p2 = repositorio_productos.crear_producto(
        Producto(codigo_barras="7790000000002", nombre="Gaseosa", precio_costo_centavos=100, precio_venta_centavos=300, stock_actual=10)
    )
    usuario = _crear_usuario()
    with obtener_conexion() as conexion:
        repositorio_ajustes.registrar_ajuste_en_conexion(
            conexion,
            AjusteStock(
                producto_id=p1.id, usuario_id=usuario.id, motivo="MERMA",
                delta=-1, stock_anterior=10, stock_resultante=9,
            ),
        )

    assert repositorio_ajustes.listar_por_producto(p2.id) == []
