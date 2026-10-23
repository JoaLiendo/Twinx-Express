"""V1.11-E: árbol de categorías en memoria (rutas, niveles, descendientes y validación de ubicación)."""

import pytest

from domain.categoria import NIVELES_MAXIMOS, ArbolCategorias, Categoria
from excepciones import DatosInvalidosError


def _arbol(*filas):
    """filas: (id, nombre, padre_id[, activa])."""
    return ArbolCategorias(
        Categoria(id=f[0], nombre=f[1], categoria_padre_id=f[2], activa=f[3] if len(f) > 3 else True) for f in filas
    )


@pytest.fixture
def arbol():
    return _arbol(
        (1, "Alimentos", None),
        (2, "Panificados", 1),
        (3, "Criollos", 2),
        (4, "Bebidas", None),
        (5, "Galletitas", 1),
        (6, "Discontinuada", None, False),
        (7, "Hija de discontinuada", 6),
    )


def test_ruta_y_nivel(arbol):
    assert arbol.ruta(3) == "Alimentos › Panificados › Criollos"
    assert arbol.ruta(4) == "Bebidas"
    assert [arbol.nivel(i) for i in (1, 2, 3)] == [1, 2, 3]


def test_descendientes_incluye_la_propia_y_todos_los_niveles(arbol):
    assert arbol.ids_con_descendientes(1) == {1, 2, 3, 5}
    assert arbol.ids_con_descendientes(3) == {3}
    assert arbol.ids_con_descendientes(999) == set()


def test_en_orden_es_profundidad_por_nombre_y_oculta_ramas_inactivas(arbol):
    assert [o.categoria.nombre for o in arbol.en_orden()] == [
        "Alimentos", "Galletitas", "Panificados", "Criollos", "Bebidas", "Discontinuada", "Hija de discontinuada",
    ]
    activas = arbol.en_orden(solo_activas=True)
    assert [o.categoria.id for o in activas] == [1, 5, 2, 3, 4]
    assert [o.nivel for o in activas] == [1, 2, 2, 3, 1]


def test_altura(arbol):
    assert arbol.altura(1) == 3
    assert arbol.altura(2) == 2
    assert arbol.altura(4) == 1


@pytest.mark.parametrize(
    "categoria_id, padre_id, mensaje",
    [
        (1, 1, "sí misma"),
        (1, 3, "sí misma"),  # su propia nieta
        (None, 3, "niveles"),  # una nueva dentro del tercer nivel sería el cuarto
        (2, 5, "niveles"),  # Panificados (altura 2) dentro de un nivel 2 -> 4 niveles
        (None, 6, "dada de baja"),
        (None, 999, "no existe"),
    ],
)
def test_ubicaciones_invalidas(arbol, categoria_id, padre_id, mensaje):
    with pytest.raises(DatosInvalidosError, match=mensaje):
        arbol.validar_ubicacion(categoria_id, padre_id)


def test_ubicaciones_validas(arbol):
    arbol.validar_ubicacion(None, None)
    arbol.validar_ubicacion(None, 2)  # nueva en el tercer nivel
    arbol.validar_ubicacion(2, 4)  # Panificados > Criollos dentro de Bebidas: 3 niveles
    arbol.validar_ubicacion(3, None)  # una hoja pasa a principal
    assert NIVELES_MAXIMOS == 3


def test_un_padre_inexistente_en_los_datos_no_rompe_el_arbol():
    """Una fila huérfana (no debería existir por la FK) se muestra como principal en vez de perderse."""
    arbol = _arbol((1, "Huérfana", 99))
    assert [o.categoria.id for o in arbol.en_orden()] == [1]
    assert arbol.ruta(1) == "Huérfana"
