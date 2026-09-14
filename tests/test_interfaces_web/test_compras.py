"""Pruebas HTTP end-to-end de ingreso de mercadería / compras (Fase 4B/4C)."""

from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from domain.compra import ItemCompra
from domain.usuario import Usuario
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth, servicio_compras, servicio_proveedores, servicio_stock

from ._asgi_cliente import solicitud


def _crear_usuario_y_loguearse(rol: str, nombre_usuario: str) -> str:
    repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre_usuario,
            nombre_completo="Usuario de prueba",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol=rol,
        )
    )
    return servicio_auth.iniciar_sesion(nombre_usuario, "clave-correcta-123").token


def _cookies_owner(nombre_usuario: str = "ana") -> dict[str, str]:
    return {NOMBRE_COOKIE_SESION: _crear_usuario_y_loguearse("OWNER", nombre_usuario)}


def _proveedor_y_producto():
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=5)
    return proveedor, producto


class TestListadoYDetalle:
    def test_listado_muestra_las_compras_registradas(self, base_datos_temporal):
        proveedor, producto = _proveedor_y_producto()
        cookies = _cookies_owner()
        usuario = repositorio_usuarios.obtener_por_nombre_usuario("ana")

        servicio_compras.registrar_compra(proveedor.id, usuario.id, [ItemCompra(producto.id, 3, 120)])

        respuesta = solicitud("GET", "/compras", cookies=cookies)

        assert respuesta.status == 200
        assert "Distribuidora SA" in respuesta.texto

    def test_ver_detalle_de_una_compra(self, base_datos_temporal):
        proveedor, producto = _proveedor_y_producto()
        cookies = _cookies_owner()
        usuario = repositorio_usuarios.obtener_por_nombre_usuario("ana")

        compra = servicio_compras.registrar_compra(
            proveedor.id, usuario.id, [ItemCompra(producto.id, 3, 120)], observaciones="Entrega parcial"
        )

        respuesta = solicitud("GET", f"/compras/{compra.id}", cookies=cookies)

        assert respuesta.status == 200
        assert "Alfajor" in respuesta.texto
        assert "Entrega parcial" in respuesta.texto

    def test_ver_detalle_de_compra_inexistente_no_rompe(self, base_datos_temporal):
        cookies = _cookies_owner()

        respuesta = solicitud("GET", "/compras/9999", cookies=cookies)

        assert respuesta.status == 303


class TestHistorial:
    """Fase 4C: el listado muestra N° de compra, proveedor, usuario
    (nombre completo), cantidad de líneas y total, más reciente primero."""

    def test_listado_muestra_los_campos_del_historial(self, base_datos_temporal):
        proveedor, producto = _proveedor_y_producto()
        cookies = _cookies_owner()
        usuario = repositorio_usuarios.obtener_por_nombre_usuario("ana")

        compra = servicio_compras.registrar_compra(proveedor.id, usuario.id, [ItemCompra(producto.id, 3, 120)])

        respuesta = solicitud("GET", "/compras", cookies=cookies)

        assert respuesta.status == 200
        assert f"#{compra.id}" in respuesta.texto or str(compra.id) in respuesta.texto
        assert "Distribuidora SA" in respuesta.texto
        assert usuario.nombre_completo in respuesta.texto
        assert usuario.nombre_usuario not in respuesta.texto  # nombre de usuario, no el de login

    def test_listado_muestra_orden_mas_reciente_primero(self, base_datos_temporal):
        proveedor, producto = _proveedor_y_producto()
        cookies = _cookies_owner()
        usuario = repositorio_usuarios.obtener_por_nombre_usuario("ana")
        servicio_compras.registrar_compra(proveedor.id, usuario.id, [ItemCompra(producto.id, 1, 100)])
        ultima = servicio_compras.registrar_compra(proveedor.id, usuario.id, [ItemCompra(producto.id, 1, 100)])

        respuesta = solicitud("GET", "/compras", cookies=cookies)

        assert respuesta.texto.index(f"#{ultima.id}") < respuesta.texto.index(f"#{ultima.id - 1}")

    def test_estado_vacio_sin_compras(self, base_datos_temporal):
        cookies = _cookies_owner()

        respuesta = solicitud("GET", "/compras", cookies=cookies)

        assert respuesta.status == 200
        assert "no hay compras" in respuesta.texto.lower()


class TestFiltrosDeHistorial:
    def test_filtra_por_proveedor(self, base_datos_temporal):
        proveedor_1, producto = _proveedor_y_producto()
        proveedor_2 = servicio_proveedores.crear_proveedor("Mayorista Norte")
        cookies = _cookies_owner()
        usuario = repositorio_usuarios.obtener_por_nombre_usuario("ana")
        servicio_compras.registrar_compra(proveedor_1.id, usuario.id, [ItemCompra(producto.id, 1, 100)])
        servicio_compras.registrar_compra(proveedor_2.id, usuario.id, [ItemCompra(producto.id, 1, 100)])

        respuesta = solicitud("GET", f"/compras?proveedor_id={proveedor_2.id}", cookies=cookies)

        assert respuesta.status == 200
        # "Mayorista Norte" aparece en el <select> del filtro Y en la fila de
        # su compra (2 veces); "Distribuidora SA" solo en el <select>, filtrada
        # de la tabla (1 vez) -- el <select> de proveedores siempre lista a
        # todos, filtrados o no, por eso no alcanza con "in"/"not in".
        assert respuesta.texto.count("Mayorista Norte") >= 2
        assert respuesta.texto.count("Distribuidora SA") == 1

    def test_filtra_por_rango_de_fechas(self, base_datos_temporal):
        proveedor, producto = _proveedor_y_producto()
        cookies = _cookies_owner()
        usuario = repositorio_usuarios.obtener_por_nombre_usuario("ana")
        compra = servicio_compras.registrar_compra(proveedor.id, usuario.id, [ItemCompra(producto.id, 1, 100)])
        hoy = compra.fecha[:10]

        respuesta = solicitud("GET", f"/compras?fecha_desde={hoy}&fecha_hasta={hoy}", cookies=cookies)
        assert respuesta.status == 200
        assert respuesta.texto.count("Distribuidora SA") >= 2  # <select> + fila de la compra

        respuesta_futura = solicitud("GET", "/compras?fecha_desde=2099-01-01", cookies=cookies)
        assert respuesta_futura.status == 200
        assert "no se encontraron compras" in respuesta_futura.texto.lower()

    def test_combinacion_de_filtros_sin_resultados_muestra_estado_vacio(self, base_datos_temporal):
        proveedor, producto = _proveedor_y_producto()
        cookies = _cookies_owner()
        usuario = repositorio_usuarios.obtener_por_nombre_usuario("ana")
        servicio_compras.registrar_compra(proveedor.id, usuario.id, [ItemCompra(producto.id, 1, 100)])

        respuesta = solicitud(
            "GET", f"/compras?proveedor_id={proveedor.id}&fecha_desde=2099-01-01", cookies=cookies
        )

        assert respuesta.status == 200
        assert "no se encontraron compras" in respuesta.texto.lower()

    def test_proveedor_inexistente_en_filtro_no_rompe(self, base_datos_temporal):
        cookies = _cookies_owner()

        respuesta = solicitud("GET", "/compras?proveedor_id=9999", cookies=cookies)

        assert respuesta.status == 200

    def test_fecha_invalida_en_filtro_no_rompe(self, base_datos_temporal):
        cookies = _cookies_owner()

        respuesta = solicitud("GET", "/compras?fecha_desde=no-es-una-fecha", cookies=cookies)

        assert respuesta.status == 200


class TestDetalleEnriquecido:
    def test_detalle_muestra_usuario_y_unidad_de_medida(self, base_datos_temporal):
        proveedor, producto = _proveedor_y_producto()
        cookies = _cookies_owner()
        usuario = repositorio_usuarios.obtener_por_nombre_usuario("ana")

        compra = servicio_compras.registrar_compra(proveedor.id, usuario.id, [ItemCompra(producto.id, 3, 120)])

        respuesta = solicitud("GET", f"/compras/{compra.id}", cookies=cookies)

        assert respuesta.status == 200
        assert usuario.nombre_completo in respuesta.texto
        assert "Unidad" in respuesta.texto  # etiqueta de unidad_medida='UNIDAD'

    def test_detalle_conserva_nombre_de_proveedor_inactivo(self, base_datos_temporal):
        proveedor, producto = _proveedor_y_producto()
        cookies = _cookies_owner()
        usuario = repositorio_usuarios.obtener_por_nombre_usuario("ana")
        compra = servicio_compras.registrar_compra(proveedor.id, usuario.id, [ItemCompra(producto.id, 1, 100)])
        servicio_proveedores.eliminar_proveedor(proveedor.id)  # cae a baja lógica (tiene una compra)

        respuesta = solicitud("GET", f"/compras/{compra.id}", cookies=cookies)

        assert respuesta.status == 200
        assert "Distribuidora SA" in respuesta.texto

    def test_detalle_conserva_nombre_de_producto_inactivo(self, base_datos_temporal):
        proveedor, producto = _proveedor_y_producto()
        cookies = _cookies_owner()
        usuario = repositorio_usuarios.obtener_por_nombre_usuario("ana")
        compra = servicio_compras.registrar_compra(proveedor.id, usuario.id, [ItemCompra(producto.id, 1, 100)])
        servicio_stock.eliminar_producto(producto.id)  # cae a baja lógica (tiene una compra)

        respuesta = solicitud("GET", f"/compras/{compra.id}", cookies=cookies)

        assert respuesta.status == 200
        assert "Alfajor" in respuesta.texto


class TestInmutabilidad:
    def test_no_existen_rutas_de_edicion_eliminacion_ni_anulacion(self, base_datos_temporal):
        proveedor, producto = _proveedor_y_producto()
        cookies = _cookies_owner()
        usuario = repositorio_usuarios.obtener_por_nombre_usuario("ana")
        compra = servicio_compras.registrar_compra(proveedor.id, usuario.id, [ItemCompra(producto.id, 1, 100)])

        for ruta in (f"/compras/{compra.id}/editar", f"/compras/{compra.id}/eliminar", f"/compras/{compra.id}/anular"):
            respuesta = solicitud("POST", ruta, cookies=cookies)
            assert respuesta.status == 404, f"POST {ruta} debería dar 404 (no existe), dio {respuesta.status}"

        respuesta_delete = solicitud("DELETE", f"/compras/{compra.id}", cookies=cookies)
        assert respuesta_delete.status == 405


class TestFormularioYAlta:
    def test_owner_ve_el_formulario_de_alta(self, base_datos_temporal):
        _proveedor_y_producto()
        cookies = _cookies_owner()

        respuesta = solicitud("GET", "/compras/nueva", cookies=cookies)

        assert respuesta.status == 200
        assert "Distribuidora SA" in respuesta.texto
        assert "Alfajor" in respuesta.texto

    def test_owner_puede_registrar_una_compra_con_una_linea(self, base_datos_temporal):
        proveedor, producto = _proveedor_y_producto()
        cookies = _cookies_owner()

        respuesta = solicitud(
            "POST",
            "/compras/nueva",
            cookies=cookies,
            formulario={
                "proveedor_id": str(proveedor.id),
                "observaciones": "Entrega parcial",
                "producto_id": [str(producto.id)],
                "cantidad": ["10"],
                "costo_unitario": ["1.20"],
            },
        )

        assert respuesta.status == 303
        compras = servicio_compras.listar_todas()
        assert len(compras) == 1
        assert compras[0].total_centavos == 1200
        producto_actualizado = servicio_stock.obtener_por_id(producto.id)
        assert producto_actualizado.stock_actual == 15
        assert producto_actualizado.precio_costo_centavos == 120

    def test_owner_puede_registrar_una_compra_con_varias_lineas(self, base_datos_temporal):
        proveedor, producto_1 = _proveedor_y_producto()
        producto_2 = servicio_stock.registrar_producto("7790000000002", "Gaseosa", 300, 500, stock_actual=2)
        cookies = _cookies_owner()

        respuesta = solicitud(
            "POST",
            "/compras/nueva",
            cookies=cookies,
            formulario={
                "proveedor_id": str(proveedor.id),
                "observaciones": "",
                "producto_id": [str(producto_1.id), str(producto_2.id)],
                "cantidad": ["10", "3"],
                "costo_unitario": ["1.20", "2.80"],
            },
        )

        assert respuesta.status == 303
        compra = servicio_compras.listar_todas()[0]
        assert compra.total_centavos == 10 * 120 + 3 * 280
        assert len(servicio_compras.listar_detalle(compra.id)) == 2

    def test_producto_duplicado_en_el_formulario_no_rompe_y_no_persiste(self, base_datos_temporal):
        proveedor, producto = _proveedor_y_producto()
        cookies = _cookies_owner()

        respuesta = solicitud(
            "POST",
            "/compras/nueva",
            cookies=cookies,
            formulario={
                "proveedor_id": str(proveedor.id),
                "observaciones": "",
                "producto_id": [str(producto.id), str(producto.id)],
                "cantidad": ["5", "3"],
                "costo_unitario": ["1.00", "1.10"],
            },
        )

        assert respuesta.status == 303  # error controlado, no 500
        assert servicio_compras.listar_todas() == []

    def test_proveedor_inexistente_no_rompe_y_no_persiste(self, base_datos_temporal):
        _, producto = _proveedor_y_producto()
        cookies = _cookies_owner()

        respuesta = solicitud(
            "POST",
            "/compras/nueva",
            cookies=cookies,
            formulario={
                "proveedor_id": "9999",
                "observaciones": "",
                "producto_id": [str(producto.id)],
                "cantidad": ["1"],
                "costo_unitario": ["1.00"],
            },
        )

        assert respuesta.status == 303
        assert servicio_compras.listar_todas() == []

    def test_producto_inexistente_no_rompe_y_no_persiste(self, base_datos_temporal):
        proveedor, _ = _proveedor_y_producto()
        cookies = _cookies_owner()

        respuesta = solicitud(
            "POST",
            "/compras/nueva",
            cookies=cookies,
            formulario={
                "proveedor_id": str(proveedor.id),
                "observaciones": "",
                "producto_id": ["9999"],
                "cantidad": ["1"],
                "costo_unitario": ["1.00"],
            },
        )

        assert respuesta.status == 303
        assert servicio_compras.listar_todas() == []

    def test_cantidad_cero_no_rompe_y_no_persiste(self, base_datos_temporal):
        proveedor, producto = _proveedor_y_producto()
        cookies = _cookies_owner()

        respuesta = solicitud(
            "POST",
            "/compras/nueva",
            cookies=cookies,
            formulario={
                "proveedor_id": str(proveedor.id),
                "observaciones": "",
                "producto_id": [str(producto.id)],
                "cantidad": ["0"],
                "costo_unitario": ["1.00"],
            },
        )

        assert respuesta.status == 303
        assert servicio_compras.listar_todas() == []

    def test_costo_negativo_no_rompe_y_no_persiste(self, base_datos_temporal):
        proveedor, producto = _proveedor_y_producto()
        cookies = _cookies_owner()

        respuesta = solicitud(
            "POST",
            "/compras/nueva",
            cookies=cookies,
            formulario={
                "proveedor_id": str(proveedor.id),
                "observaciones": "",
                "producto_id": [str(producto.id)],
                "cantidad": ["1"],
                "costo_unitario": ["-1.00"],
            },
        )

        assert respuesta.status == 303
        assert servicio_compras.listar_todas() == []

    def test_sin_lineas_da_respuesta_controlada_sin_500(self, base_datos_temporal):
        proveedor, _ = _proveedor_y_producto()
        cookies = _cookies_owner()

        respuesta = solicitud(
            "POST",
            "/compras/nueva",
            cookies=cookies,
            formulario={"proveedor_id": str(proveedor.id), "observaciones": ""},
        )

        assert respuesta.status == 422  # FastAPI: faltan los campos requeridos producto_id/cantidad/costo_unitario
        assert servicio_compras.listar_todas() == []


class TestPermisos:
    def test_cashier_recibe_403_en_todas_las_rutas(self, base_datos_temporal):
        proveedor, producto = _proveedor_y_producto()
        owner_id = repositorio_usuarios.crear_usuario(
            Usuario(
                nombre_usuario="ana",
                nombre_completo="Ana",
                password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
                rol="OWNER",
            )
        ).id
        compra = servicio_compras.registrar_compra(
            proveedor.id, owner_id, [ItemCompra(producto.id, 1, 100)]
        )
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")
        cookies = {NOMBRE_COOKIE_SESION: token}

        for metodo, ruta in [
            ("GET", "/compras"),
            ("GET", "/compras/nueva"),
            ("POST", "/compras/nueva"),
            ("GET", f"/compras/{compra.id}"),
        ]:
            respuesta = solicitud(metodo, ruta, cookies=cookies)
            assert respuesta.status == 403, f"{metodo} {ruta} debería dar 403 para CASHIER, dio {respuesta.status}"

    def test_sin_sesion_redirige_a_login(self, base_datos_temporal):
        proveedor, producto = _proveedor_y_producto()

        for metodo, ruta in [
            ("GET", "/compras"),
            ("GET", "/compras/nueva"),
            ("POST", "/compras/nueva"),
            ("GET", "/compras/1"),
        ]:
            respuesta = solicitud(metodo, ruta)
            assert respuesta.status == 303, f"{metodo} {ruta} debería redirigir a login, dio {respuesta.status}"
            assert respuesta.header("location").startswith("/login")

    def test_usuario_desactivado_con_sesion_previa_redirige_a_login(self, base_datos_temporal):
        cookies = _cookies_owner()
        usuario = repositorio_usuarios.obtener_por_nombre_usuario("ana")
        with obtener_conexion() as conexion:
            conexion.execute("UPDATE usuarios SET activo = 0 WHERE id = ?", (usuario.id,))

        respuesta = solicitud("GET", "/compras", cookies=cookies)

        assert respuesta.status == 303
        assert respuesta.header("location").startswith("/login")

    def test_metodos_no_previstos_dan_405(self, base_datos_temporal):
        proveedor, producto = _proveedor_y_producto()
        cookies = _cookies_owner()

        casos = [
            ("POST", "/compras"),
            ("DELETE", "/compras/1"),
            ("PUT", "/compras/nueva"),
            ("POST", "/compras/1"),
        ]
        for metodo, ruta in casos:
            respuesta = solicitud(metodo, ruta, cookies=cookies)
            assert respuesta.status == 405, f"{metodo} {ruta} debería dar 405, dio {respuesta.status}"
