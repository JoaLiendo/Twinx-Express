"""Pruebas HTTP de `GET /ventas/{id}/ticket` (Fase 5D): ticket
imprimible de solo lectura, con los datos económicos leídos
exclusivamente de la venta persistida (nunca del carrito del cliente
ni de query params/body).

Fase 5C decidió deliberadamente no persistir monto recibido/vuelto:
este ticket no los muestra ni depende de ellos -- ver
`TestSinRecibidoNiVuelto` más abajo."""

from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth, servicio_stock, servicio_ventas
from domain.venta import ItemVenta

from ._asgi_cliente import solicitud
import pytest

pytestmark = pytest.mark.usefixtures("caja_abierta")


def _cookies(rol: str = "OWNER", nombre_usuario: str = "ana") -> dict[str, str]:
    repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre_usuario,
            nombre_completo="Usuario de prueba",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol=rol,
        )
    )
    token = servicio_auth.iniciar_sesion(nombre_usuario, "clave-correcta-123").token
    return {NOMBRE_COOKIE_SESION: token}


def _registrar_venta(items: list[ItemVenta], tipo_pago: str = "EFECTIVO", clave: str = "clave-ticket"):
    return servicio_ventas.registrar_venta(items, tipo_pago, clave_idempotencia=clave)


class TestTicketDeVentaExistente:
    def test_venta_existente_devuelve_200(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = _registrar_venta([ItemVenta(producto.id, 2)], clave="clave-1")
        cookies = _cookies()

        respuesta = solicitud("GET", f"/ventas/{venta.id}/ticket", cookies=cookies)

        assert respuesta.status == 200

    def test_incluye_id_de_venta(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = _registrar_venta([ItemVenta(producto.id, 1)], clave="clave-2")

        respuesta = solicitud("GET", f"/ventas/{venta.id}/ticket", cookies=_cookies())

        assert f"#{venta.id}" in respuesta.texto

    def test_incluye_fecha_hora_exactamente_persistida(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = _registrar_venta([ItemVenta(producto.id, 1)], clave="clave-3")

        respuesta = solicitud("GET", f"/ventas/{venta.id}/ticket", cookies=_cookies())

        # La fecha que muestra el ticket debe ser exactamente la que
        # quedó en la fila de `ventas` -- no una recalculada al vuelo.
        assert venta.fecha in respuesta.texto

    def test_nombre_cantidad_precio_unitario_y_subtotal_de_una_linea(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = _registrar_venta([ItemVenta(producto.id, 3)], clave="clave-4")

        respuesta = solicitud("GET", f"/ventas/{venta.id}/ticket", cookies=_cookies())

        assert "Alfajor" in respuesta.texto
        assert ">3<" in respuesta.texto  # cantidad
        assert "2,00" in respuesta.texto  # precio unitario ($2,00, formato argentino)
        assert "6,00" in respuesta.texto  # subtotal (3 x $2,00)

    def test_total_correcto(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = _registrar_venta([ItemVenta(producto.id, 5)], clave="clave-5")

        respuesta = solicitud("GET", f"/ventas/{venta.id}/ticket", cookies=_cookies())

        assert "10,00" in respuesta.texto  # 5 x $2,00 (formato argentino)

    def test_medio_de_pago_correcto(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = _registrar_venta([ItemVenta(producto.id, 1)], tipo_pago="TARJETA", clave="clave-6")

        respuesta = solicitud("GET", f"/ventas/{venta.id}/ticket", cookies=_cookies())

        assert "TARJETA" in respuesta.texto

    def test_venta_con_multiples_productos_muestra_todas_las_lineas(self, base_datos_temporal):
        p1 = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        p2 = servicio_stock.registrar_producto("7790000000002", "Gaseosa", 150, 300, stock_actual=10)
        venta = _registrar_venta([ItemVenta(p1.id, 2), ItemVenta(p2.id, 1)], clave="clave-7")

        respuesta = solicitud("GET", f"/ventas/{venta.id}/ticket", cookies=_cookies())

        assert "Alfajor" in respuesta.texto
        assert "Gaseosa" in respuesta.texto

    def test_venta_historica_sigue_siendo_accesible(self, base_datos_temporal):
        """No hay ventana de tiempo: una venta ya vieja (simplemente una
        que no es la última creada) se sirve igual que una recién
        hecha -- se identifica por id, no por 'la más reciente'."""
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta_vieja = _registrar_venta([ItemVenta(producto.id, 1)], clave="clave-vieja")
        _registrar_venta([ItemVenta(producto.id, 1)], clave="clave-nueva")

        respuesta = solicitud("GET", f"/ventas/{venta_vieja.id}/ticket", cookies=_cookies())

        assert respuesta.status == 200
        assert f"#{venta_vieja.id}" in respuesta.texto


class TestTicketDeVentaInexistente:
    def test_venta_inexistente_da_404(self, base_datos_temporal):
        respuesta = solicitud("GET", "/ventas/9999/ticket", cookies=_cookies())
        assert respuesta.status == 404


class TestPermisos:
    def test_sin_sesion_redirige_a_login(self, base_datos_temporal):
        """`/ventas/{id}/ticket` no es una ruta `/api/`: hereda el mismo
        comportamiento que CUALQUIER otra página HTML de la app sin
        sesión -- redirect 303 a `/login` (ver
        `interfaces.web.app.manejar_no_autenticado`), no un 401 literal.
        Un 401 crudo sería, de hecho, inconsistente con el resto de la
        aplicación (comprobado leyendo `app.py`, no asumido)."""
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = _registrar_venta([ItemVenta(producto.id, 1)], clave="clave-sin-sesion")

        respuesta = solicitud("GET", f"/ventas/{venta.id}/ticket")

        assert respuesta.status == 303
        assert respuesta.header("location") == f"/login?next=/ventas/{venta.id}/ticket"


class TestQueryParamsIgnorados:
    def test_query_param_economico_no_modifica_el_resultado(self, base_datos_temporal):
        """Seguridad explícita: nada de lo que viene en la query string
        puede alterar el total/precio mostrado -- todo sale de la
        venta persistida, identificada solo por el {id} del path."""
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = _registrar_venta([ItemVenta(producto.id, 1)], clave="clave-8")

        respuesta_normal = solicitud("GET", f"/ventas/{venta.id}/ticket", cookies=_cookies())
        respuesta_con_query = solicitud(
            "GET",
            f"/ventas/{venta.id}/ticket?total_centavos=1&precio_unitario_centavos=1&monto_recibido=999999",
            cookies=_cookies(nombre_usuario="ana2"),
        )

        assert respuesta_normal.texto == respuesta_con_query.texto


class TestCssDeImpresion:
    def test_incluye_page_size_80mm(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = _registrar_venta([ItemVenta(producto.id, 1)], clave="clave-9")

        respuesta = solicitud("GET", f"/ventas/{venta.id}/ticket", cookies=_cookies())

        assert "@page" in respuesta.texto
        assert "80mm" in respuesta.texto

    def test_incluye_media_print(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = _registrar_venta([ItemVenta(producto.id, 1)], clave="clave-10")

        respuesta = solicitud("GET", f"/ventas/{venta.id}/ticket", cookies=_cookies())

        assert "@media print" in respuesta.texto

    def test_no_carga_tailwind_cdn(self, base_datos_temporal):
        """El ticket es standalone: no debe depender del CDN de Tailwind
        que sí usa base.html (diseño aprobado de 5D)."""
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = _registrar_venta([ItemVenta(producto.id, 1)], clave="clave-11")

        respuesta = solicitud("GET", f"/ventas/{venta.id}/ticket", cookies=_cookies())

        assert "cdn.tailwindcss.com" not in respuesta.texto


class TestSinRecibidoNiVuelto:
    def test_el_ticket_no_menciona_monto_recibido_ni_vuelto(self, base_datos_temporal):
        """Fase 5C: recibido/vuelto no se persisten -- por lo tanto el
        ticket (que solo lee de DB) no puede mostrarlos ni depender de
        ellos. Este test falla si alguna vez se intenta agregar esa
        info al ticket sin haber cambiado antes esa decisión."""
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = _registrar_venta([ItemVenta(producto.id, 1)], clave="clave-12")

        respuesta = solicitud("GET", f"/ventas/{venta.id}/ticket", cookies=_cookies())

        texto_normalizado = respuesta.texto.lower()
        assert "recibido" not in texto_normalizado
        assert "vuelto" not in texto_normalizado

    def test_el_endpoint_no_acepta_monto_recibido_como_parametro(self, base_datos_temporal):
        """La ruta no declara ningún parámetro de monto recibido/vuelto
        -- pasarlo en la query no debe romper nada ni cambiar nada
        (ya cubierto también por TestQueryParamsIgnorados)."""
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = _registrar_venta([ItemVenta(producto.id, 1)], clave="clave-13")

        respuesta = solicitud(
            "GET", f"/ventas/{venta.id}/ticket?monto_recibido_centavos=500&vuelto_centavos=300",
            cookies=_cookies(),
        )

        assert respuesta.status == 200
        assert "500" not in respuesta.texto
        assert "300" not in respuesta.texto
