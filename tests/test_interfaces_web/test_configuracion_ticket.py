"""V1.2 Fase 5: datos comerciales configurables del ticket (nombre, dirección,
teléfono, leyenda de pie) -- validación, persistencia, auditoría, permisos y
que el ticket siga siendo un comprobante interno sin datos fiscales."""

import pytest

from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from domain.comercio import LIMITES, NOMBRE_POR_DEFECTO, DatosComercio
from domain.usuario import Usuario
from domain.venta import ItemVenta
from excepciones import DatosInvalidosError
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth, servicio_caja, servicio_configuracion, servicio_stock, servicio_ventas

from ._asgi_cliente import solicitud


def _cookies(nombre="duenio", rol="OWNER"):
    repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre,
            nombre_completo=f"Nombre {nombre}",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol=rol,
        )
    )
    return {NOMBRE_COOKIE_SESION: servicio_auth.iniciar_sesion(nombre, "clave-correcta-123").token}


def _venta():
    servicio_caja.abrir_caja(0)
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 250, stock_actual=5)
    return servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")


def _venta_otra():
    producto = servicio_stock.registrar_producto("7790000000002", "Gaseosa", 100, 300, stock_actual=5)
    return servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")


def _ticket(cookies, venta):
    return solicitud("GET", f"/ventas/{venta.id}/ticket", cookies=cookies).texto


class TestDominio:
    def test_valores_por_defecto(self):
        assert DatosComercio() == DatosComercio(nombre="Kiosco", direccion="", telefono="", pie="")
        assert DatosComercio(nombre="   ").nombre == NOMBRE_POR_DEFECTO

    def test_limpia_espacios_saltos_de_linea_y_caracteres_de_control(self):
        datos = DatosComercio(nombre="  Kiosco \n  del   Sur\x00\x07 ", direccion="Calle\t123")

        assert (datos.nombre, datos.direccion) == ("Kiosco del Sur", "Calle 123")

    @pytest.mark.parametrize("campo", list(LIMITES))
    def test_rechaza_textos_mas_largos_que_el_limite(self, campo):
        with pytest.raises(DatosInvalidosError):
            DatosComercio(**{campo: "x" * (LIMITES[campo] + 1)})

    @pytest.mark.parametrize("campo", list(LIMITES))
    def test_acepta_el_largo_maximo_exacto(self, campo):
        assert len(getattr(DatosComercio(**{campo: "x" * LIMITES[campo]}), campo)) == LIMITES[campo]


class TestServicio:
    def test_sin_configurar_devuelve_los_valores_por_defecto(self, base_datos_temporal):
        assert servicio_configuracion.obtener_datos_comercio() == DatosComercio()

    def test_guarda_y_lee_y_reemplaza(self, base_datos_temporal):
        servicio_configuracion.guardar_datos_comercio(DatosComercio("Mi Kiosco", "Calle 1", "11-2222", "Gracias"))
        servicio_configuracion.guardar_datos_comercio(DatosComercio("Mi Kiosco", "Calle 2", "", "Gracias"))

        assert servicio_configuracion.obtener_datos_comercio() == DatosComercio("Mi Kiosco", "Calle 2", "", "Gracias")
        with obtener_conexion() as conexion:
            assert conexion.execute("SELECT COUNT(*) FROM configuracion").fetchone()[0] == 4

    def test_devuelve_los_campos_que_cambiaron_y_no_escribe_si_no_hay_cambios(self, base_datos_temporal):
        assert servicio_configuracion.guardar_datos_comercio(DatosComercio("A", "B", "C", "D")) == [
            "nombre", "dirección", "teléfono", "leyenda de pie",
        ]
        assert servicio_configuracion.guardar_datos_comercio(DatosComercio("A", "B", "C", "D")) == []
        assert servicio_configuracion.guardar_datos_comercio(DatosComercio("A", "B", "Z", "D")) == ["teléfono"]

    def test_la_auditoria_registra_que_campos_cambiaron_y_no_sus_valores(self, base_datos_temporal):
        _cookies()
        with obtener_conexion() as conexion:
            usuario_id = conexion.execute("SELECT id FROM usuarios").fetchone()[0]

        servicio_configuracion.guardar_datos_comercio(DatosComercio("Secreto S.A.", "", "11-9999"), usuario_id=usuario_id)
        servicio_configuracion.guardar_datos_comercio(DatosComercio("Secreto S.A.", "", "11-9999"), usuario_id=usuario_id)

        with obtener_conexion() as conexion:
            filas = conexion.execute("SELECT accion, entidad, usuario_id, resumen FROM auditoria").fetchall()
        assert len(filas) == 1  # el segundo guardado no cambió nada
        assert filas[0]["accion"] == "CONFIGURACION_COMERCIO_CAMBIADA" and filas[0]["usuario_id"] == usuario_id
        assert "nombre" in filas[0]["resumen"] and "teléfono" in filas[0]["resumen"]
        assert "Secreto" not in filas[0]["resumen"] and "9999" not in filas[0]["resumen"]

    def test_un_valor_invalido_no_guarda_nada(self, base_datos_temporal):
        servicio_configuracion.guardar_datos_comercio(DatosComercio("Original"))

        with pytest.raises(DatosInvalidosError):
            servicio_configuracion.guardar_datos_comercio(DatosComercio("Nuevo", direccion="x" * 500))

        assert servicio_configuracion.obtener_datos_comercio().nombre == "Original"


class TestTicket:
    def test_sin_configurar_conserva_el_kiosco_de_siempre_sin_texto_comercial_agregado(self, base_datos_temporal):
        cookies = _cookies()
        html = _ticket(cookies, _venta())

        assert "<strong>Kiosco</strong>" in html
        assert "Tel." not in html
        assert 'class="pie"' not in html.split("</style>")[1]  # sin leyenda si no se configuró una

    def test_no_hay_leyenda_fija_de_no_factura_en_ningun_caso(self, base_datos_temporal):
        cookies = _cookies()
        sin_configurar = _ticket(cookies, _venta())
        servicio_configuracion.guardar_datos_comercio(DatosComercio("Mi Kiosco", "Calle 1", "11-1", "Gracias"))
        configurado = _ticket(cookies, _venta_otra())

        for html in (sin_configurar, configurado):
            assert "No válido como factura" not in html and "Comprobante interno" not in html

    def test_un_nombre_largo_sin_espacios_no_rompe_el_formato_de_80mm(self, base_datos_temporal):
        cookies = _cookies()
        servicio_configuracion.guardar_datos_comercio(DatosComercio("K" * 60, "D" * 100, "1" * 30, "P" * 120))

        html = _ticket(cookies, _venta())

        css = html.split("</style>")[0]
        ticket_css = css.split(".ticket {")[1].split("}")[0]
        assert "max-width: 80mm" in ticket_css
        assert "overflow-wrap: anywhere" in ticket_css and "word-break: break-word" in ticket_css
        for texto in ("K" * 60, "D" * 100, "P" * 120):
            assert texto in html  # se imprime completo: se parte en líneas, no se recorta

    def test_muestra_los_datos_configurados(self, base_datos_temporal):
        cookies = _cookies()
        servicio_configuracion.guardar_datos_comercio(
            DatosComercio("Kiosco Twinx", "Av. Siempreviva 742", "11-5555-0000", "¡Gracias por su compra!")
        )

        html = _ticket(cookies, _venta())

        for texto in ("Kiosco Twinx", "Av. Siempreviva 742", "Tel. 11-5555-0000", "¡Gracias por su compra!"):
            assert texto in html
        assert "<strong>Kiosco</strong>" not in html

    def test_los_campos_vacios_no_se_imprimen(self, base_datos_temporal):
        cookies = _cookies()
        servicio_configuracion.guardar_datos_comercio(DatosComercio("Solo Nombre"))

        html = _ticket(cookies, _venta())

        assert "Solo Nombre" in html and "Tel." not in html

    def test_escapa_html_en_los_datos_del_comercio(self, base_datos_temporal):
        cookies = _cookies()
        servicio_configuracion.guardar_datos_comercio(DatosComercio('<script>alert(1)</script>', "<b>x</b>"))

        html = _ticket(cookies, _venta())

        assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
        assert "<b>x</b>" not in html

    def test_el_ticket_anulado_tambien_lleva_los_datos(self, base_datos_temporal):
        cookies = _cookies()
        servicio_configuracion.guardar_datos_comercio(DatosComercio("Kiosco Twinx"))
        venta = _venta()
        with obtener_conexion() as conexion:
            owner_id = conexion.execute("SELECT id FROM usuarios").fetchone()[0]
        servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner_id)

        html = _ticket(cookies, venta)

        assert "VENTA ANULADA" in html and "Kiosco Twinx" in html


class TestRutas:
    def test_solo_el_owner_accede(self, base_datos_temporal):
        cajera = _cookies("cajera", "CASHIER")

        assert solicitud("GET", "/configuracion", cookies=cajera).status == 403
        assert solicitud("POST", "/configuracion", cookies=cajera, formulario={"nombre": "X"}).status == 403
        assert solicitud("GET", "/configuracion").status == 303
        assert servicio_configuracion.obtener_datos_comercio().nombre == "Kiosco"

    def test_formulario_muestra_los_valores_actuales_sin_campos_fiscales(self, base_datos_temporal):
        cookies = _cookies()
        servicio_configuracion.guardar_datos_comercio(DatosComercio("Kiosco Twinx", "Calle 1"))

        html = solicitud("GET", "/configuracion", cookies=cookies).texto

        assert 'value="Kiosco Twinx"' in html and 'value="Calle 1"' in html
        for fiscal in ("CUIT", "IVA", "AFIP", "Ingresos Brutos"):
            assert fiscal not in html

    def test_guardar_por_http_persiste_y_avisa(self, base_datos_temporal):
        cookies = _cookies()

        respuesta = solicitud(
            "POST", "/configuracion", cookies=cookies,
            formulario={"nombre": "Kiosco Twinx", "direccion": "Calle 1", "telefono": "11-2222", "pie": "Gracias"},
        )

        assert respuesta.status == 303 and "tipo=success" in respuesta.header("location")
        assert servicio_configuracion.obtener_datos_comercio() == DatosComercio("Kiosco Twinx", "Calle 1", "11-2222", "Gracias")

    def test_guardar_sin_cambios_lo_dice(self, base_datos_temporal):
        cookies = _cookies()

        respuesta = solicitud("POST", "/configuracion", cookies=cookies, formulario={"nombre": "", "direccion": "", "telefono": "", "pie": ""})

        assert "No+hubo+cambios" in respuesta.header("location") or "No%20hubo%20cambios" in respuesta.header("location")

    def test_un_texto_demasiado_largo_se_rechaza_con_error(self, base_datos_temporal):
        cookies = _cookies()

        respuesta = solicitud("POST", "/configuracion", cookies=cookies, formulario={"nombre": "x" * 200})

        assert "tipo=error" in respuesta.header("location")
        assert servicio_configuracion.obtener_datos_comercio().nombre == "Kiosco"

    def test_el_menu_del_owner_incluye_configuracion_y_el_del_cashier_no(self, base_datos_temporal):
        owner, cajera = _cookies("duenio", "OWNER"), _cookies("cajera", "CASHIER")

        assert 'href="/configuracion"' in solicitud("GET", "/", cookies=owner).texto
        assert 'href="/configuracion"' not in solicitud("GET", "/", cookies=cajera).texto


class TestMigracion018:
    def test_tabla_configuracion_sin_datos_sembrados(self, base_datos_temporal):
        with obtener_conexion() as conexion:
            columnas = {f["name"] for f in conexion.execute("PRAGMA table_info(configuracion)")}
            filas = conexion.execute("SELECT COUNT(*) FROM configuracion").fetchone()[0]

        assert columnas == {"clave", "valor", "fecha_actualizacion"} and filas == 0
