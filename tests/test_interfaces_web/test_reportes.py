"""Pruebas HTTP end-to-end del módulo de Reportes.

Complementan (no reemplazan) `test_proteccion_rutas.py`, que ya cubre
que GET /reportes exige rol OWNER. Acá se prueba que la página renderiza
datos reales calculados por `services.servicio_reportes`, no el
cascarón visual anterior.
"""

from domain.usuario import Usuario
from domain.venta import ItemVenta
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth, servicio_stock, servicio_ventas

from ._asgi_cliente import solicitud


def _cookies_owner(nombre_usuario: str = "ana") -> dict[str, str]:
    from db.repositorios import usuarios as repositorio_usuarios

    repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre_usuario,
            nombre_completo="Usuario de prueba",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol="OWNER",
        )
    )
    token = servicio_auth.iniciar_sesion(nombre_usuario, "clave-correcta-123").token
    return {NOMBRE_COOKIE_SESION: token}


class TestVerReportes:
    def test_owner_ve_la_pagina_sin_ventas(self, base_datos_temporal):
        cookies = _cookies_owner()

        respuesta = solicitud("GET", "/reportes", cookies=cookies)

        assert respuesta.status == 200
        assert "Reportes" in respuesta.texto
        assert "Ventas por método de pago" in respuesta.texto
        # El cascarón visual anterior ya no debe estar.
        assert "Reportes todavía no está disponible" not in respuesta.texto

    def test_muestra_facturacion_y_producto_mas_vendido(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto(
            codigo_barras="7790000000001",
            nombre="Alfajor",
            precio_costo_centavos=100,
            precio_venta_centavos=200,
            stock_actual=10,
            stock_minimo=1,
        )
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 2)], "EFECTIVO")
        cookies = _cookies_owner()

        respuesta = solicitud("GET", "/reportes", cookies=cookies)

        assert respuesta.status == 200
        assert "Alfajor" in respuesta.texto

    def test_filtro_de_fechas_se_refleja_en_el_formulario(self, base_datos_temporal):
        cookies = _cookies_owner()

        respuesta = solicitud(
            "GET", "/reportes?fecha_desde=2024-01-01&fecha_hasta=2024-01-31", cookies=cookies
        )

        assert respuesta.status == 200
        assert 'value="2024-01-01"' in respuesta.texto
        assert 'value="2024-01-31"' in respuesta.texto

    def test_dashboard_ya_no_marca_reportes_como_proximamente(self, base_datos_temporal):
        """Pedidos y Precios siguen siendo cascarones (title con el
        sufijo "(próximamente)"): solo el acceso rápido de Reportes debe
        haber perdido esa marca al dejar de tener `proximamente=True`
        en `interfaces.web.navegacion.NAV_ITEMS`."""
        cookies = _cookies_owner()

        respuesta = solicitud("GET", "/", cookies=cookies)

        assert respuesta.status == 200
        assert 'title="Reportes"' in respuesta.texto
        assert 'title="Reportes (próximamente)"' not in respuesta.texto
        # Pedidos/Precios no se tocaron en esta fase: siguen marcados.
        assert 'title="Pedidos (próximamente)"' in respuesta.texto
