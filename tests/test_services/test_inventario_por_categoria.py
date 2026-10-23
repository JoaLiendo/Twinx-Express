"""V1.11-E: inventario físico por categoría (con o sin subcategorías). El resto del flujo (conteo a
ciegas, version_stock, confirmación atómica, RECUENTO) es el mismo de siempre y se prueba acá de punta
a punta solo para el alcance nuevo."""

import pytest

from excepciones import CategoriaNoEncontradaError, DatosInvalidosError
from services import servicio_categorias, servicio_inventario, servicio_stock
from tests.utilidades_clientes import consultar, contar, crear_owner, usuario_logueado

from tests.test_interfaces_web._asgi_cliente import solicitud


@pytest.fixture
def e(base_datos_temporal):
    owner = crear_owner()
    alimentos = servicio_categorias.crear_categoria("Alimentos")
    panificados = servicio_categorias.crear_categoria("Panificados", alimentos.id)
    bebidas = servicio_categorias.crear_categoria("Bebidas")
    r = servicio_stock.registrar_producto
    return type("E", (), {
        "ruta": base_datos_temporal, "owner": owner,
        "alimentos": alimentos, "panificados": panificados, "bebidas": bebidas,
        "fideos": r("1", "Fideos", 10, 20, stock_actual=4, categoria_id=alimentos.id),
        "pan": r("2", "Pan", 10, 20, stock_actual=6, categoria_id=panificados.id),
        "agua": r("3", "Agua", 10, 20, stock_actual=8, categoria_id=bebidas.id),
        "suelto": r("4", "Suelto", 10, 20, stock_actual=1),
    })


def _lineas(e, inventario):
    return sorted(f[0] for f in consultar(e.ruta, "SELECT producto_id FROM inventario_lineas WHERE inventario_id = ?",
                                         (inventario.id,)))


def test_categoria_con_subcategorias(e):
    inventario = servicio_inventario.crear_inventario(e.owner.id, categoria_id=e.alimentos.id)
    assert _lineas(e, inventario) == sorted([e.fideos.id, e.pan.id])
    resumen = consultar(e.ruta, "SELECT resumen FROM auditoria WHERE accion = 'INVENTARIO_CREADO'")[0][0]
    assert resumen.endswith("2 producto(s) de la categoría Alimentos (con subcategorías)")


def test_solo_la_categoria_elegida(e):
    inventario = servicio_inventario.crear_inventario(e.owner.id, categoria_id=e.alimentos.id, incluir_subcategorias=False)
    assert _lineas(e, inventario) == [e.fideos.id]


def test_subcategoria_con_su_ruta_en_la_auditoria(e):
    servicio_inventario.crear_inventario(e.owner.id, categoria_id=e.panificados.id)
    resumen = consultar(e.ruta, "SELECT resumen FROM auditoria WHERE accion = 'INVENTARIO_CREADO'")[0][0]
    assert resumen.endswith("1 producto(s) de la categoría Alimentos › Panificados")


def test_excluye_inactivos(e):
    servicio_stock.eliminar_producto(e.agua.id)
    with pytest.raises(DatosInvalidosError, match="ningún producto"):
        servicio_inventario.crear_inventario(e.owner.id, categoria_id=e.bebidas.id)
    assert contar(e.ruta, "inventarios") == 0


def test_errores_de_alcance(e):
    with pytest.raises(CategoriaNoEncontradaError):
        servicio_inventario.crear_inventario(e.owner.id, categoria_id=999)
    with pytest.raises(DatosInvalidosError, match="no ambas"):
        servicio_inventario.crear_inventario(e.owner.id, producto_ids=[e.agua.id], categoria_id=e.bebidas.id)
    assert contar(e.ruta, "inventarios") == 0


def test_flujo_completo_por_categoria_genera_recuento(e):
    inventario = servicio_inventario.crear_inventario(e.owner.id, categoria_id=e.alimentos.id)
    servicio_inventario.registrar_conteo(inventario.id, e.fideos.id, 3, e.owner.id)
    servicio_inventario.registrar_conteo(inventario.id, e.pan.id, 6, e.owner.id)
    servicio_inventario.confirmar_inventario(inventario.id, e.owner.id, "k-inv-cat")
    assert servicio_stock.obtener_por_id(e.fideos.id).stock_actual == 3
    assert servicio_stock.obtener_por_id(e.agua.id).stock_actual == 8  # fuera del alcance: intacto
    assert [tuple(f) for f in consultar(e.ruta, "SELECT motivo, delta FROM ajustes_stock WHERE inventario_id = ?", (inventario.id,))] == [
        ("RECUENTO", -1)
    ]


def test_web_por_categoria(e):
    _, cookies = usuario_logueado("OWNER", "duenio2")
    formulario = solicitud("GET", "/inventario/nuevo", cookies=cookies).texto
    assert 'value="categoria"' in formulario and "Alimentos › Panificados" in formulario
    sin_categoria = solicitud("POST", "/inventario/nuevo", cookies=cookies, formulario={"alcance": "categoria"})
    assert "tipo=error" in (sin_categoria.header("location") or "")
    solicitud("POST", "/inventario/nuevo", cookies=cookies, formulario={
        "alcance": "categoria", "categoria_id": str(e.alimentos.id), "incluir_subcategorias": "true",
    })
    assert sorted(f[0] for f in consultar(e.ruta, "SELECT producto_id FROM inventario_lineas")) == sorted(
        [e.fideos.id, e.pan.id]
    )
