"""V1.1: robustez del POS a nivel HTTP -- búsqueda por código de barras (el
endpoint que consume el lector USB) y cards de productos sin precio.

El comportamiento de JavaScript (bloqueo durante el cobro, Enter, stock de
la grilla, respuestas no JSON) se cubre en `tests_e2e/tests/09-robustez-pos.spec.js`."""

from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth, servicio_stock

from ._asgi_cliente import solicitud


def _cookies(rol: str = "CASHIER") -> dict[str, str]:
    repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario="cajera",
            nombre_completo="Cajera",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol=rol,
        )
    )
    return {NOMBRE_COOKIE_SESION: servicio_auth.iniciar_sesion("cajera", "clave-correcta-123").token}


class TestBusquedaPorCodigoDeBarras:
    """Flujo del lector USB: código -> Enter -> `GET /api/productos/buscar-codigo/{codigo}`."""

    def test_codigo_existente_devuelve_el_producto_para_el_carrito(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 250, stock_actual=7)

        respuesta = solicitud("GET", "/api/productos/buscar-codigo/7790000000001", cookies=_cookies())

        assert respuesta.status == 200
        assert respuesta.json() == {
            "id": producto.id,
            "codigo_barras": "7790000000001",
            "nombre": "Alfajor",
            "precio_venta_centavos": 250,
            "stock_actual": 7,
        }

    def test_codigo_inexistente_da_404_con_mensaje(self, base_datos_temporal):
        respuesta = solicitud("GET", "/api/productos/buscar-codigo/0000000000000", cookies=_cookies())

        assert respuesta.status == 404
        assert "No se encontró" in respuesta.json()["error"]

    def test_producto_dado_de_baja_no_se_encuentra(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 250, stock_actual=7)
        servicio_stock.eliminar_producto(producto.id)

        respuesta = solicitud("GET", "/api/productos/buscar-codigo/7790000000001", cookies=_cookies())

        assert respuesta.status == 404

    def test_el_codigo_con_caracteres_especiales_no_rompe_la_ruta(self, base_datos_temporal):
        respuesta = solicitud("GET", "/api/productos/buscar-codigo/AB%2FCD%20E", cookies=_cookies())

        assert respuesta.status == 404

    def test_sin_sesion_no_hay_acceso(self, base_datos_temporal):
        respuesta = solicitud("GET", "/api/productos/buscar-codigo/7790000000001")

        assert respuesta.status == 401


class TestProductoSinPrecioEnElPos:
    def test_la_card_indica_precio_no_configurado_en_vez_de_cero(self, base_datos_temporal):
        servicio_stock.registrar_producto("7790000000001", "Gaseosa sin precio", 0, 0, stock_actual=10)
        servicio_stock.registrar_producto("7790000000002", "Alfajor", 100, 250, stock_actual=10)

        html = solicitud("GET", "/ventas", cookies=_cookies()).texto

        assert html.count("data-precio-no-configurado") == 1
        assert "Precio no configurado" in html

    def test_el_servidor_sigue_rechazando_la_venta_de_un_producto_sin_precio(self, base_datos_temporal):
        from services import servicio_caja

        servicio_caja.abrir_caja(0)
        producto = servicio_stock.registrar_producto("7790000000001", "Sin precio", 0, 0, stock_actual=10)

        respuesta = solicitud(
            "POST",
            "/api/ventas",
            cookies=_cookies(),
            json_body={
                "items": [{"producto_id": producto.id, "cantidad": 1}],
                "tipo_pago": "TARJETA",
                "clave_idempotencia": "k-sin-precio",
            },
        )

        assert respuesta.status == 422
        assert "precio" in respuesta.json()["error"].lower()
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 10
