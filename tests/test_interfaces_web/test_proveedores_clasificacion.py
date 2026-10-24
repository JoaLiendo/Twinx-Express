"""V1.13-A por HTTP: alta y edición con categorías y datos fiscales, filtros del listado y permisos."""

import pytest

from services import servicio_categorias, servicio_proveedores
from services.servicio_proveedores import DatosFiscales
from tests.utilidades_clientes import consultar, usuario_logueado

from ._asgi_cliente import solicitud


@pytest.fixture
def e(base_datos_temporal):
    _owner, cookies_owner = usuario_logueado("OWNER", "duenio")
    _cajera, cookies_cajera = usuario_logueado("CASHIER", "cajera")
    bebidas = servicio_categorias.crear_categoria("Bebidas")
    return type(
        "Escenario",
        (),
        {
            "ruta": base_datos_temporal,
            "co": cookies_owner,
            "cc": cookies_cajera,
            "bebidas": bebidas,
            "gaseosas": servicio_categorias.crear_categoria("Gaseosas", bebidas.id),
            "limpieza": servicio_categorias.crear_categoria("Limpieza"),
        },
    )


def _formulario(**extra) -> dict:
    return {"nombre": "Distribuidora X", **extra}


def test_alta_con_varias_categorias_y_datos_fiscales(e):
    respuesta = solicitud(
        "POST", "/proveedores/nuevo", cookies=e.co,
        formulario=_formulario(
            categoria_id=[str(e.gaseosas.id), str(e.limpieza.id)], cuit="30-71234567-1",
            razon_social="Distribuidora X SRL", condicion_fiscal="MONOTRIBUTO", convenio_multilateral="1",
        ),
    )

    assert respuesta.status == 303
    [proveedor] = servicio_proveedores.listar_todos()
    assert proveedor.cuit == "30712345671" and proveedor.convenio_multilateral
    texto = solicitud("GET", f"/proveedores/{proveedor.id}", cookies=e.co).texto
    assert "30-71234567-1" in texto and "Distribuidora X SRL" in texto and "Monotributo" in texto
    assert "Bebidas › Gaseosas" in texto and "Limpieza" in texto


def test_cuit_invalido_vuelve_con_error_y_no_crea_nada(e):
    respuesta = solicitud("POST", "/proveedores/nuevo", cookies=e.co, formulario=_formulario(cuit="30-71234567-2"))

    assert respuesta.status == 303
    assert servicio_proveedores.listar_todos() == []


def test_editar_muestra_marcadas_sus_categorias_y_permite_quitarlas_todas(e):
    proveedor = servicio_proveedores.crear_proveedor("A", categoria_ids=[e.bebidas.id])

    formulario_html = solicitud("GET", f"/proveedores/{proveedor.id}/editar", cookies=e.co).texto
    assert f'value="{e.bebidas.id}" class="accent-primary"\n                   checked' in formulario_html
    solicitud("POST", f"/proveedores/{proveedor.id}/editar", cookies=e.co, formulario={"nombre": "A"})

    assert servicio_proveedores.ids_categorias(proveedor.id) == set()
    assert servicio_categorias.obtener_por_id(e.bebidas.id) is not None


def test_listado_filtra_por_categoria_incluyendo_subcategorias_y_muestra_chips(e):
    servicio_proveedores.crear_proveedor("Gaseosera", categoria_ids=[e.gaseosas.id])
    servicio_proveedores.crear_proveedor("Limpito", categoria_ids=[e.limpieza.id], fiscales=DatosFiscales(cuit="20-12345678-6"))

    filtrado = solicitud("GET", f"/proveedores?categoria_id={e.bebidas.id}", cookies=e.co).texto
    por_cuit = solicitud("GET", "/proveedores?q=20123456", cookies=e.co).texto

    assert "Gaseosera" in filtrado and "Limpito" not in filtrado and "data-categorias" in filtrado
    assert "Limpito" in por_cuit and "CUIT 20-12345678-6" in por_cuit


def test_filtro_de_categoria_invalido_no_es_un_error_del_servidor(e):
    respuesta = solicitud("GET", "/proveedores?categoria_id=abc", cookies=e.co)
    assert respuesta.status == 303


@pytest.mark.parametrize("ruta", ["/proveedores/nuevo", "/proveedores/1/editar"])
def test_cashier_no_crea_ni_edita_proveedores(e, ruta):
    respuesta = solicitud("POST", ruta, cookies=e.cc, formulario=_formulario(categoria_id=[str(e.bebidas.id)]))

    assert respuesta.status == 403
    assert consultar(e.ruta, "SELECT COUNT(*) FROM proveedor_categorias")[0][0] == 0
