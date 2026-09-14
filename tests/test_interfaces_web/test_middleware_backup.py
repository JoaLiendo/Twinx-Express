"""Tests HTTP del middleware de bloqueo de escrituras durante backup
(ver `interfaces/web/app.py` y `services/control_escrituras.py`).

Incluye la regresión del deadlock real detectado en auditoría: el
propio `POST /backup/crear` no debe contarse como una escritura de
negocio, porque `crear_backup()` espera a que esas escrituras drenen y
quedaría esperándose a sí mismo.
"""

import sqlite3
import threading

import pytest

from domain.usuario import Usuario
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth
from services.control_escrituras import control_escrituras

import interfaces.web.rutas.backup as modulo_ruta_backup
import services.servicio_backup as modulo_servicio_backup

from ._asgi_cliente import solicitud


@pytest.fixture(autouse=True)
def _control_escrituras_en_estado_normal():
    """El singleton es compartido por proceso: asegura que ningún test
    empiece ni termine con un backup activo colgado."""
    if control_escrituras.backup_activo:
        control_escrituras.finalizar_backup()
    yield
    if control_escrituras.backup_activo:
        control_escrituras.finalizar_backup()


@pytest.fixture
def entorno_backup_real(tmp_path, monkeypatch, base_datos_temporal, directorio_imagenes_temporal):
    """Redirige `servicio_backup` a la misma DB/imágenes temporales que
    ya usa el resto de la suite (`servicio_backup` tiene su propio
    binding de `RUTA_BASE_DATOS`/`DIRECTORIO_IMAGENES_PRODUCTOS`,
    independiente del de `db.conexion`/`servicio_imagenes`), y la
    carpeta de backups a un directorio temporal."""
    monkeypatch.setattr(modulo_servicio_backup, "RUTA_BASE_DATOS", base_datos_temporal)
    monkeypatch.setattr(modulo_servicio_backup, "DIRECTORIO_IMAGENES_PRODUCTOS", directorio_imagenes_temporal)
    monkeypatch.setattr(modulo_ruta_backup, "DIRECTORIO_BACKUPS", tmp_path / "backups")
    return tmp_path / "backups"


def _cookies_owner(nombre_usuario: str = "ana") -> dict[str, str]:
    from db.repositorios import usuarios as repositorio_usuarios

    repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre_usuario,
            nombre_completo="Owner de prueba",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol="OWNER",
        )
    )
    token = servicio_auth.iniciar_sesion(nombre_usuario, "clave-correcta-123").token
    return {NOMBRE_COOKIE_SESION: token}


def test_lectura_normal_funciona():
    respuesta = solicitud("GET", "/login")

    assert respuesta.status != 503


def test_escritura_normal_funciona_cuando_no_hay_backup_en_curso(base_datos_temporal):
    respuesta = solicitud("POST", "/login", formulario={"nombre_usuario": "x", "password": "y"})

    assert respuesta.status != 503


def test_bloquea_escrituras_nuevas_durante_backup_y_las_libera_al_terminar():
    control_escrituras.iniciar_backup()

    respuesta_bloqueada = solicitud("POST", "/login", formulario={"nombre_usuario": "x", "password": "y"})
    assert respuesta_bloqueada.status == 503

    control_escrituras.finalizar_backup()

    respuesta_normal = solicitud("POST", "/login", formulario={"nombre_usuario": "x", "password": "y"})
    assert respuesta_normal.status != 503


def test_post_backup_crear_no_genera_deadlock(entorno_backup_real, base_datos_temporal):
    """Regresión del deadlock: si `POST /backup/crear` se contara como
    escritura de negocio, este test quedaría colgado (acotado a 30s por
    el timeout de `iniciar_backup` en `servicio_backup.crear_backup`,
    nunca "para siempre" como con el bug original).

    El chequeo posterior usa `POST /caja/abrir` -- una operación de
    negocio real (inserta en `caja_movimientos`), no `/login` (que solo
    demuestra que el middleware deja pasar un POST cualquiera, sin
    probar que una escritura de negocio real funcione).
    """
    cookies = _cookies_owner()

    respuesta = solicitud("POST", "/backup/crear", cookies=cookies)

    assert respuesta.status == 303
    assert control_escrituras.escrituras_de_negocio_en_curso == 0
    assert control_escrituras.backup_activo is False

    # el estado quedó realmente sano: una operación de negocio real funciona
    respuesta_caja = solicitud(
        "POST", "/caja/abrir", cookies=cookies, formulario={"monto_inicial": "100"}
    )
    assert respuesta_caja.status == 303

    conexion = sqlite3.connect(base_datos_temporal)
    try:
        cantidad = conexion.execute(
            "SELECT COUNT(*) FROM caja_movimientos WHERE tipo = 'APERTURA'"
        ).fetchone()[0]
    finally:
        conexion.close()
    assert cantidad == 1


def test_dos_post_backup_crear_simultaneos_solo_uno_ejecuta(entorno_backup_real, base_datos_temporal):
    cookies = _cookies_owner()
    resultados = {}

    def disparar(nombre):
        resultados[nombre] = solicitud("POST", "/backup/crear", cookies=cookies)

    # Igual que en el test de servicio: se deja el backup "activo" a
    # mano para simular que un primer request ya está en curso cuando
    # llega el segundo, sin depender de una carrera de hilos real.
    control_escrituras.iniciar_backup()

    hilo_b = threading.Thread(target=disparar, args=("B",))
    hilo_b.start()
    hilo_b.join()

    # Un backup ya activo hace que `crear_backup` levante `ErrorBackup`,
    # traducido por el manejador global de `ErrorAplicacion` a un
    # redirect con toast de error (no un 500).
    assert resultados["B"].status == 303
    assert not list(entorno_backup_real.glob("*.zip")) if entorno_backup_real.exists() else True
