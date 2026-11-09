"""Gastos operativos por HTTP: solo OWNER, alta con egreso de caja atómico e idempotente, listado con
filtros, detalle, corrección, anulación con reintegro y CSV."""

import pytest

from db.repositorios import auditoria as repositorio_auditoria
from services import servicio_caja, servicio_gastos
from tests.utilidades_clientes import usuario_logueado

from ._asgi_cliente import solicitud

pytestmark = pytest.mark.usefixtures("caja_abierta")


@pytest.fixture
def owner(base_datos_temporal):
    usuario, cookies = usuario_logueado("OWNER", "duenio")
    servicio_caja.registrar_ingreso(1_000_000, "Fondo", usuario_id=usuario.id)
    return usuario, cookies


def _formulario(**extra):
    datos = {
        "fecha": "2026-11-05",
        "categoria": "ALQUILER",
        "descripcion": "Alquiler noviembre",
        "importe": "150000,00",
        "medio_pago": "TRANSFERENCIA",
        "referencia": "REC 44",
        "responsable": "Ana",
        "observaciones": "",
        "clave_idempotencia": "clave-web-1",
    }
    datos.update(extra)
    return datos


def _esperado() -> int:
    return servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos


class TestPermisos:
    def test_cashier_no_ve_ni_registra_gastos(self, base_datos_temporal):
        _, cookies = usuario_logueado("CASHIER", "cajera")
        for metodo, ruta in (("GET", "/gastos"), ("GET", "/gastos/nuevo"), ("GET", "/gastos/exportar"), ("GET", "/gastos/1")):
            assert solicitud(metodo, ruta, cookies=cookies).status == 403, ruta
        assert solicitud("POST", "/gastos/nuevo", cookies=cookies, formulario=_formulario()).status == 403
        assert solicitud("GET", "/gastos").status == 303

    def test_el_menu_del_owner_tiene_gastos_y_el_del_cajero_no(self, base_datos_temporal):
        _, owner = usuario_logueado("OWNER", "duenio")
        _, cajero = usuario_logueado("CASHIER", "cajera")
        assert 'href="/gastos"' in solicitud("GET", "/", cookies=owner).texto
        assert 'href="/gastos"' not in solicitud("GET", "/", cookies=cajero).texto


class TestAlta:
    def test_formulario_y_alta_fuera_de_la_caja(self, owner):
        _, cookies = owner
        formulario = solicitud("GET", "/gastos/nuevo", cookies=cookies)
        assert formulario.status == 200 and 'name="clave_idempotencia"' in formulario.texto
        assert "Impuestos y tasas" in formulario.texto

        esperado = _esperado()
        respuesta = solicitud("POST", "/gastos/nuevo", cookies=cookies, formulario=_formulario())
        assert respuesta.status == 303 and respuesta.header("location").startswith("/gastos/1?tipo=success")
        assert _esperado() == esperado
        detalle = solicitud("GET", "/gastos/1", cookies=cookies).texto
        assert "150.000,00" in detalle and "Alquiler noviembre" in detalle and "REC 44" in detalle
        assert "Transferencia" in detalle

    def test_alta_con_efectivo_de_la_caja_crea_el_egreso_y_el_reenvio_no_duplica(self, owner):
        _, cookies = owner
        esperado = _esperado()
        datos = _formulario(medio_pago="EFECTIVO_CAJA", importe="2500")
        assert solicitud("POST", "/gastos/nuevo", cookies=cookies, formulario=datos).status == 303
        assert solicitud("POST", "/gastos/nuevo", cookies=cookies, formulario=datos).status == 303
        assert _esperado() == esperado - 250_000
        listado = servicio_gastos.listar_gastos(servicio_gastos.filtros_del_listado(None, None, None, None, None, None))
        assert listado.cantidad == 1 and listado.gastos[0].caja_movimiento_id is not None
        assert "egreso de caja #" in solicitud("GET", "/gastos/1", cookies=cookies).texto
        assert "Gasto operativo: Alquiler – Alquiler noviembre" in solicitud("GET", "/caja", cookies=cookies).texto

    def test_datos_invalidos_vuelven_con_mensaje_y_no_escriben(self, owner):
        _, cookies = owner
        respuesta = solicitud("POST", "/gastos/nuevo", cookies=cookies, formulario=_formulario(importe="0"))
        assert respuesta.status in (303, 400, 422)
        assert "mayor que cero" in (respuesta.header("location") or "") + respuesta.texto or respuesta.status != 200
        otro = solicitud("POST", "/gastos/nuevo", cookies=cookies, formulario=_formulario(categoria="OTRO", descripcion="", clave_idempotencia="k2"))
        assert otro.status < 500
        assert servicio_gastos.listar_gastos(servicio_gastos.filtros_del_listado(None, None, None, None, None, None)).cantidad == 0

    @pytest.mark.sin_caja_abierta
    def test_con_la_caja_cerrada_el_medio_caja_queda_deshabilitado_y_el_servidor_lo_rechaza(self, base_datos_temporal):
        _, cookies = usuario_logueado("OWNER", "duenio")
        html = solicitud("GET", "/gastos/nuevo", cookies=cookies).texto
        assert 'value="EFECTIVO_CAJA" disabled' in html
        respuesta = solicitud("POST", "/gastos/nuevo", cookies=cookies, formulario=_formulario(medio_pago="EFECTIVO_CAJA"))
        assert respuesta.status < 500
        assert servicio_gastos.listar_gastos(servicio_gastos.filtros_del_listado(None, None, None, None, None, None)).cantidad == 0


class TestListadoYExportacion:
    def test_listado_filtra_y_muestra_el_total_vigente(self, owner):
        usuario, cookies = owner
        servicio_gastos.registrar_gasto(usuario_id=usuario.id, fecha="2026-11-01", categoria="AGUA", descripcion="Aysa", importe_centavos=20_000, medio_pago="EFECTIVO")
        anulado = servicio_gastos.registrar_gasto(usuario_id=usuario.id, fecha="2026-11-02", categoria="AGUA", descripcion="Dup", importe_centavos=99_000, medio_pago="EFECTIVO")
        servicio_gastos.anular_gasto(anulado.id, usuario_id=usuario.id, motivo="Duplicado")
        servicio_gastos.registrar_gasto(usuario_id=usuario.id, fecha="2026-10-02", categoria="SALARIOS", descripcion="Sueldo", importe_centavos=500_000, medio_pago="TRANSFERENCIA")

        html = solicitud("GET", "/gastos", cookies=cookies).texto
        assert 'id="total-gastos-vigentes">$5.200,00' in html and "ANULADO" in html
        filtrado = solicitud("GET", "/gastos?categoria=AGUA&estado=VIGENTE", cookies=cookies).texto
        assert "Aysa" in filtrado and "Sueldo" not in filtrado and "Dup" not in filtrado
        assert "Quitar filtros" in filtrado
        por_texto = solicitud("GET", "/gastos?q=sueldo", cookies=cookies).texto
        assert "Sueldo" in por_texto and "Aysa" not in por_texto
        assert solicitud("GET", "/gastos?categoria=MERCADERIA", cookies=cookies).status < 500

    def test_exportacion_csv_respeta_filtros(self, owner):
        usuario, cookies = owner
        servicio_gastos.registrar_gasto(usuario_id=usuario.id, fecha="2026-11-01", categoria="AGUA", descripcion="Aysa", importe_centavos=20_000, medio_pago="EFECTIVO")
        servicio_gastos.registrar_gasto(usuario_id=usuario.id, fecha="2026-11-01", categoria="INTERNET", descripcion="Fibra", importe_centavos=30_000, medio_pago="TARJETA")
        respuesta = solicitud("GET", "/gastos/exportar?categoria=AGUA", cookies=cookies)
        assert respuesta.status == 200 and respuesta.header("content-type").startswith("text/csv")
        assert "Aysa" in respuesta.texto and "Fibra" not in respuesta.texto and "200.00" in respuesta.texto

    def test_detalle_inexistente_redirige(self, owner):
        _, cookies = owner
        assert solicitud("GET", "/gastos/999", cookies=cookies).header("location").startswith("/gastos?tipo=error")


class TestCorreccionYAnulacion:
    def test_corregir_lo_descriptivo(self, owner):
        usuario, cookies = owner
        gasto = servicio_gastos.registrar_gasto(usuario_id=usuario.id, fecha="2026-11-01", categoria="AGUA", descripcion="Aysa", importe_centavos=20_000, medio_pago="EFECTIVO")
        assert "Aysa" in solicitud("GET", f"/gastos/{gasto.id}/editar", cookies=cookies).texto
        respuesta = solicitud(
            "POST", f"/gastos/{gasto.id}/editar", cookies=cookies,
            formulario={"categoria": "SERVICIOS", "descripcion": "Agua y cloacas", "referencia": "", "responsable": "Juan", "observaciones": ""},
        )
        assert respuesta.status == 303
        detalle = solicitud("GET", f"/gastos/{gasto.id}", cookies=cookies).texto
        assert "Agua y cloacas" in detalle and "Servicios" in detalle and "Última corrección" in detalle
        assert len(repositorio_auditoria.listar(accion="GASTO_MODIFICADO")) == 1

    def test_anular_con_reintegro_desde_el_formulario(self, owner):
        usuario, cookies = owner
        gasto = servicio_gastos.registrar_gasto(usuario_id=usuario.id, fecha="2026-11-01", categoria="TRANSPORTE", descripcion="Flete", importe_centavos=40_000, medio_pago="EFECTIVO_CAJA")
        esperado = _esperado()
        html = solicitud("GET", f"/gastos/{gasto.id}/anular", cookies=cookies).texto
        assert 'name="reintegro_a_caja"' in html
        respuesta = solicitud("POST", f"/gastos/{gasto.id}/anular", cookies=cookies, formulario={"motivo": "Error de carga", "reintegro_a_caja": "SI"})
        assert respuesta.status == 303
        assert _esperado() == esperado + 40_000
        detalle = solicitud("GET", f"/gastos/{gasto.id}", cookies=cookies).texto
        assert "ANULADO" in detalle and "volvió a la caja" in detalle and "Error de carga" in detalle
        # Un gasto anulado no se edita ni se anula de nuevo.
        assert solicitud("GET", f"/gastos/{gasto.id}/editar", cookies=cookies).header("location").startswith(f"/gastos/{gasto.id}?tipo=error")
        assert solicitud("GET", f"/gastos/{gasto.id}/anular", cookies=cookies).header("location").startswith(f"/gastos/{gasto.id}?tipo=error")
        assert solicitud("POST", f"/gastos/{gasto.id}/anular", cookies=cookies, formulario={"motivo": "otra vez"}).status < 500

    def test_anular_uno_pagado_con_la_caja_sin_decir_si_vuelve_se_rechaza(self, owner):
        usuario, cookies = owner
        gasto = servicio_gastos.registrar_gasto(usuario_id=usuario.id, fecha="2026-11-01", categoria="TRANSPORTE", descripcion="Flete", importe_centavos=40_000, medio_pago="EFECTIVO_CAJA")
        respuesta = solicitud("POST", f"/gastos/{gasto.id}/anular", cookies=cookies, formulario={"motivo": "Error"})
        assert respuesta.status < 500
        assert servicio_gastos.obtener_gasto(gasto.id).vigente


class TestDashboard:
    def test_el_dashboard_del_owner_muestra_gastos_y_resultado_y_el_del_cajero_no(self, owner):
        usuario, cookies = owner
        from datetime import date

        servicio_gastos.registrar_gasto(usuario_id=usuario.id, fecha=date.today().isoformat(), categoria="AGUA", descripcion="Aysa", importe_centavos=20_000, medio_pago="EFECTIVO")
        html = solicitud("GET", "/", cookies=cookies).texto
        assert 'data-indicador="gastos-30"' in html and "$200,00" in html
        assert 'data-indicador="resultado-30"' in html and "estimado" in html
        _, cajero = usuario_logueado("CASHIER", "cajera")
        html_cajero = solicitud("GET", "/", cookies=cajero).texto
        assert 'data-indicador="gastos-30"' not in html_cajero and "$200,00" not in html_cajero
