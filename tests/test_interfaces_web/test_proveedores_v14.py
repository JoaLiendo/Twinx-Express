"""Proveedores por HTTP (V1.4): búsqueda, ficha con historial, vínculos producto-proveedor,
principal, auditoría, reposición con proveedor y permisos (todo el módulo es solo OWNER)."""

import pytest

from domain.compra import ItemCompra
from services import servicio_compras, servicio_proveedores
from tests.utilidades_clientes import consultar, crear_producto, escribir, usuario_logueado

from ._asgi_cliente import solicitud


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
            "co": cookies_owner,
            "cc": cookies_cajera,
            "prov": servicio_proveedores.crear_proveedor("Distribuidora Norte", telefono="4444-1"),
            "otro": servicio_proveedores.crear_proveedor("Mayorista Sur"),
            "producto": crear_producto("7790000000001"),
        },
    )


def _location(respuesta) -> str:
    return respuesta.header("location") or ""


def test_sin_sesion_se_redirige_al_login_y_cashier_recibe_403(e):
    for ruta in ("/proveedores", f"/proveedores/{e.prov.id}"):
        assert _location(solicitud("GET", ruta)).startswith("/login")
        assert solicitud("GET", ruta, cookies=e.cc).status == 403


@pytest.mark.parametrize("sufijo", ["/productos", "/productos/1/quitar", "/productos/1/principal"])
def test_cashier_no_puede_gestionar_vinculos(e, sufijo):
    respuesta = solicitud(
        "POST", f"/proveedores/{e.prov.id}{sufijo}", cookies=e.cc, formulario={"producto_id": str(e.producto.id)}
    )

    assert respuesta.status == 403
    assert consultar(e.ruta, "SELECT COUNT(*) FROM producto_proveedor")[0][0] == 0


def test_la_lista_busca_y_conserva_el_texto_buscado(e):
    respuesta = solicitud("GET", "/proveedores?q=norte", cookies=e.co)

    assert respuesta.status == 200
    assert "Distribuidora Norte" in respuesta.texto and "Mayorista Sur" not in respuesta.texto
    assert 'value="norte"' in respuesta.texto


def test_busqueda_sin_resultados_muestra_estado_vacio(e):
    assert "No se encontraron proveedores" in solicitud("GET", "/proveedores?q=zzz", cookies=e.co).texto


def test_la_lista_enlaza_a_la_ficha(e):
    assert f'href="/proveedores/{e.prov.id}"' in solicitud("GET", "/proveedores", cookies=e.co).texto


def test_ficha_muestra_datos_productos_vinculados_e_historial(e):
    servicio_compras.registrar_compra(e.prov.id, e.owner.id, [ItemCompra(e.producto.id, 3, 250)])

    texto = solicitud("GET", f"/proveedores/{e.prov.id}", cookies=e.co).texto

    assert "Distribuidora Norte" in texto and "4444-1" in texto
    assert e.producto.nombre in texto and "Principal" in texto
    assert 'id="tabla-compras-proveedor"' in texto and "$7,50" in texto


def test_ficha_de_un_proveedor_inexistente_redirige_con_error(e):
    respuesta = solicitud("GET", "/proveedores/9999", cookies=e.co)

    assert respuesta.status == 303 and _location(respuesta).startswith("/proveedores")


def test_ficha_de_un_proveedor_inactivo_se_ve_y_ofrece_reactivar(e):
    servicio_compras.registrar_compra(e.prov.id, e.owner.id, [ItemCompra(e.producto.id, 1, 100)])
    servicio_proveedores.eliminar_proveedor(e.prov.id)

    texto = solicitud("GET", f"/proveedores/{e.prov.id}", cookies=e.co).texto

    assert "Inactivo" in texto and f"/proveedores/{e.prov.id}/reactivar" in texto
    assert "form-vincular-producto" not in texto


def test_vincular_desde_la_ficha_crea_el_vinculo_y_audita(e):
    respuesta = solicitud(
        "POST",
        f"/proveedores/{e.prov.id}/productos",
        cookies=e.co,
        formulario={"producto_id": str(e.producto.id), "codigo_proveedor": "ABC-1"},
    )

    assert respuesta.status == 303 and _location(respuesta).startswith(f"/proveedores/{e.prov.id}")
    fila = consultar(e.ruta, "SELECT codigo_proveedor, es_principal FROM producto_proveedor")[0]
    assert (fila[0], fila[1]) == ("ABC-1", 1)
    assert "ABC-1" in solicitud("GET", f"/proveedores/{e.prov.id}", cookies=e.co).texto
    assert consultar(e.ruta, "SELECT COUNT(*) FROM auditoria WHERE accion = 'PRODUCTO_PROVEEDOR_VINCULADO'")[0][0] == 1


def test_vincular_dos_veces_muestra_error_y_no_duplica(e):
    formulario = {"producto_id": str(e.producto.id)}
    solicitud("POST", f"/proveedores/{e.prov.id}/productos", cookies=e.co, formulario=formulario)

    respuesta = solicitud("POST", f"/proveedores/{e.prov.id}/productos", cookies=e.co, formulario=formulario)

    assert respuesta.status == 303 and "error" in _location(respuesta)
    assert consultar(e.ruta, "SELECT COUNT(*) FROM producto_proveedor")[0][0] == 1


def test_cambiar_principal_y_quitar_desde_la_ficha(e):
    formulario = {"producto_id": str(e.producto.id)}
    solicitud("POST", f"/proveedores/{e.prov.id}/productos", cookies=e.co, formulario=formulario)
    solicitud("POST", f"/proveedores/{e.otro.id}/productos", cookies=e.co, formulario=formulario)

    solicitud("POST", f"/proveedores/{e.otro.id}/productos/{e.producto.id}/principal", cookies=e.co)
    principales = consultar(e.ruta, "SELECT proveedor_id FROM producto_proveedor WHERE es_principal = 1")
    assert [f[0] for f in principales] == [e.otro.id]

    solicitud("POST", f"/proveedores/{e.otro.id}/productos/{e.producto.id}/quitar", cookies=e.co)
    assert consultar(e.ruta, "SELECT COUNT(*) FROM producto_proveedor")[0][0] == 1


def test_crear_editar_y_baja_por_http_dejan_auditoria_con_el_usuario(e):
    solicitud("POST", "/proveedores/nuevo", cookies=e.co, formulario={"nombre": "Nuevo Prov"})
    solicitud("POST", f"/proveedores/{e.otro.id}/editar", cookies=e.co, formulario={"nombre": "Sur SRL"})
    solicitud("POST", f"/proveedores/{e.otro.id}/eliminar", cookies=e.co)

    filas = consultar(e.ruta, "SELECT accion, usuario_id FROM auditoria ORDER BY id")
    assert [tuple(f) for f in filas] == [
        ("PROVEEDOR_CREADO", e.owner.id),
        ("PROVEEDOR_EDITADO", e.owner.id),
        ("PROVEEDOR_BAJA", e.owner.id),
    ]


def test_reposicion_muestra_proveedor_principal_y_costo(e):
    servicio_compras.registrar_compra(e.prov.id, e.owner.id, [ItemCompra(e.producto.id, 1, 1234)])
    escribir(e.ruta, "UPDATE productos SET stock_minimo = 50, stock_actual = 1")

    texto = solicitud("GET", "/reposicion", cookies=e.co).texto

    assert "Distribuidora Norte" in texto and "$12,34" in texto and "Proveedor" in texto


def test_reposicion_sin_principal_lo_indica(e):
    escribir(e.ruta, "UPDATE productos SET stock_minimo = 50, stock_actual = 1")

    assert "Sin proveedor principal" in solicitud("GET", "/reposicion", cookies=e.co).texto
