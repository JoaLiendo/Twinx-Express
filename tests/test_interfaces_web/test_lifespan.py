"""Tests del `lifespan` de `interfaces/web/app.py`: backup automático V1
(ver auditoría de distribución/backup).

El chequeo de backup automático se dispara en un hilo daemon de un solo
uso, para que un backup lento (o que directamente falla) nunca demore ni
rompa el arranque del servidor.
"""

import asyncio
import threading
import time

import interfaces.web.app as modulo_app
import services.servicio_backup as modulo_servicio_backup


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
