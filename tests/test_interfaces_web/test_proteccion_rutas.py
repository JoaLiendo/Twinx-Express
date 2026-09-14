"""Pruebas HTTP end-to-end de protección de rutas por rol (Fase 2E).

Verifican que `requiere_rol(...)` está realmente conectada a los
routers (no solo que la función funciona aislada, eso ya lo prueba
test_auth.py) golpeando la app real vía el cliente ASGI sin `httpx`
(ver _asgi_cliente.py). Esto es lo que demuestra que un CASHIER no
puede entrar a `/empleados` escribiendo la URL a mano.
"""

from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from domain.venta import ItemVenta
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth, servicio_categorias, servicio_stock, servicio_ventas

from ._asgi_cliente import solicitud

_RUTAS_OWNER_Y_CASHIER = ["/", "/ventas", "/caja", "/caja/arqueo", "/productos"]
_RUTAS_SOLO_OWNER = ["/pedidos", "/precios", "/proveedores", "/compras", "/reportes", "/empleados"]
_RUTAS_PRODUCTOS_SOLO_OWNER = ["/productos/nuevo", "/productos/importar"]


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


class TestSinSesion:
    def test_ruta_protegida_sin_sesion_redirige_a_login(self, base_datos_temporal):
        respuesta = solicitud("GET", "/empleados")

        assert respuesta.status == 303
        assert respuesta.header("location").startswith("/login")

    def test_dashboard_sin_sesion_tambien_redirige_a_login(self, base_datos_temporal):
        respuesta = solicitud("GET", "/")

        assert respuesta.status == 303
        assert respuesta.header("location").startswith("/login")


class TestOwnerAccedeATodo:
    def test_owner_accede_a_rutas_compartidas_y_exclusivas(self, base_datos_temporal):
        token = _crear_usuario_y_loguearse("OWNER", "ana")
        cookies = {NOMBRE_COOKIE_SESION: token}

        for ruta in _RUTAS_OWNER_Y_CASHIER + _RUTAS_SOLO_OWNER + _RUTAS_PRODUCTOS_SOLO_OWNER:
            respuesta = solicitud("GET", ruta, cookies=cookies)
            assert respuesta.status == 200, f"OWNER debería poder acceder a {ruta}, dio {respuesta.status}"


class TestCashierAccesoRestringido:
    def test_cashier_accede_a_dashboard_ventas_caja_y_stock(self, base_datos_temporal):
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")
        cookies = {NOMBRE_COOKIE_SESION: token}

        for ruta in _RUTAS_OWNER_Y_CASHIER:
            respuesta = solicitud("GET", ruta, cookies=cookies)
            assert respuesta.status == 200, f"CASHIER debería poder acceder a {ruta}, dio {respuesta.status}"

    def test_cashier_recibe_403_en_secciones_exclusivas_de_owner(self, base_datos_temporal):
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")
        cookies = {NOMBRE_COOKIE_SESION: token}

        for ruta in _RUTAS_SOLO_OWNER:
            respuesta = solicitud("GET", ruta, cookies=cookies)
            assert respuesta.status == 403, f"CASHIER no debería poder acceder a {ruta}, dio {respuesta.status}"

    def test_acceso_directo_por_url_a_empleados_queda_bloqueado_para_cashier(self, base_datos_temporal):
        """El ejemplo explícito del pedido: escribir /empleados a mano."""
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")

        respuesta = solicitud("GET", "/empleados", cookies={NOMBRE_COOKIE_SESION: token})

        assert respuesta.status == 403

    def test_stock_para_cashier_es_solo_consulta_no_alta_ni_edicion(self, base_datos_temporal):
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")
        cookies = {NOMBRE_COOKIE_SESION: token}
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)

        for ruta in _RUTAS_PRODUCTOS_SOLO_OWNER:
            respuesta = solicitud("GET", ruta, cookies=cookies)
            assert respuesta.status == 403, f"CASHIER no debería poder acceder a {ruta}, dio {respuesta.status}"

        respuesta_editar = solicitud("GET", f"/productos/{producto.id}/editar", cookies=cookies)
        assert respuesta_editar.status == 403

    def test_api_de_codigo_de_barras_del_pos_sigue_disponible_para_cashier(self, base_datos_temporal):
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")
        servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)

        respuesta = solicitud(
            "GET",
            "/api/productos/buscar-codigo/7790000000001",
            cookies={NOMBRE_COOKIE_SESION: token},
        )

        assert respuesta.status == 200


class TestUsuarioInactivoConSesionExistente:
    def test_se_trata_como_no_autenticado_y_redirige_a_login(self, base_datos_temporal):
        token = _crear_usuario_y_loguearse("OWNER", "ana")
        usuario = repositorio_usuarios.obtener_por_nombre_usuario("ana")
        respuesta_previa = solicitud("GET", "/", cookies={NOMBRE_COOKIE_SESION: token})
        assert respuesta_previa.status == 200  # todavía activo

        with obtener_conexion() as conexion:
            conexion.execute("UPDATE usuarios SET activo = 0 WHERE id = ?", (usuario.id,))

        respuesta = solicitud("GET", "/", cookies={NOMBRE_COOKIE_SESION: token})

        assert respuesta.status == 303
        assert respuesta.header("location").startswith("/login")


def _producto_inactivo() -> int:
    """Crea un producto, le registra una venta y lo da de baja lógica.
    Devuelve su id, ya inactivo."""
    producto = servicio_stock.registrar_producto(
        "7790000000001", "Producto de prueba", 100, 200, stock_actual=10, stock_minimo=1
    )
    servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
    servicio_stock.eliminar_producto(producto.id)
    return producto.id


class TestReactivarProducto:
    def test_owner_puede_reactivar(self, base_datos_temporal):
        producto_id = _producto_inactivo()
        token = _crear_usuario_y_loguearse("OWNER", "ana")

        respuesta = solicitud(
            "POST", f"/productos/{producto_id}/reactivar", cookies={NOMBRE_COOKIE_SESION: token}
        )

        assert respuesta.status == 303
        assert servicio_stock.obtener_por_id(producto_id) is not None

    def test_cashier_recibe_403(self, base_datos_temporal):
        producto_id = _producto_inactivo()
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")

        respuesta = solicitud(
            "POST", f"/productos/{producto_id}/reactivar", cookies={NOMBRE_COOKIE_SESION: token}
        )

        assert respuesta.status == 403
        assert servicio_stock.obtener_por_id(producto_id) is None  # sigue inactivo

    def test_sin_sesion_redirige_a_login(self, base_datos_temporal):
        producto_id = _producto_inactivo()

        respuesta = solicitud("POST", f"/productos/{producto_id}/reactivar")

        assert respuesta.status == 303
        assert respuesta.header("location").startswith("/login")

    def test_owner_ve_el_listado_de_inactivos(self, base_datos_temporal):
        _producto_inactivo()
        token = _crear_usuario_y_loguearse("OWNER", "ana")

        respuesta = solicitud(
            "GET", "/productos?mostrar_inactivos=true", cookies={NOMBRE_COOKIE_SESION: token}
        )

        assert respuesta.status == 200
        assert "Producto de prueba" in respuesta.texto

    def test_cashier_no_ve_el_listado_de_inactivos_aunque_fuerce_el_parametro(self, base_datos_temporal):
        _producto_inactivo()
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")

        respuesta = solicitud(
            "GET", "/productos?mostrar_inactivos=true", cookies={NOMBRE_COOKIE_SESION: token}
        )

        assert respuesta.status == 200  # la ruta sigue permitida, solo se ignora el flag
        assert "Producto de prueba" not in respuesta.texto


class TestGestionDeCategorias:
    def test_owner_puede_listar_categorias(self, base_datos_temporal):
        servicio_categorias.crear_categoria("Bebidas")
        token = _crear_usuario_y_loguearse("OWNER", "ana")

        respuesta = solicitud("GET", "/productos/categorias", cookies={NOMBRE_COOKIE_SESION: token})

        assert respuesta.status == 200
        assert "Bebidas" in respuesta.texto

    def test_owner_puede_crear_categoria(self, base_datos_temporal):
        token = _crear_usuario_y_loguearse("OWNER", "ana")

        respuesta = solicitud(
            "POST", "/productos/categorias", cookies={NOMBRE_COOKIE_SESION: token},
            formulario={"nombre": "Bebidas"},
        )

        assert respuesta.status == 303
        assert servicio_categorias.obtener_por_nombre("Bebidas") is not None

    def test_owner_puede_eliminar_categoria(self, base_datos_temporal):
        categoria = servicio_categorias.crear_categoria("Bebidas")
        token = _crear_usuario_y_loguearse("OWNER", "ana")

        respuesta = solicitud(
            "POST", f"/productos/categorias/{categoria.id}/eliminar",
            cookies={NOMBRE_COOKIE_SESION: token},
        )

        assert respuesta.status == 303
        assert servicio_categorias.obtener_por_id(categoria.id) is None

    def test_cashier_recibe_403_al_listar_categorias(self, base_datos_temporal):
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")

        respuesta = solicitud("GET", "/productos/categorias", cookies={NOMBRE_COOKIE_SESION: token})

        assert respuesta.status == 403

    def test_cashier_recibe_403_al_crear_categoria(self, base_datos_temporal):
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")

        respuesta = solicitud(
            "POST", "/productos/categorias", cookies={NOMBRE_COOKIE_SESION: token},
            formulario={"nombre": "Bebidas"},
        )

        assert respuesta.status == 403
        assert servicio_categorias.obtener_por_nombre("Bebidas") is None

    def test_cashier_recibe_403_al_eliminar_categoria(self, base_datos_temporal):
        categoria = servicio_categorias.crear_categoria("Bebidas")
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")

        respuesta = solicitud(
            "POST", f"/productos/categorias/{categoria.id}/eliminar",
            cookies={NOMBRE_COOKIE_SESION: token},
        )

        assert respuesta.status == 403
        assert servicio_categorias.obtener_por_id(categoria.id) is not None

    def test_cashier_puede_seguir_consultando_productos(self, base_datos_temporal):
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")

        respuesta = solicitud("GET", "/productos", cookies={NOMBRE_COOKIE_SESION: token})

        assert respuesta.status == 200

    def test_owner_puede_editar_categoria(self, base_datos_temporal):
        categoria = servicio_categorias.crear_categoria("Bebidas")
        token = _crear_usuario_y_loguearse("OWNER", "ana")

        respuesta = solicitud(
            "POST", f"/productos/categorias/{categoria.id}/editar",
            cookies={NOMBRE_COOKIE_SESION: token},
            formulario={"nombre": "Bebidas frías"},
        )

        assert respuesta.status == 303
        actualizada = servicio_categorias.obtener_por_id(categoria.id)
        assert actualizada.nombre == "Bebidas frías"
        assert actualizada.id == categoria.id

    def test_owner_puede_reactivar_categoria(self, base_datos_temporal):
        categoria = servicio_categorias.crear_categoria("Bebidas")
        servicio_stock.registrar_producto("7790000000001", "Gaseosa", 100, 200, categoria_id=categoria.id)
        servicio_categorias.eliminar_categoria(categoria.id)  # con producto asociado: baja lógica
        token = _crear_usuario_y_loguearse("OWNER", "ana")

        respuesta = solicitud(
            "POST", f"/productos/categorias/{categoria.id}/reactivar",
            cookies={NOMBRE_COOKIE_SESION: token},
        )

        assert respuesta.status == 303
        reactivada = servicio_categorias.obtener_por_id(categoria.id)
        assert reactivada.activa is True
        assert reactivada.id == categoria.id

    def test_cashier_recibe_403_al_editar_categoria(self, base_datos_temporal):
        categoria = servicio_categorias.crear_categoria("Bebidas")
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")

        respuesta = solicitud(
            "POST", f"/productos/categorias/{categoria.id}/editar",
            cookies={NOMBRE_COOKIE_SESION: token},
            formulario={"nombre": "Hackeado"},
        )

        assert respuesta.status == 403
        assert servicio_categorias.obtener_por_id(categoria.id).nombre == "Bebidas"

    def test_cashier_recibe_403_al_reactivar_categoria(self, base_datos_temporal):
        categoria = servicio_categorias.crear_categoria("Bebidas")
        servicio_stock.registrar_producto("7790000000001", "Gaseosa", 100, 200, categoria_id=categoria.id)
        servicio_categorias.eliminar_categoria(categoria.id)  # con producto asociado: baja lógica
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")

        respuesta = solicitud(
            "POST", f"/productos/categorias/{categoria.id}/reactivar",
            cookies={NOMBRE_COOKIE_SESION: token},
        )

        assert respuesta.status == 403
        assert servicio_categorias.obtener_por_id(categoria.id).activa is False


class TestFiltroPorCategoria:
    def test_filtra_productos_por_categoria(self, base_datos_temporal):
        bebidas = servicio_categorias.crear_categoria("Bebidas")
        servicio_categorias.crear_categoria("Golosinas")
        servicio_stock.registrar_producto("7790000000001", "Gaseosa", 100, 200, categoria_id=bebidas.id)
        servicio_stock.registrar_producto("7790000000002", "Chicle", 50, 90)
        token = _crear_usuario_y_loguearse("OWNER", "ana")

        respuesta = solicitud(
            "GET", f"/productos?categoria_id={bebidas.id}", cookies={NOMBRE_COOKIE_SESION: token}
        )

        assert respuesta.status == 200
        assert "Gaseosa" in respuesta.texto
        assert "Chicle" not in respuesta.texto

    def test_filtro_de_categoria_combina_con_busqueda_por_nombre(self, base_datos_temporal):
        bebidas = servicio_categorias.crear_categoria("Bebidas")
        servicio_stock.registrar_producto("7790000000001", "Gaseosa Cola", 100, 200, categoria_id=bebidas.id)
        servicio_stock.registrar_producto("7790000000002", "Gaseosa Lima", 100, 200)
        token = _crear_usuario_y_loguearse("OWNER", "ana")

        respuesta = solicitud(
            "GET", f"/productos?q=Gaseosa&categoria_id={bebidas.id}",
            cookies={NOMBRE_COOKIE_SESION: token},
        )

        assert "Gaseosa Cola" in respuesta.texto
        assert "Gaseosa Lima" not in respuesta.texto

    def test_sin_filtro_de_categoria_se_ven_todos_los_productos(self, base_datos_temporal):
        bebidas = servicio_categorias.crear_categoria("Bebidas")
        servicio_stock.registrar_producto("7790000000001", "Gaseosa", 100, 200, categoria_id=bebidas.id)
        servicio_stock.registrar_producto("7790000000002", "Chicle", 50, 90)
        token = _crear_usuario_y_loguearse("OWNER", "ana")

        respuesta = solicitud("GET", "/productos", cookies={NOMBRE_COOKIE_SESION: token})

        assert "Gaseosa" in respuesta.texto
        assert "Chicle" in respuesta.texto
