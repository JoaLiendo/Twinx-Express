"""Proceso real para los tests multiproceso de instancia única (`tests/test_instancia_unica.py`).

Un hilo no sirve para estos tests: lo que se prueba es la exclusión entre procesos del sistema operativo,
y que el sistema la libere cuando el proceso muere. Cada modo informa lo que le pasó con una línea JSON
por stdout y se queda vivo hasta que el test cierra su stdin.

Uso: `python -m tests.proceso_instancia <modo> <ruta_db> [argumentos]`. Nunca usa ARCA, la base real ni
los backups o secretos reales.
"""

import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path

CODIGO_RECHAZADA = 3


def _informar(evento: str, **datos) -> None:
    print(json.dumps({"evento": evento, **datos}), flush=True)


def _aislar(ruta_db: Path) -> None:
    import config
    import db.conexion as modulo_conexion
    from services.fiscal.arca.endpoints import VARIABLE_BLOQUEO_PRODUCCION

    os.environ[VARIABLE_BLOQUEO_PRODUCCION] = "1"
    modulo_conexion.RUTA_BASE_DATOS = ruta_db
    config.DIRECTORIO_SECRETOS_FISCALES = ruta_db.parent / "fiscal_privado"


def _preparar_aplicacion(ruta_db: Path):
    """La app real con su lifespan real. Solo se apagan los hilos de segundo plano (backup automático y
    conciliación), para que el estado que ve el test sea exactamente el que dejó el arranque."""
    _aislar(ruta_db)
    import interfaces.web.app as modulo_app
    from services import servicio_backup
    from services.fiscal import gateway, servicio_facturacion, servicio_notas
    from tests.fake_arca import ARCAFalso

    backups = ruta_db.parent / "backups"
    modulo_app.DIRECTORIO_BACKUPS = backups
    servicio_backup.DIRECTORIO_BACKUPS = backups
    falso = ARCAFalso()
    gateway.establecer_fabrica(lambda perfil: falso)
    servicio_notas.conciliar_y_aplicar = lambda: None
    servicio_backup.ejecutar_backup_automatico_si_corresponde = lambda *_: None

    migrar = servicio_backup.migrar_base_datos_con_backup_preventivo
    recuperar = servicio_facturacion.recuperar_al_iniciar

    def migrar_informando(*argumentos):
        _informar("migracion")
        return migrar(*argumentos)

    def recuperar_informando():
        recuperados = recuperar()
        _informar("recuperacion", comprobantes=recuperados)
        return recuperados

    servicio_backup.migrar_base_datos_con_backup_preventivo = migrar_informando
    servicio_facturacion.recuperar_al_iniciar = recuperar_informando
    return modulo_app


def _tomar(ruta_db: Path, inicio: str | None = None) -> int:
    """Toma la instancia a secas. Con `inicio` (epoch) espera a ese instante, para competir con otros."""
    from db import instancia_unica
    from excepciones import InstanciaEnUsoError

    if inicio is not None:
        while time.time() < float(inicio):
            pass
    try:
        with instancia_unica.exclusiva(ruta_db):
            _informar("listo")
            sys.stdin.readline()
    except InstanciaEnUsoError as error:
        _informar("rechazada", tipo=type(error).__name__, mensaje=str(error))
        return CODIGO_RECHAZADA
    return 0


def _arrancar(ruta_db: Path, comprobante_id: str | None = None) -> int:
    """Corre el lifespan real. Con `comprobante_id`, ya arrancado lo deja en ENVIANDO y sigue vivo: el
    punto crítico de `_enviar`, con el FECAESolicitar todavía sin respuesta."""
    modulo_app = _preparar_aplicacion(ruta_db)

    async def _correr() -> None:
        async with modulo_app.lifespan(modulo_app.app):
            if comprobante_id is not None:
                from tests import utilidades_fiscales

                utilidades_fiscales.transicionar(
                    int(comprobante_id), "ENVIANDO", numero=1, fecha_comprobante="2026-11-06"
                )
            _informar("listo")
            await asyncio.to_thread(sys.stdin.readline)

    try:
        asyncio.run(_correr())
    except Exception as error:  # noqa: BLE001 -- se informa al test cuál fue, sea el esperado o no
        _informar("rechazada", tipo=type(error).__name__, mensaje=str(error))
        return CODIGO_RECHAZADA
    return 0


def _servir(ruta_db: Path, puerto: str) -> int:
    """Uvicorn real en `puerto`: el lock no puede depender del puerto ni de quién levante el servidor."""
    import uvicorn

    modulo_app = _preparar_aplicacion(ruta_db)
    servidor = uvicorn.Server(uvicorn.Config(modulo_app.app, host="127.0.0.1", port=int(puerto), log_level="critical"))

    def _vigilar() -> None:
        while not servidor.started:
            time.sleep(0.02)
        _informar("listo")
        sys.stdin.readline()
        servidor.should_exit = True

    threading.Thread(target=_vigilar, daemon=True).start()
    try:
        servidor.run()
    except SystemExit:
        causa = getattr(modulo_app.app.state, "error_de_inicio", None)
        _informar("rechazada", tipo=type(causa).__name__, mensaje=str(causa))
        return CODIGO_RECHAZADA
    return 0


def _lanzador(ruta_db: Path) -> int:
    """El camino del ejecutable: lo que ve el operador al abrir Twinx Express por segunda vez."""
    _aislar(ruta_db)
    import builtins

    import lanzador

    builtins.input = lambda *_: ""
    evento_listo = threading.Event()
    lanzador.iniciar_servidor(evento_listo)
    _informar("fin", servidor_listo=evento_listo.is_set(), app_importada="interfaces.web.app" in sys.modules)
    return 0


_MODOS = {"tomar": _tomar, "arrancar": _arrancar, "servir": _servir, "lanzador": _lanzador}


if __name__ == "__main__":
    sys.exit(_MODOS[sys.argv[1]](Path(sys.argv[2]), *sys.argv[3:]))
