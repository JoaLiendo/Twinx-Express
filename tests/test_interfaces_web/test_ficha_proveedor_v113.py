"""V1.13-D por HTTP: ficha por secciones con documentación fiscal y resumen del listado."""

import pytest

from domain.compra import ItemCompra
from services import servicio_compras, servicio_evaluaciones_proveedor, servicio_proveedores, servicio_reclamos_proveedor
from tests.utilidades_clientes import consultar, crear_producto, usuario_logueado

from ._asgi_cliente import solicitud


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies_owner = usuario_logueado("OWNER", "duenio")
    _cajera, cookies_cajera = usuario_logueado("CASHIER", "cajera")
    return type("E", (), {"ruta": base_datos_temporal, "owner": owner, "co": cookies_owner, "cc": cookies_cajera,
                          "prov": servicio_proveedores.crear_proveedor("Norte")})


def test_ficha_tiene_las_secciones_y_los_indicadores(e):
    texto = solicitud("GET", f"/proveedores/{e.prov.id}", cookies=e.co).texto

    for ancla in ('id="indicadores-proveedor"', 'id="seccion-cuenta"', 'id="evaluacion"', 'id="reclamos"',
                  'id="fiscal"', 'id="documentos-proveedor"', 'id="secciones-ficha"'):
        assert ancla in texto
    assert texto.index('id="seccion-cuenta"') < texto.index('id="evaluacion"') < texto.index('id="reclamos"') < texto.index('id="fiscal"')
    assert 'id="sin-documentos"' in texto and "no consulta ARCA" in texto


def test_registrar_documento_desde_la_ficha_y_doble_envio(e):
    formulario = {"tipo": "CM05", "fecha_documento": "2026-05-31", "referencia": "2025", "observacion": "",
                  "clave_idempotencia": "doc-1"}
    for _ in range(2):
        assert solicitud("POST", f"/proveedores/{e.prov.id}/documentos", cookies=e.co, formulario=formulario).status == 303

    texto = solicitud("GET", f"/proveedores/{e.prov.id}", cookies=e.co).texto
    assert consultar(e.ruta, "SELECT COUNT(*) FROM documentos_proveedor")[0][0] == 1
    assert 'data-tipo="CM05"' in texto and "CM05 (Convenio Multilateral)" in texto and "2026-05-31" in texto


def test_documento_invalido_no_se_registra(e):
    solicitud("POST", f"/proveedores/{e.prov.id}/documentos", cookies=e.co,
              formulario={"tipo": "CM05", "fecha_documento": "2999-01-01"})
    assert consultar(e.ruta, "SELECT COUNT(*) FROM documentos_proveedor")[0][0] == 0


def test_cashier_no_registra_documentos(e):
    respuesta = solicitud("POST", f"/proveedores/{e.prov.id}/documentos", cookies=e.cc,
                          formulario={"tipo": "CM05", "fecha_documento": "2026-01-01"})
    assert respuesta.status == 403
    assert consultar(e.ruta, "SELECT COUNT(*) FROM documentos_proveedor")[0][0] == 0


def test_listado_filtra_por_dimension_y_conserva_los_filtros_en_los_enlaces(e):
    deudor = servicio_proveedores.crear_proveedor("Deudor")
    servicio_compras.registrar_compra(deudor.id, e.owner.id, [ItemCompra(crear_producto().id, 2, 150)], condicion_pago="CREDITO")
    servicio_evaluaciones_proveedor.registrar_evaluacion(e.prov.id, e.owner.id, 1, "malo")
    servicio_reclamos_proveedor.crear_reclamo(e.prov.id, e.owner.id, "OTRO", "x")

    con_deuda = solicitud("GET", "/proveedores?filtro=con_deuda", cookies=e.co).texto
    baja = solicitud("GET", "/proveedores?filtro=baja_calificacion&mostrar_inactivos=true", cookies=e.co).texto

    assert "Deudor" in con_deuda and "Deuda $3,00" in con_deuda and ">Norte<" not in con_deuda
    assert ">Norte<" in baja and "Deudor" not in baja and 'data-reclamos-abiertos="1"' in baja
    assert 'href="/proveedores?q=&amp;categoria_id=&amp;filtro=con_deuda&amp;mostrar_inactivos=true"' in baja
    sin_inactivos = solicitud("GET", "/proveedores", cookies=e.co).texto
    assert "mostrar_inactivos=&" not in sin_inactivos and "mostrar_inactivos=\"" not in sin_inactivos
    assert solicitud("GET", "/proveedores?filtro=raro", cookies=e.co).status == 303
