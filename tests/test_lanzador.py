"""Tests de `lanzador.py`: manejo de errores de arranque y apertura
condicional del navegador.

No se ejercita el hilo real ni uvicorn/FastAPI: `iniciar_servidor`
recibe la función de arranque como parámetro (`ejecutar`) para poder
reemplazarla por un doble de prueba, y `abrir_navegador_cuando_este_listo`
recibe `abrir` y el reloj de la misma forma.
"""
import threading

import pytest

import lanzador
from config import ErrorMigracionDatos
from excepciones import ErrorInicioServidor, ErrorRestore, PuertoOcupadoError


@pytest.fixture(autouse=True)
def _sin_log_real(monkeypatch):
    """Los tests del lanzador nunca deben crear el archivo de log real."""
    monkeypatch.setattr(lanzador, "_configurar_logging", lambda: None)


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
    llamadas_input = []
    monkeypatch.setattr("builtins.input", lambda *_: llamadas_input.append(1))
    evento = threading.Event()

    def ejecutar_falso(_evento):
        raise PuertoOcupadoError("El puerto 8000 ya está en uso.")

    lanzador.iniciar_servidor(evento, ejecutar=ejecutar_falso)

    salida = capsys.readouterr()
    assert not evento.is_set()
    assert "ya está ejecutándose o el puerto 8000 está ocupado" in salida.out
    assert "Traceback" not in salida.err
    assert llamadas_input == [1]


def test_iniciar_servidor_error_de_inicio_no_se_reporta_como_puerto_ocupado(monkeypatch, capsys):
    """A1 (V1.1): un fallo real de arranque (DB corrupta, migración, seed,
    backup) muestra la causa, dónde están los datos y cómo restaurar -- y
    nunca el mensaje de puerto ocupado."""
    llamadas_input = []
    monkeypatch.setattr("builtins.input", lambda *_: llamadas_input.append(1))
    evento = threading.Event()

    def ejecutar_falso(_evento):
        raise ErrorInicioServidor("ErrorBaseDatos: file is not a database")

    lanzador.iniciar_servidor(evento, ejecutar=ejecutar_falso)

    salida = capsys.readouterr().out
    assert not evento.is_set()
    assert "NO PUDO INICIAR" in salida
    assert "file is not a database" in salida
    assert "Restaurar_backup.bat" in salida
    assert "Backups:" in salida
    assert "puerto" not in salida.lower()
    assert llamadas_input == [1]


def test_puerto_ocupado_no_abre_navegador():
    evento = threading.Event()
    llamadas = []

    abrio = lanzador.abrir_navegador_cuando_este_listo(evento, abrir=llamadas.append, servidor_activo=lambda: False)

    assert abrio is False
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

    monkeypatch.setattr(lanzador, "_puerto_ocupado", lambda *a, **k: False)
    monkeypatch.setattr("uvicorn.Config", lambda *a, **k: None)
    monkeypatch.setattr("uvicorn.Server", ServidorFalsoQueArranca)

    evento = threading.Event()
    lanzador._importar_y_ejecutar_servidor(evento)

    assert evento.is_set()


class _ServidorFalsoQueFalla:
    """Uvicorn 0.52.4 termina con `SystemExit` tanto ante un bind fallido
    como ante un fallo del lifespan, sin haber marcado `started`."""

    def __init__(self, config):
        self.started = False

    def run(self):
        raise SystemExit(3)


def test_fallo_real_de_startup_no_se_reporta_como_puerto_ocupado(monkeypatch):
    """A1: con el puerto libre, un `SystemExit` de uvicorn es un fallo del
    lifespan y conserva la causa real registrada en `app.state`."""
    from interfaces.web.app import app

    causa = RuntimeError("base de datos ilegible")
    monkeypatch.setattr(app.state, "error_de_inicio", causa, raising=False)
    monkeypatch.setattr(lanzador, "_puerto_ocupado", lambda *a, **k: False)
    monkeypatch.setattr("uvicorn.Config", lambda *a, **k: None)
    monkeypatch.setattr("uvicorn.Server", _ServidorFalsoQueFalla)

    evento = threading.Event()
    with pytest.raises(ErrorInicioServidor) as info:
        lanzador._importar_y_ejecutar_servidor(evento)

    assert "base de datos ilegible" in str(info.value)
    assert info.value.__cause__ is causa
    assert not evento.is_set()


def test_fallo_real_de_bind_si_se_identifica_como_puerto_ocupado(monkeypatch):
    """Si tras el `SystemExit` el puerto está ocupado, es un bind fallido."""
    estados = iter([False, True])  # libre en el pre-chequeo, ocupado después
    monkeypatch.setattr(lanzador, "_puerto_ocupado", lambda *a, **k: next(estados))
    monkeypatch.setattr("uvicorn.Config", lambda *a, **k: None)
    monkeypatch.setattr("uvicorn.Server", _ServidorFalsoQueFalla)

    evento = threading.Event()
    with pytest.raises(PuertoOcupadoError):
        lanzador._importar_y_ejecutar_servidor(evento)

    assert not evento.is_set()


def test_puerto_ocupado_en_el_prechequeo_no_importa_la_app_ni_arranca_uvicorn(monkeypatch):
    """Una segunda instancia no debe correr migraciones/backup: se detiene
    antes de construir el servidor."""
    construidos = []
    monkeypatch.setattr(lanzador, "_puerto_ocupado", lambda *a, **k: True)
    monkeypatch.setattr("uvicorn.Server", lambda config: construidos.append(config))

    with pytest.raises(PuertoOcupadoError):
        lanzador._importar_y_ejecutar_servidor(threading.Event())

    assert construidos == []


def test_puerto_ocupado_detecta_un_puerto_realmente_en_uso():
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as ocupante:
        ocupante.bind(("127.0.0.1", 0))
        ocupante.listen()
        puerto = ocupante.getsockname()[1]

        assert lanzador._puerto_ocupado("127.0.0.1", puerto) is True

    assert lanzador._puerto_ocupado("127.0.0.1", puerto) is False


class _EventoQueSeMarcaTras:
    """Doble de `threading.Event` cuyo `wait` avanza un reloj falso y
    responde `True` recién en la N-ésima espera."""

    def __init__(self, reloj, marcar_en_la_espera: int | None) -> None:
        self._reloj = reloj
        self._marcar_en = marcar_en_la_espera
        self.esperas = 0

    def wait(self, timeout):
        self.esperas += 1
        self._reloj.avanzar(timeout)
        return self._marcar_en is not None and self.esperas >= self._marcar_en


def test_abre_navegador_si_el_servidor_ya_arranco():
    evento = threading.Event()
    evento.set()
    llamadas = []

    abrio = lanzador.abrir_navegador_cuando_este_listo(evento, abrir=llamadas.append)

    assert abrio is True
    assert llamadas == ["http://127.0.0.1:8000"]


def test_a3_un_arranque_lento_igual_abre_el_navegador(reloj_falso):
    """Arranque que tarda ~12 s (48 esperas de 0,25 s): la versión anterior,
    con un único `sleep(1.5)`, no habría abierto nada."""
    evento = _EventoQueSeMarcaTras(reloj_falso, marcar_en_la_espera=48)
    llamadas = []

    abrio = lanzador.abrir_navegador_cuando_este_listo(evento, abrir=llamadas.append, reloj=reloj_falso)

    assert abrio is True
    assert llamadas == ["http://127.0.0.1:8000"]
    assert reloj_falso.ahora - 1000.0 < 30


def test_a3_no_espera_indefinidamente_si_el_servidor_nunca_arranca(reloj_falso):
    evento = _EventoQueSeMarcaTras(reloj_falso, marcar_en_la_espera=None)
    llamadas = []

    abrio = lanzador.abrir_navegador_cuando_este_listo(evento, abrir=llamadas.append, reloj=reloj_falso)

    assert abrio is False
    assert llamadas == []
    assert 30 <= reloj_falso.ahora - 1000.0 < 31
    assert evento.esperas <= 121  # sin polling agresivo: intervalos de 0,25 s


def test_a3_deja_de_esperar_apenas_el_hilo_del_servidor_termina(reloj_falso):
    evento = _EventoQueSeMarcaTras(reloj_falso, marcar_en_la_espera=None)
    llamadas = []

    abrio = lanzador.abrir_navegador_cuando_este_listo(
        evento, abrir=llamadas.append, servidor_activo=lambda: False, reloj=reloj_falso
    )

    assert abrio is False
    assert evento.esperas == 1
    assert llamadas == []


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
