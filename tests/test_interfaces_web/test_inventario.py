"""Inventario físico por HTTP (V1.4): permisos OWNER/CASHIER, conteo a ciegas (formulario y API JSON),
confirmación, rechazo por stock cambiado y cancelación, contra la app real vía el cliente ASGI."""

from urllib.parse import unquote

import pytest

from domain.venta import ItemVenta
from services import servicio_inventario, servicio_stock, servicio_ventas
from tests.utilidades_clientes import consultar, contar, crear_producto, usuario_logueado

from ._asgi_cliente import solicitud

pytestmark = pytest.mark.usefixtures("caja_abierta")

STOCK_SECRETO = 7351  # número reconocible: nunca debe aparecer en una pantalla o respuesta de conteo


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies_owner = usuario_logueado("OWNER", "duenio")
    cajera, cookies_cajera = usuario_logueado("CASHIER", "cajera")
    return type(
        "Escenario",
        (),
        {
            "ruta": base_datos_temporal,
            "owner": owner,
            "cajera": cajera,
            "co": cookies_owner,
            "cc": cookies_cajera,
            "a": crear_producto("7790000000001", stock=STOCK_SECRETO),
            "b": crear_producto("7790000000002", stock=20),
        },
    )


def _location(respuesta) -> str:
    return respuesta.header("location") or ""


def _mensaje(respuesta) -> str:
    return unquote(_location(respuesta).split("msg=")[-1]) if "msg=" in _location(respuesta) else ""


def _abrir(e, *productos):
    return servicio_inventario.crear_inventario(e.owner.id, [p.id for p in productos] or None)


# --- acceso y permisos ------------------------------------------------------------------------------------


def test_sin_sesion_se_redirige_al_login_y_la_api_responde_401(e):
    for ruta in ("/inventario", "/inventario/conteo", "/inventario/nuevo", "/inventario/1"):
        respuesta = solicitud("GET", ruta)
        assert respuesta.status == 303 and _location(respuesta).startswith("/login")
    assert solicitud("POST", "/api/inventario/conteo/1", json_body={"cantidad": 1}).status == 401


@pytest.mark.parametrize("rol", ["co", "cc"])
def test_ambos_roles_ven_inventario_en_el_menu(e, rol):
    assert 'href="/inventario"' in solicitud("GET", "/", cookies=getattr(e, rol)).texto


def test_el_cashier_es_llevado_a_la_pantalla_de_conteo(e):
    respuesta = solicitud("GET", "/inventario", cookies=e.cc)

    assert respuesta.status == 303 and _location(respuesta) == "/inventario/conteo"


@pytest.mark.parametrize(
    ("metodo", "ruta"),
    [
        ("GET", "/inventario/nuevo"),
        ("POST", "/inventario/nuevo"),
        ("GET", "/inventario/1"),
        ("POST", "/inventario/1/confirmar"),
        ("POST", "/inventario/1/cancelar"),
    ],
)
def test_el_cashier_no_puede_crear_revisar_confirmar_ni_cancelar(e, metodo, ruta):
    _abrir(e, e.a)

    respuesta = solicitud(metodo, ruta, cookies=e.cc)

    assert respuesta.status == 403
    assert servicio_inventario.obtener_inventario_abierto(e.owner.id) is not None
    assert contar(e.ruta, "inventarios") == 1


# --- creación -----------------------------------------------------------------------------------------------


def test_el_owner_crea_un_inventario_de_todos_los_productos(e):
    respuesta = solicitud("POST", "/inventario/nuevo", cookies=e.co, formulario={"alcance": "todos"})

    assert respuesta.status == 303 and _location(respuesta).startswith("/inventario/1")
    assert contar(e.ruta, "inventario_lineas") == 2


def test_el_owner_crea_un_inventario_con_seleccion_manual(e):
    solicitud(
        "POST", "/inventario/nuevo", cookies=e.co, formulario={"alcance": "manual", "producto_id": [str(e.b.id)]}
    )

    assert [f[0] for f in consultar(e.ruta, "SELECT producto_id FROM inventario_lineas")] == [e.b.id]


def test_seleccion_manual_vacia_o_alcance_invalido_no_crean_nada(e):
    for formulario in ({"alcance": "manual"}, {"alcance": "categoria"}):
        respuesta = solicitud("POST", "/inventario/nuevo", cookies=e.co, formulario=formulario)
        assert respuesta.status == 303 and "tipo=error" in _location(respuesta)
    assert contar(e.ruta, "inventarios") == 0


def test_no_se_puede_abrir_un_segundo_inventario(e):
    _abrir(e)

    formulario = solicitud("GET", "/inventario/nuevo", cookies=e.co)
    envio = solicitud("POST", "/inventario/nuevo", cookies=e.co, formulario={"alcance": "todos"})

    assert formulario.status == 303 and "warning" in _location(formulario)
    assert envio.status == 303 and "tipo=error" in _location(envio)
    assert contar(e.ruta, "inventarios") == 1


def test_el_formulario_ofrece_los_productos_y_las_opciones_de_alcance(e):
    texto = solicitud("GET", "/inventario/nuevo", cookies=e.co).texto

    assert "form-nuevo-inventario" in texto and e.a.nombre in texto and 'value="manual"' in texto


# --- conteo a ciegas ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("rol", ["co", "cc"])
def test_la_pantalla_de_conteo_nunca_muestra_el_stock_esperado(e, rol):
    inventario = _abrir(e)
    servicio_inventario.registrar_conteo(inventario.id, e.b.id, 5, e.owner.id)

    respuesta = solicitud("GET", "/inventario/conteo", cookies=getattr(e, rol))

    assert respuesta.status == 200 and str(STOCK_SECRETO) not in respuesta.texto
    assert e.a.nombre in respuesta.texto and "Contado: 5" in respuesta.texto and "1 de 2 contados" in respuesta.texto
    assert "Esperado" not in respuesta.texto and "Diferencia" not in respuesta.texto


def test_sin_inventario_abierto_la_pantalla_de_conteo_lo_dice(e):
    assert "No hay ningún inventario abierto" in solicitud("GET", "/inventario/conteo", cookies=e.cc).texto


def test_contar_por_formulario_registra_y_redirige_al_conteo(e):
    inventario = _abrir(e)

    respuesta = solicitud(
        "POST", f"/inventario/conteo/{e.a.id}", cookies=e.cc, formulario={"cantidad": "6"}
    )

    assert respuesta.status == 303 and _location(respuesta).startswith("/inventario/conteo")
    fila = consultar(e.ruta, "SELECT cantidad_contada, usuario_conteo_id FROM inventario_lineas WHERE producto_id = ?", (e.a.id,))[0]
    assert (fila[0], fila[1]) == (6, e.cajera.id)
    assert inventario.id == 1


@pytest.mark.parametrize("cantidad", ["", "abc", "1.5", "-2"])
def test_contar_con_una_cantidad_invalida_muestra_error_y_no_registra(e, cantidad):
    _abrir(e)

    respuesta = solicitud("POST", f"/inventario/conteo/{e.a.id}", cookies=e.cc, formulario={"cantidad": cantidad})

    assert respuesta.status == 303 and "tipo=error" in _location(respuesta)
    assert consultar(e.ruta, "SELECT COUNT(cantidad_contada) FROM inventario_lineas")[0][0] == 0


def test_contar_sin_inventario_abierto_muestra_error(e):
    respuesta = solicitud("POST", f"/inventario/conteo/{e.a.id}", cookies=e.cc, formulario={"cantidad": "1"})

    assert respuesta.status == 303 and "tipo=error" in _location(respuesta)


def test_la_api_registra_el_conteo_y_no_devuelve_esperado_ni_diferencia(e):
    _abrir(e)

    respuesta = solicitud("POST", f"/api/inventario/conteo/{e.a.id}", cookies=e.cc, json_body={"cantidad": 3})

    assert respuesta.status == 200
    assert respuesta.json() == {"producto_id": e.a.id, "cantidad_contada": 3}
    assert str(STOCK_SECRETO) not in respuesta.texto


@pytest.mark.parametrize("cantidad", ["3", 1.5, True, None, -1])
def test_la_api_rechaza_cantidades_invalidas_con_422(e, cantidad):
    _abrir(e)

    respuesta = solicitud("POST", f"/api/inventario/conteo/{e.a.id}", cookies=e.cc, json_body={"cantidad": cantidad})

    assert respuesta.status == 422 and "error" in respuesta.json()
    assert consultar(e.ruta, "SELECT COUNT(cantidad_contada) FROM inventario_lineas")[0][0] == 0


def test_la_api_informa_si_no_hay_inventario_o_el_producto_no_pertenece(e):
    sin_inventario = solicitud("POST", f"/api/inventario/conteo/{e.a.id}", cookies=e.cc, json_body={"cantidad": 1})
    _abrir(e, e.a)
    ajeno = solicitud("POST", f"/api/inventario/conteo/{e.b.id}", cookies=e.cc, json_body={"cantidad": 1})

    assert sin_inventario.status == 422 and "No hay un inventario abierto" in sin_inventario.json()["error"]
    assert ajeno.status == 422


# --- revisión, confirmación y cancelación --------------------------------------------------------------------------


def test_el_detalle_del_owner_muestra_esperado_contado_diferencia_y_quien_conto(e):
    inventario = _abrir(e, e.a)
    servicio_inventario.registrar_conteo(inventario.id, e.a.id, STOCK_SECRETO - 3, e.cajera.id)

    texto = solicitud("GET", f"/inventario/{inventario.id}", cookies=e.co).texto

    assert str(STOCK_SECRETO) in texto and "-3" in texto and "Cajera" in texto
    assert "form-confirmar-inventario" in texto and "form-cancelar-inventario" in texto


def test_confirmar_por_http_ajusta_el_stock_y_deja_el_inventario_cerrado(e):
    inventario = _abrir(e, e.a)
    servicio_inventario.registrar_conteo(inventario.id, e.a.id, STOCK_SECRETO - 3, e.cajera.id)

    respuesta = solicitud(
        "POST", f"/inventario/{inventario.id}/confirmar", cookies=e.co, formulario={"clave_idempotencia": "k1"}
    )

    assert respuesta.status == 303 and "tipo=success" in _location(respuesta)
    assert servicio_stock.obtener_por_id(e.a.id).stock_actual == STOCK_SECRETO - 3
    assert "form-confirmar-inventario" not in solicitud("GET", f"/inventario/{inventario.id}", cookies=e.co).texto
    reenvio = solicitud(
        "POST", f"/inventario/{inventario.id}/confirmar", cookies=e.co, formulario={"clave_idempotencia": "k1"}
    )
    assert "tipo=success" in _location(reenvio) and contar(e.ruta, "ajustes_stock") == 1


def test_confirmar_con_stock_cambiado_muestra_el_error_y_no_modifica_nada(e):
    inventario = _abrir(e, e.a)
    servicio_inventario.registrar_conteo(inventario.id, e.a.id, STOCK_SECRETO - 2, e.owner.id)
    servicio_ventas.registrar_venta([ItemVenta(e.a.id, 2)], "EFECTIVO", usuario_id=e.cajera.id)

    respuesta = solicitud("POST", f"/inventario/{inventario.id}/confirmar", cookies=e.co)

    assert respuesta.status == 303 and "tipo=error" in _location(respuesta)
    assert "cambió después del conteo" in _mensaje(respuesta) and e.a.nombre in _mensaje(respuesta)
    assert servicio_stock.obtener_por_id(e.a.id).stock_actual == STOCK_SECRETO - 2
    assert contar(e.ruta, "ajustes_stock") == 0
    detalle = solicitud("GET", f"/inventario/{inventario.id}", cookies=e.co).texto
    assert "aviso-desactualizadas" in detalle and "Desactualizado" in detalle


def test_cancelar_por_http_no_toca_el_stock(e):
    inventario = _abrir(e, e.a)
    servicio_inventario.registrar_conteo(inventario.id, e.a.id, 1, e.owner.id)

    respuesta = solicitud("POST", f"/inventario/{inventario.id}/cancelar", cookies=e.co)

    assert respuesta.status == 303 and "tipo=success" in _location(respuesta)
    assert servicio_stock.obtener_por_id(e.a.id).stock_actual == STOCK_SECRETO
    assert consultar(e.ruta, "SELECT estado FROM inventarios")[0][0] == "CANCELADO"


def test_detalle_de_un_inventario_inexistente_redirige_con_error(e):
    respuesta = solicitud("GET", "/inventario/999", cookies=e.co)

    assert respuesta.status == 303 and "tipo=error" in _location(respuesta)


def test_la_lista_muestra_el_historial_y_el_estado_del_abierto(e):
    primero = _abrir(e, e.a)
    servicio_inventario.cancelar_inventario(primero.id, e.owner.id)
    segundo = _abrir(e, e.b)

    texto = solicitud("GET", "/inventario", cookies=e.co).texto

    assert "tabla-inventarios" in texto and "Cancelado" in texto and "Abierto" in texto
    assert f"Ver inventario abierto #{segundo.id}" in texto and "nuevo-inventario" not in texto
