"""Pruebas HTTP end-to-end del módulo de Reportes.

Complementan (no reemplazan) `test_proteccion_rutas.py`, que ya cubre
que GET /reportes exige rol OWNER. Acá se prueba que la página renderiza
datos reales calculados por `services.servicio_reportes`, no el
cascarón visual anterior.
"""

from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from db.repositorios import ventas as repositorio_ventas
from domain.usuario import Usuario
from domain.venta import ItemVenta
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth, servicio_caja, servicio_stock, servicio_ventas

from ._asgi_cliente import solicitud


def _cookies_owner(nombre_usuario: str = "ana") -> dict[str, str]:
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


class TestRentabilidadEnLaPagina:
    """Reportes V2: sección de rentabilidad y aviso de ventas excluidas."""

    def test_muestra_margen_de_una_venta_con_costo_conocido(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto(
            codigo_barras="7790000000001",
            nombre="Alfajor",
            precio_costo_centavos=100,
            precio_venta_centavos=200,
            stock_actual=10,
        )
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        cookies = _cookies_owner()

        respuesta = solicitud("GET", "/reportes", cookies=cookies)

        assert respuesta.status == 200
        assert "Rentabilidad" in respuesta.texto
        assert "50" in respuesta.texto  # margen % (100/200*100)

    def test_avisa_cuando_hay_ventas_sin_costo_historico(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto(
            codigo_barras="7790000000001",
            nombre="Alfajor",
            precio_costo_centavos=100,
            precio_venta_centavos=200,
            stock_actual=10,
        )
        with obtener_conexion() as conexion:
            repositorio_ventas.registrar_venta_con_detalle(
                conexion, 200, "EFECTIVO", [(ItemVenta(producto.id, 1), 200)]
            )
        cookies = _cookies_owner()

        respuesta = solicitud("GET", "/reportes", cookies=cookies)

        assert respuesta.status == 200
        assert "costo histórico registrado" in respuesta.texto
        assert "1" in respuesta.texto  # cantidad_ventas_sin_costo_historico

    def test_texto_obsoleto_ya_no_aparece(self, base_datos_temporal):
        cookies = _cookies_owner()

        respuesta = solicitud("GET", "/reportes", cookies=cookies)

        assert "todavía no incluye márgenes" not in respuesta.texto


class TestVentasPorVendedorEnLaPagina:
    def test_muestra_la_venta_del_usuario_autenticado(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto(
            codigo_barras="7790000000001",
            nombre="Alfajor",
            precio_costo_centavos=100,
            precio_venta_centavos=200,
            stock_actual=10,
        )
        usuario_owner = repositorio_usuarios.crear_usuario(
            Usuario(
                nombre_usuario="duenio",
                nombre_completo="Usuario de prueba",
                password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
                rol="OWNER",
            )
        )
        token = servicio_auth.iniciar_sesion("duenio", "clave-correcta-123").token
        cookies = {NOMBRE_COOKIE_SESION: token}
        # La venta la registra el mismo OWNER logueado, vía el servicio real
        # (igual que haría el POS), para quedar asociada a su usuario_id.
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO", usuario_id=usuario_owner.id)

        respuesta = solicitud("GET", "/reportes", cookies=cookies)

        assert respuesta.status == 200
        assert "Ventas por vendedor" in respuesta.texto
        assert "Usuario de prueba" in respuesta.texto

    def test_sin_ventas_muestra_estado_vacio(self, base_datos_temporal):
        cookies = _cookies_owner()

        respuesta = solicitud("GET", "/reportes", cookies=cookies)

        assert respuesta.status == 200
        assert "Ventas por vendedor" in respuesta.texto


class TestResumenAnulacionesEnLaPagina:
    """Visibilidad de Anulaciones: sección "Anulaciones del período" de
    /reportes, probada de punta a punta contra la ruta y el template
    reales (no solo contra `servicio_reportes`, ya cubierto en
    tests/test_services/test_servicio_reportes.py)."""

    def test_muestra_cantidad_monto_y_motivo_sin_contaminar_los_kpis_existentes(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto(
            codigo_barras="7790000000001",
            nombre="Alfajor",
            precio_costo_centavos=100,
            precio_venta_centavos=200,
            stock_actual=10,
        )
        usuario_owner = repositorio_usuarios.crear_usuario(
            Usuario(
                nombre_usuario="duenio",
                nombre_completo="Usuario de prueba",
                password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
                rol="OWNER",
            )
        )
        token = servicio_auth.iniciar_sesion("duenio", "clave-correcta-123").token
        cookies = {NOMBRE_COOKIE_SESION: token}

        servicio_caja.abrir_caja(100_000)
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")  # activa: 200 centavos
        venta_anulada = servicio_ventas.registrar_venta([ItemVenta(producto.id, 3)], "EFECTIVO")  # 600 centavos
        servicio_ventas.anular_venta(
            venta_anulada.id, motivo="ARREPENTIMIENTO_CLIENTE", observaciones=None, usuario_id=usuario_owner.id
        )

        respuesta = solicitud("GET", "/reportes", cookies=cookies)

        assert respuesta.status == 200
        assert "Anulaciones del período" in respuesta.texto
        assert "ARREPENTIMIENTO_CLIENTE" in respuesta.texto
        assert "6,00" in respuesta.texto  # monto anulado (600 centavos)
        # KPI operativo: solo la venta activa (200 centavos) -- si la
        # anulada hubiera contaminado la facturación, el total sería
        # 800 centavos ("8,00"), nunca "2,00".
        assert "2,00" in respuesta.texto
        assert "8,00" not in respuesta.texto

    def test_sin_anulaciones_muestra_estado_vacio(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto(
            codigo_barras="7790000000001",
            nombre="Alfajor",
            precio_costo_centavos=100,
            precio_venta_centavos=200,
            stock_actual=10,
        )
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        cookies = _cookies_owner()

        respuesta = solicitud("GET", "/reportes", cookies=cookies)

        assert respuesta.status == 200
        assert "Sin anulaciones en el período" in respuesta.texto


class TestValorizacionEnLaPagina:
    """Valorización de Inventario: sección "Valorización de inventario"
    de /reportes, a HOY -- probada de punta a punta contra la ruta y el
    template reales, incluyendo la garantía de que no depende del
    filtro de fecha del resto de la página."""

    def test_muestra_unidades_valor_y_cantidad_de_productos(self, base_datos_temporal):
        servicio_stock.registrar_producto(
            codigo_barras="7790000000001",
            nombre="Alfajor Valorizado",
            precio_costo_centavos=100,
            precio_venta_centavos=200,
            stock_actual=5,
        )
        cookies = _cookies_owner()

        respuesta = solicitud("GET", "/reportes", cookies=cookies)

        assert respuesta.status == 200
        assert "Valorización de inventario" in respuesta.texto
        assert "A hoy" in respuesta.texto
        assert "Alfajor Valorizado" in respuesta.texto
        assert "5,00" in respuesta.texto  # valor: 5 * 100 centavos = 500 -> "5,00"

    def test_incluye_producto_inactivo_con_stock(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto(
            codigo_barras="7790000000001",
            nombre="Descontinuado Con Stock",
            precio_costo_centavos=100,
            precio_venta_centavos=200,
            stock_actual=3,
        )
        with obtener_conexion() as conexion:
            conexion.execute("UPDATE productos SET activo = 0 WHERE id = ?", (producto.id,))
        cookies = _cookies_owner()

        respuesta = solicitud("GET", "/reportes", cookies=cookies)

        assert respuesta.status == 200
        assert "Descontinuado Con Stock" in respuesta.texto

    def test_no_cambia_con_distintos_periodos_de_reporte(self, base_datos_temporal):
        """Garantía central del diseño: la valorización es idéntica sin
        importar qué fecha_desde/fecha_hasta se pida -- no hay ningún
        parámetro por el que el filtro de período pueda llegar a
        afectarla."""
        servicio_stock.registrar_producto(
            codigo_barras="7790000000001",
            nombre="Alfajor",
            precio_costo_centavos=150,
            precio_venta_centavos=300,
            stock_actual=4,
        )
        cookies = _cookies_owner()

        respuesta_rango_1 = solicitud(
            "GET", "/reportes?fecha_desde=2020-01-01&fecha_hasta=2020-01-31", cookies=cookies
        )
        respuesta_rango_2 = solicitud(
            "GET", "/reportes?fecha_desde=2099-01-01&fecha_hasta=2099-12-31", cookies=cookies
        )

        assert respuesta_rango_1.status == 200
        assert respuesta_rango_2.status == 200
        valor_esperado = "6,00"  # 4 * 150 centavos = 600
        assert valor_esperado in respuesta_rango_1.texto
        assert valor_esperado in respuesta_rango_2.texto

    def test_sin_stock_para_valorizar_muestra_estado_vacio(self, base_datos_temporal):
        cookies = _cookies_owner()

        respuesta = solicitud("GET", "/reportes", cookies=cookies)

        assert respuesta.status == 200
        assert "Sin stock para valorizar" in respuesta.texto

    def test_no_altera_los_kpis_de_ventas_existentes(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto(
            codigo_barras="7790000000001",
            nombre="Alfajor",
            precio_costo_centavos=100,
            precio_venta_centavos=200,
            stock_actual=10,
        )
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        cookies = _cookies_owner()

        respuesta = solicitud("GET", "/reportes", cookies=cookies)

        assert respuesta.status == 200
        assert "Ventas por método de pago" in respuesta.texto
        assert "Rentabilidad" in respuesta.texto
        assert "Ventas por vendedor" in respuesta.texto
