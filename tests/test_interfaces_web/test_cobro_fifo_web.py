"""V1.14-D por HTTP: la pantalla de cobro muestra las obligaciones en orden FIFO; el reparto manual lo ve y
lo usa solo el OWNER, y un CASHIER que lo arma a mano en el POST no registra nada."""

import pytest

from domain.venta import ItemVenta
from services import servicio_clientes, servicio_cuenta_corriente, servicio_ventas
from tests.utilidades_clientes import consultar, contar, crear_producto, habilitar_credito_general, usuario_logueado

from ._asgi_cliente import solicitud

pytestmark = pytest.mark.usefixtures("caja_abierta")


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies_owner = usuario_logueado("OWNER", "duenio")
    cajera, cookies_cajera = usuario_logueado("CASHIER", "cajera")
    habilitar_credito_general(owner.id)
    cliente = servicio_clientes.crear_cliente("Ana", owner.id)
    producto = crear_producto(precio_centavos=1_000, stock=50)
    ventas = [servicio_ventas.registrar_venta([ItemVenta(producto.id, n)], "CUENTA_CORRIENTE", usuario_id=cajera.id,
                                              cliente_id=cliente.id) for n in (1, 2)]
    obligaciones = servicio_cuenta_corriente.listar_obligaciones_pendientes(cliente.id)
    return type("E", (), {"ruta": base_datos_temporal, "co": cookies_owner, "cc": cookies_cajera, "cliente": cliente,
                          "ventas": ventas, "obligaciones": obligaciones})


def _cobro(e, cookies, **extra):
    formulario = {"monto": "10.00", "clave_idempotencia": "k1", **extra}
    return solicitud("POST", f"/clientes/{e.cliente.id}/cobro", cookies=cookies, formulario=formulario)


def test_la_pantalla_muestra_las_obligaciones_y_solo_el_owner_ve_el_reparto_manual(e):
    owner = solicitud("GET", f"/clientes/{e.cliente.id}/cobro", cookies=e.co).texto
    cajera = solicitud("GET", f"/clientes/{e.cliente.id}/cobro", cookies=e.cc).texto
    assert 'id="cobro-obligaciones"' in owner and 'id="cobro-obligaciones"' in cajera
    assert owner.index(f"Venta #{e.ventas[0].id}") < owner.index(f"Venta #{e.ventas[1].id}")
    assert 'id="cobro-imputacion-manual"' in owner and 'id="cobro-imputacion-manual"' not in cajera


def test_owner_imputa_a_mano_por_http(e):
    nueva = e.obligaciones[1]
    respuesta = _cobro(e, e.co, imputacion_obligacion_id=[str(o.id) for o in e.obligaciones],
                       imputacion_monto=["", "10.00"], observacion_imputacion="Paga la segunda")
    assert respuesta.status == 303 and "tipo=success" in respuesta.header("location")
    fila = consultar(e.ruta, "SELECT obligacion_id, tipo FROM imputaciones_cobro")[0]
    assert (fila["obligacion_id"], fila["tipo"]) == (nueva.id, "MANUAL")


def test_cashier_no_impone_reparto_manual_con_post_armado(e):
    respuesta = _cobro(e, e.cc, imputacion_obligacion_id=[str(e.obligaciones[1].id)], imputacion_monto=["10.00"],
                       observacion_imputacion="me conviene")
    assert "tipo=error" in (respuesta.header("location") or "") or respuesta.status == 403
    assert contar(e.ruta, "movimientos_cuenta", "tipo = 'COBRO'") == 0


def test_cobro_comun_de_cashier_sigue_siendo_fifo(e):
    assert _cobro(e, e.cc).status == 303
    fila = consultar(e.ruta, "SELECT obligacion_id, tipo FROM imputaciones_cobro")[0]
    assert (fila["obligacion_id"], fila["tipo"]) == (e.obligaciones[0].id, "FIFO")


@pytest.mark.parametrize("obligacion", ["abc", "9" * 5000])
def test_id_de_obligacion_adulterado_es_un_error_controlado(e, obligacion):
    respuesta = _cobro(e, e.co, imputacion_obligacion_id=[obligacion], imputacion_monto=["10.00"],
                       observacion_imputacion="x")
    assert respuesta.status < 500
    assert contar(e.ruta, "movimientos_cuenta", "tipo = 'COBRO'") == 0
