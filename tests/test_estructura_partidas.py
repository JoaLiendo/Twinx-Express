"""Test ESTRUCTURAL (V1.12-A): ningún camino de la aplicación puede cambiar el stock sin reflejarlo en las
partidas. Toda función de producción que escribe `stock_actual` (`actualizar_stock_en_conexion`) o da de
alta stock (`crear_producto_en_conexion` seguido del stock inicial) tiene que llamar, en la misma función,
a `verificar_conciliacion_en_conexion`. Un camino nuevo que lo olvide hace fallar este test."""

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
_EXCLUIDOS = {"tests", "tests_e2e", "dist", "build", "node_modules", ".venv", "venv", "__pycache__", ".git"}

# Funciones que escriben stock y DEBEN conciliar.
ESPERADAS = {
    ("services/servicio_compras.py", "registrar_compra"),
    ("services/servicio_compras.py", "anular_compra"),
    ("services/servicio_ventas.py", "registrar_venta"),
    ("services/servicio_ventas.py", "anular_venta"),
    ("services/servicio_stock.py", "aplicar_ajuste_en_conexion"),
}


def _funciones():
    for ruta in RAIZ.rglob("*.py"):
        relativa = ruta.relative_to(RAIZ)
        if set(relativa.parts) & _EXCLUIDOS or relativa.parts[0] == "db":
            continue
        arbol = ast.parse(ruta.read_text(encoding="utf-8-sig"))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield relativa.as_posix(), nodo


def _llamadas(funcion) -> set[str]:
    return {
        nodo.func.attr if isinstance(nodo.func, ast.Attribute) else getattr(nodo.func, "id", "")
        for nodo in ast.walk(funcion)
        if isinstance(nodo, ast.Call)
    }


def test_toda_escritura_de_stock_concilia_con_las_partidas():
    escritoras = {
        (archivo, funcion.name)
        for archivo, funcion in _funciones()
        if "actualizar_stock_en_conexion" in _llamadas(funcion)
    }
    assert escritoras == ESPERADAS, "camino nuevo que escribe stock: integrarlo a servicio_partidas"
    sin_conciliar = {
        (archivo, funcion.name)
        for archivo, funcion in _funciones()
        if (archivo, funcion.name) in escritoras and "verificar_conciliacion_en_conexion" not in _llamadas(funcion)
    }
    assert sin_conciliar == set()


def test_el_stock_de_alta_concilia():
    """Toda alta con stock pasa por `registrar_stock_inicial_en_conexion`, que crea la partida y concilia."""
    altas = {
        (archivo, funcion.name)
        for archivo, funcion in _funciones()
        if "crear_producto_en_conexion" in _llamadas(funcion)
    }
    assert altas == {("services/servicio_stock.py", "registrar_producto"), ("services/servicio_importacion.py", "_procesar_fila")}
    funciones = {(a, f.name): f for a, f in _funciones()}
    for clave in altas:
        assert "registrar_stock_inicial_en_conexion" in _llamadas(funciones[clave]), clave
    inicial = funciones[("services/servicio_stock.py", "registrar_stock_inicial_en_conexion")]
    assert {"registrar_ingreso_en_conexion", "verificar_conciliacion_en_conexion"} <= _llamadas(inicial)
