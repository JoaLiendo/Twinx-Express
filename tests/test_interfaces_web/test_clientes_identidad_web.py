"""V1.14-A por HTTP: alta de persona y empresa, ficha con identidad comercial y compradores autorizados,
y permisos (un CASHIER no administra autorizados ni con un POST armado a mano)."""

import pytest

from services import servicio_clientes
from tests.utilidades_clientes import contar, usuario_logueado

from ._asgi_cliente import solicitud

EMPRESA = {
    "nombre": "Norte",
    "tipo_cliente": "EMPRESA",
    "condicion_fiscal": "RESPONSABLE_INSCRIPTO",
    "razon_social": "Distribuidora Norte SRL",
    "cuit": "30-71234567-1",
    "telefono": "4444",
    "direccion": "Calle 1",
    "contacto": "Marta",
}


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies_owner = usuario_logueado("OWNER", "duenio")
    cajera, cookies_cajera = usuario_logueado("CASHIER", "cajera")
    return type("E", (), {"ruta": base_datos_temporal, "owner": owner, "cajera": cajera, "co": cookies_owner, "cc": cookies_cajera})


def test_formulario_ofrece_tipo_y_condicion_fiscal(e):
    texto = solicitud("GET", "/clientes/nuevo", cookies=e.cc).texto
    assert 'name="tipo_cliente" value="PERSONA"' in texto and 'name="tipo_cliente" value="EMPRESA"' in texto
    assert 'value="CONSUMIDOR_FINAL"' in texto and 'name="cuit"' in texto


def test_alta_de_empresa_y_ficha_con_identidad_y_autorizados(e):
    respuesta = solicitud("POST", "/clientes/nuevo", cookies=e.co, formulario=EMPRESA)
    assert respuesta.status == 303 and "tipo=success" in respuesta.header("location")
    empresa = servicio_clientes.listar_clientes()[0]
    solicitud("POST", f"/clientes/{empresa.id}/autorizados", cookies=e.co, formulario={"nombre": "Juan Pérez", "documento": "30111222"})
    ficha = solicitud("GET", f"/clientes/{empresa.id}", cookies=e.co).texto
    assert "Empresa" in ficha and "Responsable inscripto" in ficha and "30-71234567-1" in ficha
    assert 'id="autorizados"' in ficha and "Juan Pérez" in ficha and "1 de 2 activos" in ficha


def test_alta_de_empresa_incompleta_no_crea_nada(e):
    solicitud("POST", "/clientes/nuevo", cookies=e.co, formulario={**EMPRESA, "cuit": ""})
    assert contar(e.ruta, "clientes") == 0


def test_la_ficha_de_una_persona_no_muestra_autorizados(e):
    persona = servicio_clientes.crear_cliente("Ana", e.owner.id)
    assert 'id="autorizados"' not in solicitud("GET", f"/clientes/{persona.id}", cookies=e.co).texto


def test_cashier_no_puede_administrar_autorizados_ni_con_post_manual(e):
    solicitud("POST", "/clientes/nuevo", cookies=e.co, formulario=EMPRESA)
    empresa = servicio_clientes.listar_clientes()[0]
    juan = servicio_clientes.agregar_comprador_autorizado(empresa.id, "Juan", e.owner.id)
    alta = solicitud("POST", f"/clientes/{empresa.id}/autorizados", cookies=e.cc, formulario={"nombre": "Intruso"})
    baja = solicitud("POST", f"/clientes/{empresa.id}/autorizados/{juan.id}/baja", cookies=e.cc)
    assert alta.status == 403 and baja.status == 403
    assert contar(e.ruta, "compradores_autorizados", "activo = 1") == 1
    ficha = solicitud("GET", f"/clientes/{empresa.id}", cookies=e.cc).texto
    assert "Juan" in ficha and 'id="form-agregar-autorizado"' not in ficha and "Dar de baja" not in ficha
