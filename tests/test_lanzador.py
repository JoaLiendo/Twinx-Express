"""Tests de `lanzador.py`: manejo de errores de arranque y apertura
condicional del navegador.

No se ejercita el hilo real ni uvicorn/FastAPI: `iniciar_servidor`
recibe la función de arranque como parámetro (`ejecutar`) para poder
reemplazarla por un doble de prueba, y `abrir_navegador_si_corresponde`
recibe `abrir` de la misma forma.
"""
import threading

import pytest

import lanzador
from config import ErrorMigracionDatos
from excepciones import ErrorRestore


def test_error_migracion_datos_no_imprime_traceback_y_muestra_mensaje_claro(monkeypatch, capsys):
    llamadas_input = []
    monkeypatch.setattr("builtins.input", lambda *_: llamadas_input.append(1))
    evento = threading.Event()

    def ejecutar_falso(_evento):
        raise ErrorMigracionDatos("mensaje claro para el usuario")

    lanzador.iniciar_servidor(evento, ejecutar=ejecutar_falso)

    salida = capsys.readouterr()
    assert "mensaje claro para el usuario" in salida.out
    assert "Traceback" not in salida.out
    assert "Traceback" not in salida.err
    assert not evento.is_set()
    assert llamadas_input == [1]


def test_error_inesperado_conserva_traceback(monkeypatch, capsys):
    llamadas_input = []
    monkeypatch.setattr("builtins.input", lambda *_: llamadas_input.append(1))
    evento = threading.Event()

    def ejecutar_falso(_evento):
        raise RuntimeError("fallo inesperado")

    lanzador.iniciar_servidor(evento, ejecutar=ejecutar_falso)

    salida = capsys.readouterr()
    assert "RuntimeError" in salida.err
    assert "fallo inesperado" in salida.err
    assert not evento.is_set()
    assert llamadas_input == [1]


def test_iniciar_servidor_marca_evento_cuando_ejecutar_tiene_exito(capsys):
    evento = threading.Event()

    def ejecutar_exitoso(evento_servidor_listo):
        # Simula lo que hace `_importar_y_ejecutar_servidor` hasta justo
        # antes de `uvicorn.run(...)` (que bloquearía el test para
        # siempre): marca el evento y retorna sin excepción.
        evento_servidor_listo.set()

    lanzador.iniciar_servidor(evento, ejecutar=ejecutar_exitoso)

    salida = capsys.readouterr()
    assert evento.is_set()
    assert salida.out == ""
    assert salida.err == ""


def test_iniciar_servidor_puerto_ocupado_no_marca_evento_y_muestra_diagnostico(monkeypatch, capsys):
    """Uvicorn 0.52.4 (versión instalada, ver `server.py::Server.startup`)
    señaliza un fallo de bind con `sys.exit(STARTUP_FAILURE)`, no con una
    excepción común -- `SystemExit` debe quedar atrapado acá, con un
    mensaje claro, no como un traceback genérico ni como un crash."""
    llamadas_input = []
    monkeypatch.setattr("builtins.input", lambda *_: llamadas_input.append(1))
    evento = threading.Event()

    def ejecutar_falso(_evento):
        raise SystemExit(3)

    lanzador.iniciar_servidor(evento, ejecutar=ejecutar_falso)

    salida = capsys.readouterr()
    assert not evento.is_set()
    assert "puerto 8000" in salida.out.lower()
    assert llamadas_input == [1]


def test_puerto_ocupado_no_abre_navegador(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: None)
    evento = threading.Event()

    def ejecutar_falso(_evento):
        raise SystemExit(3)

    lanzador.iniciar_servidor(evento, ejecutar=ejecutar_falso)

    llamadas = []
    lanzador.abrir_navegador_si_corresponde(evento, abrir=llamadas.append)

    assert llamadas == []


def test_importar_y_ejecutar_servidor_marca_evento_solo_cuando_uvicorn_confirma_arranque(monkeypatch):
    """Prueba `_importar_y_ejecutar_servidor` en sí (no un doble de toda
    la función): con un `uvicorn.Server` falso que solo marca `started`
    dentro de `run()`, el evento debe quedar marcado -- reproduce
    exactamente el contrato real de uvicorn (`server.started`)."""

    class ServidorFalsoQueArranca:
        def __init__(self, config):
            self.started = False

        def run(self):
            self.started = True

    monkeypatch.setattr("uvicorn.Config", lambda *a, **k: None)
    monkeypatch.setattr("uvicorn.Server", ServidorFalsoQueArranca)

    evento = threading.Event()
    lanzador._importar_y_ejecutar_servidor(evento)

    assert evento.is_set()


def test_importar_y_ejecutar_servidor_no_marca_evento_si_uvicorn_no_arranca(monkeypatch):
    """Simula el fallo real de bind de uvicorn 0.52.4: `run()` lanza
    `SystemExit` sin haber marcado `started` -- el evento nunca debe
    quedar marcado."""

    class ServidorFalsoQueFalla:
        def __init__(self, config):
            self.started = False

        def run(self):
            raise SystemExit(3)

    monkeypatch.setattr("uvicorn.Config", lambda *a, **k: None)
    monkeypatch.setattr("uvicorn.Server", ServidorFalsoQueFalla)

    evento = threading.Event()
    with pytest.raises(SystemExit):
        lanzador._importar_y_ejecutar_servidor(evento)

    assert not evento.is_set()


def test_no_abre_navegador_si_el_servidor_no_arranco():
    evento = threading.Event()
    llamadas = []

    lanzador.abrir_navegador_si_corresponde(evento, abrir=llamadas.append)

    assert llamadas == []


def test_abre_navegador_si_el_servidor_arranco():
    evento = threading.Event()
    evento.set()
    llamadas = []

    lanzador.abrir_navegador_si_corresponde(evento, abrir=llamadas.append)

    assert llamadas == ["http://127.0.0.1:8000"]


def test_detecta_flag_restore_con_ruta():
    assert lanzador._ruta_zip_restore_o_none(["lanzador.py", "--restore", "C:\\backup.zip"]) == "C:\\backup.zip"


def test_no_detecta_restore_en_ejecucion_normal():
    assert lanzador._ruta_zip_restore_o_none(["lanzador.py"]) is None


def test_no_detecta_restore_si_falta_la_ruta():
    assert lanzador._ruta_zip_restore_o_none(["lanzador.py", "--restore"]) is None


def test_modo_restore_invoca_restore_y_devuelve_0_si_tiene_exito(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *_: None)
    llamadas = []

    def ejecutar_falso(ruta_zip):
        llamadas.append(ruta_zip)

    codigo = lanzador._modo_restore("C:\\backup.zip", lambda: ejecutar_falso)

    assert codigo == 0
    assert llamadas == ["C:\\backup.zip"]
    assert "completado" in capsys.readouterr().out.lower()


def test_modo_restore_error_restore_no_imprime_traceback(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *_: None)

    def ejecutar_falso(ruta_zip):
        raise ErrorRestore("el backup no es valido")

    codigo = lanzador._modo_restore("C:\\backup.zip", lambda: ejecutar_falso)

    salida = capsys.readouterr()
    assert codigo == 1
    assert "el backup no es valido" in salida.out
    assert "Traceback" not in salida.out
    assert "Traceback" not in salida.err


def test_modo_restore_error_inesperado_conserva_traceback(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *_: None)

    def ejecutar_falso(ruta_zip):
        raise RuntimeError("fallo inesperado durante restore")

    codigo = lanzador._modo_restore("C:\\backup.zip", lambda: ejecutar_falso)

    salida = capsys.readouterr()
    assert codigo == 1
    assert "RuntimeError" in salida.err
    assert "fallo inesperado durante restore" in salida.err
