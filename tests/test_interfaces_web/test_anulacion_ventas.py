"""Pruebas HTTP end-to-end de anulación de ventas: formulario, submit,
visibilidad en historial/detalle/ticket. Exclusivo de OWNER (igual
criterio que `test_ajuste_stock.py`, mismo patrón de este archivo)."""

from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from domain.venta import ItemVenta
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth, servicio_caja, servicio_stock, servicio_ventas

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


def _crear_venta(producto_id: int, cantidad: int = 1, tipo_pago: str = "EFECTIVO"):
    return servicio_ventas.registrar_venta([ItemVenta(producto_id, cantidad)], tipo_pago)


class TestFormularioAnular:
    def test_owner_puede_ver_el_formulario(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = _crear_producto()
        venta = _crear_venta(producto.id)
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")

        respuesta = solicitud("GET", f"/ventas/{venta.id}/anular", cookies=cookies)

        assert respuesta.status == 200

    def test_cashier_recibe_403(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = _crear_producto()
        venta = _crear_venta(producto.id)
        _, cookies = _crear_usuario_logueado("CASHIER", "cajera1")

        respuesta = solicitud("GET", f"/ventas/{venta.id}/anular", cookies=cookies)

        assert respuesta.status == 403

    def test_sin_sesion_redirige_a_login(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = _crear_producto()
        venta = _crear_venta(producto.id)

        respuesta = solicitud("GET", f"/ventas/{venta.id}/anular")

        assert respuesta.status == 303
        assert respuesta.header("location").startswith("/login")

    def test_venta_inexistente_redirige_con_error(self, base_datos_temporal):
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")

        respuesta = solicitud("GET", "/ventas/9999/anular", cookies=cookies)

        assert respuesta.status == 303
        assert "tipo=error" in respuesta.header("location")

    def test_venta_ya_anulada_redirige_con_error(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = _crear_producto()
        venta = _crear_venta(producto.id)
        owner, cookies = _crear_usuario_logueado("OWNER", "duenio")
        servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        respuesta = solicitud("GET", f"/ventas/{venta.id}/anular", cookies=cookies)

        assert respuesta.status == 303
        assert "tipo=error" in respuesta.header("location")


class TestSubmitAnular:
    def test_owner_puede_anular_y_el_stock_se_restaura(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = _crear_producto(stock_actual=10)
        venta = _crear_venta(producto.id, cantidad=3)
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")

        respuesta = solicitud(
            "POST",
            f"/ventas/{venta.id}/anular",
            cookies=cookies,
            formulario={"motivo": "ERROR_CARGA", "observaciones": ""},
        )

        assert respuesta.status == 303
        assert "tipo=success" in respuesta.header("location")
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 10
        with obtener_conexion() as conexion:
            estado = conexion.execute("SELECT estado FROM ventas WHERE id = ?", (venta.id,)).fetchone()["estado"]
        assert estado == "ANULADA"

    def test_registra_el_usuario_autenticado_como_quien_anula(self, base_datos_temporal):
        """No confía en ningún dato del formulario para saber quién
        anula: siempre el usuario de la sesión."""
        servicio_caja.abrir_caja(100_000)
        producto = _crear_producto()
        venta = _crear_venta(producto.id)
        owner, cookies = _crear_usuario_logueado("OWNER", "duenio")

        solicitud(
            "POST",
            f"/ventas/{venta.id}/anular",
            cookies=cookies,
            formulario={"motivo": "ERROR_CARGA", "observaciones": ""},
        )

        with obtener_conexion() as conexion:
            anulada_por = conexion.execute(
                "SELECT anulada_por_usuario_id FROM ventas WHERE id = ?", (venta.id,)
            ).fetchone()["anulada_por_usuario_id"]
        assert anulada_por == owner.id

    def test_cashier_recibe_403_y_no_modifica_nada(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = _crear_producto(stock_actual=10)
        venta = _crear_venta(producto.id, cantidad=3)
        _, cookies = _crear_usuario_logueado("CASHIER", "cajera1")

        respuesta = solicitud(
            "POST",
            f"/ventas/{venta.id}/anular",
            cookies=cookies,
            formulario={"motivo": "ERROR_CARGA", "observaciones": ""},
        )

        assert respuesta.status == 403
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 7

    def test_sin_sesion_redirige_a_login_y_no_modifica_nada(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = _crear_producto(stock_actual=10)
        venta = _crear_venta(producto.id, cantidad=3)

        respuesta = solicitud(
            "POST",
            f"/ventas/{venta.id}/anular",
            formulario={"motivo": "ERROR_CARGA", "observaciones": ""},
        )

        assert respuesta.status == 303
        assert respuesta.header("location").startswith("/login")
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 7

    def test_otro_sin_observaciones_es_rechazado_en_el_servidor(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = _crear_producto(stock_actual=10)
        venta = _crear_venta(producto.id, cantidad=3)
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")

        respuesta = solicitud(
            "POST",
            f"/ventas/{venta.id}/anular",
            cookies=cookies,
            formulario={"motivo": "OTRO", "observaciones": ""},
        )

        assert respuesta.status == 303
        assert "tipo=error" in respuesta.header("location")
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 7

    def test_motivo_invalido_es_rechazado(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = _crear_producto(stock_actual=10)
        venta = _crear_venta(producto.id, cantidad=3)
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")

        respuesta = solicitud(
            "POST",
            f"/ventas/{venta.id}/anular",
            cookies=cookies,
            formulario={"motivo": "PORQUE_SI", "observaciones": ""},
        )

        assert respuesta.status == 303
        assert "tipo=error" in respuesta.header("location")
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 7

    def test_venta_ya_anulada_es_rechazada(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = _crear_producto(stock_actual=10)
        venta = _crear_venta(producto.id, cantidad=3)
        owner, cookies = _crear_usuario_logueado("OWNER", "duenio")
        servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        respuesta = solicitud(
            "POST",
            f"/ventas/{venta.id}/anular",
            cookies=cookies,
            formulario={"motivo": "OTRO", "observaciones": "segundo intento"},
        )

        assert respuesta.status == 303
        assert "tipo=error" in respuesta.header("location")
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 10  # sin restaurar dos veces

    def test_sin_caja_abierta_es_rechazada(self, base_datos_temporal):
        producto = _crear_producto(stock_actual=10)
        venta = _crear_venta(producto.id, cantidad=3)
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")

        respuesta = solicitud(
            "POST",
            f"/ventas/{venta.id}/anular",
            cookies=cookies,
            formulario={"motivo": "ERROR_CARGA", "observaciones": ""},
        )

        assert respuesta.status == 303
        assert "tipo=error" in respuesta.header("location")
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 7

    def test_no_inserta_ninguna_fila_en_caja_movimientos(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = _crear_producto(stock_actual=10)
        venta = _crear_venta(producto.id, cantidad=3)
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")

        solicitud(
            "POST",
            f"/ventas/{venta.id}/anular",
            cookies=cookies,
            formulario={"motivo": "ERROR_CARGA", "observaciones": ""},
        )

        with obtener_conexion() as conexion:
            total = conexion.execute("SELECT COUNT(*) AS n FROM caja_movimientos").fetchone()["n"]
        assert total == 1  # solo la APERTURA original


class TestVisibilidadDeVentaAnulada:
    def test_historial_muestra_la_venta_anulada(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = _crear_producto()
        venta = _crear_venta(producto.id)
        owner, cookies = _crear_usuario_logueado("OWNER", "duenio")
        servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        respuesta = solicitud(
            "GET", f"/ventas/historial?fecha_desde=2000-01-01&fecha_hasta=2099-12-31", cookies=cookies
        )

        assert respuesta.status == 200
        assert "ANULADA" in respuesta.texto
        assert f"/ventas/{venta.id}" in respuesta.texto

    def test_detalle_de_venta_anulada_sigue_accesible(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = _crear_producto()
        venta = _crear_venta(producto.id)
        owner, cookies = _crear_usuario_logueado("OWNER", "duenio")
        servicio_ventas.anular_venta(
            venta.id, motivo="ARREPENTIMIENTO_CLIENTE", observaciones=None, usuario_id=owner.id
        )

        respuesta = solicitud("GET", f"/ventas/{venta.id}", cookies=cookies)

        assert respuesta.status == 200
        assert "ANULADA" in respuesta.texto
        assert "ARREPENTIMIENTO_CLIENTE" in respuesta.texto

    def test_ticket_de_venta_anulada_sigue_accesible_con_aviso(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = _crear_producto()
        venta = _crear_venta(producto.id)
        owner, cookies = _crear_usuario_logueado("OWNER", "duenio")
        servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        respuesta = solicitud("GET", f"/ventas/{venta.id}/ticket", cookies=cookies)

        assert respuesta.status == 200
        assert "VENTA ANULADA" in respuesta.texto

    def test_venta_activa_no_muestra_boton_de_anular_para_cashier(self, base_datos_temporal):
        """El botón "Anular venta" de `detalle.html` es condicional a
        `usuario_actual.es_owner` -- un CASHIER autenticado no debe verlo,
        aunque pueda ver el resto del detalle."""
        servicio_caja.abrir_caja(100_000)
        producto = _crear_producto()
        venta = _crear_venta(producto.id)
        _, cookies = _crear_usuario_logueado("CASHIER", "cajera1")

        respuesta = solicitud("GET", f"/ventas/{venta.id}", cookies=cookies)

        assert respuesta.status == 200
        assert f"/ventas/{venta.id}/anular" not in respuesta.texto

    def test_venta_activa_muestra_boton_de_anular_para_owner(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = _crear_producto()
        venta = _crear_venta(producto.id)
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")

        respuesta = solicitud("GET", f"/ventas/{venta.id}", cookies=cookies)

        assert respuesta.status == 200
        assert f"/ventas/{venta.id}/anular" in respuesta.texto


class TestFiltroEstadoEnHistorial:
    """Visibilidad de Anulaciones: filtro `estado` del Historial,
    probado de punta a punta contra la ruta y el template reales (no
    solo contra el repositorio/servicio, ya cubiertos en
    tests/test_db/test_ventas.py y tests/test_servicio_ventas.py)."""

    def _href(self, venta_id: int) -> str:
        return f'href="/ventas/{venta_id}"'

    def test_sin_estado_mantiene_el_comportamiento_actual(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = _crear_producto()
        venta_activa = _crear_venta(producto.id)
        venta_anulada = _crear_venta(producto.id)
        owner, cookies = _crear_usuario_logueado("OWNER", "duenio")
        servicio_ventas.anular_venta(venta_anulada.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        respuesta = solicitud("GET", "/ventas/historial", cookies=cookies)

        assert respuesta.status == 200
        assert self._href(venta_activa.id) in respuesta.texto
        assert self._href(venta_anulada.id) in respuesta.texto

    def test_estado_activa_excluye_anuladas(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = _crear_producto()
        venta_activa = _crear_venta(producto.id)
        venta_anulada = _crear_venta(producto.id)
        owner, cookies = _crear_usuario_logueado("OWNER", "duenio")
        servicio_ventas.anular_venta(venta_anulada.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        respuesta = solicitud("GET", "/ventas/historial?estado=ACTIVA", cookies=cookies)

        assert respuesta.status == 200
        assert self._href(venta_activa.id) in respuesta.texto
        assert self._href(venta_anulada.id) not in respuesta.texto

    def test_estado_anulada_excluye_activas(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = _crear_producto()
        venta_activa = _crear_venta(producto.id)
        venta_anulada = _crear_venta(producto.id)
        owner, cookies = _crear_usuario_logueado("OWNER", "duenio")
        servicio_ventas.anular_venta(venta_anulada.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        respuesta = solicitud("GET", "/ventas/historial?estado=ANULADA", cookies=cookies)

        assert respuesta.status == 200
        assert self._href(venta_anulada.id) in respuesta.texto
        assert self._href(venta_activa.id) not in respuesta.texto

    def test_estado_vacio_del_formulario_no_rompe_el_listado(self, base_datos_temporal):
        """El <select> de "Todas" envía `estado=""` -- no debe filtrar a
        cero resultados (ver bonus fix de este mismo bloque:
        `estado or None` en `interfaces.web.rutas.ventas.historial_ventas`).

        Solo `estado=""` acá -- `tipo_pago=""` tiene un defecto
        preexistente y ya conocido (mismo síntoma, otro campo, fuera de
        alcance de este bloque: ver auditoría), que combinado rompería
        este test por una razón ajena a lo que se quiere probar."""
        servicio_caja.abrir_caja(100_000)
        producto = _crear_producto()
        venta = _crear_venta(producto.id)
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")

        respuesta = solicitud("GET", "/ventas/historial?estado=", cookies=cookies)

        assert respuesta.status == 200
        assert self._href(venta.id) in respuesta.texto
        assert "Sin ventas en el período" not in respuesta.texto

    def test_estado_se_combina_con_fecha_y_tipo_pago_desde_http(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = _crear_producto()
        venta_efectivo_anulada = _crear_venta(producto.id, tipo_pago="EFECTIVO")
        venta_tarjeta_activa = _crear_venta(producto.id, tipo_pago="TARJETA")
        owner, cookies = _crear_usuario_logueado("OWNER", "duenio")
        servicio_ventas.anular_venta(
            venta_efectivo_anulada.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id
        )

        respuesta = solicitud(
            "GET",
            "/ventas/historial?estado=ANULADA&tipo_pago=EFECTIVO&fecha_desde=2000-01-01&fecha_hasta=2099-12-31",
            cookies=cookies,
        )

        assert respuesta.status == 200
        assert self._href(venta_efectivo_anulada.id) in respuesta.texto
        assert self._href(venta_tarjeta_activa.id) not in respuesta.texto

    def test_venta_anulada_renderiza_badge_y_motivo(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = _crear_producto()
        venta = _crear_venta(producto.id)
        owner, cookies = _crear_usuario_logueado("OWNER", "duenio")
        servicio_ventas.anular_venta(venta.id, motivo="PRODUCTO_INCORRECTO", observaciones=None, usuario_id=owner.id)

        respuesta = solicitud("GET", "/ventas/historial", cookies=cookies)

        assert respuesta.status == 200
        assert "ANULADA" in respuesta.texto
        assert "PRODUCTO_INCORRECTO" in respuesta.texto

    def test_venta_activa_no_renderiza_motivo_de_anulacion(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = _crear_producto()
        _crear_venta(producto.id)
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")

        respuesta = solicitud("GET", "/ventas/historial", cookies=cookies)

        assert respuesta.status == 200
        # Ninguno de los motivos reales (salvo "OTRO", que también es una
        # opción del <select> de tipo de pago y aparecería igual sin
        # relación con esto) debe aparecer: no hay ninguna venta anulada.
        assert "ERROR_CARGA" not in respuesta.texto
        assert "ARREPENTIMIENTO_CLIENTE" not in respuesta.texto
        assert "PRODUCTO_INCORRECTO" not in respuesta.texto
