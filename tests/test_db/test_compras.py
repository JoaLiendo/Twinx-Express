"""Pruebas de integración de db.repositorios.compras contra una base de
datos SQLite real (temporal y aislada, ver tests/conftest.py)."""

import sqlite3

import pytest

from db.conexion import obtener_conexion
from db.repositorios import compras as repositorio_compras
from db.repositorios import productos as repositorio_productos
from db.repositorios import proveedores as repositorio_proveedores
from db.repositorios import usuarios as repositorio_usuarios
from domain.compra import ItemCompra
from domain.producto import Producto
from domain.proveedor import Proveedor
from domain.usuario import Usuario
from excepciones import ErrorBaseDatos


def _crear_proveedor():
    return repositorio_proveedores.crear_proveedor(Proveedor(nombre="Distribuidora SA"))


def _crear_usuario():
    return repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario="ana",
            nombre_completo="Ana",
            password_hash="hash-de-prueba",
            rol="OWNER",
        )
    )


def _crear_producto(codigo="7790000000001", nombre="Alfajor"):
    return repositorio_productos.crear_producto(
        Producto(codigo_barras=codigo, nombre=nombre, precio_costo_centavos=100, precio_venta_centavos=200)
    )


def test_registrar_compra_con_detalle_inserta_cabecera_y_lineas(base_datos_temporal):
    proveedor = _crear_proveedor()
    usuario = _crear_usuario()
    producto_1 = _crear_producto("7790000000001", "Alfajor")
    producto_2 = _crear_producto("7790000000002", "Gaseosa")

    items = [
        ItemCompra(producto_1.id, 10, 120),
        ItemCompra(producto_2.id, 5, 300),
    ]
    items_con_subtotal = [(item, item.cantidad * item.costo_unitario_centavos) for item in items]
    total_centavos = sum(subtotal for _, subtotal in items_con_subtotal)

    with obtener_conexion() as conexion:
        compra = repositorio_compras.registrar_compra_con_detalle(
            conexion, proveedor.id, usuario.id, "Entrega parcial", total_centavos, items_con_subtotal
        )

    assert compra.id is not None
    assert compra.proveedor_id == proveedor.id
    assert compra.usuario_id == usuario.id
    assert compra.observaciones == "Entrega parcial"
    assert compra.total_centavos == 10 * 120 + 5 * 300
    assert compra.fecha is not None

    detalle = repositorio_compras.listar_detalle(compra.id)
    assert len(detalle) == 2
    assert {d.producto_id for d in detalle} == {producto_1.id, producto_2.id}
    linea_1 = next(d for d in detalle if d.producto_id == producto_1.id)
    assert linea_1.cantidad == 10
    assert linea_1.costo_unitario_centavos == 120
    assert linea_1.subtotal_centavos == 1200


def test_obtener_por_id_devuelve_compra_existente(base_datos_temporal):
    proveedor = _crear_proveedor()
    usuario = _crear_usuario()
    producto = _crear_producto()
    items_con_subtotal = [(ItemCompra(producto.id, 2, 100), 200)]

    with obtener_conexion() as conexion:
        creada = repositorio_compras.registrar_compra_con_detalle(
            conexion, proveedor.id, usuario.id, None, 200, items_con_subtotal
        )

    encontrada = repositorio_compras.obtener_por_id(creada.id)
    assert encontrada is not None
    assert encontrada.total_centavos == 200


def test_obtener_por_id_inexistente_devuelve_none(base_datos_temporal):
    assert repositorio_compras.obtener_por_id(9999) is None


def test_listar_todas_devuelve_todas_las_compras(base_datos_temporal):
    proveedor = _crear_proveedor()
    usuario = _crear_usuario()
    producto = _crear_producto()
    items_con_subtotal = [(ItemCompra(producto.id, 1, 100), 100)]

    with obtener_conexion() as conexion:
        repositorio_compras.registrar_compra_con_detalle(conexion, proveedor.id, usuario.id, None, 100, items_con_subtotal)
    with obtener_conexion() as conexion:
        repositorio_compras.registrar_compra_con_detalle(conexion, proveedor.id, usuario.id, None, 100, items_con_subtotal)

    assert len(repositorio_compras.listar_todas()) == 2


def test_listar_detalle_de_compra_inexistente_devuelve_lista_vacia(base_datos_temporal):
    assert repositorio_compras.listar_detalle(9999) == []


def test_fk_proveedor_inexistente_falla(base_datos_temporal):
    usuario = _crear_usuario()
    producto = _crear_producto()
    items_con_subtotal = [(ItemCompra(producto.id, 1, 100), 100)]

    with pytest.raises(ErrorBaseDatos) as excinfo:
        with obtener_conexion() as conexion:
            repositorio_compras.registrar_compra_con_detalle(conexion, 9999, usuario.id, None, 100, items_con_subtotal)
    assert isinstance(excinfo.value.__cause__, sqlite3.IntegrityError)


def test_fk_usuario_inexistente_falla(base_datos_temporal):
    proveedor = _crear_proveedor()
    producto = _crear_producto()
    items_con_subtotal = [(ItemCompra(producto.id, 1, 100), 100)]

    with pytest.raises(ErrorBaseDatos) as excinfo:
        with obtener_conexion() as conexion:
            repositorio_compras.registrar_compra_con_detalle(conexion, proveedor.id, 9999, None, 100, items_con_subtotal)
    assert isinstance(excinfo.value.__cause__, sqlite3.IntegrityError)


def test_fk_producto_inexistente_falla(base_datos_temporal):
    proveedor = _crear_proveedor()
    usuario = _crear_usuario()
    items_con_subtotal = [(ItemCompra(9999, 1, 100), 100)]

    with pytest.raises(ErrorBaseDatos) as excinfo:
        with obtener_conexion() as conexion:
            repositorio_compras.registrar_compra_con_detalle(conexion, proveedor.id, usuario.id, None, 100, items_con_subtotal)
    assert isinstance(excinfo.value.__cause__, sqlite3.IntegrityError)


def test_cascade_de_detalle_al_borrar_la_compra(base_datos_temporal):
    """No se borran compras en la aplicación (son inmutables), pero se
    verifica que la FK compra_id -> compras(id) ON DELETE CASCADE está
    bien declarada: si alguna vez se borra una compra directamente en
    la base, su detalle se va con ella (no queda huérfano)."""
    proveedor = _crear_proveedor()
    usuario = _crear_usuario()
    producto = _crear_producto()
    items_con_subtotal = [(ItemCompra(producto.id, 1, 100), 100)]

    with obtener_conexion() as conexion:
        compra = repositorio_compras.registrar_compra_con_detalle(
            conexion, proveedor.id, usuario.id, None, 100, items_con_subtotal
        )

    with obtener_conexion() as conexion:
        conexion.execute("DELETE FROM compras WHERE id = ?", (compra.id,))

    assert repositorio_compras.listar_detalle(compra.id) == []


def test_fk_proveedor_con_compra_asociada_no_permite_eliminarlo_fisicamente(base_datos_temporal):
    """Verifica en vivo la promesa de la auditoría de Fase 4A: ahora que
    `compras` existe, `eliminar_proveedor` debe caer a baja lógica."""
    proveedor = _crear_proveedor()
    usuario = _crear_usuario()
    producto = _crear_producto()
    items_con_subtotal = [(ItemCompra(producto.id, 1, 100), 100)]

    with obtener_conexion() as conexion:
        repositorio_compras.registrar_compra_con_detalle(conexion, proveedor.id, usuario.id, None, 100, items_con_subtotal)

    fue_baja_logica = repositorio_proveedores.eliminar_proveedor(proveedor.id)

    assert fue_baja_logica is True
    encontrado = repositorio_proveedores.obtener_por_id(proveedor.id)
    assert encontrado is None  # obtener_por_id solo devuelve activos
    inactivo = repositorio_proveedores.listar_todos()
    assert any(p.id == proveedor.id and p.activo is False for p in inactivo)


class TestListarResumen:
    """`listar_resumen` resuelve compra + proveedor + usuario + cantidad
    de líneas en una única consulta con JOIN (Fase 4C), sin N+1."""

    def test_devuelve_los_campos_resueltos_por_join(self, base_datos_temporal):
        proveedor = _crear_proveedor()
        usuario = _crear_usuario()
        producto_1 = _crear_producto("7790000000001", "Alfajor")
        producto_2 = _crear_producto("7790000000002", "Gaseosa")
        items_con_subtotal = [
            (ItemCompra(producto_1.id, 10, 120), 1200),
            (ItemCompra(producto_2.id, 5, 300), 1500),
        ]
        with obtener_conexion() as conexion:
            compra = repositorio_compras.registrar_compra_con_detalle(
                conexion, proveedor.id, usuario.id, "Entrega parcial", 2700, items_con_subtotal
            )

        resumenes = repositorio_compras.listar_resumen()

        assert len(resumenes) == 1
        resumen = resumenes[0]
        assert resumen.id == compra.id
        assert resumen.proveedor_nombre == "Distribuidora SA"
        assert resumen.usuario_nombre_completo == "Ana"
        assert resumen.cantidad_lineas == 2
        assert resumen.total_centavos == 2700
        assert resumen.observaciones == "Entrega parcial"

    def test_orden_mas_reciente_primero(self, base_datos_temporal):
        proveedor = _crear_proveedor()
        usuario = _crear_usuario()
        producto = _crear_producto()
        items = [(ItemCompra(producto.id, 1, 100), 100)]
        with obtener_conexion() as conexion:
            primera = repositorio_compras.registrar_compra_con_detalle(conexion, proveedor.id, usuario.id, None, 100, items)
        with obtener_conexion() as conexion:
            segunda = repositorio_compras.registrar_compra_con_detalle(conexion, proveedor.id, usuario.id, None, 100, items)

        ids = [r.id for r in repositorio_compras.listar_resumen()]

        assert ids == [segunda.id, primera.id]

    def test_lista_vacia_sin_compras(self, base_datos_temporal):
        assert repositorio_compras.listar_resumen() == []

    def test_filtra_por_proveedor(self, base_datos_temporal):
        proveedor_1 = _crear_proveedor()
        proveedor_2 = repositorio_proveedores.crear_proveedor(Proveedor(nombre="Mayorista Norte"))
        usuario = _crear_usuario()
        producto = _crear_producto()
        items = [(ItemCompra(producto.id, 1, 100), 100)]
        with obtener_conexion() as conexion:
            repositorio_compras.registrar_compra_con_detalle(conexion, proveedor_1.id, usuario.id, None, 100, items)
        with obtener_conexion() as conexion:
            repositorio_compras.registrar_compra_con_detalle(conexion, proveedor_2.id, usuario.id, None, 100, items)

        resumenes = repositorio_compras.listar_resumen(proveedor_id=proveedor_2.id)

        assert len(resumenes) == 1
        assert resumenes[0].proveedor_nombre == "Mayorista Norte"

    def test_filtra_por_proveedor_inexistente_devuelve_vacio(self, base_datos_temporal):
        proveedor = _crear_proveedor()
        usuario = _crear_usuario()
        producto = _crear_producto()
        with obtener_conexion() as conexion:
            repositorio_compras.registrar_compra_con_detalle(
                conexion, proveedor.id, usuario.id, None, 100, [(ItemCompra(producto.id, 1, 100), 100)]
            )

        assert repositorio_compras.listar_resumen(proveedor_id=9999) == []

    def test_filtra_por_rango_de_fechas(self, base_datos_temporal):
        proveedor = _crear_proveedor()
        usuario = _crear_usuario()
        producto = _crear_producto()
        items = [(ItemCompra(producto.id, 1, 100), 100)]
        with obtener_conexion() as conexion:
            compra = repositorio_compras.registrar_compra_con_detalle(conexion, proveedor.id, usuario.id, None, 100, items)

        hoy = compra.fecha[:10]
        assert len(repositorio_compras.listar_resumen(fecha_desde=hoy, fecha_hasta=hoy)) == 1
        assert repositorio_compras.listar_resumen(fecha_desde="2099-01-01") == []
        assert repositorio_compras.listar_resumen(fecha_hasta="1999-01-01") == []

    def test_combinacion_de_filtros_sin_resultados(self, base_datos_temporal):
        proveedor = _crear_proveedor()
        usuario = _crear_usuario()
        producto = _crear_producto()
        with obtener_conexion() as conexion:
            repositorio_compras.registrar_compra_con_detalle(
                conexion, proveedor.id, usuario.id, None, 100, [(ItemCompra(producto.id, 1, 100), 100)]
            )

        resumenes = repositorio_compras.listar_resumen(proveedor_id=proveedor.id, fecha_desde="2099-01-01")

        assert resumenes == []

    def test_conserva_nombre_de_proveedor_inactivo(self, base_datos_temporal):
        proveedor = _crear_proveedor()
        usuario = _crear_usuario()
        producto = _crear_producto()
        with obtener_conexion() as conexion:
            repositorio_compras.registrar_compra_con_detalle(
                conexion, proveedor.id, usuario.id, None, 100, [(ItemCompra(producto.id, 1, 100), 100)]
            )
        repositorio_proveedores.eliminar_proveedor(proveedor.id)  # cae a baja lógica (tiene una compra)

        resumenes = repositorio_compras.listar_resumen()

        assert resumenes[0].proveedor_nombre == "Distribuidora SA"


class TestObtenerResumenPorId:
    def test_devuelve_resumen_de_compra_existente(self, base_datos_temporal):
        proveedor = _crear_proveedor()
        usuario = _crear_usuario()
        producto = _crear_producto()
        with obtener_conexion() as conexion:
            compra = repositorio_compras.registrar_compra_con_detalle(
                conexion, proveedor.id, usuario.id, None, 100, [(ItemCompra(producto.id, 1, 100), 100)]
            )

        resumen = repositorio_compras.obtener_resumen_por_id(compra.id)

        assert resumen is not None
        assert resumen.proveedor_nombre == "Distribuidora SA"
        assert resumen.usuario_nombre_completo == "Ana"
        assert resumen.cantidad_lineas == 1

    def test_inexistente_devuelve_none(self, base_datos_temporal):
        assert repositorio_compras.obtener_resumen_por_id(9999) is None


class TestListarDetalleConProducto:
    def test_incluye_nombre_y_unidad_del_producto(self, base_datos_temporal):
        proveedor = _crear_proveedor()
        usuario = _crear_usuario()
        producto = repositorio_productos.crear_producto(
            Producto(
                codigo_barras="7790000000001", nombre="Leche", precio_costo_centavos=100,
                precio_venta_centavos=200, unidad_medida="LITRO",
            )
        )
        with obtener_conexion() as conexion:
            compra = repositorio_compras.registrar_compra_con_detalle(
                conexion, proveedor.id, usuario.id, None, 300, [(ItemCompra(producto.id, 3, 100), 300)]
            )

        detalle = repositorio_compras.listar_detalle_con_producto(compra.id)

        assert len(detalle) == 1
        assert detalle[0].producto_nombre == "Leche"
        assert detalle[0].producto_unidad_medida == "LITRO"
        assert detalle[0].cantidad == 3
        assert detalle[0].subtotal_centavos == 300

    def test_conserva_nombre_de_producto_inactivo(self, base_datos_temporal):
        proveedor = _crear_proveedor()
        usuario = _crear_usuario()
        producto = _crear_producto()
        with obtener_conexion() as conexion:
            compra = repositorio_compras.registrar_compra_con_detalle(
                conexion, proveedor.id, usuario.id, None, 100, [(ItemCompra(producto.id, 1, 100), 100)]
            )
        repositorio_productos.eliminar_producto(producto.id)  # cae a baja lógica (tiene una compra)

        detalle = repositorio_compras.listar_detalle_con_producto(compra.id)

        assert detalle[0].producto_nombre == "Alfajor"

    def test_compra_inexistente_devuelve_lista_vacia(self, base_datos_temporal):
        assert repositorio_compras.listar_detalle_con_producto(9999) == []
