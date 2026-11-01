"""Fixtures compartidas por toda la suite de tests.

Ningún test corre contra `data/kiosco.db`: todos usan una base de
datos SQLite temporal y aislada por test (ver `base_datos_temporal`).
Tampoco se escriben imágenes reales bajo `data/imagenes_productos/`
(ver `directorio_imagenes_temporal`).
"""

import config
import db.conexion as modulo_conexion
import services.servicio_auth as modulo_auth
import services.servicio_imagenes as modulo_imagenes
from services.limitador_login import LimitadorIntentosLogin

import pytest


class RelojFalso:
    """Reloj controlable para probar bloqueos por tiempo sin `sleep`."""

    def __init__(self, ahora: float = 1000.0) -> None:
        self.ahora = ahora

    def __call__(self) -> float:
        return self.ahora

    def avanzar(self, segundos: float) -> None:
        self.ahora += segundos


@pytest.fixture
def reloj_falso():
    return RelojFalso()


@pytest.fixture(autouse=True)
def backups_aislados(tmp_path, monkeypatch):
    """Ningún test escribe en la carpeta real de backups ni lee las imágenes
    reales: desde V1.15 cerrar la caja dispara un backup, así que cualquier
    test de caja lo genera. Se parchea `servicio_backup.DIRECTORIO_BACKUPS` y
    el directorio de imágenes activo (los tests que necesitan imágenes propias
    usan `directorio_imagenes_temporal`, que pisa este valor)."""
    import services.servicio_backup as modulo_backup

    imagenes = tmp_path / "imagenes_aisladas"
    imagenes.mkdir()
    monkeypatch.setattr(modulo_backup, "DIRECTORIO_BACKUPS", tmp_path / "backups_aislados")
    monkeypatch.setattr(config, "DIRECTORIO_VERSIONES", tmp_path / "versiones_aisladas")
    monkeypatch.setattr(modulo_imagenes, "DIRECTORIO_IMAGENES_PRODUCTOS", imagenes)
    # Síncrona en tests: un hilo que sobreviviera al test leería la base real tras el teardown.
    monkeypatch.setattr(modulo_backup, "_lanzar_copia_secundaria", modulo_backup.copiar_a_destino_secundario)
    return tmp_path / "backups_aislados"


@pytest.fixture(autouse=True)
def fiscal_aislado(tmp_path, monkeypatch):
    """V1.16: ningún test lee ni escribe credenciales fiscales reales, y ninguno puede abrir conexión con
    ARCA producción (el transporte lo rechaza con esta variable, pase lo que pase en la configuración)."""
    from services.fiscal.arca.endpoints import VARIABLE_BLOQUEO_PRODUCCION

    monkeypatch.setenv(VARIABLE_BLOQUEO_PRODUCCION, "1")
    monkeypatch.setattr(config, "DIRECTORIO_SECRETOS_FISCALES", tmp_path / "fiscal_privado_aislado")
    return tmp_path / "fiscal_privado_aislado"


@pytest.fixture
def arca_falso():
    """ARCA falso determinista inyectado como gateway de todos los servicios fiscales del test."""
    from services.fiscal import gateway
    from tests.fake_arca import ARCAFalso

    falso = ARCAFalso()
    gateway.establecer_fabrica(lambda perfil: falso)
    yield falso
    gateway.establecer_fabrica(None)


@pytest.fixture(autouse=True)
def limitador_login_aislado(monkeypatch, reloj_falso):
    """Reemplaza el limitador de login por uno nuevo (con reloj falso)
    en cada test. Cada test usa una base temporal nueva donde los ids de
    usuario se repiten, así que un limitador compartido filtraría
    contadores de un test a otro.
    """
    limitador = LimitadorIntentosLogin(reloj=reloj_falso)
    monkeypatch.setattr(modulo_auth, "_limitador", limitador)
    return limitador


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


@pytest.fixture
def caja_abierta(request, base_datos_temporal):
    """Deja una caja abierta antes del test: desde V1.1 no se puede
    registrar una venta sin caja abierta. Los tests que abren/cierran la
    caja por su cuenta se marcan con `@pytest.mark.sin_caja_abierta`."""
    if request.node.get_closest_marker("sin_caja_abierta"):
        return
    from db.repositorios import caja as repositorio_caja
    from domain.caja import MovimientoCaja

    repositorio_caja.registrar_movimiento(MovimientoCaja(tipo="APERTURA", monto_centavos=0))
