"""Pruebas estructurales del HTML servido por `GET /ventas` (Fase 5B):
barra inferior fija y bottom sheet del carrito para mobile/tablet, más
los ids/atributos de los que depende `static/js/pos.js`.

No prueban JS (no hay runner de JS en el proyecto, ver diseño de Fase
5B): solo confirman que el HTML servido contiene los ganchos correctos,
igual que ya hacía `test_ventas.py` a nivel de API."""

from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth, servicio_stock

from ._asgi_cliente import solicitud


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


def _html_pos(base_datos_temporal) -> str:
    servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
    respuesta = solicitud("GET", "/ventas", cookies=_cookies())
    assert respuesta.status == 200
    return respuesta.texto


class TestGanchosExistentesDePosJs:
    """Ids/atributos que `pos.js` ya lee con `getElementById`/
    `querySelector` -- deben sobrevivir intactos a la Fase 5B."""

    def test_elementos_criticos_del_pos_siguen_presentes(self, base_datos_temporal):
        html = _html_pos(base_datos_temporal)

        assert 'id="input-codigo-barras"' in html
        assert 'data-producto-card' in html
        assert 'id="carrito-lista"' in html
        assert 'id="carrito-vacio"' in html
        assert 'id="carrito-total"' in html
        assert 'id="boton-cobrar"' in html
        assert 'name="tipo_pago"' in html


class TestBarraInferiorFija:
    def test_barra_inferior_presente_con_total_cantidad_y_cobrar(self, base_datos_temporal):
        html = _html_pos(base_datos_temporal)

        assert 'id="carrito-bar"' in html
        assert 'id="carrito-bar-cantidad"' in html
        assert 'id="carrito-bar-total"' in html
        assert 'id="boton-cobrar-bar"' in html

    def test_boton_cobrar_de_la_barra_arranca_deshabilitado(self, base_datos_temporal):
        html = _html_pos(base_datos_temporal)
        inicio = html.index('id="boton-cobrar-bar"')
        fragmento = html[max(0, inicio - 200):inicio + 50]
        assert "disabled" in fragmento


class TestBottomSheetDelCarrito:
    def test_sheet_y_backdrop_presentes(self, base_datos_temporal):
        html = _html_pos(base_datos_temporal)

        assert 'id="carrito-sheet"' in html
        assert 'id="carrito-backdrop"' in html

    def test_trigger_tiene_aria_expanded_y_aria_controls(self, base_datos_temporal):
        html = _html_pos(base_datos_temporal)

        assert 'id="carrito-bar-toggle"' in html
        assert 'aria-controls="carrito-sheet"' in html
        assert 'aria-expanded="false"' in html

    def test_sheet_tiene_boton_de_cierre(self, base_datos_temporal):
        html = _html_pos(base_datos_temporal)
        assert 'id="carrito-sheet-cerrar"' in html


class TestAccesibilidad:
    def test_input_codigo_barras_tiene_aria_label(self, base_datos_temporal):
        html = _html_pos(base_datos_temporal)
        inicio = html.index('id="input-codigo-barras"')
        fragmento = html[max(0, inicio - 200):inicio + 200]
        assert "aria-label" in fragmento

    def test_toast_container_tiene_aria_live(self, base_datos_temporal):
        html = _html_pos(base_datos_temporal)
        inicio = html.index('id="toast-container"')
        fragmento = html[max(0, inicio - 50):inicio + 150]
        assert 'aria-live="polite"' in fragmento


class TestAccionesPostVenta:
    """Fase 5D: bloque de 'Imprimir ticket'/'Nueva venta' tras un cobro
    exitoso -- oculto por defecto en el HTML servido, lo muestra
    pos.js recién después de un POST /api/ventas exitoso."""

    def test_bloque_post_venta_presente_y_oculto_por_defecto(self, base_datos_temporal):
        html = _html_pos(base_datos_temporal)
        import re

        coincidencia = re.search(r'<div id="post-venta-acciones" class="([^"]*)"', html)
        assert coincidencia is not None
        assert "hidden" in coincidencia.group(1).split()

    def test_boton_imprimir_ticket_abre_en_otra_pestana(self, base_datos_temporal):
        html = _html_pos(base_datos_temporal)
        inicio = html.index('id="post-venta-imprimir"')
        fragmento = html[inicio:inicio + 150]
        assert 'target="_blank"' in fragmento

    def test_boton_nueva_venta_presente(self, base_datos_temporal):
        html = _html_pos(base_datos_temporal)
        assert 'id="post-venta-nueva-venta"' in html
