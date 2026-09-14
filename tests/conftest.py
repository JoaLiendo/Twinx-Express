"""Fixtures compartidas por toda la suite de tests.

Ningún test corre contra `data/kiosco.db`: todos usan una base de
datos SQLite temporal y aislada por test (ver `base_datos_temporal`).
Tampoco se escriben imágenes reales bajo `data/imagenes_productos/`
(ver `directorio_imagenes_temporal`).
"""

import db.conexion as modulo_conexion
import services.servicio_imagenes as modulo_imagenes

import pytest


@pytest.fixture
def directorio_imagenes_temporal(tmp_path, monkeypatch):
    """Redirige el guardado de imágenes de producto a un directorio
    temporal y vacío, aislado por test.

    Parchea `services.servicio_imagenes.DIRECTORIO_IMAGENES_PRODUCTOS`
    (el nombre tal como quedó importado en ese módulo, no
    `config.DIRECTORIO_IMAGENES_PRODUCTOS` -- `from x import y` crea
    un binding propio, parchear el original no lo actualiza; mismo
    motivo por el que `base_datos_temporal` parchea
    `db.conexion.RUTA_BASE_DATOS` y no `config.RUTA_BASE_DATOS`).
    """
    directorio = tmp_path / "imagenes_productos"
    directorio.mkdir()
    monkeypatch.setattr(modulo_imagenes, "DIRECTORIO_IMAGENES_PRODUCTOS", directorio)
    return directorio


@pytest.fixture
def base_datos_temporal(tmp_path, monkeypatch):
    """Redirige la aplicación a una base de datos SQLite temporal y vacía.

    Se usa un archivo real dentro del directorio temporal del test
    (`tmp_path`), no `:memory:`: cada llamada a `obtener_conexion()`
    abre su propia conexión nueva, y una base en memoria se perdería
    entre una llamada y la siguiente.

    Parchea `db.conexion.RUTA_BASE_DATOS` (el global que
    `obtener_conexion()` lee en cada llamada, sin importar desde qué
    módulo se la haya importado) y ejecuta la migración inicial real
    contra ese archivo, así los tests validan el mismo esquema que usa
    la aplicación. `tmp_path` es un directorio nuevo por test, así que
    cada test parte de una base completamente vacía.
    """
    ruta_bd_prueba = tmp_path / "test_kiosco.db"
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta_bd_prueba)
    modulo_conexion.inicializar_base_datos()
    return ruta_bd_prueba
