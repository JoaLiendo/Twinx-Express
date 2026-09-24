"""Historial y detalle de ventas con cuenta corriente (021): columna Cliente, filtro CUENTA_CORRIENTE,
identificación de la venta a cuenta y, sobre todo, que una venta a cuenta nunca ofrezca anularse
(y que el servidor lo siga rechazando si alguien lo intenta a mano)."""

import pytest

from db.repositorios import clientes as repositorio_clientes
from domain.venta import ItemVenta
from services import servicio_clientes, servicio_stock, servicio_ventas
from tests.utilidades_clientes import consultar, contar, usuario_logueado, violaciones_de_invariantes

from ._asgi_cliente import solicitud

pytestmark = pytest.mark.usefixtures("caja_abierta")

CC = "CUENTA_CORRIENTE"
AMPLIO = "fecha_desde=2000-01-01&fecha_hasta=2099-12-31"


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies_owner = usuario_logueado("OWNER", "duenio")
    cajera, cookies_cajera = usuario_logueado("CASHIER", "cajera")
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=50)
    cliente = servicio_clientes.crear_cliente("Ana Gómez", owner.id)
    return type(
        "Escenario",
        (),
        {"ruta": base_datos_temporal, "owner": owner, "cajera": cajera, "co": cookies_owner, "cc": cookies_cajera,
         "producto": producto, "cliente": cliente},
    )


def _a_cuenta(e):
    return servicio_ventas.registrar_venta(
        [ItemVenta(e.producto.id, 1)], CC, usuario_id=e.cajera.id, cliente_id=e.cliente.id
    )


def _normal(e, tipo_pago="EFECTIVO"):
    return servicio_ventas.registrar_venta([ItemVenta(e.producto.id, 1)], tipo_pago, usuario_id=e.cajera.id)


def _historial(e, consulta="", cookies=None) -> str:
    respuesta = solicitud("GET", f"/ventas/historial?{AMPLIO}&{consulta}", cookies=cookies or e.co)
    assert respuesta.status == 200
    return respuesta.texto


def _href(venta_id: int) -> str:
    return f'href="/ventas/{venta_id}"'


# --- historial -----------------------------------------------------------------------------------------


def test_el_historial_muestra_la_columna_cliente_y_el_cliente_de_una_venta_a_cuenta(e):
    venta = _a_cuenta(e)

    texto = _historial(e, cookies=e.cc)

    assert ">Cliente</th>" in texto
    assert _href(venta.id) in texto
    assert f'href="/clientes/{e.cliente.id}"' in texto and "Ana Gómez" in texto
    assert CC in texto  # el badge que identifica la venta a cuenta


def test_una_venta_sin_cliente_muestra_un_guion_y_ningun_enlace_a_clientes(e):
    _normal(e)

    texto = _historial(e)

    assert "/clientes/" not in texto


def test_la_venta_a_cuenta_lleva_el_badge_de_marca_distinto_del_de_los_otros_medios(e):
    _a_cuenta(e)
    _normal(e, "TARJETA")

    texto = _historial(e)

    assert texto.count("bg-primary-subtle") >= 1  # variante 'marca' de CUENTA_CORRIENTE
    assert "bg-info/10" in texto  # TARJETA conserva el suyo


def test_el_filtro_de_medio_de_pago_ofrece_cuenta_corriente_y_filtra(e):
    a_cuenta = _a_cuenta(e)
    efectivo = _normal(e)

    sin_filtro = _historial(e)
    filtrado = _historial(e, f"tipo_pago={CC}")

    assert f'<option value="{CC}"' in sin_filtro
    assert f'<option value="{CC}" selected>' in filtrado
    assert _href(a_cuenta.id) in filtrado and _href(efectivo.id) not in filtrado


def test_los_otros_filtros_siguen_funcionando_y_no_incluyen_ventas_a_cuenta(e):
    a_cuenta = _a_cuenta(e)
    efectivo = _normal(e)

    filtrado = _historial(e, "tipo_pago=EFECTIVO")

    assert _href(efectivo.id) in filtrado and _href(a_cuenta.id) not in filtrado


def test_el_filtro_a_cuenta_se_combina_con_estado_y_fechas(e):
    a_cuenta = _a_cuenta(e)
    otra = _a_cuenta(e)

    activas = _historial(e, f"tipo_pago={CC}&estado=ACTIVA")
    anuladas = _historial(e, f"tipo_pago={CC}&estado=ANULADA")
    fuera_de_rango = solicitud(
        "GET", f"/ventas/historial?fecha_desde=2000-01-01&fecha_hasta=2000-01-02&tipo_pago={CC}", cookies=e.co
    ).texto

    assert _href(a_cuenta.id) in activas and _href(otra.id) in activas
    assert _href(a_cuenta.id) not in anuladas
    assert _href(a_cuenta.id) not in fuera_de_rango


def test_las_ventas_anuladas_normales_siguen_mostrandose_como_anuladas(e):
    venta = _normal(e)
    servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=e.owner.id)

    texto = _historial(e, "estado=ANULADA")

    assert _href(venta.id) in texto and "ANULADA" in texto


# --- detalle -----------------------------------------------------------------------------------------------


def test_el_detalle_de_una_venta_a_cuenta_muestra_el_medio_y_el_cliente(e):
    venta = _a_cuenta(e)

    texto = solicitud("GET", f"/ventas/{venta.id}", cookies=e.cc).texto

    assert CC in texto
    assert f'href="/clientes/{e.cliente.id}"' in texto and "Ana Gómez" in texto


def test_el_detalle_de_una_venta_a_cuenta_no_ofrece_anular_ni_al_owner(e):
    venta = _a_cuenta(e)

    texto = solicitud("GET", f"/ventas/{venta.id}", cookies=e.co).texto

    assert f"/ventas/{venta.id}/anular" not in texto
    assert "Anular venta" not in texto
    assert "venta-a-cuenta-no-anulable" in texto and "no se puede anular" in texto


def test_el_detalle_de_una_venta_normal_sigue_ofreciendo_anular_al_owner_y_no_al_cashier(e):
    venta = _normal(e)

    del_owner = solicitud("GET", f"/ventas/{venta.id}", cookies=e.co).texto
    del_cashier = solicitud("GET", f"/ventas/{venta.id}", cookies=e.cc).texto

    assert f"/ventas/{venta.id}/anular" in del_owner
    assert f"/ventas/{venta.id}/anular" not in del_cashier
    assert "venta-a-cuenta-no-anulable" not in del_owner


def test_el_detalle_de_una_venta_sin_cliente_no_muestra_el_bloque_de_cliente(e):
    venta = _normal(e)

    assert "/clientes/" not in solicitud("GET", f"/ventas/{venta.id}", cookies=e.co).texto


# --- guard del servidor -----------------------------------------------------------------------------------------


def test_el_formulario_de_anulacion_de_una_venta_a_cuenta_redirige_con_un_mensaje(e):
    venta = _a_cuenta(e)

    respuesta = solicitud("GET", f"/ventas/{venta.id}/anular", cookies=e.co)

    assert respuesta.status == 303
    assert f"/ventas/{venta.id}?tipo=error" in (respuesta.header("location") or "")


def test_anular_una_venta_a_cuenta_a_mano_sigue_rechazado_por_el_servidor_y_no_cambia_nada(e):
    venta = _a_cuenta(e)
    stock_antes = servicio_stock.obtener_por_id(e.producto.id).stock_actual

    respuesta = solicitud(
        "POST", f"/ventas/{venta.id}/anular", cookies=e.co, formulario={"motivo": "ERROR_CARGA", "observaciones": ""}
    )

    assert respuesta.status == 303 and "tipo=error" in (respuesta.header("location") or "")
    assert consultar(e.ruta, "SELECT estado FROM ventas WHERE id = ?", (venta.id,))[0]["estado"] == "ACTIVA"
    assert servicio_stock.obtener_por_id(e.producto.id).stock_actual == stock_antes
    assert repositorio_clientes.obtener_saldo(e.cliente.id) == 200
    assert contar(e.ruta, "movimientos_cuenta") == 1
    assert violaciones_de_invariantes(e.ruta) == []
