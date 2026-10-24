"""V1.13-C por HTTP: alta de reclamos desde la ficha y desde un ajuste FALLA_FABRICA, cambio de estado,
doble envío y permisos."""

import pytest

from domain.compra import ItemCompra
from services import servicio_compras, servicio_proveedores, servicio_reclamos_proveedor, servicio_stock
from tests.utilidades_clientes import consultar, crear_producto, usuario_logueado

from ._asgi_cliente import solicitud


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies_owner = usuario_logueado("OWNER", "duenio")
    _cajera, cookies_cajera = usuario_logueado("CASHIER", "cajera")
    a = servicio_proveedores.crear_proveedor("Fábrica A")
    b = servicio_proveedores.crear_proveedor("Fábrica B")
    producto = crear_producto(stock=0)
    return type("E", (), {"ruta": base_datos_temporal, "owner": owner, "co": cookies_owner, "cc": cookies_cajera,
                          "a": a, "b": b, "producto": producto})


def _comprar(e, proveedor):
    return servicio_compras.registrar_compra(proveedor.id, e.owner.id, [ItemCompra(e.producto.id, 5, 100)])


def _location(respuesta) -> str:
    return respuesta.header("location") or ""


def test_desde_la_ficha_se_crea_y_se_ve_el_reclamo(e):
    compra = _comprar(e, e.a)
    formulario_html = solicitud("GET", f"/proveedores/reclamos/nuevo?proveedor_id={e.a.id}", cookies=e.co).texto
    assert 'id="proveedor-fijo"' in formulario_html and f'value="{compra.id}"' in formulario_html

    respuesta = solicitud("POST", "/proveedores/reclamos/nuevo", cookies=e.co, formulario={
        "proveedor_id": str(e.a.id), "tipo": "CALIDAD", "descripcion": "vino húmedo", "producto_id": str(e.producto.id),
        "compra_id": str(compra.id), "cantidad": "2", "clave_idempotencia": "k1",
    })

    [reclamo] = servicio_reclamos_proveedor.listar_de_proveedor(e.a.id)
    assert _location(respuesta).startswith(f"/proveedores/{e.a.id}/reclamos/{reclamo.id}")
    detalle = solicitud("GET", f"/proveedores/{e.a.id}/reclamos/{reclamo.id}", cookies=e.co).texto
    assert "vino húmedo" in detalle and 'data-estado="PENDIENTE"' in detalle and f"Compra #{compra.id}" in detalle
    ficha = solicitud("GET", f"/proveedores/{e.a.id}", cookies=e.co).texto
    assert "1 abierto(s) · 1 en total" in ficha and f'data-reclamo="{reclamo.id}"' in ficha


def test_desde_un_ajuste_identificado_el_proveedor_queda_fijo(e):
    _comprar(e, e.a)
    ajuste = servicio_stock.ajustar_stock(e.producto.id, -1, "FALLA_FABRICA", e.owner.id)

    ajustes_html = solicitud("GET", f"/productos/{e.producto.id}/ajustes", cookies=e.co).texto
    formulario_html = solicitud("GET", f"/proveedores/reclamos/nuevo?ajuste_id={ajuste.id}", cookies=e.co).texto

    assert f'data-reclamar-ajuste="{ajuste.id}"' in ajustes_html
    assert 'id="proveedor-fijo"' in formulario_html and "Fábrica A" in formulario_html
    assert 'name="ajuste_id"' in formulario_html and 'id="proveedor-no-identificado"' not in formulario_html


def test_desde_un_ajuste_no_identificado_el_owner_elige_y_luego_el_ajuste_enlaza_al_reclamo(e):
    _comprar(e, e.a)
    _comprar(e, e.b)
    ajuste = servicio_stock.ajustar_stock(e.producto.id, -1, "FALLA_FABRICA", e.owner.id)  # FEFO entre dos partidas

    formulario_html = solicitud("GET", f"/proveedores/reclamos/nuevo?ajuste_id={ajuste.id}", cookies=e.co).texto
    assert 'id="proveedor-no-identificado"' in formulario_html and 'name="proveedor_id" id="proveedor_id"' in formulario_html

    solicitud("POST", "/proveedores/reclamos/nuevo", cookies=e.co, formulario={
        "proveedor_id": str(e.b.id), "tipo": "FALLA_FABRICA", "descripcion": "rotos", "ajuste_id": str(ajuste.id),
        "cantidad": "1",
    })
    [reclamo] = servicio_reclamos_proveedor.listar_de_proveedor(e.b.id)
    ajustes_html = solicitud("GET", f"/productos/{e.producto.id}/ajustes", cookies=e.co).texto
    assert f'data-reclamo-de-ajuste="{reclamo.id}"' in ajustes_html
    reintento = solicitud("GET", f"/proveedores/reclamos/nuevo?ajuste_id={ajuste.id}", cookies=e.co)
    assert _location(reintento).startswith(f"/proveedores/{e.b.id}/reclamos/{reclamo.id}")


def test_alta_sin_proveedor_o_invalida_vuelve_con_error(e):
    for formulario in ({"tipo": "OTRO", "descripcion": "x"}, {"proveedor_id": str(e.a.id), "tipo": "OTRO", "descripcion": ""},
                       {"proveedor_id": str(e.a.id), "tipo": "OTRO", "descripcion": "x", "cantidad": "dos"}):
        assert solicitud("POST", "/proveedores/reclamos/nuevo", cookies=e.co, formulario=formulario).status == 303
    assert consultar(e.ruta, "SELECT COUNT(*) FROM reclamos_proveedor")[0][0] == 0


def test_cambio_de_estado_por_formulario_y_doble_envio(e):
    reclamo = servicio_reclamos_proveedor.crear_reclamo(e.a.id, e.owner.id, "ENTREGA", "tarde")
    ruta = f"/proveedores/{e.a.id}/reclamos/{reclamo.id}/estado"
    formulario = {"estado_nuevo": "RECONOCIDO", "estado_actual": "PENDIENTE", "observacion": "lo reconoce",
                  "clave_idempotencia": "c1"}

    solicitud("POST", ruta, cookies=e.co, formulario=formulario)
    solicitud("POST", ruta, cookies=e.co, formulario=formulario)

    actualizado = servicio_reclamos_proveedor.obtener_por_id(reclamo.id)
    assert actualizado.estado == "RECONOCIDO" and len(actualizado.cambios) == 1
    detalle = solicitud("GET", f"/proveedores/{e.a.id}/reclamos/{reclamo.id}", cookies=e.co).texto
    assert "Pendiente → Reconocido" in detalle and "lo reconoce" in detalle
    assert 'value="RESUELTO"' in detalle and 'value="PENDIENTE"' not in detalle.split('id="form-estado-reclamo"')[1].split("estado_actual")[1]


def test_reclamo_de_otro_proveedor_por_url_no_se_muestra_ni_se_cambia(e):
    reclamo = servicio_reclamos_proveedor.crear_reclamo(e.a.id, e.owner.id, "OTRO", "x")

    assert _location(solicitud("GET", f"/proveedores/{e.b.id}/reclamos/{reclamo.id}", cookies=e.co)).startswith(f"/proveedores/{e.b.id}")
    solicitud("POST", f"/proveedores/{e.b.id}/reclamos/{reclamo.id}/estado", cookies=e.co,
              formulario={"estado_nuevo": "RESUELTO", "estado_actual": "PENDIENTE", "observacion": "x"})
    assert servicio_reclamos_proveedor.obtener_por_id(reclamo.id).estado == "PENDIENTE"


def test_cashier_no_accede_a_reclamos(e):
    reclamo = servicio_reclamos_proveedor.crear_reclamo(e.a.id, e.owner.id, "OTRO", "x")
    assert solicitud("GET", f"/proveedores/reclamos/nuevo?proveedor_id={e.a.id}", cookies=e.cc).status == 403
    assert solicitud("GET", f"/proveedores/{e.a.id}/reclamos/{reclamo.id}", cookies=e.cc).status == 403
    respuesta = solicitud("POST", "/proveedores/reclamos/nuevo", cookies=e.cc,
                          formulario={"proveedor_id": str(e.a.id), "tipo": "OTRO", "descripcion": "x"})
    assert respuesta.status == 403
    respuesta = solicitud("POST", f"/proveedores/{e.a.id}/reclamos/{reclamo.id}/estado", cookies=e.cc,
                          formulario={"estado_nuevo": "RESUELTO", "estado_actual": "PENDIENTE", "observacion": "x"})
    assert respuesta.status == 403
    assert consultar(e.ruta, "SELECT COUNT(*) FROM reclamos_proveedor")[0][0] == 1
    assert consultar(e.ruta, "SELECT COUNT(*) FROM reclamos_proveedor_cambios")[0][0] == 0
