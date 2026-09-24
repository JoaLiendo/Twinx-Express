"""Cobro de cuenta corriente por HTTP (021): la pantalla dedicada y su POST, que pasa siempre por
`servicio_cuenta_corriente.registrar_cobro`."""

import pytest

from db.repositorios import clientes as repositorio_clientes
from domain.venta import ItemVenta
from services import servicio_caja, servicio_clientes, servicio_ventas
from tests.utilidades_clientes import (
    consultar,
    contar,
    crear_producto,
    forzar_cliente_inactivo,
    usuario_logueado,
    violaciones_de_invariantes,
)

from ._asgi_cliente import solicitud

pytestmark = pytest.mark.usefixtures("caja_abierta")

CC = "CUENTA_CORRIENTE"
PRECIO = 1000  # centavos: cada unidad vendida a cuenta suma $10,00 de deuda


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies_owner = usuario_logueado("OWNER", "duenio")
    cajera, cookies_cajera = usuario_logueado("CASHIER", "cajera")
    cliente = servicio_clientes.crear_cliente("Ana", owner.id)
    producto = crear_producto(precio_centavos=PRECIO, stock=50)
    servicio_ventas.registrar_venta([ItemVenta(producto.id, 5)], CC, usuario_id=cajera.id, cliente_id=cliente.id)
    return type(
        "Escenario",
        (),
        {"ruta": base_datos_temporal, "owner": owner, "cajera": cajera, "co": cookies_owner, "cc": cookies_cajera,
         "cliente": cliente, "producto": producto},
    )  # deuda inicial: 5000 centavos


def _cobrar(e, monto, cookies=None, clave="clave-1", descripcion="", cliente_id=None):
    return solicitud(
        "POST",
        f"/clientes/{cliente_id or e.cliente.id}/cobro",
        cookies=cookies or e.cc,
        formulario={"monto": monto, "descripcion": descripcion, "clave_idempotencia": clave},
    )


def _location(respuesta) -> str:
    return respuesta.header("location") or ""


def _saldo(e) -> int:
    return repositorio_clientes.obtener_saldo(e.cliente.id)


def _estado(e) -> dict:
    return {
        "cobros": contar(e.ruta, "movimientos_cuenta", "tipo = 'COBRO'"),
        "ingresos": contar(e.ruta, "caja_movimientos", "origen = 'COBRO_CUENTA'"),
        "saldo": _saldo(e),
        "auditoria": contar(e.ruta, "auditoria"),
    }


# --- pantalla -----------------------------------------------------------------------------------------


def test_la_pantalla_de_cobro_muestra_saldo_formulario_y_una_clave_de_idempotencia(e):
    respuesta = solicitud("GET", f"/clientes/{e.cliente.id}/cobro", cookies=e.cc)

    assert respuesta.status == 200
    texto = respuesta.texto
    assert "$50,00" in texto  # saldo actual
    assert 'name="monto"' in texto and 'name="clave_idempotencia"' in texto
    assert "Cobrar el total" in texto and "Confirmar cobro" in texto and "Saldo resultante" in texto


def test_cada_pantalla_de_cobro_lleva_una_clave_distinta(e):
    def clave(texto):
        inicio = texto.index('name="clave_idempotencia" value="') + len('name="clave_idempotencia" value="')
        return texto[inicio : texto.index('"', inicio)]

    a = solicitud("GET", f"/clientes/{e.cliente.id}/cobro", cookies=e.cc).texto
    b = solicitud("GET", f"/clientes/{e.cliente.id}/cobro", cookies=e.cc).texto

    assert clave(a) and clave(a) != clave(b)


def test_sin_deuda_no_hay_pantalla_de_cobro(e):
    sin_deuda = servicio_clientes.crear_cliente("Beto", e.owner.id)

    respuesta = solicitud("GET", f"/clientes/{sin_deuda.id}/cobro", cookies=e.cc)

    assert respuesta.status == 303 and f"/clientes/{sin_deuda.id}?tipo=warning" in _location(respuesta)


def test_la_pantalla_de_un_cliente_inexistente_redirige_a_la_lista(e):
    respuesta = solicitud("GET", "/clientes/999/cobro", cookies=e.cc)

    assert respuesta.status == 303 and "tipo=error" in _location(respuesta)


def test_con_la_caja_cerrada_la_pantalla_avisa_y_deshabilita_confirmar(e):
    servicio_caja.cerrar_caja(0)

    texto = solicitud("GET", f"/clientes/{e.cliente.id}/cobro", cookies=e.cc).texto

    assert "aviso-caja-cerrada" in texto and "disabled" in texto.split('id="cobro-confirmar"')[1].split(">")[0]


def test_sin_sesion_se_redirige_al_login(e):
    assert solicitud("GET", f"/clientes/{e.cliente.id}/cobro").status == 303
    assert solicitud("POST", f"/clientes/{e.cliente.id}/cobro", formulario={"monto": "1"}).status == 303
    assert _saldo(e) == 5000


# --- cobro ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("rol", ["co", "cc"])
def test_owner_y_cashier_pueden_cobrar(e, rol):
    respuesta = _cobrar(e, "20.00", cookies=getattr(e, rol))

    assert respuesta.status == 303
    assert f"/clientes/{e.cliente.id}?tipo=success" in _location(respuesta)
    assert _saldo(e) == 3000


def test_cobro_parcial_genera_ingreso_de_caja_cobro_y_auditoria_unica(e):
    _cobrar(e, "12.50", descripcion="entrega")

    ingreso = consultar(e.ruta, "SELECT * FROM caja_movimientos WHERE origen = 'COBRO_CUENTA'")[0]
    cobro = consultar(e.ruta, "SELECT * FROM movimientos_cuenta WHERE tipo = 'COBRO'")[0]
    assert (ingreso["tipo"], ingreso["monto_centavos"]) == ("INGRESO", 1250)
    assert (cobro["monto_centavos"], cobro["descripcion"], cobro["caja_movimiento_id"]) == (1250, "entrega", ingreso["id"])
    assert contar(e.ruta, "auditoria", "accion = 'COBRO_CUENTA'") == 1
    assert contar(e.ruta, "auditoria", "accion = 'CAJA_INGRESO'") == 0
    assert _saldo(e) == 3750
    assert violaciones_de_invariantes(e.ruta) == []


def test_cobro_total_deja_saldo_cero_y_luego_no_se_puede_cobrar_mas(e):
    respuesta = _cobrar(e, "50.00")
    antes = _estado(e)
    otra = _cobrar(e, "1.00", clave="clave-2")

    assert _saldo(e) == 0 and respuesta.status == 303
    assert "tipo=error" in _location(otra)
    assert _estado(e) == antes


@pytest.mark.parametrize("monto", ["0", "0.00", "-5", "-0.01"])
def test_monto_cero_o_negativo_se_rechaza(e, monto):
    antes = _estado(e)

    respuesta = _cobrar(e, monto)

    assert "tipo=error" in _location(respuesta)
    assert _estado(e) == antes


def test_monto_mayor_al_saldo_se_rechaza(e):
    antes = _estado(e)

    respuesta = _cobrar(e, "50.01")

    assert "tipo=error" in _location(respuesta)
    assert _estado(e) == antes


@pytest.mark.parametrize("monto", ["", "   ", "abc", "1,2,3"])
def test_monto_ilegible_se_rechaza_sin_tocar_nada(e, monto):
    antes = _estado(e)

    respuesta = _cobrar(e, monto)

    assert "tipo=error" in _location(respuesta)
    assert _estado(e) == antes


def test_con_la_caja_cerrada_el_cobro_se_rechaza_y_no_deja_nada(e):
    servicio_caja.cerrar_caja(0)
    antes = _estado(e)

    respuesta = _cobrar(e, "10.00")

    assert "tipo=error" in _location(respuesta)
    assert _estado(e) == antes


def test_cobrar_a_un_cliente_inexistente_se_rechaza(e):
    antes = _estado(e)

    respuesta = _cobrar(e, "10.00", cliente_id=999)

    assert "tipo=error" in _location(respuesta)
    assert _estado(e) == antes


def test_quien_cobra_es_el_usuario_de_la_sesion(e):
    _cobrar(e, "10.00", cookies=e.cc)

    assert consultar(e.ruta, "SELECT usuario_id FROM movimientos_cuenta WHERE tipo = 'COBRO'")[0][0] == e.cajera.id
    assert consultar(e.ruta, "SELECT usuario_id FROM auditoria WHERE accion = 'COBRO_CUENTA'")[0][0] == e.cajera.id


# --- idempotencia --------------------------------------------------------------------------------------------


def test_el_doble_envio_con_la_misma_clave_cobra_una_sola_vez(e):
    primero = _cobrar(e, "10.00", clave="unica")
    segundo = _cobrar(e, "10.00", clave="unica")

    assert primero.status == 303 and segundo.status == 303
    assert "tipo=success" in _location(segundo)  # el reintento devuelve el resultado original
    assert _saldo(e) == 4000
    assert contar(e.ruta, "caja_movimientos", "origen = 'COBRO_CUENTA'") == 1
    assert contar(e.ruta, "movimientos_cuenta", "tipo = 'COBRO'") == 1


def test_la_misma_clave_con_otro_monto_se_rechaza(e):
    _cobrar(e, "10.00", clave="unica")
    antes = _estado(e)

    respuesta = _cobrar(e, "20.00", clave="unica")

    assert "tipo=error" in _location(respuesta)
    assert _estado(e) == antes


def test_el_reenvio_tras_cerrar_la_caja_devuelve_el_cobro_original(e):
    _cobrar(e, "10.00", clave="unica")
    servicio_caja.cerrar_caja(1000)

    respuesta = _cobrar(e, "10.00", clave="unica")

    assert "tipo=success" in _location(respuesta)
    assert contar(e.ruta, "movimientos_cuenta", "tipo = 'COBRO'") == 1


# --- cliente inactivo ---------------------------------------------------------------------------------------------


def test_un_cliente_inactivo_con_deuda_puede_pagarla_desde_la_pantalla(e):
    forzar_cliente_inactivo(e.ruta, e.cliente.id)  # estado que la app no produce; solo para probar la regla

    pantalla = solicitud("GET", f"/clientes/{e.cliente.id}/cobro", cookies=e.cc)
    respuesta = _cobrar(e, "50.00")

    assert pantalla.status == 200 and "(inactivo)" in pantalla.texto
    assert "tipo=success" in _location(respuesta)
    assert _saldo(e) == 0
    assert consultar(e.ruta, "SELECT activo FROM clientes")[0]["activo"] == 0
    assert violaciones_de_invariantes(e.ruta) == []


def test_un_cliente_inactivo_sin_deuda_no_puede_cobrar(e):
    _cobrar(e, "50.00")
    servicio_clientes.desactivar_cliente(e.cliente.id, e.owner.id)  # saldo 0: la baja normal está permitida
    antes = _estado(e)

    pantalla = solicitud("GET", f"/clientes/{e.cliente.id}/cobro", cookies=e.cc)
    respuesta = _cobrar(e, "1.00", clave="otra")

    assert pantalla.status == 303 and "tipo=warning" in _location(pantalla)
    assert "tipo=error" in _location(respuesta)
    assert _estado(e) == antes
