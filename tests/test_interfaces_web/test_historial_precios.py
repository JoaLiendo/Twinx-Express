"""V1.2 Fase 1: historial de cambios de precio (migración 015): registro en
edición y en compras, sin ruido cuando no cambia, ventas históricas
intactas, atomicidad y pantalla de consulta."""

import pytest

from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from domain.compra import ItemCompra
from domain.usuario import Usuario
from domain.venta import ItemVenta
from excepciones import CodigoBarrasDuplicadoError
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth, servicio_caja, servicio_compras, servicio_precios, servicio_proveedores, servicio_stock, servicio_ventas

from ._asgi_cliente import solicitud


def _usuario(nombre="ana", rol="OWNER"):
    return repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre,
            nombre_completo=f"Nombre {nombre}",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol=rol,
        )
    )


def _cookies(nombre: str, rol: str) -> dict[str, str]:
    _usuario(nombre, rol)
    return {NOMBRE_COOKIE_SESION: servicio_auth.iniciar_sesion(nombre, "clave-correcta-123").token}


def _producto(costo=100, venta=200, codigo="7790000000001", nombre="Alfajor", stock=10):
    return servicio_stock.registrar_producto(codigo, nombre, costo, venta, stock_actual=stock)


def _editar(producto, *, costo=None, venta=None, nombre=None, usuario_id=None, codigo=None):
    return servicio_stock.actualizar_producto(
        producto_id=producto.id,
        codigo_barras=codigo or producto.codigo_barras,
        nombre=nombre or producto.nombre,
        precio_costo_centavos=producto.precio_costo_centavos if costo is None else costo,
        precio_venta_centavos=producto.precio_venta_centavos if venta is None else venta,
        stock_minimo=producto.stock_minimo,
        usuario_id=usuario_id,
    )


def _filas():
    with obtener_conexion() as conexion:
        return [dict(f) for f in conexion.execute("SELECT * FROM historial_precios ORDER BY id").fetchall()]


class TestRegistroEnEdicion:
    def test_cambio_de_precio_de_venta_registra_anterior_nuevo_usuario_y_origen(self, base_datos_temporal):
        usuario = _usuario()
        producto = _producto()

        _editar(producto, venta=250, usuario_id=usuario.id)

        (fila,) = _filas()
        assert (fila["producto_id"], fila["campo"], fila["precio_anterior_centavos"], fila["precio_nuevo_centavos"]) == (
            producto.id,
            "VENTA",
            200,
            250,
        )
        assert fila["usuario_id"] == usuario.id
        assert fila["origen"] == "EDICION"
        assert fila["fecha"]

    def test_cambio_de_costo_y_de_venta_registra_una_fila_por_campo(self, base_datos_temporal):
        producto = _producto()

        _editar(producto, costo=120, venta=260)

        assert sorted((f["campo"], f["precio_anterior_centavos"], f["precio_nuevo_centavos"]) for f in _filas()) == [
            ("COSTO", 100, 120),
            ("VENTA", 200, 260),
        ]

    def test_sin_cambio_de_precio_no_registra_nada(self, base_datos_temporal):
        producto = _producto()

        _editar(producto)
        _editar(producto, nombre="Alfajor triple")

        assert _filas() == []

    def test_guardar_el_mismo_precio_no_es_un_cambio(self, base_datos_temporal):
        producto = _producto()
        _editar(producto, venta=250)

        _editar(servicio_stock.obtener_por_id(producto.id), venta=250)

        assert len(_filas()) == 1

    def test_sin_usuario_autenticado_el_usuario_queda_null(self, base_datos_temporal):
        producto = _producto()

        _editar(producto, venta=250)

        assert _filas()[0]["usuario_id"] is None

    def test_una_edicion_fallida_no_deja_historial_ni_cambia_el_precio(self, base_datos_temporal):
        _producto(codigo="7790000000001", nombre="Alfajor")
        otro = _producto(codigo="7790000000002", nombre="Gaseosa", venta=500)

        with pytest.raises(CodigoBarrasDuplicadoError):
            _editar(otro, venta=999, codigo="7790000000001")

        assert _filas() == []
        assert servicio_stock.obtener_por_id(otro.id).precio_venta_centavos == 500

    def test_el_historial_de_un_producto_no_incluye_los_de_otro(self, base_datos_temporal):
        a = _producto(codigo="7790000000001", nombre="A")
        b = _producto(codigo="7790000000002", nombre="B")
        _editar(a, venta=300)
        _editar(b, venta=400)

        assert [c.precio_nuevo_centavos for c in servicio_precios.listar_historial_de_producto(a.id)] == [300]

    def test_lista_el_mas_reciente_primero_con_el_nombre_del_usuario(self, base_datos_temporal):
        usuario = _usuario()
        producto = _producto()
        _editar(producto, venta=250, usuario_id=usuario.id)
        _editar(servicio_stock.obtener_por_id(producto.id), venta=300, usuario_id=usuario.id)

        cambios = servicio_precios.listar_historial_de_producto(producto.id)

        assert [c.precio_nuevo_centavos for c in cambios] == [300, 250]
        assert cambios[0].usuario_nombre_completo == "Nombre ana"
        assert cambios[0].diferencia_centavos == 50


class TestRegistroEnCompras:
    def _preparar(self):
        return servicio_proveedores.crear_proveedor("Distribuidora SA"), _usuario(), _producto()

    def test_una_compra_con_otro_costo_registra_el_cambio_de_costo(self, base_datos_temporal):
        proveedor, usuario, producto = self._preparar()

        servicio_compras.registrar_compra(proveedor.id, usuario.id, [ItemCompra(producto.id, 5, 130)])

        (fila,) = _filas()
        assert (fila["campo"], fila["precio_anterior_centavos"], fila["precio_nuevo_centavos"], fila["origen"]) == (
            "COSTO",
            100,
            130,
            "COMPRA",
        )
        assert fila["usuario_id"] == usuario.id

    def test_una_compra_al_mismo_costo_no_registra_nada(self, base_datos_temporal):
        proveedor, usuario, producto = self._preparar()

        servicio_compras.registrar_compra(proveedor.id, usuario.id, [ItemCompra(producto.id, 5, 100)])

        assert _filas() == []

    def test_una_compra_rechazada_no_deja_historial(self, base_datos_temporal):
        from excepciones import ProductoNoEncontradoError

        proveedor, usuario, producto = self._preparar()

        with pytest.raises(ProductoNoEncontradoError):
            servicio_compras.registrar_compra(
                proveedor.id, usuario.id, [ItemCompra(producto.id, 5, 130), ItemCompra(99999, 1, 10)]
            )

        assert _filas() == []
        assert servicio_stock.obtener_por_id(producto.id).precio_costo_centavos == 100


class TestVentasHistoricasIntactas:
    def test_cambiar_el_precio_no_altera_el_precio_cobrado_en_ventas_anteriores(self, base_datos_temporal):
        servicio_caja.abrir_caja(0)
        producto = _producto(venta=200)
        venta_vieja = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")

        _editar(servicio_stock.obtener_por_id(producto.id), venta=300)
        venta_nueva = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")

        with obtener_conexion() as conexion:
            precios = {
                f["venta_id"]: f["precio_unitario_centavos"]
                for f in conexion.execute("SELECT venta_id, precio_unitario_centavos FROM detalle_venta")
            }
        assert precios == {venta_vieja.id: 200, venta_nueva.id: 300}
        assert venta_vieja.total_centavos == 200


class TestMigracion015:
    def test_no_admite_una_fila_sin_cambio_real(self, base_datos_temporal):
        import sqlite3

        producto = _producto()

        with pytest.raises(sqlite3.IntegrityError):
            with sqlite3.connect(base_datos_temporal) as conexion:
                conexion.execute(
                    "INSERT INTO historial_precios (producto_id, campo, precio_anterior_centavos,"
                    " precio_nuevo_centavos, origen) VALUES (?, 'VENTA', 100, 100, 'EDICION')",
                    (producto.id,),
                )

    def test_rechaza_campo_y_origen_invalidos(self, base_datos_temporal):
        import sqlite3

        producto = _producto()
        for campo, origen in (("OTRO", "EDICION"), ("VENTA", "MAGIA")):
            with pytest.raises(sqlite3.IntegrityError):
                with sqlite3.connect(base_datos_temporal) as conexion:
                    conexion.execute(
                        "INSERT INTO historial_precios (producto_id, campo, precio_anterior_centavos,"
                        " precio_nuevo_centavos, origen) VALUES (?, ?, 100, 200, ?)",
                        (producto.id, campo, origen),
                    )


class TestPantallaYRuta:
    def test_editar_por_http_registra_el_usuario_autenticado_y_la_pantalla_lo_muestra(self, base_datos_temporal):
        cookies = _cookies("duenio", "OWNER")
        producto = _producto()

        respuesta = solicitud(
            "POST",
            f"/productos/{producto.id}/editar",
            cookies=cookies,
            formulario={
                "codigo_barras": producto.codigo_barras,
                "nombre": producto.nombre,
                "precio_costo": "1.00",
                "precio_venta": "3.50",
                "stock_minimo": "0",
                "categoria_id": "",
                "unidad_medida": "UNIDAD",
            },
        )
        pantalla = solicitud("GET", f"/productos/{producto.id}/precios", cookies=cookies)

        assert respuesta.status == 303 and "tipo=success" in respuesta.header("location")
        assert pantalla.status == 200
        assert "Nombre duenio" in pantalla.texto
        assert "2,00" in pantalla.texto and "3,50" in pantalla.texto
        assert "Edición" in pantalla.texto

    def test_sin_cambios_muestra_el_estado_vacio(self, base_datos_temporal):
        cookies = _cookies("duenio", "OWNER")
        producto = _producto()

        pantalla = solicitud("GET", f"/productos/{producto.id}/precios", cookies=cookies)

        assert pantalla.status == 200
        assert "Sin cambios registrados" in pantalla.texto

    def test_solo_el_owner_puede_ver_el_historial(self, base_datos_temporal):
        producto = _producto()

        assert solicitud("GET", f"/productos/{producto.id}/precios", cookies=_cookies("cajera", "CASHIER")).status == 403
        assert solicitud("GET", f"/productos/{producto.id}/precios").status == 303

    def test_producto_inexistente_redirige_con_error(self, base_datos_temporal):
        cookies = _cookies("duenio", "OWNER")

        respuesta = solicitud("GET", "/productos/9999/precios", cookies=cookies)

        assert respuesta.status == 303 and "tipo=error" in respuesta.header("location")

    def test_la_pantalla_de_edicion_enlaza_al_historial(self, base_datos_temporal):
        cookies = _cookies("duenio", "OWNER")
        producto = _producto()

        pantalla = solicitud("GET", f"/productos/{producto.id}/editar", cookies=cookies)

        assert f"/productos/{producto.id}/precios" in pantalla.texto


class TestBajaDeProductoConHistorial:
    """M4: el historial de precios no rompe la baja existente y el motivo que se
    informa es el real (no siempre "ventas")."""

    def _baja_por_http(self, cookies, producto):
        return solicitud("POST", f"/productos/{producto.id}/eliminar", cookies=cookies)

    def test_producto_con_historial_y_sin_ventas_se_da_de_baja_con_el_motivo_real(self, base_datos_temporal):
        usuario = _usuario()
        producto = _producto()
        _editar(producto, venta=300, usuario_id=usuario.id)

        baja_logica = servicio_stock.eliminar_producto(producto.id, usuario_id=usuario.id)

        assert baja_logica is True
        with obtener_conexion() as conexion:
            fila = conexion.execute("SELECT activo FROM productos WHERE id = ?", (producto.id,)).fetchone()
            resumen = conexion.execute("SELECT resumen FROM auditoria WHERE accion = 'PRODUCTO_BAJA'").fetchone()[0]
        assert fila["activo"] == 0
        assert "cambios de precio" in resumen and "ventas" not in resumen
        assert servicio_stock.motivos_de_conservacion(producto.id) == ["cambios de precio"]

    def test_el_mensaje_web_indica_la_razon_real(self, base_datos_temporal):
        cookies = _cookies("duenio", "OWNER")
        producto = _producto()
        _editar(producto, venta=300)

        respuesta = self._baja_por_http(cookies, producto)

        ubicacion = respuesta.header("location")
        assert respuesta.status == 303 and "tipo=success" in ubicacion
        assert "cambios%20de%20precio" in ubicacion or "cambios+de+precio" in ubicacion
        assert "ventas" not in ubicacion

    def test_con_ventas_y_cambios_de_precio_se_informan_ambos_motivos(self, base_datos_temporal):
        servicio_caja.abrir_caja(0)
        producto = _producto()
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        _editar(servicio_stock.obtener_por_id(producto.id), venta=300)

        assert servicio_stock.eliminar_producto(producto.id) is True
        assert servicio_stock.motivos_de_conservacion(producto.id) == ["ventas", "cambios de precio"]

    def test_un_producto_sin_ningun_registro_sigue_eliminandose_fisicamente(self, base_datos_temporal):
        producto = _producto()

        assert servicio_stock.eliminar_producto(producto.id) is False

        with obtener_conexion() as conexion:
            assert conexion.execute("SELECT COUNT(*) FROM productos").fetchone()[0] == 0

    def test_el_producto_dado_de_baja_por_historial_se_puede_reactivar_y_conserva_su_historial(self, base_datos_temporal):
        producto = _producto()
        _editar(producto, venta=300)
        servicio_stock.eliminar_producto(producto.id)

        reactivado = servicio_stock.reactivar_producto(producto.id)

        assert reactivado.activo is True and reactivado.precio_venta_centavos == 300
        assert len(servicio_precios.listar_historial_de_producto(producto.id)) == 1

    def test_una_baja_de_un_producto_inexistente_o_ya_inactivo_sigue_fallando(self, base_datos_temporal):
        from excepciones import ProductoNoEncontradoError

        producto = _producto()
        _editar(producto, venta=300)
        servicio_stock.eliminar_producto(producto.id)

        with pytest.raises(ProductoNoEncontradoError):
            servicio_stock.eliminar_producto(producto.id)
        with pytest.raises(ProductoNoEncontradoError):
            servicio_stock.eliminar_producto(9999)
