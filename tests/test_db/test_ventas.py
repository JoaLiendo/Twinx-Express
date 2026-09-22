"""Pruebas de integración de db.repositorios.ventas para la idempotencia
de Fase 5A. El resto del repositorio (alta de venta+detalle, listado
del día) ya está cubierto end-to-end por tests/test_servicio_ventas.py."""

import secrets
import sqlite3
from contextlib import contextmanager

import pytest

import db.conexion as modulo_conexion
from db.conexion import obtener_conexion
from db.repositorios import productos as repositorio_productos
from db.repositorios import usuarios as repositorio_usuarios
from db.repositorios import ventas as repositorio_ventas
from domain.producto import Producto
from domain.usuario import Usuario
from domain.venta import ItemVenta
from excepciones import ErrorBaseDatos


def _crear_producto(codigo="7790000000001"):
    return repositorio_productos.crear_producto(
        Producto(codigo_barras=codigo, nombre="Alfajor", precio_costo_centavos=100, precio_venta_centavos=200)
    )


def _crear_usuario(nombre_usuario="cajera1", rol="CASHIER"):
    return repositorio_usuarios.crear_usuario(
        Usuario(nombre_usuario=nombre_usuario, nombre_completo="Test", password_hash="hash", rol=rol)
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


class TestHistoricoDeCostoYUsuario:
    """Migración 009: `ventas.usuario_id` y
    `detalle_venta.costo_unitario_centavos`, ambos aditivos y opcionales
    en `registrar_venta_con_detalle` -- ver `services.servicio_ventas`
    para el flujo real que siempre los provee."""

    def test_persiste_usuario_id(self, base_datos_temporal):
        producto = _crear_producto()
        usuario = _crear_usuario()
        items_con_precio = [(ItemVenta(producto.id, 1), 200)]

        with obtener_conexion() as conexion:
            repositorio_ventas.registrar_venta_con_detalle(
                conexion, 200, "EFECTIVO", items_con_precio, usuario_id=usuario.id
            )

        with obtener_conexion() as conexion:
            fila = conexion.execute("SELECT usuario_id FROM ventas").fetchone()
        assert fila["usuario_id"] == usuario.id

    def test_sin_usuario_id_queda_null(self, base_datos_temporal):
        """Compatibilidad con el CLI: `usuario_id` es opcional, por
        defecto `None` -- no se inventa ningún usuario."""
        producto = _crear_producto()
        items_con_precio = [(ItemVenta(producto.id, 1), 200)]

        with obtener_conexion() as conexion:
            repositorio_ventas.registrar_venta_con_detalle(conexion, 200, "EFECTIVO", items_con_precio)

        with obtener_conexion() as conexion:
            fila = conexion.execute("SELECT usuario_id FROM ventas").fetchone()
        assert fila["usuario_id"] is None

    def test_persiste_costo_unitario_por_producto(self, base_datos_temporal):
        producto = _crear_producto()
        items_con_precio = [(ItemVenta(producto.id, 2), 200)]

        with obtener_conexion() as conexion:
            repositorio_ventas.registrar_venta_con_detalle(
                conexion,
                400,
                "EFECTIVO",
                items_con_precio,
                costos_unitarios_por_producto_id={producto.id: 100},
            )

        with obtener_conexion() as conexion:
            fila = conexion.execute("SELECT costo_unitario_centavos FROM detalle_venta").fetchone()
        assert fila["costo_unitario_centavos"] == 100

    def test_producto_sin_entrada_en_el_diccionario_de_costos_queda_null(self, base_datos_temporal):
        p1 = repositorio_productos.crear_producto(
            Producto(codigo_barras="7790000000001", nombre="Alfajor", precio_costo_centavos=100, precio_venta_centavos=200)
        )
        p2 = repositorio_productos.crear_producto(
            Producto(codigo_barras="7790000000002", nombre="Gaseosa", precio_costo_centavos=150, precio_venta_centavos=300)
        )
        items_con_precio = [(ItemVenta(p1.id, 1), 200), (ItemVenta(p2.id, 1), 300)]

        with obtener_conexion() as conexion:
            # Solo p1 tiene costo conocido -- no se inventa uno para p2.
            repositorio_ventas.registrar_venta_con_detalle(
                conexion, 500, "EFECTIVO", items_con_precio, costos_unitarios_por_producto_id={p1.id: 100}
            )

        with obtener_conexion() as conexion:
            filas = conexion.execute(
                "SELECT producto_id, costo_unitario_centavos FROM detalle_venta ORDER BY id"
            ).fetchall()
        costos_por_producto = {fila["producto_id"]: fila["costo_unitario_centavos"] for fila in filas}
        assert costos_por_producto[p1.id] == 100
        assert costos_por_producto[p2.id] is None

    def test_sin_diccionario_de_costos_todo_queda_null(self, base_datos_temporal):
        """Compatibilidad con los tests/llamados existentes que no pasan
        `costos_unitarios_por_producto_id`."""
        producto = _crear_producto()
        items_con_precio = [(ItemVenta(producto.id, 1), 200)]

        with obtener_conexion() as conexion:
            repositorio_ventas.registrar_venta_con_detalle(conexion, 200, "EFECTIVO", items_con_precio)

        with obtener_conexion() as conexion:
            fila = conexion.execute("SELECT costo_unitario_centavos FROM detalle_venta").fetchone()
        assert fila["costo_unitario_centavos"] is None

    def test_migracion_009_agrega_las_columnas_nuevas(self, base_datos_temporal):
        with obtener_conexion() as conexion:
            columnas_ventas = {fila["name"] for fila in conexion.execute("PRAGMA table_info(ventas)").fetchall()}
            columnas_detalle = {
                fila["name"] for fila in conexion.execute("PRAGMA table_info(detalle_venta)").fetchall()
            }
        assert "usuario_id" in columnas_ventas
        assert "costo_unitario_centavos" in columnas_detalle

    def test_migracion_009_no_inventa_datos_para_ventas_anteriores(self, tmp_path, monkeypatch):
        """Simula una DB que ya tenía ventas registradas antes de que
        existiera esta migración: aplica solo 001-008 a mano, inserta una
        venta+detalle con el esquema viejo, y recién después corre
        `inicializar_base_datos()` completo (aplica 009 en adelante).
        La fila vieja debe quedar con ambas columnas nuevas en NULL --
        nunca con un valor inventado retroactivamente.
        """
        ruta_bd = tmp_path / "test_kiosco_pre_009.db"
        monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta_bd)

        rutas_previas = [
            ruta
            for ruta in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql"))
            if ruta.name < "009_costo_y_usuario_ventas.sql"
        ]
        assert rutas_previas, "no se encontraron migraciones anteriores a la 009"

        with modulo_conexion.obtener_conexion() as conexion:
            conexion.execute(modulo_conexion._TABLA_MIGRACIONES)
            for ruta in rutas_previas:
                conexion.executescript(ruta.read_text(encoding="utf-8"))
                conexion.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (ruta.name,))
            conexion.execute(
                """
                INSERT INTO productos (codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos)
                VALUES ('7790000000099', 'Producto viejo', 100, 200)
                """
            )
            conexion.execute("INSERT INTO ventas (total_centavos, tipo_pago) VALUES (200, 'EFECTIVO')")
            conexion.execute(
                """
                INSERT INTO detalle_venta
                    (venta_id, producto_id, cantidad, precio_unitario_centavos, subtotal_centavos)
                VALUES (1, 1, 1, 200, 200)
                """
            )

        # Recién ahora se aplica la migración 009 (y cualquier otra pendiente).
        modulo_conexion.inicializar_base_datos()

        with modulo_conexion.obtener_conexion() as conexion:
            fila_venta = conexion.execute("SELECT usuario_id FROM ventas WHERE id = 1").fetchone()
            fila_detalle = conexion.execute(
                "SELECT costo_unitario_centavos FROM detalle_venta WHERE venta_id = 1"
            ).fetchone()

        assert fila_venta["usuario_id"] is None
        assert fila_detalle["costo_unitario_centavos"] is None


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


class TestListarEnRango:
    """Módulo de Reportes: filtro de ventas por rango de fechas. Mismo
    criterio que `db.repositorios.compras.listar_resumen` (ver sus
    tests): fecha_desde/fecha_hasta son inclusivas, y un rango que no
    matchea ninguna fila da lista vacía, nunca error."""

    def _venta_de_hoy(self):
        # Código de barras único por llamada (varios productos por test):
        # evita chocar con el UNIQUE de `codigo_barras`.
        producto = _crear_producto(codigo=f"779{secrets.token_hex(5)}")
        items_con_precio = [(ItemVenta(producto.id, 1), 200)]
        with obtener_conexion() as conexion:
            return repositorio_ventas.registrar_venta_con_detalle(conexion, 200, "EFECTIVO", items_con_precio)

    def test_sin_filtros_devuelve_todas(self, base_datos_temporal):
        self._venta_de_hoy()
        self._venta_de_hoy()

        assert len(repositorio_ventas.listar_en_rango()) == 2

    def test_rango_que_incluye_hoy_encuentra_la_venta(self, base_datos_temporal):
        self._venta_de_hoy()
        hoy = repositorio_ventas.listar_en_rango()[0].fecha[:10]

        resultado = repositorio_ventas.listar_en_rango(fecha_desde=hoy, fecha_hasta=hoy)

        assert len(resultado) == 1

    def test_rango_futuro_no_encuentra_nada(self, base_datos_temporal):
        self._venta_de_hoy()

        assert repositorio_ventas.listar_en_rango(fecha_desde="2099-01-01") == []

    def test_rango_pasado_no_encuentra_nada(self, base_datos_temporal):
        self._venta_de_hoy()

        assert repositorio_ventas.listar_en_rango(fecha_hasta="1999-01-01") == []

    def test_venta_fuera_de_rango_por_fecha_manipulada_queda_excluida(self, base_datos_temporal):
        """A diferencia de los tests de arriba (que solo prueban límites
        muy lejanos), esta manipula `fecha` directamente para confirmar
        que el límite es exacto, no solo "aproximadamente hoy"."""
        venta_vieja = self._venta_de_hoy()
        with obtener_conexion() as conexion:
            conexion.execute("UPDATE ventas SET fecha = ? WHERE id = ?", ("2020-01-01 10:00:00", venta_vieja.id))
        self._venta_de_hoy()

        resultado = repositorio_ventas.listar_en_rango(fecha_desde="2020-01-02")

        assert len(resultado) == 1
        assert resultado[0].id != venta_vieja.id


class TestListarProductosMasVendidosEnRango:
    """Módulo de Reportes: ranking de productos por unidades vendidas."""

    def test_ordena_por_unidades_vendidas_descendente(self, base_datos_temporal):
        mas_vendido = repositorio_productos.crear_producto(
            Producto(codigo_barras="7790000000010", nombre="Top", precio_costo_centavos=100, precio_venta_centavos=200)
        )
        menos_vendido = repositorio_productos.crear_producto(
            Producto(codigo_barras="7790000000011", nombre="Menos", precio_costo_centavos=100, precio_venta_centavos=200)
        )
        with obtener_conexion() as conexion:
            repositorio_ventas.registrar_venta_con_detalle(
                conexion, 1000, "EFECTIVO", [(ItemVenta(mas_vendido.id, 5), 200)]
            )
        with obtener_conexion() as conexion:
            repositorio_ventas.registrar_venta_con_detalle(
                conexion, 200, "EFECTIVO", [(ItemVenta(menos_vendido.id, 1), 200)]
            )

        resultado = repositorio_ventas.listar_productos_mas_vendidos_en_rango()

        assert [p.producto_nombre for p in resultado] == ["Top", "Menos"]
        assert resultado[0].unidades_vendidas == 5
        assert resultado[0].total_vendido_centavos == 1000

    def test_suma_unidades_de_varias_ventas_del_mismo_producto(self, base_datos_temporal):
        producto = _crear_producto()
        with obtener_conexion() as conexion:
            repositorio_ventas.registrar_venta_con_detalle(conexion, 200, "EFECTIVO", [(ItemVenta(producto.id, 1), 200)])
        with obtener_conexion() as conexion:
            repositorio_ventas.registrar_venta_con_detalle(conexion, 400, "EFECTIVO", [(ItemVenta(producto.id, 2), 200)])

        resultado = repositorio_ventas.listar_productos_mas_vendidos_en_rango()

        assert len(resultado) == 1
        assert resultado[0].unidades_vendidas == 3
        assert resultado[0].total_vendido_centavos == 600

    def test_respeta_el_limite(self, base_datos_temporal):
        for i in range(3):
            producto = repositorio_productos.crear_producto(
                Producto(codigo_barras=f"77900000002{i}", nombre=f"Producto {i}", precio_costo_centavos=100, precio_venta_centavos=200)
            )
            with obtener_conexion() as conexion:
                repositorio_ventas.registrar_venta_con_detalle(
                    conexion, 200, "EFECTIVO", [(ItemVenta(producto.id, 1), 200)]
                )

        assert len(repositorio_ventas.listar_productos_mas_vendidos_en_rango(limite=2)) == 2

    def test_filtra_por_rango_de_fechas(self, base_datos_temporal):
        producto = _crear_producto()
        with obtener_conexion() as conexion:
            venta_vieja = repositorio_ventas.registrar_venta_con_detalle(
                conexion, 200, "EFECTIVO", [(ItemVenta(producto.id, 1), 200)]
            )
        with obtener_conexion() as conexion:
            conexion.execute("UPDATE ventas SET fecha = ? WHERE id = ?", ("2020-01-01 10:00:00", venta_vieja.id))

        assert repositorio_ventas.listar_productos_mas_vendidos_en_rango(fecha_desde="2020-01-02") == []

    def test_sin_ventas_devuelve_lista_vacia(self, base_datos_temporal):
        assert repositorio_ventas.listar_productos_mas_vendidos_en_rango() == []
