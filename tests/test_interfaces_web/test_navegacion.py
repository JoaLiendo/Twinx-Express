"""Pruebas de interfaces.web.navegacion (Fase 2D): filtrado de NAV_ITEMS
por rol, centralizado en un único lugar (no se duplica esta regla en
ningún template).

No dependen de base de datos: `navegacion_visible_para` es una función
pura sobre la lista `NAV_ITEMS`.
"""

from interfaces.web.navegacion import NAV_ITEMS, navegacion_visible_para

_SECCIONES_OWNER_EXCLUSIVAS = {"/pedidos", "/precios", "/proveedores", "/compras", "/reportes", "/empleados"}
# 021: Clientes es visible para ambos roles (los dos pueden verlos, crearlos y cobrar; editar/desactivar
# lo restringe cada ruta, no el menú).
_SECCIONES_CASHIER = {"/", "/ventas", "/caja", "/productos", "/clientes"}


def _hrefs(items):
    return {item["href"] for item in items}


_PROXIMAMENTE = {"/pedidos"}


def test_owner_ve_toda_la_navegacion_con_funcionalidad_real():
    visibles = navegacion_visible_para("OWNER")

    assert _hrefs(visibles) == _hrefs(NAV_ITEMS) - _PROXIMAMENTE


def test_las_secciones_proximamente_no_aparecen_en_ningun_menu():
    """V1.1: Pedidos sigue siendo un cascarón sin funcionalidad (Precios es real desde V1.2)."""
    for rol in ("OWNER", "CASHIER", None):
        assert _hrefs(navegacion_visible_para(rol)).isdisjoint(_PROXIMAMENTE)
    assert _PROXIMAMENTE <= _hrefs(NAV_ITEMS)  # siguen definidas, solo ocultas


def test_cashier_ve_las_secciones_definidas_para_ese_rol():
    visibles = navegacion_visible_para("CASHIER")

    assert _hrefs(visibles) == _SECCIONES_CASHIER


def test_cashier_no_ve_las_secciones_restringidas_a_owner():
    visibles = navegacion_visible_para("CASHIER")

    assert _hrefs(visibles).isdisjoint(_SECCIONES_OWNER_EXCLUSIVAS)


def test_none_es_una_decision_temporal_de_2d_no_una_regla_de_autorizacion():
    """`navegacion_visible_para` filtra el MENÚ, no autoriza nada.

    Que `rol=None` (nadie autenticado) devuelva hoy la navegación
    completa es una decisión temporal de compatibilidad visual de la
    Fase 2D (todavía no existe login real, ver docstring del módulo)
    — NO es una regla de "sin autenticar se puede ver todo". Quién
    puede hacer qué de verdad lo decide `interfaces.web.auth.requiere_rol`
    (autorización de backend, separada de esta función a propósito);
    qué responde el servidor cuando no hay sesión es una decisión de
    la Fase 2E que todavía no existe. Este test deja registrada esa
    frontera para que el comportamiento actual no se vuelva una regla
    de autorización permanente por inercia, sin que nadie lo haya
    decidido explícitamente.
    """
    visibles = navegacion_visible_para(None)

    assert _hrefs(visibles) == _hrefs(NAV_ITEMS) - _PROXIMAMENTE


def test_rol_desconocido_no_ve_secciones_restringidas_a_owner():
    """Deny-by-default: un rol que no sea exactamente 'OWNER' nunca debe
    heredar accidentalmente las secciones exclusivas de ese rol."""
    visibles = navegacion_visible_para("ALGO_INVENTADO")

    assert _hrefs(visibles).isdisjoint(_SECCIONES_OWNER_EXCLUSIVAS)


def test_navegacion_visible_para_no_muta_nav_items():
    hrefs_originales = _hrefs(NAV_ITEMS)

    navegacion_visible_para("CASHIER")

    assert _hrefs(NAV_ITEMS) == hrefs_originales
