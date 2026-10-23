"""V1.11-E: listado de productos en tabla con filtros, subcategorías y etiquetas (interfaz web)."""

import pytest

from services import servicio_categorias, servicio_etiquetas, servicio_stock
from tests.utilidades_clientes import consultar, usuario_logueado

from ._asgi_cliente import solicitud


def _location(respuesta) -> str:
    return respuesta.header("location") or ""


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies = usuario_logueado("OWNER", "duenio")
    _, cajera = usuario_logueado("CASHIER", "cajera")
    alimentos = servicio_categorias.crear_categoria("Alimentos")
    panificados = servicio_categorias.crear_categoria("Panificados", alimentos.id)
    frio = servicio_etiquetas.crear_etiqueta("Frío")
    pan = servicio_stock.registrar_producto(
        "7790000000001", "Pan de campo", 1234, 2500, stock_actual=3, stock_minimo=5, categoria_id=panificados.id
    )
    yogur = servicio_stock.registrar_producto(
        "7790000000002", "Yogur", 400, 900, stock_actual=0, etiqueta_ids=[frio.id]
    )
    return type("E", (), {"ruta": base_datos_temporal, "owner": owner, "co": cookies, "cajera": cajera,
                          "alimentos": alimentos, "panificados": panificados, "frio": frio, "pan": pan, "yogur": yogur})


def test_tabla_con_columnas_ruta_de_categoria_y_etiquetas(e):
    html = solicitud("GET", "/productos", cookies=e.co).texto
    assert 'id="tabla-productos"' in html
    for columna in ("Código", "Producto", "Categoría", "Stock", "Precio", "Estado"):
        assert f">{columna}</th>" in html
    assert "Alimentos › Panificados" in html
    assert "Frío</span>" in html
    assert "costo $12,34" in html
    assert "Sin stock" in html  # el yogur, sin confundirlo con "Crítico"


def test_cajero_no_ve_costos_ni_acciones_de_administracion(e):
    html = solicitud("GET", "/productos", cookies=e.cajera).texto
    assert "Pan de campo" in html
    assert "costo $" not in html
    assert "/editar" not in html and "/eliminar" not in html and "/productos/nuevo" not in html
    assert "Dados de baja" not in html


def test_filtro_por_categoria_incluye_subcategorias(e):
    html = solicitud("GET", f"/productos?categoria_id={e.alimentos.id}", cookies=e.co).texto
    assert "Pan de campo" in html and "Yogur" not in html


@pytest.mark.parametrize(
    "consulta, esperados, ausentes",
    [
        ("estado_stock=critico", ["Pan de campo"], ["Yogur"]),
        ("solo_criticos=true", ["Pan de campo"], ["Yogur"]),
        ("estado_stock=sin_stock", ["Yogur"], ["Pan de campo"]),
        ("q=7790000000002", ["Yogur"], ["Pan de campo"]),
        ("q=PAN", ["Pan de campo"], ["Yogur"]),
    ],
)
def test_filtros_rapidos(e, consulta, esperados, ausentes):
    html = solicitud("GET", f"/productos?{consulta}", cookies=e.co).texto
    for nombre in esperados:
        assert nombre in html
    for nombre in ausentes:
        assert f">{nombre}</p>" not in html


def test_filtro_por_etiqueta(e):
    html = solicitud("GET", f"/productos?etiqueta_id={e.frio.id}", cookies=e.co).texto
    assert ">Yogur</p>" in html and ">Pan de campo</p>" not in html


def test_un_filtro_con_id_no_numerico_es_un_dato_invalido_no_un_error_del_servidor(e):
    respuesta = solicitud("GET", "/productos?categoria_id=abc", cookies=e.co)
    assert respuesta.status == 303 and "tipo=error" in _location(respuesta)


def test_alta_de_subcategoria_y_etiqueta_desde_la_web(e):
    solicitud("POST", "/productos/categorias", cookies=e.co,
              formulario={"nombre": "Criollos", "categoria_padre_id": str(e.panificados.id)})
    solicitud("POST", "/productos/etiquetas", cookies=e.co, formulario={"nombre": "Sin TACC"})
    html = solicitud("GET", "/productos/categorias", cookies=e.co).texto
    assert 'data-nivel="3"' in html and "Criollos" in html and "Sin TACC" in html
    criollos = servicio_categorias.obtener_por_nombre("Criollos")
    assert criollos.categoria_padre_id == e.panificados.id
    # el tercer nivel no se ofrece como ubicación de una nueva categoría
    assert f'<option value="{criollos.id}">Dentro de:' not in html


def test_edicion_web_mueve_categoria_y_rechaza_ciclos(e):
    respuesta = solicitud("POST", f"/productos/categorias/{e.alimentos.id}/editar", cookies=e.co,
                          formulario={"nombre": "Alimentos", "categoria_padre_id": str(e.panificados.id)})
    assert "tipo=error" in _location(respuesta)
    assert servicio_categorias.obtener_por_id(e.alimentos.id).categoria_padre_id is None
    solicitud("POST", f"/productos/categorias/{e.panificados.id}/editar", cookies=e.co,
              formulario={"nombre": "Panificados", "categoria_padre_id": ""})
    assert servicio_categorias.obtener_por_id(e.panificados.id).categoria_padre_id is None


def test_editar_producto_desde_la_web_reemplaza_etiquetas(e):
    oferta = servicio_etiquetas.crear_etiqueta("Oferta")
    formulario = solicitud("GET", f"/productos/{e.yogur.id}/editar", cookies=e.co).texto
    assert f'name="etiqueta_id" value="{e.frio.id}"' in formulario
    solicitud("POST", f"/productos/{e.yogur.id}/editar", cookies=e.co, formulario={
        "codigo_barras": "7790000000002", "nombre": "Yogur", "precio_costo": "4", "precio_venta": "9",
        "stock_minimo": "0", "categoria_id": str(e.panificados.id), "unidad_medida": "UNIDAD",
        "etiqueta_id": [str(oferta.id)],
    })
    assert servicio_etiquetas.ids_de_producto(e.yogur.id) == [oferta.id]
    assert servicio_stock.obtener_por_id(e.yogur.id).categoria_id == e.panificados.id


def test_cajero_no_gestiona_etiquetas(e):
    respuesta = solicitud("POST", "/productos/etiquetas", cookies=e.cajera, formulario={"nombre": "Hack"})
    assert respuesta.status == 403
    assert consultar(e.ruta, "SELECT COUNT(*) FROM etiquetas")[0][0] == 1
