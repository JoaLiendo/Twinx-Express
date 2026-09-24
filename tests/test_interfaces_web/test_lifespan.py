"""Tests del `lifespan` de `interfaces/web/app.py`: backup automático V1
(ver auditoría de distribución/backup).

El chequeo de backup automático se dispara en un hilo daemon de un solo
uso, para que un backup lento (o que directamente falla) nunca demore ni
rompa el arranque del servidor.
"""

import asyncio
import threading
import time

import pytest

import interfaces.web.app as modulo_app
import services.servicio_backup as modulo_servicio_backup
import services.servicio_catalogo_inicial as modulo_catalogo
from excepciones import ErrorCatalogoInicial


def _entrar_y_salir_del_lifespan() -> None:
    async def _correr():
        async with modulo_app.lifespan(modulo_app.app):
            pass

    asyncio.run(_correr())


def test_lifespan_dispara_el_backup_automatico_en_un_hilo_sin_bloquear_el_arranque(
    base_datos_temporal, monkeypatch
):
    entro_al_backup = threading.Event()
    liberar_backup = threading.Event()

    def backup_automatico_falso(*_args, **_kwargs):
        entro_al_backup.set()
        liberar_backup.wait(timeout=5)

    monkeypatch.setattr(
        modulo_servicio_backup, "ejecutar_backup_automatico_si_corresponde", backup_automatico_falso
    )

    inicio = time.monotonic()
    _entrar_y_salir_del_lifespan()
    duracion = time.monotonic() - inicio

    try:
        assert entro_al_backup.wait(timeout=2), "el hilo de backup automático nunca arrancó"
        assert duracion < 2, "el arranque esperó al backup automático en vez de seguir de largo"
    finally:
        liberar_backup.set()


def test_lifespan_migra_a_traves_del_backup_preventivo_con_el_directorio_de_backups(
    base_datos_temporal, monkeypatch
):
    llamadas = []
    monkeypatch.setattr(
        modulo_servicio_backup,
        "migrar_base_datos_con_backup_preventivo",
        lambda directorio, control: llamadas.append(directorio),
    )
    monkeypatch.setattr(
        modulo_servicio_backup, "ejecutar_backup_automatico_si_corresponde", lambda *_a, **_k: None
    )

    _entrar_y_salir_del_lifespan()

    assert llamadas == [modulo_app.DIRECTORIO_BACKUPS]


# ---------------------------------------------------------------------------
# Catálogo inicial de distribución
# ---------------------------------------------------------------------------


def _contar(tabla: str) -> int:
    from db.conexion import obtener_conexion

    with obtener_conexion() as conexion:
        return conexion.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]


@pytest.fixture
def sin_backup_automatico(monkeypatch):
    monkeypatch.setattr(
        modulo_servicio_backup, "ejecutar_backup_automatico_si_corresponde", lambda *_a, **_k: None
    )


def test_lifespan_con_el_seed_habilitado_siembra_el_catalogo_sin_usuarios(
    base_datos_temporal, monkeypatch, sin_backup_automatico
):
    monkeypatch.setattr(modulo_app, "SEMBRAR_CATALOGO_INICIAL", True)

    _entrar_y_salir_del_lifespan()

    assert _contar("productos") == 41
    assert _contar("categorias") == 10
    assert _contar("usuarios") == 0


def test_lifespan_con_el_seed_deshabilitado_no_siembra_nada(
    base_datos_temporal, monkeypatch, sin_backup_automatico
):
    monkeypatch.setattr(modulo_app, "SEMBRAR_CATALOGO_INICIAL", False)

    _entrar_y_salir_del_lifespan()

    assert _contar("productos") == 0
    assert _contar("categorias") == 0


def test_lifespan_reiniciado_con_el_seed_habilitado_no_duplica_el_catalogo(
    base_datos_temporal, monkeypatch, sin_backup_automatico
):
    monkeypatch.setattr(modulo_app, "SEMBRAR_CATALOGO_INICIAL", True)

    _entrar_y_salir_del_lifespan()
    _entrar_y_salir_del_lifespan()  # "segundo arranque"

    assert _contar("productos") == 41


def test_lifespan_aborta_el_arranque_si_el_catalogo_no_se_puede_cargar(base_datos_temporal, monkeypatch):
    monkeypatch.setattr(modulo_app, "SEMBRAR_CATALOGO_INICIAL", True)
    arranco_el_backup = threading.Event()

    def sembrar_que_falla():
        raise ErrorCatalogoInicial("catálogo inválido simulado")

    monkeypatch.setattr(modulo_catalogo, "sembrar_si_corresponde", sembrar_que_falla)
    monkeypatch.setattr(
        modulo_servicio_backup, "ejecutar_backup_automatico_si_corresponde", lambda *_a, **_k: arranco_el_backup.set()
    )

    with pytest.raises(ErrorCatalogoInicial):
        _entrar_y_salir_del_lifespan()

    time.sleep(0.2)
    assert not arranco_el_backup.is_set(), "el arranque siguió como si nada tras el fallo del catálogo"
