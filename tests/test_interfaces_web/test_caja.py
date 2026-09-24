"""Pruebas HTTP end-to-end de las 4 acciones de caja (apertura, cierre,
ingreso, egreso), con foco en la trazabilidad de usuario (migración 010):
no existía ningún test a este nivel antes de este bloque -- toda la
cobertura previa era de `services.servicio_caja` directamente."""

from urllib.parse import unquote

from db.conexion import obtener_conexion
from db.repositorios import caja as repositorio_caja
from db.repositorios import usuarios as repositorio_usuarios
from domain.caja import MovimientoCaja
from domain.usuario import Usuario
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth

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


def _usuario_id_del_ultimo_movimiento() -> int | None:
    with obtener_conexion() as conexion:
        return conexion.execute(
            "SELECT usuario_id FROM caja_movimientos ORDER BY id DESC LIMIT 1"
        ).fetchone()["usuario_id"]


class TestAbrirCaja:
    def test_persiste_el_usuario_autenticado(self, base_datos_temporal):
        usuario, cookies = _crear_usuario_logueado("OWNER", "duenio")

        respuesta = solicitud(
            "POST", "/caja/abrir", cookies=cookies, formulario={"monto_inicial": "1000", "descripcion": "Apertura"}
        )

        assert respuesta.status == 303
        assert _usuario_id_del_ultimo_movimiento() == usuario.id

    def test_sin_sesion_redirige_a_login_y_no_persiste_nada(self, base_datos_temporal):
        respuesta = solicitud("POST", "/caja/abrir", formulario={"monto_inicial": "1000"})

        assert respuesta.status == 303
        assert respuesta.header("location").startswith("/login")
        with obtener_conexion() as conexion:
            total = conexion.execute("SELECT COUNT(*) AS n FROM caja_movimientos").fetchone()["n"]
        assert total == 0


class TestCerrarCaja:
    def test_persiste_el_usuario_autenticado(self, base_datos_temporal):
        usuario, cookies = _crear_usuario_logueado("CASHIER", "cajera1")
        solicitud("POST", "/caja/abrir", cookies=cookies, formulario={"monto_inicial": "1000"})

        respuesta = solicitud("POST", "/caja/cerrar", cookies=cookies, formulario={"monto_final": "1000"})

        assert respuesta.status == 303
        assert _usuario_id_del_ultimo_movimiento() == usuario.id


class TestRegistrarIngreso:
    def test_persiste_el_usuario_autenticado(self, base_datos_temporal):
        usuario, cookies = _crear_usuario_logueado("OWNER", "duenio")
        solicitud("POST", "/caja/abrir", cookies=cookies, formulario={"monto_inicial": "1000"})

        respuesta = solicitud(
            "POST", "/caja/ingreso", cookies=cookies, formulario={"monto": "500", "descripcion": "cambio"}
        )

        assert respuesta.status == 303
        assert _usuario_id_del_ultimo_movimiento() == usuario.id


class TestRegistrarEgreso:
    def test_persiste_el_usuario_autenticado(self, base_datos_temporal):
        usuario, cookies = _crear_usuario_logueado("CASHIER", "cajera1")
        solicitud("POST", "/caja/abrir", cookies=cookies, formulario={"monto_inicial": "1000"})

        respuesta = solicitud(
            "POST", "/caja/egreso", cookies=cookies, formulario={"monto": "200", "descripcion": "pago a proveedor"}
        )

        assert respuesta.status == 303
        assert _usuario_id_del_ultimo_movimiento() == usuario.id


class TestDosUsuariosDistintos:
    def test_cada_movimiento_queda_asociado_a_su_propio_usuario(self, base_datos_temporal):
        owner, cookies_owner = _crear_usuario_logueado("OWNER", "duenio")
        cajera, cookies_cajera = _crear_usuario_logueado("CASHIER", "cajera1")

        solicitud("POST", "/caja/abrir", cookies=cookies_owner, formulario={"monto_inicial": "1000"})
        assert _usuario_id_del_ultimo_movimiento() == owner.id

        solicitud(
            "POST", "/caja/ingreso", cookies=cookies_cajera, formulario={"monto": "500", "descripcion": "cambio"}
        )
        assert _usuario_id_del_ultimo_movimiento() == cajera.id


class TestFlujoExistenteDeCajaSigueFuncionando:
    """Regresión: la protección de roles y el flujo normal de caja no
    cambian por agregar la trazabilidad de usuario."""

    def test_ciclo_completo_via_http(self, base_datos_temporal):
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")

        r1 = solicitud("POST", "/caja/abrir", cookies=cookies, formulario={"monto_inicial": "1000"})
        r2 = solicitud(
            "POST", "/caja/ingreso", cookies=cookies, formulario={"monto": "500", "descripcion": "cambio"}
        )
        r3 = solicitud(
            "POST", "/caja/egreso", cookies=cookies, formulario={"monto": "200", "descripcion": "pago"}
        )
        r4 = solicitud("POST", "/caja/cerrar", cookies=cookies, formulario={"monto_final": "1300"})

        assert all(r.status == 303 for r in (r1, r2, r3, r4))

    def test_sin_caja_abierta_sigue_rechazando_ingreso(self, base_datos_temporal):
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")

        respuesta = solicitud(
            "POST", "/caja/ingreso", cookies=cookies, formulario={"monto": "500", "descripcion": "cambio"}
        )

        # ErrorAplicacion (CajaError) -> redirect con toast de error, no 500.
        # No se persistió nada (ni ese ni ningún otro movimiento).
        assert respuesta.status == 303
        with obtener_conexion() as conexion:
            total = conexion.execute("SELECT COUNT(*) AS n FROM caja_movimientos").fetchone()["n"]
        assert total == 0


class TestDiferenciaDeCierre:
    """Migración 011: el toast de cierre y el historial de movimientos
    reflejan el resultado (sobrante/faltante/cuadrada)."""

    def test_cierre_exacto_muestra_caja_cuadrada(self, base_datos_temporal):
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")
        solicitud("POST", "/caja/abrir", cookies=cookies, formulario={"monto_inicial": "1000"})

        respuesta = solicitud("POST", "/caja/cerrar", cookies=cookies, formulario={"monto_final": "1000"})

        assert respuesta.status == 303
        assert "Caja cuadrada" in unquote(respuesta.header("location"))

    def test_cierre_con_sobrante_muestra_el_toast_correcto(self, base_datos_temporal):
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")
        solicitud("POST", "/caja/abrir", cookies=cookies, formulario={"monto_inicial": "1000"})

        respuesta = solicitud("POST", "/caja/cerrar", cookies=cookies, formulario={"monto_final": "1005"})

        assert respuesta.status == 303
        assert "Sobrante" in unquote(respuesta.header("location"))

    def test_cierre_con_faltante_muestra_el_toast_correcto(self, base_datos_temporal):
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")
        solicitud("POST", "/caja/abrir", cookies=cookies, formulario={"monto_inicial": "1000"})

        respuesta = solicitud("POST", "/caja/cerrar", cookies=cookies, formulario={"monto_final": "995"})

        assert respuesta.status == 303
        assert "Faltante" in unquote(respuesta.header("location"))

    def test_historial_muestra_el_resultado_del_cierre(self, base_datos_temporal):
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")
        solicitud("POST", "/caja/abrir", cookies=cookies, formulario={"monto_inicial": "1000"})
        solicitud("POST", "/caja/cerrar", cookies=cookies, formulario={"monto_final": "1005"})

        respuesta = solicitud("GET", "/caja", cookies=cookies)

        assert respuesta.status == 200
        assert "Sobrante" in respuesta.texto

    def test_cierre_historico_sin_diferencia_no_rompe_el_historial(self, base_datos_temporal):
        """Un cierre insertado directo (simulando uno anterior a la
        migración 011, sin diferencia_centavos) no debe romper el
        render de /caja ni mostrar ningún resultado inventado."""
        repositorio_caja.registrar_movimiento(MovimientoCaja(tipo="APERTURA", monto_centavos=100000))
        repositorio_caja.registrar_movimiento(MovimientoCaja(tipo="CIERRE", monto_centavos=100000))
        _, cookies = _crear_usuario_logueado("OWNER", "duenio")

        respuesta = solicitud("GET", "/caja", cookies=cookies)

        assert respuesta.status == 200
        assert "Sobrante" not in respuesta.texto
        assert "Faltante" not in respuesta.texto
        assert "Caja cuadrada" not in respuesta.texto

    def test_cashier_tambien_ve_el_resultado_del_cierre(self, base_datos_temporal):
        _, cookies = _crear_usuario_logueado("CASHIER", "cajera1")
        solicitud("POST", "/caja/abrir", cookies=cookies, formulario={"monto_inicial": "1000"})

        respuesta = solicitud("POST", "/caja/cerrar", cookies=cookies, formulario={"monto_final": "990"})

        assert respuesta.status == 303
        assert "Faltante" in unquote(respuesta.header("location"))
