"""Pruebas HTTP end-to-end de las 3 rutas de ajuste manual de stock:
formulario, submit e historial. Todas exclusivas de OWNER (igual
criterio que alta/edición/eliminación de productos, ver
`interfaces.web.rutas.productos`)."""

from urllib.parse import unquote

from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth, servicio_stock

from ._asgi_cliente import solicitud


def _crear_usuario_logueado(rol: str, nombre_usuario: str) -> tuple[Usuario, dict[str, str]]:
    usuario_creado = repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre_usuario,
            nombre_completo="Usuario de prueba",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol=rol,
        )
    )
    token = servicio_auth.iniciar_sesion(nombre_usuario, "clave-correcta-123").token
    return usuario_creado, {NOMBRE_COOKIE_SESION: token}


def _crear_producto(stock_actual: int = 10):
    return servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=stock_actual)


class TestFormularioAjustar:
    def test_owner_puede_ver_el_formulario(self, base_datos_temporal):
        producto = _crear_producto()
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")

        respuesta = solicitud("GET", f"/productos/{producto.id}/ajustar", cookies=cookies)

        assert respuesta.status == 200

    def test_cashier_recibe_403(self, base_datos_temporal):
        producto = _crear_producto()
        _, cookies = _crear_usuario_logueado("CASHIER", "cajera1")

        respuesta = solicitud("GET", f"/productos/{producto.id}/ajustar", cookies=cookies)

        assert respuesta.status == 403

    def test_sin_sesion_redirige_a_login(self, base_datos_temporal):
        producto = _crear_producto()

        respuesta = solicitud("GET", f"/productos/{producto.id}/ajustar")

        assert respuesta.status == 303
        assert respuesta.header("location").startswith("/login")

    def test_producto_inexistente_redirige_con_error(self, base_datos_temporal):
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")

        respuesta = solicitud("GET", "/productos/9999/ajustar", cookies=cookies)

        assert respuesta.status == 303
        assert "tipo=error" in respuesta.header("location")


class TestSubmitAjustar:
    def test_owner_puede_ajustar_y_el_stock_queda_modificado(self, base_datos_temporal):
        producto = _crear_producto(stock_actual=10)
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")

        respuesta = solicitud(
            "POST",
            f"/productos/{producto.id}/ajustar",
            cookies=cookies,
            formulario={"motivo": "MERMA", "direccion": "restar", "cantidad": "3"},
        )

        assert respuesta.status == 303
        assert "tipo=success" in respuesta.header("location")
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 7

    def test_sumar_incrementa_el_stock(self, base_datos_temporal):
        producto = _crear_producto(stock_actual=10)
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")

        solicitud(
            "POST",
            f"/productos/{producto.id}/ajustar",
            cookies=cookies,
            formulario={"motivo": "RECUENTO", "direccion": "sumar", "cantidad": "5"},
        )

        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 15

    def test_registra_el_usuario_autenticado_como_autor_del_ajuste(self, base_datos_temporal):
        producto = _crear_producto(stock_actual=10)
        usuario, cookies = _crear_usuario_logueado("OWNER", "duenio")

        solicitud(
            "POST",
            f"/productos/{producto.id}/ajustar",
            cookies=cookies,
            formulario={"motivo": "MERMA", "direccion": "restar", "cantidad": "1"},
        )

        with obtener_conexion() as conexion:
            usuario_id = conexion.execute(
                "SELECT usuario_id FROM ajustes_stock ORDER BY id DESC LIMIT 1"
            ).fetchone()["usuario_id"]
        assert usuario_id == usuario.id

    def test_cashier_recibe_403_y_no_modifica_el_stock(self, base_datos_temporal):
        producto = _crear_producto(stock_actual=10)
        _, cookies = _crear_usuario_logueado("CASHIER", "cajera1")

        respuesta = solicitud(
            "POST",
            f"/productos/{producto.id}/ajustar",
            cookies=cookies,
            formulario={"motivo": "MERMA", "direccion": "restar", "cantidad": "3"},
        )

        assert respuesta.status == 403
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 10

    def test_sin_sesion_redirige_a_login_y_no_modifica_el_stock(self, base_datos_temporal):
        producto = _crear_producto(stock_actual=10)

        respuesta = solicitud(
            "POST",
            f"/productos/{producto.id}/ajustar",
            formulario={"motivo": "MERMA", "direccion": "restar", "cantidad": "3"},
        )

        assert respuesta.status == 303
        assert respuesta.header("location").startswith("/login")
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 10

    def test_otro_sin_observaciones_es_rechazado_en_el_servidor(self, base_datos_temporal):
        """La regla vive en `domain.ajuste_stock`: el servidor la aplica
        aunque el HTML/JS del formulario intente evitarla del lado del
        cliente (no se confía en JavaScript para reglas de negocio)."""
        producto = _crear_producto(stock_actual=10)
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")

        respuesta = solicitud(
            "POST",
            f"/productos/{producto.id}/ajustar",
            cookies=cookies,
            formulario={"motivo": "OTRO", "direccion": "restar", "cantidad": "1", "observaciones": ""},
        )

        assert respuesta.status == 303
        assert "tipo=error" in respuesta.header("location")
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 10

    def test_otro_con_observaciones_es_aceptado(self, base_datos_temporal):
        producto = _crear_producto(stock_actual=10)
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")

        respuesta = solicitud(
            "POST",
            f"/productos/{producto.id}/ajustar",
            cookies=cookies,
            formulario={"motivo": "OTRO", "direccion": "restar", "cantidad": "1", "observaciones": "explicación"},
        )

        assert respuesta.status == 303
        assert "tipo=success" in respuesta.header("location")

    def test_cantidad_cero_es_rechazada(self, base_datos_temporal):
        producto = _crear_producto(stock_actual=10)
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")

        respuesta = solicitud(
            "POST",
            f"/productos/{producto.id}/ajustar",
            cookies=cookies,
            formulario={"motivo": "MERMA", "direccion": "restar", "cantidad": "0"},
        )

        assert respuesta.status == 303
        assert "tipo=error" in respuesta.header("location")
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 10

    def test_direccion_invalida_es_rechazada_y_no_modifica_nada(self, base_datos_temporal):
        """No confiar en el <select> del HTML: un valor de `direccion`
        que no sea exactamente "sumar" o "restar" debe rechazarse en
        servidor, en vez de interpretarse silenciosamente como resta."""
        producto = _crear_producto(stock_actual=10)
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")

        respuesta = solicitud(
            "POST",
            f"/productos/{producto.id}/ajustar",
            cookies=cookies,
            formulario={"motivo": "MERMA", "direccion": "otro", "cantidad": "3"},
        )

        assert respuesta.status == 303
        assert "tipo=error" in respuesta.header("location")
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 10
        with obtener_conexion() as conexion:
            total = conexion.execute("SELECT COUNT(*) AS n FROM ajustes_stock").fetchone()["n"]
        assert total == 0

    def test_stock_insuficiente_redirige_con_error_y_no_modifica_nada(self, base_datos_temporal):
        producto = _crear_producto(stock_actual=3)
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")

        respuesta = solicitud(
            "POST",
            f"/productos/{producto.id}/ajustar",
            cookies=cookies,
            formulario={"motivo": "MERMA", "direccion": "restar", "cantidad": "5"},
        )

        assert respuesta.status == 303
        assert "tipo=error" in respuesta.header("location")
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 3

    def test_producto_inexistente_redirige_con_error(self, base_datos_temporal):
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")

        respuesta = solicitud(
            "POST",
            "/productos/9999/ajustar",
            cookies=cookies,
            formulario={"motivo": "MERMA", "direccion": "restar", "cantidad": "1"},
        )

        assert respuesta.status == 303
        assert "tipo=error" in respuesta.header("location")


class TestHistorialAjustes:
    def test_owner_ve_el_historial_con_los_ajustes_registrados(self, base_datos_temporal):
        producto = _crear_producto(stock_actual=10)
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")
        solicitud(
            "POST",
            f"/productos/{producto.id}/ajustar",
            cookies=cookies,
            formulario={"motivo": "MERMA", "direccion": "restar", "cantidad": "3"},
        )

        respuesta = solicitud("GET", f"/productos/{producto.id}/ajustes", cookies=cookies)

        assert respuesta.status == 200
        assert "MERMA" in respuesta.texto

    def test_cashier_recibe_403(self, base_datos_temporal):
        producto = _crear_producto()
        _, cookies = _crear_usuario_logueado("CASHIER", "cajera1")

        respuesta = solicitud("GET", f"/productos/{producto.id}/ajustes", cookies=cookies)

        assert respuesta.status == 403

    def test_sin_sesion_redirige_a_login(self, base_datos_temporal):
        producto = _crear_producto()

        respuesta = solicitud("GET", f"/productos/{producto.id}/ajustes")

        assert respuesta.status == 303
        assert respuesta.header("location").startswith("/login")

    def test_producto_inexistente_redirige_con_error(self, base_datos_temporal):
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")

        respuesta = solicitud("GET", "/productos/9999/ajustes", cookies=cookies)

        assert respuesta.status == 303
        assert "tipo=error" in respuesta.header("location")
