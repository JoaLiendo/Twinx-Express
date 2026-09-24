"""V1.1: log persistente local (`logging_config.py`)."""

import logging

import pytest

import config
import logging_config


@pytest.fixture
def logging_aislado():
    """Deja los loggers globales como estaban al terminar el test."""
    raiz = logging.getLogger()
    nivel_original = raiz.level
    handlers_originales = list(raiz.handlers)
    yield
    for logger in (raiz, logging.getLogger("uvicorn"), logging.getLogger("uvicorn.access")):
        for handler in list(logger.handlers):
            if handler.get_name() == logging_config._NOMBRE_DEL_HANDLER:
                logger.removeHandler(handler)
                handler.close()
    raiz.handlers = handlers_originales
    raiz.setLevel(nivel_original)


def _vaciar(logger_name: str = "") -> None:
    for handler in logging.getLogger(logger_name).handlers:
        handler.flush()


def test_crea_el_directorio_y_escribe_timestamp_nivel_y_mensaje(tmp_path, logging_aislado):
    directorio = tmp_path / "logs"

    ruta = logging_config.configurar_logging(directorio)
    logging.getLogger("services.prueba").info("Venta registrada: id=7")
    logging.getLogger("services.prueba").error("Algo falló")
    _vaciar()

    assert ruta == directorio / logging_config.NOMBRE_ARCHIVO_LOG
    lineas = ruta.read_text(encoding="utf-8").splitlines()
    assert any(l[:4].isdigit() and "[INFO] services.prueba: Venta registrada: id=7" in l for l in lineas)
    assert any("[ERROR] services.prueba: Algo falló" in l for l in lineas)


def test_es_idempotente_no_duplica_el_handler_ni_las_lineas(tmp_path, logging_aislado):
    directorio = tmp_path / "logs"

    logging_config.configurar_logging(directorio)
    ruta = logging_config.configurar_logging(directorio)
    logging.getLogger("x").info("una sola vez")
    _vaciar()

    contenido = ruta.read_text(encoding="utf-8")
    assert contenido.count("una sola vez") == 1
    manejadores = [h for h in logging.getLogger().handlers if h.get_name() == logging_config._NOMBRE_DEL_HANDLER]
    assert len(manejadores) == 1


def test_tambien_registra_los_loggers_de_uvicorn_que_no_propagan(tmp_path, logging_aislado):
    ruta = logging_config.configurar_logging(tmp_path / "logs")

    logging.getLogger("uvicorn").propagate = False
    logging.getLogger("uvicorn.error").info("Application startup failed. Exiting.")
    logging.getLogger("uvicorn.access").info('127.0.0.1 - "GET /ventas HTTP/1.1" 200')
    _vaciar("uvicorn")

    contenido = ruta.read_text(encoding="utf-8")
    assert "Application startup failed" in contenido
    assert "GET /ventas" in contenido


def test_no_registra_contrasenas_tokens_ni_cookies_aunque_lleguen_al_mensaje(tmp_path, logging_aislado):
    ruta = logging_config.configurar_logging(tmp_path / "logs")

    logging.getLogger("x").warning("login password=SuperSecreta123 usuario=ana")
    logging.getLogger("x").warning("cookie: abc123DEF token=tok_9999 hash=pbkdf2$600000$zzz")
    logging.getLogger("x").warning("clave: %s", "otra-clave-secreta")
    _vaciar()

    contenido = ruta.read_text(encoding="utf-8")
    for secreto in ("SuperSecreta123", "abc123DEF", "tok_9999", "pbkdf2$600000$zzz", "otra-clave-secreta"):
        assert secreto not in contenido
    assert "usuario=ana" in contenido  # lo no sensible se conserva


def test_rota_y_no_crece_indefinidamente(tmp_path, monkeypatch, logging_aislado):
    monkeypatch.setattr(logging_config, "TAMANO_MAXIMO_BYTES", 500)
    monkeypatch.setattr(logging_config, "ARCHIVOS_ROTADOS", 2)
    directorio = tmp_path / "logs"
    logging_config.configurar_logging(directorio)

    for i in range(300):
        logging.getLogger("x").info("línea de relleno número %s con texto suficiente", i)
    _vaciar()

    archivos = sorted(p.name for p in directorio.iterdir())
    assert len(archivos) == 3  # el actual + 2 rotados, nunca más
    assert all(p.stat().st_size < 1_000 for p in directorio.iterdir())


def test_los_logs_viven_junto_a_data_y_backups():
    assert config.DIRECTORIO_LOGS == config.DIRECTORIO_DATA.parent / "logs"
    assert config.DIRECTORIO_LOGS.name == "logs"
