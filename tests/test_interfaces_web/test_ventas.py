"""Pruebas HTTP end-to-end de POST /api/ventas, incluida la
idempotencia de Fase 5A. Antes de esta fase no existía ningún test a
este nivel -- toda la cobertura previa era de `services.servicio_ventas`
directamente."""

import threading

from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from domain.venta import ItemVenta
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth, servicio_stock, servicio_ventas

from ._asgi_cliente import solicitud
import pytest

pytestmark = pytest.mark.usefixtures("caja_abierta")


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


def _cookies(rol: str, nombre_usuario: str) -> dict[str, str]:
    return {NOMBRE_COOKIE_SESION: _crear_usuario_y_loguearse(rol, nombre_usuario)}


def _cuerpo(producto_id: int, cantidad: int, tipo_pago: str = "EFECTIVO", clave: str = "clave-1", **extra):
    item = {"producto_id": producto_id, "cantidad": cantidad, **extra}
    return {"items": [item], "tipo_pago": tipo_pago, "clave_idempotencia": clave}


def _contar_ventas() -> int:
    with obtener_conexion() as conexion:
        return conexion.execute("SELECT COUNT(*) AS n FROM ventas").fetchone()["n"]


def _crear_usuario_logueado(rol: str, nombre_usuario: str) -> tuple[Usuario, str]:
    """Igual que `_crear_usuario_y_loguearse`, pero devuelve también el
    `Usuario` creado (con su `id`), para verificar que la venta quede
    asociada al usuario correcto (migración 009)."""
    usuario_creado = repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre_usuario,
            nombre_completo="Usuario de prueba",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol=rol,
        )
    )
    token = servicio_auth.iniciar_sesion(nombre_usuario, "clave-correcta-123").token
    return usuario_creado, token


def _usuario_id_de_la_venta(venta_id: int) -> int | None:
    with obtener_conexion() as conexion:
        return conexion.execute("SELECT usuario_id FROM ventas WHERE id = ?", (venta_id,)).fetchone()["usuario_id"]


class TestVentaValida:
    def test_venta_valida_con_clave_devuelve_200_y_persiste(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        cookies = _cookies("OWNER", "ana")

        respuesta = solicitud(
            "POST", "/api/ventas", cookies=cookies,
            json_body=_cuerpo(producto.id, 2, clave="clave-valida"),
        )

        assert respuesta.status == 200
        datos = respuesta.json()
        assert datos["total_centavos"] == 400
        assert datos["tipo_pago"] == "EFECTIVO"
        assert _contar_ventas() == 1
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 8


class TestIdempotenciaHttp:
    def test_doble_post_con_misma_clave_da_una_sola_venta(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        cookies = _cookies("OWNER", "ana")
        cuerpo = _cuerpo(producto.id, 2, clave="clave-doble")

        r1 = solicitud("POST", "/api/ventas", cookies=cookies, json_body=cuerpo)
        r2 = solicitud("POST", "/api/ventas", cookies=cookies, json_body=cuerpo)

        assert r1.status == 200 and r2.status == 200
        assert r1.json()["id"] == r2.json()["id"]
        assert _contar_ventas() == 1
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 8  # una sola vez

    def test_mismo_key_items_en_otro_orden_sigue_siendo_una_sola_venta(self, base_datos_temporal):
        p1 = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        p2 = servicio_stock.registrar_producto("7790000000002", "Gaseosa", 100, 300, stock_actual=10)
        cookies = _cookies("OWNER", "ana")

        r1 = solicitud(
            "POST", "/api/ventas", cookies=cookies,
            json_body={
                "items": [{"producto_id": p1.id, "cantidad": 2}, {"producto_id": p2.id, "cantidad": 1}],
                "tipo_pago": "EFECTIVO", "clave_idempotencia": "clave-orden",
            },
        )
        r2 = solicitud(
            "POST", "/api/ventas", cookies=cookies,
            json_body={
                "items": [{"producto_id": p2.id, "cantidad": 1}, {"producto_id": p1.id, "cantidad": 2}],
                "tipo_pago": "EFECTIVO", "clave_idempotencia": "clave-orden",
            },
        )

        assert r1.status == 200 and r2.status == 200
        assert r1.json()["id"] == r2.json()["id"]
        assert _contar_ventas() == 1

    def test_mismo_key_cantidad_distinta_da_422(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        cookies = _cookies("OWNER", "ana")
        solicitud("POST", "/api/ventas", cookies=cookies, json_body=_cuerpo(producto.id, 2, clave="clave-c"))

        respuesta = solicitud(
            "POST", "/api/ventas", cookies=cookies, json_body=_cuerpo(producto.id, 3, clave="clave-c")
        )

        assert respuesta.status == 422
        assert _contar_ventas() == 1

    def test_mismo_key_tipo_de_pago_distinto_da_422(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        cookies = _cookies("OWNER", "ana")
        solicitud(
            "POST", "/api/ventas", cookies=cookies,
            json_body=_cuerpo(producto.id, 2, tipo_pago="EFECTIVO", clave="clave-d"),
        )

        respuesta = solicitud(
            "POST", "/api/ventas", cookies=cookies,
            json_body=_cuerpo(producto.id, 2, tipo_pago="TARJETA", clave="clave-d"),
        )

        assert respuesta.status == 422
        assert _contar_ventas() == 1

    def test_sin_clave_de_idempotencia_da_422(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        cookies = _cookies("OWNER", "ana")

        respuesta = solicitud(
            "POST", "/api/ventas", cookies=cookies,
            json_body={"items": [{"producto_id": producto.id, "cantidad": 1}], "tipo_pago": "EFECTIVO"},
        )

        assert respuesta.status == 422
        assert _contar_ventas() == 0

    def test_reintento_tras_respuesta_perdida_recupera_la_misma_venta(self, base_datos_temporal):
        """Simula que el servidor ya proceso la venta pero la respuesta
        nunca llego al cliente (falla de red): el cliente reintenta con
        HTTP real, mismo body y misma clave."""
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        cookies = _cookies("OWNER", "ana")

        venta_ya_procesada = servicio_ventas.registrar_venta(
            [ItemVenta(producto.id, 2)], "EFECTIVO", clave_idempotencia="clave-perdida"
        )

        respuesta = solicitud(
            "POST", "/api/ventas", cookies=cookies, json_body=_cuerpo(producto.id, 2, clave="clave-perdida")
        )

        assert respuesta.status == 200
        assert respuesta.json()["id"] == venta_ya_procesada.id
        assert _contar_ventas() == 1
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 8

    def test_dos_requests_concurrentes_con_la_misma_clave_nunca_duplican(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        cookies = _cookies("OWNER", "ana")
        cuerpo = _cuerpo(producto.id, 2, clave="clave-concurrente")
        resultados: list = []

        def hacer_request():
            resultados.append(solicitud("POST", "/api/ventas", cookies=cookies, json_body=cuerpo))

        hilo_1 = threading.Thread(target=hacer_request)
        hilo_2 = threading.Thread(target=hacer_request)
        hilo_1.start()
        hilo_2.start()
        hilo_1.join()
        hilo_2.join()

        # Nunca debe haber más de una venta, sin importar si alguna de
        # las dos respuestas dio un error transitorio de lock.
        assert _contar_ventas() == 1
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 8


class TestVentaSinCajaAbierta:
    """C2 (V1.1): sin caja abierta la API rechaza la venta con un mensaje
    claro y no persiste nada."""

    @pytest.mark.sin_caja_abierta
    def test_sin_caja_abierta_da_422_con_mensaje_claro_y_no_persiste(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=5)
        cookies = _cookies("CASHIER", "cajera")

        respuesta = solicitud("POST", "/api/ventas", cookies=cookies, json_body=_cuerpo(producto.id, 1))

        assert respuesta.status == 422
        assert "abrí la caja" in respuesta.json()["error"]
        assert _contar_ventas() == 0
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 5

    @pytest.mark.sin_caja_abierta
    def test_el_pos_avisa_que_la_caja_esta_cerrada(self, base_datos_temporal):
        cookies = _cookies("CASHIER", "cajera")

        respuesta = solicitud("GET", "/ventas", cookies=cookies)

        assert respuesta.status == 200
        assert "id=\"aviso-caja-cerrada\"" in respuesta.texto

    def test_el_pos_no_avisa_con_la_caja_abierta(self, base_datos_temporal):
        cookies = _cookies("CASHIER", "cajera")

        respuesta = solicitud("GET", "/ventas", cookies=cookies)

        assert "aviso-caja-cerrada" not in respuesta.texto


class TestValidacionesDeNegocio:
    def test_stock_insuficiente_da_422_y_no_persiste(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=1)
        cookies = _cookies("OWNER", "ana")

        respuesta = solicitud(
            "POST", "/api/ventas", cookies=cookies, json_body=_cuerpo(producto.id, 5, clave="clave-stock")
        )

        assert respuesta.status == 422
        assert _contar_ventas() == 0

    def test_producto_inexistente_da_422(self, base_datos_temporal):
        cookies = _cookies("OWNER", "ana")

        respuesta = solicitud(
            "POST", "/api/ventas", cookies=cookies, json_body=_cuerpo(9999, 1, clave="clave-inexistente")
        )

        assert respuesta.status == 422
        assert _contar_ventas() == 0

    def test_producto_inactivo_da_422(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        servicio_stock.eliminar_producto(producto.id)
        cookies = _cookies("OWNER", "ana")

        respuesta = solicitud(
            "POST", "/api/ventas", cookies=cookies, json_body=_cuerpo(producto.id, 1, clave="clave-inactivo")
        )

        assert respuesta.status == 422
        assert _contar_ventas() == 0

    def test_cantidad_cero_da_422(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        cookies = _cookies("OWNER", "ana")

        respuesta = solicitud(
            "POST", "/api/ventas", cookies=cookies, json_body=_cuerpo(producto.id, 0, clave="clave-cero")
        )

        assert respuesta.status == 422
        assert _contar_ventas() == 0

    def test_cantidad_negativa_da_422(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        cookies = _cookies("OWNER", "ana")

        respuesta = solicitud(
            "POST", "/api/ventas", cookies=cookies, json_body=_cuerpo(producto.id, -1, clave="clave-neg")
        )

        assert respuesta.status == 422
        assert _contar_ventas() == 0

    def test_precio_falso_enviado_por_el_cliente_es_ignorado(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        cookies = _cookies("OWNER", "ana")

        respuesta = solicitud(
            "POST", "/api/ventas", cookies=cookies,
            json_body=_cuerpo(producto.id, 2, clave="clave-precio", precio_unitario_centavos=1),
        )

        assert respuesta.status == 200
        # El total se calcula con el precio real del producto (200), no
        # con el precio falso (1) que mandó el cliente.
        assert respuesta.json()["total_centavos"] == 400

    def test_tipo_de_pago_invalido_da_422(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        cookies = _cookies("OWNER", "ana")

        respuesta = solicitud(
            "POST", "/api/ventas", cookies=cookies,
            json_body=_cuerpo(producto.id, 1, tipo_pago="CRIPTO", clave="clave-pago"),
        )

        assert respuesta.status == 422
        assert _contar_ventas() == 0


class TestPermisos:
    def test_owner_puede_vender(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        cookies = _cookies("OWNER", "ana")

        respuesta = solicitud(
            "POST", "/api/ventas", cookies=cookies, json_body=_cuerpo(producto.id, 1, clave="clave-owner")
        )

        assert respuesta.status == 200

    def test_cashier_puede_vender(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        cookies = _cookies("CASHIER", "carlos")

        respuesta = solicitud(
            "POST", "/api/ventas", cookies=cookies, json_body=_cuerpo(producto.id, 1, clave="clave-cashier")
        )

        assert respuesta.status == 200

    def test_sin_sesion_da_401_json(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)

        respuesta = solicitud("POST", "/api/ventas", json_body=_cuerpo(producto.id, 1, clave="clave-sin-sesion"))

        assert respuesta.status == 401
        assert "error" in respuesta.json()
        assert _contar_ventas() == 0


class TestUsuarioDeLaVenta:
    """Migración 009: la venta queda asociada al usuario autenticado que
    la registró -- nunca al que manda el cliente en el body (`VentaEntrada`
    no tiene ese campo, ver `interfaces/web/esquemas.py`)."""

    def test_venta_queda_asociada_al_usuario_autenticado(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        usuario, token = _crear_usuario_logueado("OWNER", "ana")

        respuesta = solicitud(
            "POST", "/api/ventas",
            cookies={NOMBRE_COOKIE_SESION: token},
            json_body=_cuerpo(producto.id, 1, clave="clave-usuario-1"),
        )

        assert respuesta.status == 200
        venta_id = respuesta.json()["id"]
        assert _usuario_id_de_la_venta(venta_id) == usuario.id

    def test_dos_usuarios_distintos_quedan_asociados_a_sus_propias_ventas(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        owner, token_owner = _crear_usuario_logueado("OWNER", "duenio")
        cajera, token_cajera = _crear_usuario_logueado("CASHIER", "cajera1")

        respuesta_owner = solicitud(
            "POST", "/api/ventas",
            cookies={NOMBRE_COOKIE_SESION: token_owner},
            json_body=_cuerpo(producto.id, 1, clave="clave-owner"),
        )
        respuesta_cajera = solicitud(
            "POST", "/api/ventas",
            cookies={NOMBRE_COOKIE_SESION: token_cajera},
            json_body=_cuerpo(producto.id, 1, clave="clave-cajera"),
        )

        assert _usuario_id_de_la_venta(respuesta_owner.json()["id"]) == owner.id
        assert _usuario_id_de_la_venta(respuesta_cajera.json()["id"]) == cajera.id


class TestHistorialDeVentas:
    def test_owner_puede_ver_el_historial(self, base_datos_temporal):
        cookies = _cookies("OWNER", "duenio")

        respuesta = solicitud("GET", "/ventas/historial", cookies=cookies)

        assert respuesta.status == 200
        assert "Historial" in respuesta.texto

    def test_cashier_puede_ver_el_historial(self, base_datos_temporal):
        cookies = _cookies("CASHIER", "cajera1")

        respuesta = solicitud("GET", "/ventas/historial", cookies=cookies)

        assert respuesta.status == 200

    def test_sin_sesion_redirige_a_login(self, base_datos_temporal):
        respuesta = solicitud("GET", "/ventas/historial")

        assert respuesta.status == 303
        assert respuesta.header("location").startswith("/login")

    def test_filtros_se_reflejan_en_el_formulario(self, base_datos_temporal):
        cookies = _cookies("OWNER", "duenio")

        respuesta = solicitud(
            "GET", "/ventas/historial?fecha_desde=2024-01-01&fecha_hasta=2024-01-31&tipo_pago=TARJETA",
            cookies=cookies,
        )

        assert respuesta.status == 200
        assert 'value="2024-01-01"' in respuesta.texto
        assert 'value="2024-01-31"' in respuesta.texto

    def test_muestra_vendedor_conocido_y_guion_cuando_no_hay(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        owner, token_owner = _crear_usuario_logueado("OWNER", "duenio")
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO", usuario_id=owner.id)
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "TARJETA")  # sin usuario, como el CLI

        respuesta = solicitud(
            "GET", "/ventas/historial", cookies={NOMBRE_COOKIE_SESION: token_owner}
        )

        assert respuesta.status == 200
        assert "Usuario de prueba" in respuesta.texto
        assert "—" in respuesta.texto


class TestDetalleDeVenta:
    def test_muestra_cabecera_lineas_y_total(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        owner, token_owner = _crear_usuario_logueado("OWNER", "duenio")
        venta = servicio_ventas.registrar_venta(
            [ItemVenta(producto.id, 2)], "EFECTIVO", usuario_id=owner.id
        )

        respuesta = solicitud(
            "GET", f"/ventas/{venta.id}", cookies={NOMBRE_COOKIE_SESION: token_owner}
        )

        assert respuesta.status == 200
        assert "Alfajor" in respuesta.texto
        assert "Usuario de prueba" in respuesta.texto
        assert f'href="/ventas/{venta.id}/ticket"' in respuesta.texto
        assert 'target="_blank"' in respuesta.texto
        assert 'href="/ventas/historial"' in respuesta.texto

    def test_vendedor_null_muestra_guion(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        cookies = _cookies("OWNER", "duenio")

        respuesta = solicitud("GET", f"/ventas/{venta.id}", cookies=cookies)

        assert respuesta.status == 200
        assert "—" in respuesta.texto

    def test_venta_inexistente_redirige_al_historial_con_mensaje(self, base_datos_temporal):
        cookies = _cookies("OWNER", "duenio")

        respuesta = solicitud("GET", "/ventas/9999", cookies=cookies)

        assert respuesta.status == 303
        assert respuesta.header("location").startswith("/ventas/historial")

    def test_cashier_tambien_puede_ver_el_detalle(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        cookies = _cookies("CASHIER", "cajera1")

        respuesta = solicitud("GET", f"/ventas/{venta.id}", cookies=cookies)

        assert respuesta.status == 200

    def test_sin_sesion_redirige_a_login(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")

        respuesta = solicitud("GET", f"/ventas/{venta.id}")

        assert respuesta.status == 303
        assert respuesta.header("location").startswith("/login")


class TestVentaConPrecioCero:
    def test_producto_sin_precio_da_422_con_mensaje_claro_y_no_persiste(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("TX-P-001", "Sin precio", 0, 0, stock_actual=10)
        cookies = _cookies("OWNER", "ana")

        respuesta = solicitud(
            "POST", "/api/ventas", cookies=cookies, json_body=_cuerpo(producto.id, 1, clave="clave-precio-cero")
        )

        assert respuesta.status == 422
        assert "precio" in respuesta.texto.lower()
        assert _contar_ventas() == 0
        assert servicio_stock.buscar_por_codigo_barras("TX-P-001").stock_actual == 10


class TestAlertaVisualDeStockCritico:
    """Un producto 0/0 (ej. recién sembrado) no es una alerta; uno con stock bajo el mínimo sí."""

    def test_producto_sin_stock_ni_minimo_no_muestra_alerta_en_lista_ni_pos(self, base_datos_temporal):
        servicio_stock.registrar_producto("TX-A-001", "Sembrado", 0, 0)
        cookies = _cookies("OWNER", "ana")

        lista = solicitud("GET", "/productos", cookies=cookies)
        pos = solicitud("GET", "/ventas", cookies=cookies)

        assert "Sembrado" in lista.texto and "Sembrado" in pos.texto
        assert "tabular-nums text-error" not in lista.texto
        assert "bg-error mt-1" not in pos.texto

    def test_producto_con_stock_bajo_el_minimo_si_muestra_alerta_en_lista_y_pos(self, base_datos_temporal):
        servicio_stock.registrar_producto("TX-A-002", "Escaso", 0, 100, stock_actual=2, stock_minimo=5)
        cookies = _cookies("OWNER", "ana")

        lista = solicitud("GET", "/productos", cookies=cookies)
        pos = solicitud("GET", "/ventas", cookies=cookies)

        assert "tabular-nums text-error" in lista.texto
        assert "bg-error mt-1" in pos.texto
