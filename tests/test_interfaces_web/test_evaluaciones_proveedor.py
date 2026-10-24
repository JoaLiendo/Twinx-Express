"""V1.13-B por HTTP: evaluar desde la ficha, historial visible, permisos y advertencia al comprar."""

import pytest

from services import servicio_evaluaciones_proveedor, servicio_proveedores
from tests.utilidades_clientes import consultar, crear_producto, usuario_logueado

from ._asgi_cliente import solicitud


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies_owner = usuario_logueado("OWNER", "duenio")
    _cajera, cookies_cajera = usuario_logueado("CASHIER", "cajera")
    crear_producto()
    return type(
        "E", (), {
            "ruta": base_datos_temporal, "owner": owner, "co": cookies_owner, "cc": cookies_cajera,
            "malo": servicio_proveedores.crear_proveedor("Malo"),
            "bueno": servicio_proveedores.crear_proveedor("Bueno"),
        },
    )


def _evaluar(e, proveedor_id, **campos):
    formulario = {"estrellas": "4", "comentario": "cumple", "clave_idempotencia": "", **campos}
    return solicitud("POST", f"/proveedores/{proveedor_id}/evaluaciones", cookies=e.co, formulario=formulario)


def test_evaluar_desde_la_ficha_y_verla_con_su_historial(e):
    _evaluar(e, e.malo.id, estrellas="5", comentario="primera")
    respuesta = _evaluar(e, e.malo.id, estrellas="2", comentario="se atrasó dos veces", entrega="1", precio="")

    assert respuesta.status == 303
    texto = solicitud("GET", f"/proveedores/{e.malo.id}", cookies=e.co).texto
    assert 'id="calificacion-actual"' in texto and 'aria-label="2 de 5 estrellas"' in texto
    assert "se atrasó dos veces" in texto and "Baja calificación" in texto
    assert "Historial (2 evaluaciones)" in texto and "primera" in texto
    assert servicio_evaluaciones_proveedor.historial(e.malo.id)[0].criterios == {"ENTREGA": 1}


def test_proveedor_sin_evaluar_muestra_que_no_tiene_calificacion(e):
    texto = solicitud("GET", f"/proveedores/{e.bueno.id}", cookies=e.co).texto
    assert 'id="sin-calificacion"' in texto and "Evaluar proveedor" in texto


@pytest.mark.parametrize("campos", [{"estrellas": ""}, {"estrellas": "9"}, {"estrellas": "x"}, {"comentario": " "}, {"atencion": "0"}])
def test_datos_invalidos_vuelven_con_error_sin_registrar(e, campos):
    respuesta = _evaluar(e, e.malo.id, **campos)

    assert respuesta.status == 303
    assert consultar(e.ruta, "SELECT COUNT(*) FROM evaluaciones_proveedor")[0][0] == 0


def test_doble_envio_del_mismo_formulario_registra_una_sola_evaluacion(e):
    _evaluar(e, e.malo.id, clave_idempotencia="k-1")
    _evaluar(e, e.malo.id, clave_idempotencia="k-1")
    assert consultar(e.ruta, "SELECT COUNT(*) FROM evaluaciones_proveedor")[0][0] == 1


def test_cashier_no_evalua_ni_ve_la_ficha(e):
    respuesta = solicitud(
        "POST", f"/proveedores/{e.malo.id}/evaluaciones", cookies=e.cc, formulario={"estrellas": "1", "comentario": "x"}
    )
    assert respuesta.status == 403
    assert solicitud("GET", f"/proveedores/{e.malo.id}", cookies=e.cc).status == 403
    assert consultar(e.ruta, "SELECT COUNT(*) FROM evaluaciones_proveedor")[0][0] == 0


def test_formulario_de_compra_marca_la_calificacion_y_advierte_sin_bloquear(e):
    servicio_evaluaciones_proveedor.registrar_evaluacion(e.malo.id, e.owner.id, 1, "pésimo")
    servicio_evaluaciones_proveedor.registrar_evaluacion(e.bueno.id, e.owner.id, 5, "excelente")

    texto = solicitud("GET", "/compras/nueva", cookies=e.co).texto

    assert f'value="{e.malo.id}" data-calificacion="1"' in texto
    assert f'value="{e.bueno.id}" data-calificacion="5"' in texto
    assert 'id="advertencia-calificacion"' in texto and 'data-umbral="2"' in texto
    assert "hidden" in texto.split('id="advertencia-calificacion"')[1].split(">")[0]


def test_listado_muestra_estrellas_y_baja_calificacion(e):
    servicio_evaluaciones_proveedor.registrar_evaluacion(e.malo.id, e.owner.id, 2, "x")

    texto = solicitud("GET", "/proveedores", cookies=e.co).texto

    assert 'data-calificacion="2"' in texto and "Baja calificación" in texto and "Sin calificación" in texto
