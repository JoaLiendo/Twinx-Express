"""Pruebas de integración de db.repositorios.ventas para la idempotencia
de Fase 5A. El resto del repositorio (alta de venta+detalle, listado
del día) ya está cubierto end-to-end por tests/test_servicio_ventas.py."""

import sqlite3
from contextlib import contextmanager

import pytest

from db.conexion import obtener_conexion
from db.repositorios import productos as repositorio_productos
from db.repositorios import ventas as repositorio_ventas
from domain.producto import Producto
from domain.venta import ItemVenta
from excepciones import ErrorBaseDatos


def _crear_producto(codigo="7790000000001"):
    return repositorio_productos.crear_producto(
        Producto(codigo_barras=codigo, nombre="Alfajor", precio_costo_centavos=100, precio_venta_centavos=200)
    )


def test_registrar_venta_con_detalle_guarda_clave_y_hash(base_datos_temporal):
    producto = _crear_producto()
    items_con_precio = [(ItemVenta(producto.id, 2), 200)]

    with obtener_conexion() as conexion:
        venta = repositorio_ventas.registrar_venta_con_detalle(
            conexion, 400, "EFECTIVO", items_con_precio,
            clave_idempotencia="clave-1", contenido_hash="hash-1",
        )

    resultado = repositorio_ventas.obtener_por_clave_idempotencia("clave-1")
    assert resultado is not None
    venta_encontrada, hash_encontrado = resultado
    assert venta_encontrada.id == venta.id
    assert hash_encontrado == "hash-1"


def test_registrar_venta_con_detalle_sin_clave_sigue_funcionando(base_datos_temporal):
    """Compatibilidad con el CLI/tests existentes: clave_idempotencia y
    contenido_hash son opcionales, por defecto None."""
    producto = _crear_producto()
    items_con_precio = [(ItemVenta(producto.id, 1), 200)]

    with obtener_conexion() as conexion:
        venta = repositorio_ventas.registrar_venta_con_detalle(conexion, 200, "EFECTIVO", items_con_precio)

    assert venta.id is not None
    assert repositorio_ventas.obtener_por_clave_idempotencia("clave-inexistente") is None


def test_dos_ventas_sin_clave_no_chocan_entre_si(base_datos_temporal):
    producto = _crear_producto()
    items_con_precio = [(ItemVenta(producto.id, 1), 200)]

    with obtener_conexion() as conexion:
        repositorio_ventas.registrar_venta_con_detalle(conexion, 200, "EFECTIVO", items_con_precio)
    with obtener_conexion() as conexion:
        repositorio_ventas.registrar_venta_con_detalle(conexion, 200, "EFECTIVO", items_con_precio)

    with obtener_conexion() as conexion:
        total = conexion.execute("SELECT COUNT(*) AS n FROM ventas").fetchone()["n"]
    assert total == 2


def test_clave_idempotencia_duplicada_falla_con_integrity_error(base_datos_temporal):
    producto = _crear_producto()
    items_con_precio = [(ItemVenta(producto.id, 1), 200)]

    with obtener_conexion() as conexion:
        repositorio_ventas.registrar_venta_con_detalle(
            conexion, 200, "EFECTIVO", items_con_precio,
            clave_idempotencia="clave-repetida", contenido_hash="hash-a",
        )

    with pytest.raises(ErrorBaseDatos) as excinfo:
        with obtener_conexion() as conexion:
            repositorio_ventas.registrar_venta_con_detalle(
                conexion, 200, "EFECTIVO", items_con_precio,
                clave_idempotencia="clave-repetida", contenido_hash="hash-b",
            )
    assert isinstance(excinfo.value.__cause__, sqlite3.IntegrityError)

    # La transacción perdedora se revirtió por completo: sigue habiendo
    # una sola venta con esa clave, no dos ni ninguna a medias.
    with obtener_conexion() as conexion:
        total = conexion.execute("SELECT COUNT(*) AS n FROM ventas").fetchone()["n"]
    assert total == 1


def test_obtener_por_clave_idempotencia_en_conexion(base_datos_temporal):
    producto = _crear_producto()
    items_con_precio = [(ItemVenta(producto.id, 1), 200)]
    with obtener_conexion() as conexion:
        repositorio_ventas.registrar_venta_con_detalle(
            conexion, 200, "EFECTIVO", items_con_precio,
            clave_idempotencia="clave-x", contenido_hash="hash-x",
        )

    with obtener_conexion() as conexion:
        resultado = repositorio_ventas.obtener_por_clave_idempotencia_en_conexion(conexion, "clave-x")

    assert resultado is not None
    venta, hash_guardado = resultado
    assert hash_guardado == "hash-x"
    assert venta.total_centavos == 200


def test_obtener_por_clave_idempotencia_en_conexion_inexistente_devuelve_none(base_datos_temporal):
    with obtener_conexion() as conexion:
        assert repositorio_ventas.obtener_por_clave_idempotencia_en_conexion(conexion, "no-existe") is None


class TestObtenerVentaConDetalle:
    """Fase 5D: lectura histórica de una venta + sus líneas para el
    ticket imprimible. Ninguna de estas pruebas toca `services/` --
    es exactamente la misma separación que ya usan las de arriba."""

    def test_venta_inexistente_devuelve_none(self, base_datos_temporal):
        assert repositorio_ventas.obtener_venta_con_detalle(9999) is None

    def test_venta_con_una_linea(self, base_datos_temporal):
        producto = _crear_producto()
        items_con_precio = [(ItemVenta(producto.id, 3), 200)]
        with obtener_conexion() as conexion:
            venta = repositorio_ventas.registrar_venta_con_detalle(conexion, 600, "EFECTIVO", items_con_precio)

        resultado = repositorio_ventas.obtener_venta_con_detalle(venta.id)

        assert resultado is not None
        assert resultado.venta.id == venta.id
        assert resultado.venta.total_centavos == 600
        assert resultado.venta.tipo_pago == "EFECTIVO"
        assert resultado.venta.fecha == venta.fecha
        assert len(resultado.lineas) == 1
        linea = resultado.lineas[0]
        assert linea.producto_nombre == "Alfajor"
        assert linea.cantidad == 3
        assert linea.precio_unitario_centavos == 200
        assert linea.subtotal_centavos == 600

    def test_venta_con_multiples_lineas_conserva_precios_y_subtotales_congelados(self, base_datos_temporal):
        p1 = repositorio_productos.crear_producto(
            Producto(codigo_barras="7790000000001", nombre="Alfajor", precio_costo_centavos=100, precio_venta_centavos=200)
        )
        p2 = repositorio_productos.crear_producto(
            Producto(codigo_barras="7790000000002", nombre="Gaseosa", precio_costo_centavos=150, precio_venta_centavos=300)
        )
        items_con_precio = [(ItemVenta(p1.id, 2), 200), (ItemVenta(p2.id, 1), 300)]
        with obtener_conexion() as conexion:
            venta = repositorio_ventas.registrar_venta_con_detalle(conexion, 700, "TARJETA", items_con_precio)

        resultado = repositorio_ventas.obtener_venta_con_detalle(venta.id)

        assert resultado is not None
        assert len(resultado.lineas) == 2
        nombres = {linea.producto_nombre for linea in resultado.lineas}
        assert nombres == {"Alfajor", "Gaseosa"}
        linea_alfajor = next(l for l in resultado.lineas if l.producto_nombre == "Alfajor")
        assert linea_alfajor.cantidad == 2
        assert linea_alfajor.precio_unitario_centavos == 200
        assert linea_alfajor.subtotal_centavos == 400

    def test_nombre_de_producto_renombrado_despues_de_la_venta_muestra_el_nombre_actual(self, base_datos_temporal):
        """Limitación conocida y aceptada (ver diseño de Fase 5D): el
        nombre no se historiza, solo el precio/subtotal."""
        producto = _crear_producto()
        items_con_precio = [(ItemVenta(producto.id, 1), 200)]
        with obtener_conexion() as conexion:
            venta = repositorio_ventas.registrar_venta_con_detalle(conexion, 200, "EFECTIVO", items_con_precio)

        with obtener_conexion() as conexion:
            conexion.execute("UPDATE productos SET nombre = ? WHERE id = ?", ("Alfajor Nuevo", producto.id))

        resultado = repositorio_ventas.obtener_venta_con_detalle(venta.id)

        assert resultado.lineas[0].producto_nombre == "Alfajor Nuevo"

    def test_producto_desactivado_despues_de_la_venta_sigue_apareciendo(self, base_datos_temporal):
        """El FK ON DELETE RESTRICT de detalle_venta garantiza que un
        producto con ventas nunca se borra físicamente, como mucho se
        desactiva (activo=0) -- el JOIN sigue encontrándolo."""
        producto = _crear_producto()
        items_con_precio = [(ItemVenta(producto.id, 1), 200)]
        with obtener_conexion() as conexion:
            venta = repositorio_ventas.registrar_venta_con_detalle(conexion, 200, "EFECTIVO", items_con_precio)

        with obtener_conexion() as conexion:
            conexion.execute("UPDATE productos SET activo = 0 WHERE id = ?", (producto.id,))

        resultado = repositorio_ventas.obtener_venta_con_detalle(venta.id)

        assert resultado is not None
        assert resultado.lineas[0].producto_nombre == "Alfajor"

    def test_resuelve_la_venta_completa_con_una_sola_sentencia_sql(self, base_datos_temporal, monkeypatch):
        """Guardrail contra N+1, ahora exigiendo la condición literal del
        diseño aprobado (JOIN único): `obtener_venta_con_detalle()` debe
        ejecutar EXACTAMENTE una sentencia SQL, sin importar cuántas
        líneas tenga la venta -- se prueba con una de 1 línea y otra de
        5, y en ambos casos el conteo debe dar 1 (no "igual entre sí",
        que sería compatible con 2+2 o con cualquier otro par igual).

        Cuenta con `sqlite3.Connection.set_trace_callback` (no se puede
        monkeypatchear `sqlite3.Connection.execute`: es un tipo
        inmutable de C). Como `db.repositorios.ventas` hizo
        `from db.conexion import obtener_conexion`, ese `import`
        creó su propio binding en este módulo -- para interceptarlo
        hay que parchear `repositorio_ventas.obtener_conexion`
        directamente, no `db.conexion.obtener_conexion` (mismo
        principio que ya documenta `conftest.py` para
        `RUTA_BASE_DATOS`)."""

        def _crear_venta_con_n_lineas(n):
            productos = [
                repositorio_productos.crear_producto(
                    Producto(
                        codigo_barras=f"7790000{n}{i:03d}",
                        nombre=f"Producto {n}-{i}",
                        precio_costo_centavos=100,
                        precio_venta_centavos=200,
                    )
                )
                for i in range(n)
            ]
            items_con_precio = [(ItemVenta(p.id, 1), 200) for p in productos]
            with obtener_conexion() as conexion:
                return repositorio_ventas.registrar_venta_con_detalle(
                    conexion, 200 * n, "EFECTIVO", items_con_precio
                )

        venta_1_linea = _crear_venta_con_n_lineas(1)
        venta_5_lineas = _crear_venta_con_n_lineas(5)

        def _contar_sentencias(venta_id):
            contador = {"n": 0}
            obtener_conexion_original = repositorio_ventas.obtener_conexion

            @contextmanager
            def obtener_conexion_contando():
                with obtener_conexion_original() as conexion:
                    conexion.set_trace_callback(lambda _: contador.update(n=contador["n"] + 1))
                    yield conexion

            monkeypatch.setattr(repositorio_ventas, "obtener_conexion", obtener_conexion_contando)
            try:
                resultado = repositorio_ventas.obtener_venta_con_detalle(venta_id)
            finally:
                monkeypatch.setattr(repositorio_ventas, "obtener_conexion", obtener_conexion_original)
            return contador["n"], resultado

        conteo_1, resultado_1 = _contar_sentencias(venta_1_linea.id)
        conteo_5, resultado_5 = _contar_sentencias(venta_5_lineas.id)

        assert len(resultado_1.lineas) == 1
        assert len(resultado_5.lineas) == 5
        assert conteo_1 == 1
        assert conteo_5 == 1
