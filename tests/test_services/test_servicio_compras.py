"""Pruebas de integración de services.servicio_compras contra una base
de datos SQLite real (temporal y aislada, ver tests/conftest.py)."""

import threading

import pytest

from db.repositorios import compras as repositorio_compras
from db.repositorios import productos as repositorio_productos
from domain.compra import ItemCompra
from excepciones import (
    DatosInvalidosError,
    ErrorBaseDatos,
    ProductoDuplicadoEnCompraError,
    ProductoNoEncontradoError,
    ProveedorNoEncontradoError,
)
from services import servicio_compras, servicio_proveedores, servicio_stock


def _crear_usuario(rol="OWNER"):
    from db.repositorios import usuarios as repositorio_usuarios
    from domain.usuario import Usuario

    return repositorio_usuarios.crear_usuario(
        Usuario(nombre_usuario="ana", nombre_completo="Ana", password_hash="hash-de-prueba", rol=rol)
    )


def test_registrar_compra_simple(base_datos_temporal):
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    usuario = _crear_usuario()
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=5)

    compra = servicio_compras.registrar_compra(
        proveedor_id=proveedor.id,
        usuario_id=usuario.id,
        items=[ItemCompra(producto.id, 10, 120)],
        observaciones="Entrega parcial",
    )

    assert compra.id is not None
    assert compra.proveedor_id == proveedor.id
    assert compra.usuario_id == usuario.id
    assert compra.observaciones == "Entrega parcial"
    assert compra.total_centavos == 1200

    producto_actualizado = servicio_stock.obtener_por_id(producto.id)
    assert producto_actualizado.stock_actual == 15  # 5 + 10
    assert producto_actualizado.precio_costo_centavos == 120  # costo vigente actualizado


def test_registrar_compra_con_multiples_productos(base_datos_temporal):
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    usuario = _crear_usuario()
    producto_1 = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=5)
    producto_2 = servicio_stock.registrar_producto("7790000000002", "Gaseosa", 300, 500, stock_actual=2)

    compra = servicio_compras.registrar_compra(
        proveedor_id=proveedor.id,
        usuario_id=usuario.id,
        items=[
            ItemCompra(producto_1.id, 10, 120),
            ItemCompra(producto_2.id, 3, 280),
        ],
    )

    assert compra.total_centavos == 10 * 120 + 3 * 280

    p1 = servicio_stock.obtener_por_id(producto_1.id)
    p2 = servicio_stock.obtener_por_id(producto_2.id)
    assert p1.stock_actual == 15
    assert p1.precio_costo_centavos == 120
    assert p2.stock_actual == 5
    assert p2.precio_costo_centavos == 280

    detalle = repositorio_compras.listar_detalle(compra.id)
    assert len(detalle) == 2
    linea_1 = next(d for d in detalle if d.producto_id == producto_1.id)
    assert linea_1.subtotal_centavos == 1200


def test_dos_compras_concurrentes_del_mismo_producto_no_pierden_ningun_incremento(base_datos_temporal):
    """Misma clase de bug que en `servicio_ventas` (ver auditoría de
    concurrencia, Stage D), en la dirección aditiva: dos compras reales
    y concurrentes (dos hilos, dos conexiones SQLite reales, nunca
    monkeypatch) del mismo producto no deben perder ningún incremento
    de stock por leer el mismo valor de partida."""
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    usuario = _crear_usuario()
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=0)

    barrera = threading.Barrier(2)
    resultados = {}

    def comprar(nombre):
        barrera.wait()
        compra = servicio_compras.registrar_compra(
            proveedor_id=proveedor.id, usuario_id=usuario.id, items=[ItemCompra(producto.id, 10, 120)]
        )
        resultados[nombre] = compra.id

    hilo_a = threading.Thread(target=comprar, args=("A",))
    hilo_b = threading.Thread(target=comprar, args=("B",))
    hilo_a.start()
    hilo_b.start()
    hilo_a.join(timeout=10)
    hilo_b.join(timeout=10)

    assert not hilo_a.is_alive()
    assert not hilo_b.is_alive()
    assert resultados["A"] != resultados["B"]  # dos compras distintas, ninguna se perdió

    producto_final = servicio_stock.obtener_por_id(producto.id)
    assert producto_final.stock_actual == 20  # 0 + 10 + 10, ningún incremento se perdió


def test_registrar_compra_sin_items_falla(base_datos_temporal):
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    usuario = _crear_usuario()

    with pytest.raises(DatosInvalidosError):
        servicio_compras.registrar_compra(proveedor.id, usuario.id, items=[])


def test_registrar_compra_con_proveedor_inexistente_falla(base_datos_temporal):
    usuario = _crear_usuario()
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)

    with pytest.raises(ProveedorNoEncontradoError):
        servicio_compras.registrar_compra(9999, usuario.id, [ItemCompra(producto.id, 1, 100)])

    assert repositorio_compras.listar_todas() == []


def test_registrar_compra_con_proveedor_inactivo_falla(base_datos_temporal):
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    servicio_proveedores.eliminar_proveedor(proveedor.id)
    usuario = _crear_usuario()
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)

    with pytest.raises(ProveedorNoEncontradoError):
        servicio_compras.registrar_compra(proveedor.id, usuario.id, [ItemCompra(producto.id, 1, 100)])

    assert repositorio_compras.listar_todas() == []


def test_registrar_compra_con_producto_inexistente_falla_y_no_deja_nada(base_datos_temporal):
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    usuario = _crear_usuario()
    producto_valido = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=5)

    with pytest.raises(ProductoNoEncontradoError):
        servicio_compras.registrar_compra(
            proveedor.id,
            usuario.id,
            [ItemCompra(producto_valido.id, 10, 120), ItemCompra(9999, 1, 50)],
        )

    assert repositorio_compras.listar_todas() == []
    assert servicio_stock.obtener_por_id(producto_valido.id).stock_actual == 5  # sin cambios
    assert servicio_stock.obtener_por_id(producto_valido.id).precio_costo_centavos == 100  # sin cambios


def test_registrar_compra_con_producto_inactivo_falla(base_datos_temporal):
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    usuario = _crear_usuario()
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)
    servicio_stock.eliminar_producto(producto.id)

    with pytest.raises(ProductoNoEncontradoError):
        servicio_compras.registrar_compra(proveedor.id, usuario.id, [ItemCompra(producto.id, 1, 100)])

    assert repositorio_compras.listar_todas() == []


def test_registrar_compra_con_producto_duplicado_falla(base_datos_temporal):
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    usuario = _crear_usuario()
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=5)

    with pytest.raises(ProductoDuplicadoEnCompraError):
        servicio_compras.registrar_compra(
            proveedor.id,
            usuario.id,
            [ItemCompra(producto.id, 5, 100), ItemCompra(producto.id, 3, 110)],
        )

    assert repositorio_compras.listar_todas() == []
    assert servicio_stock.obtener_por_id(producto.id).stock_actual == 5  # sin cambios


def test_registrar_compra_con_cantidad_invalida_falla_antes_de_tocar_la_base(base_datos_temporal):
    with pytest.raises(DatosInvalidosError):
        ItemCompra(producto_id=1, cantidad=0, costo_unitario_centavos=100)


def test_registrar_compra_con_costo_negativo_falla_antes_de_tocar_la_base(base_datos_temporal):
    with pytest.raises(DatosInvalidosError):
        ItemCompra(producto_id=1, cantidad=1, costo_unitario_centavos=-1)


def test_registrar_compra_falla_a_mitad_de_camino_no_deja_nada_persistido(base_datos_temporal, monkeypatch):
    """MUY IMPORTANTE: si el primer producto ya se proceso (stock y costo
    actualizados) y el segundo falla, la transaccion completa se revierte:
    ni la compra, ni el detalle, ni el stock/costo del PRIMER producto
    quedan modificados."""
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    usuario = _crear_usuario()
    producto_1 = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=5)
    producto_2 = servicio_stock.registrar_producto("7790000000002", "Gaseosa", 300, 500, stock_actual=2)

    original = repositorio_productos.actualizar_costo_con_evento_en_conexion
    llamadas = {"contador": 0}

    def falla_en_el_segundo(conexion, producto_id, nuevo_costo_centavos, *args, **kwargs):
        llamadas["contador"] += 1
        if llamadas["contador"] == 2:
            raise ErrorBaseDatos("fallo simulado para probar atomicidad")
        return original(conexion, producto_id, nuevo_costo_centavos, *args, **kwargs)

    monkeypatch.setattr(repositorio_productos, "actualizar_costo_con_evento_en_conexion", falla_en_el_segundo)

    with pytest.raises(ErrorBaseDatos):
        servicio_compras.registrar_compra(
            proveedor.id,
            usuario.id,
            [ItemCompra(producto_1.id, 10, 120), ItemCompra(producto_2.id, 3, 280)],
        )

    assert repositorio_compras.listar_todas() == []
    p1 = servicio_stock.obtener_por_id(producto_1.id)
    p2 = servicio_stock.obtener_por_id(producto_2.id)
    assert p1.stock_actual == 5  # el primer producto NO quedo con el incremento aplicado
    assert p1.precio_costo_centavos == 100
    assert p2.stock_actual == 2
    assert p2.precio_costo_centavos == 300


def test_listar_compras(base_datos_temporal):
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    usuario = _crear_usuario()
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)

    servicio_compras.registrar_compra(proveedor.id, usuario.id, [ItemCompra(producto.id, 1, 100)])
    servicio_compras.registrar_compra(proveedor.id, usuario.id, [ItemCompra(producto.id, 2, 100)])

    assert len(servicio_compras.listar_todas()) == 2


def test_obtener_compra_y_su_detalle(base_datos_temporal):
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    usuario = _crear_usuario()
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)

    creada = servicio_compras.registrar_compra(proveedor.id, usuario.id, [ItemCompra(producto.id, 2, 150)])

    assert servicio_compras.obtener_por_id(creada.id).id == creada.id
    assert servicio_compras.obtener_por_id(9999) is None
    assert len(servicio_compras.listar_detalle(creada.id)) == 1


def test_listar_resumen(base_datos_temporal):
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    usuario = _crear_usuario()
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)

    creada = servicio_compras.registrar_compra(proveedor.id, usuario.id, [ItemCompra(producto.id, 2, 150)])

    resumenes = servicio_compras.listar_resumen()
    assert len(resumenes) == 1
    assert resumenes[0].id == creada.id
    assert resumenes[0].proveedor_nombre == "Distribuidora SA"
    assert resumenes[0].usuario_nombre_completo == "Ana"
    assert resumenes[0].cantidad_lineas == 1

    assert servicio_compras.listar_resumen(proveedor_id=9999) == []


def test_obtener_resumen_por_id(base_datos_temporal):
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    usuario = _crear_usuario()
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)

    creada = servicio_compras.registrar_compra(proveedor.id, usuario.id, [ItemCompra(producto.id, 2, 150)])

    assert servicio_compras.obtener_resumen_por_id(creada.id).proveedor_nombre == "Distribuidora SA"
    assert servicio_compras.obtener_resumen_por_id(9999) is None


def test_listar_detalle_con_producto(base_datos_temporal):
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    usuario = _crear_usuario()
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, unidad_medida="UNIDAD")

    creada = servicio_compras.registrar_compra(proveedor.id, usuario.id, [ItemCompra(producto.id, 2, 150)])

    detalle = servicio_compras.listar_detalle_con_producto(creada.id)
    assert len(detalle) == 1
    assert detalle[0].producto_nombre == "Alfajor"
    assert detalle[0].producto_unidad_medida == "UNIDAD"
