"""V1.14-F por HTTP: ficha del cliente por secciones, API de crédito del POS, autorización temporal y
reapertura. El CASHIER ve lo que necesita para vender y cobrar, no la configuración interna, y no puede
autorizar ni reabrir con un POST armado a mano."""

import re
from datetime import date, timedelta

import pytest

from domain.venta import CompradorVenta, ItemVenta
from services import servicio_clientes, servicio_credito, servicio_ventas
from tests.utilidades_clientes import (
    conectar,
    consultar,
    contar,
    crear_producto,
    habilitar_credito_general,
    usuario_logueado,
)

from ._asgi_cliente import solicitud

pytestmark = pytest.mark.usefixtures("caja_abierta")

HOY = date.today()


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies_owner = usuario_logueado("OWNER", "duenio")
    cajera, cookies_cajera = usuario_logueado("CASHIER", "cajera")
    habilitar_credito_general(owner.id, limite_centavos=1_000_000)
    return type("E", (), {
        "ruta": base_datos_temporal, "owner": owner, "cajera": cajera, "co": cookies_owner, "cc": cookies_cajera,
        "cliente": servicio_clientes.crear_cliente("Ana", owner.id),
        "producto": crear_producto(precio_centavos=10_000, stock=100),
    })


def _deuda_vieja(e, dias: int, monto: int = 10_000) -> None:
    sesion = consultar(e.ruta, "SELECT id FROM sesiones_caja WHERE estado = 'ABIERTA'")[0]["id"]
    fecha = f"{(HOY - timedelta(days=dias)).isoformat()} 10:00:00"
    conexion = conectar(e.ruta)
    try:
        venta_id = conexion.execute(
            "INSERT INTO ventas (fecha, total_centavos, tipo_pago, usuario_id, sesion_caja_id, cliente_id)"
            " VALUES (?, ?, 'CUENTA_CORRIENTE', ?, ?, ?) RETURNING id", (fecha, monto, e.cajera.id, sesion, e.cliente.id)
        ).fetchone()[0]
        conexion.execute("INSERT INTO movimientos_cuenta (fecha, cliente_id, tipo, monto_centavos, venta_id, usuario_id)"
                         " VALUES (?, ?, 'CARGO', ?, ?, ?)", (fecha, e.cliente.id, monto, venta_id, e.cajera.id))
        conexion.commit()
    finally:
        conexion.close()


def _ficha(e, cookies):
    respuesta = solicitud("GET", f"/clientes/{e.cliente.id}", cookies=cookies)
    assert respuesta.status == 200
    return respuesta.texto


def test_la_ficha_resume_saldo_limite_disponible_estado_y_strikes(e):
    servicio_ventas.registrar_venta([ItemVenta(e.producto.id, 3)], "CUENTA_CORRIENTE", usuario_id=e.cajera.id,
                                    cliente_id=e.cliente.id, pago_contado_centavos=10_000)
    ficha = _ficha(e, e.co)
    for seccion in ("resumen-cliente", "datos", "cuenta-corriente", "obligaciones", "imputaciones",
                    "historial-credito", "credito-cliente"):
        assert f'id="{seccion}"' in ficha, seccion
    assert re.search(r'id="cliente-saldo"[^>]*>\$200,00', ficha)
    assert re.search(r'id="credito-disponible"[^>]*>\$9\.800,00', ficha)
    assert 'data-estado="NORMAL"' in ficha and "0 de 3" in ficha


def test_el_cashier_ve_lo_necesario_y_no_la_configuracion_interna(e):
    ficha = _ficha(e, e.cc)
    assert 'id="resumen-cliente"' in ficha and 'id="obligaciones"' in ficha and 'id="credito-disponible"' in ficha
    for oculto in ('id="credito-cliente"', 'id="historial-credito"', 'id="imputaciones"', "General</span>",
                   'action="/clientes/%d/credito/politica"' % e.cliente.id):
        assert oculto not in ficha, oculto


def test_api_de_credito_para_el_pos(e):
    _deuda_vieja(e, dias=50)
    datos = solicitud("GET", f"/api/clientes/{e.cliente.id}/credito", cookies=e.cc).json()
    assert datos["estado"] == "DEMORADO" and datos["requiere_autorizacion"] is True
    assert "autorización temporal" in datos["bloqueo"]
    assert (datos["saldo_centavos"], datos["disponible_centavos"], datos["strikes"]) == (10_000, 990_000, 0)
    assert "origen" not in datos and "limite_centavos" not in datos
    assert solicitud("GET", "/api/clientes/999/credito", cookies=e.cc).status == 404


def test_api_lista_solo_autorizados_vigentes_de_una_empresa(e):
    empresa = servicio_clientes.crear_cliente(
        "Norte", e.owner.id, tipo_cliente="EMPRESA", condicion_fiscal="EXENTO", razon_social="Norte SA",
        cuit="30-71234567-1", telefono="1", direccion="d", contacto="c",
    )
    juan = servicio_clientes.agregar_comprador_autorizado(empresa.id, "Juan", e.owner.id)
    pedro = servicio_clientes.agregar_comprador_autorizado(empresa.id, "Pedro", e.owner.id)
    servicio_clientes.dar_de_baja_comprador_autorizado(empresa.id, pedro.id, e.owner.id)
    datos = solicitud("GET", f"/api/clientes/{empresa.id}/credito", cookies=e.cc).json()
    assert datos["es_empresa"] is True and datos["autorizados"] == [{"id": juan.id, "nombre": "Juan"}]


def test_owner_autoriza_un_demorado_por_http_y_el_cashier_no(e):
    _deuda_vieja(e, dias=50)
    hasta = (HOY + timedelta(days=3)).isoformat()
    cashier = solicitud("POST", f"/clientes/{e.cliente.id}/credito/autorizacion", cookies=e.cc,
                        formulario={"compromiso": "paga el lunes", "fecha_limite": hasta})
    assert cashier.status == 403 and contar(e.ruta, "autorizaciones_credito") == 0
    assert 'id="form-autorizacion-demorado"' in _ficha(e, e.co)
    owner = solicitud("POST", f"/clientes/{e.cliente.id}/credito/autorizacion", cookies=e.co,
                      formulario={"compromiso": "paga el lunes", "fecha_limite": hasta})
    assert owner.status == 303 and "tipo=success" in owner.header("location")
    assert servicio_credito.situacion_crediticia(e.cliente.id).autorizacion_vigente is not None
    venta = servicio_ventas.registrar_venta([ItemVenta(e.producto.id, 1)], "CUENTA_CORRIENTE", usuario_id=e.cajera.id,
                                            cliente_id=e.cliente.id)
    assert venta.estado == "ACTIVA"


@pytest.mark.parametrize("fecha", ["", "mañana", "2026-02-30", "9" * 5000])
def test_fecha_limite_invalida_es_error_controlado(e, fecha):
    _deuda_vieja(e, dias=50)
    respuesta = solicitud("POST", f"/clientes/{e.cliente.id}/credito/autorizacion", cookies=e.co,
                          formulario={"compromiso": "x", "fecha_limite": fecha})
    assert respuesta.status < 500 and contar(e.ruta, "autorizaciones_credito") == 0


def test_reapertura_por_http_solo_owner_y_luego_desaparece_el_formulario(e):
    _deuda_vieja(e, dias=61)
    ficha = _ficha(e, e.co)  # abrir la ficha registra el cierre
    assert 'data-estado="CRITICA"' in ficha and "Crédito cerrado" in ficha
    from services import servicio_cuenta_corriente

    servicio_cuenta_corriente.registrar_cobro(e.cliente.id, 10_000, e.cajera.id)
    assert solicitud("POST", f"/clientes/{e.cliente.id}/credito/reapertura", cookies=e.cc,
                     formulario={"observacion": "x"}).status == 403
    respuesta = solicitud("POST", f"/clientes/{e.cliente.id}/credito/reapertura", cookies=e.co,
                          formulario={"observacion": "pagó todo"})
    assert respuesta.status == 303 and "tipo=success" in respuesta.header("location")
    assert 'id="form-reapertura"' not in _ficha(e, e.co)


def test_pos_incluye_el_bloque_de_credito_y_su_script(e):
    html = solicitud("GET", "/ventas", cookies=e.cc).texto
    assert 'id="bloque-credito"' in html and 'data-credito-contado' in html and 'value="WHATSAPP"' in html
    assert html.index("/static/js/cliente_selector.js") < html.index("/static/js/credito_pos.js") < html.index("/static/js/pos.js")
    assert solicitud("GET", "/static/js/credito_pos.js").status == 200


def test_detalle_y_ticket_muestran_reparto_y_comprador(e):
    empresa = servicio_clientes.crear_cliente(
        "Norte", e.owner.id, tipo_cliente="EMPRESA", condicion_fiscal="EXENTO", razon_social="Norte SA",
        cuit="30-71234567-1", telefono="1", direccion="d", contacto="c",
    )
    venta = servicio_ventas.registrar_venta(
        [ItemVenta(e.producto.id, 5)], "CUENTA_CORRIENTE", usuario_id=e.cajera.id, cliente_id=empresa.id,
        pago_contado_centavos=20_000,
        comprador=CompradorVenta(nombre_excepcional="Pedro", metodo_autorizacion="EMAIL", detalle_autorizacion="mail 9:15"),
    )
    detalle = solicitud("GET", f"/ventas/{venta.id}", cookies=e.co).texto
    assert "Efectivo $200,00" in detalle and "A cuenta $300,00" in detalle and "Pedro" in detalle and "mail 9:15" in detalle
    ticket = solicitud("GET", f"/ventas/{venta.id}/ticket", cookies=e.co).texto
    assert "Pagó en efectivo: $200,00" in ticket and "A cuenta: $300,00" in ticket and "Compró: Pedro" in ticket


def test_el_modulo_js_de_credito_no_interpreta_html():
    from pathlib import Path

    codigo = Path("interfaces/web/static/js/credito_pos.js").read_text(encoding="utf-8")
    for api in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "DOMParser"):
        assert api not in codigo
