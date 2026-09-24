"""Test ESTRUCTURAL (V1.2): ningún camino de la aplicación puede cambiar el precio
de venta o el costo de un producto sin pasar por el historial de precios.

Recorre el código de producción y exige que los únicos `UPDATE productos` que
tocan `precio_venta_centavos` / `precio_costo_centavos` estén en dos funciones
del repositorio, y que ambas registren el cambio en `historial_precios`. Un
`UPDATE` nuevo en cualquier otro lugar hace fallar este test: quien lo agregue
debe pasar por `cambiar_precio_en_conexion` (o registrar el historial y sumarse a
esta lista de forma consciente)."""

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
_EXCLUIDOS = {"tests", "tests_e2e", "dist", "build", "node_modules", ".venv", "venv", "__pycache__", ".git"}

# (archivo, función) -> las dos únicas escrituras de precio permitidas.
ESCRITURAS_DE_PRECIO_PERMITIDAS = {
    ("db/repositorios/productos.py", "actualizar_datos_en_conexion"),
    ("db/repositorios/productos.py", "cambiar_precio_en_conexion"),
}


def _archivos_de_produccion():
    for ruta in RAIZ.rglob("*.py"):
        relativa = ruta.relative_to(RAIZ)
        if not (set(relativa.parts) & _EXCLUIDOS):
            yield ruta, relativa.as_posix()


def _es_update_de_precio(texto: str) -> bool:
    minuscula = texto.lower()
    return "update productos" in minuscula and any(
        marca in minuscula for marca in ("precio_costo", "precio_venta", "{columna}")
    )


def _escrituras_de_precio() -> set[tuple[str, str]]:
    encontradas = set()
    for ruta, relativa in _archivos_de_produccion():
        arbol = ast.parse(ruta.read_text(encoding="utf-8-sig"))
        for funcion in ast.walk(arbol):
            if not isinstance(funcion, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for nodo in ast.walk(funcion):
                if isinstance(nodo, (ast.Constant, ast.JoinedStr)) and _es_update_de_precio(ast.unparse(nodo)):
                    encontradas.add((relativa, funcion.name))
    return encontradas


def _funcion(relativa: str, nombre: str) -> ast.FunctionDef:
    arbol = ast.parse((RAIZ / relativa).read_text(encoding="utf-8-sig"))
    return next(n for n in ast.walk(arbol) if isinstance(n, ast.FunctionDef) and n.name == nombre)


def _llamadas(funcion: ast.FunctionDef) -> set[str]:
    nombres = set()
    for nodo in ast.walk(funcion):
        if isinstance(nodo, ast.Call):
            destino = nodo.func
            nombres.add(destino.attr if isinstance(destino, ast.Attribute) else getattr(destino, "id", ""))
    return nombres


def test_el_detector_encuentra_las_escrituras_conocidas():
    """Guarda contra un detector que no ve nada (y por eso siempre pasaría)."""
    assert _escrituras_de_precio(), "el detector no encontró ni siquiera las escrituras permitidas"


def test_solo_hay_dos_puntos_de_escritura_de_precio_y_ambos_estan_permitidos():
    assert _escrituras_de_precio() == ESCRITURAS_DE_PRECIO_PERMITIDAS


def test_ambos_puntos_de_escritura_registran_el_historial():
    for relativa, nombre in ESCRITURAS_DE_PRECIO_PERMITIDAS:
        assert "registrar_cambio_en_conexion" in _llamadas(_funcion(relativa, nombre)), (
            f"{relativa}::{nombre} cambia precios y no registra el historial"
        )


def test_el_costo_de_una_compra_pasa_por_el_punto_unico():
    assert "actualizar_costo_en_conexion" in _llamadas(_funcion("services/servicio_compras.py", "registrar_compra"))
    assert "cambiar_precio_en_conexion" in _llamadas(_funcion("db/repositorios/productos.py", "actualizar_costo_en_conexion"))


def test_la_actualizacion_masiva_pasa_por_el_punto_unico():
    assert "cambiar_precio_en_conexion" in _llamadas(_funcion("services/servicio_precios.py", "aplicar_actualizacion"))


def test_la_edicion_manual_y_la_importacion_pasan_por_actualizar_datos():
    assert "actualizar_datos" in _llamadas(_funcion("services/servicio_stock.py", "actualizar_producto"))
    assert "actualizar_datos_en_conexion" in _llamadas(_funcion("services/servicio_importacion.py", "_procesar_fila"))


def test_ningun_servicio_ni_ruta_escribe_precios_con_sql_propio():
    """Servicios e interfaces no ejecutan SQL de productos (regla de capas)."""
    for _, relativa in _archivos_de_produccion():
        if relativa.startswith(("services/", "interfaces/", "domain/")):
            texto = (RAIZ / relativa).read_text(encoding="utf-8-sig").lower()
            assert "update productos" not in texto, f"{relativa} ejecuta SQL de productos"
