"""Pruebas estructurales del HTML servido por `GET /ventas` (Fase 5C):
bloque de "monto recibido + vuelto" y su script asociado.

La lógica monetaria en sí (texto -> centavos, insuficiente/exacto/con
vuelto, rango seguro) ya está cubierta exhaustivamente por los tests
puros de `tests_js/dinero_cobro.test.js` (node:test) -- acá solo se
verifica que el HTML que sirve `ventas.py` tenga los ganchos que
`pos.js` necesita para engancharse, igual que ya hacía
`test_pos_layout.py` para los elementos de Fase 5B."""

import re

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


class TestBloqueEfectivoVuelto:
    def test_bloque_efectivo_presente_y_oculto_por_defecto(self, base_datos_temporal):
        """Verifica que la clase `hidden` esté presente como palabra
        completa en el `class` del bloque, sin importar en qué posición
        aparezca respecto de otras clases (ej. `space-y-2 hidden` debe
        pasar igual que `hidden space-y-2`)."""
        html = _html_pos(base_datos_temporal)
        coincidencia = re.search(r'<div id="bloque-efectivo" class="([^"]*)"', html)
        assert coincidencia is not None
        clases = coincidencia.group(1).split()
        assert "hidden" in clases

    def test_input_monto_recibido_tiene_label_asociado(self, base_datos_temporal):
        html = _html_pos(base_datos_temporal)
        assert 'id="monto-recibido"' in html
        assert 'for="monto-recibido"' in html
        assert 'inputmode="decimal"' in html

    def test_vuelto_resultado_tiene_aria_live(self, base_datos_temporal):
        html = _html_pos(base_datos_temporal)
        inicio = html.index('id="vuelto-resultado"')
        fragmento = html[max(0, inicio - 30):inicio + 60]
        assert 'aria-live="polite"' in fragmento

    def test_dinero_cobro_js_se_sirve_antes_que_pos_js(self, base_datos_temporal):
        html = _html_pos(base_datos_temporal)
        indice_dinero = html.index('/static/js/dinero_cobro.js')
        indice_pos = html.index('/static/js/pos.js')
        assert indice_dinero < indice_pos

    def test_no_se_agrego_ningun_campo_de_monto_recibido_al_esquema_de_venta(self, base_datos_temporal):
        """Regresión de alcance: el bloqueo por efectivo insuficiente es
        solo de UX (ver pos.js::actualizarEstadoCobrar) -- el body de
        POST /api/ventas sigue siendo items/tipo_pago/clave (+ `cliente_id`, opcional, desde
        la 021: la venta a cuenta). Ningún campo de monto recibido ni de vuelto."""
        from interfaces.web.esquemas import VentaEntrada

        campos = set(VentaEntrada.model_fields.keys())
        assert campos == {"items", "tipo_pago", "clave_idempotencia", "cliente_id"}
        assert not {"monto_recibido", "vuelto", "efectivo_recibido"} & campos
        assert VentaEntrada.model_fields["cliente_id"].is_required() is False
