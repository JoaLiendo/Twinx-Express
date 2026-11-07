"""Asistencia local (V1.18): la información de soporte describe la instalación sin exponer secretos ni datos
de negocio, y el diagnóstico exportable es un ZIP sanitizado sin la base de datos."""

import io
import json
import zipfile

import config
import db.conexion as modulo_conexion
from logging_config import NOMBRE_ARCHIVO_LOG
from services import servicio_soporte, servicio_stock
from version import VERSION


def _log_con_secretos(tmp_path, monkeypatch) -> None:
    directorio = tmp_path / "logs"
    directorio.mkdir()
    (directorio / NOMBRE_ARCHIVO_LOG).write_text(
        "2026-11-07 10:00:00 [INFO] lanzador: Twinx Express arrancó\n"
        "2026-11-07 10:01:00 [WARNING] services.servicio_backup: disco casi lleno\n"
        "2026-11-07 10:02:00 [ERROR] interfaces.web: fallo con token=abc123 y password=hola\n"
        '2026-11-07 10:03:00 [INFO] uvicorn.access: 127.0.0.1:50000 - "GET /clientes?q=Gomez+Ana HTTP/1.1" 200\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "DIRECTORIO_LOGS", directorio)


def test_informacion_describe_la_instalacion_sin_contenido_de_negocio(base_datos_temporal, tmp_path, monkeypatch):
    _log_con_secretos(tmp_path, monkeypatch)
    servicio_stock.registrar_producto("1", "Alfajor secreto", 100, 200, stock_actual=5)

    info = servicio_soporte.obtener_informacion()

    assert info.version == VERSION and info.ejecutable is False
    assert info.ruta_base == str(base_datos_temporal)
    assert info.base.existe and info.base.integra and info.base.integridad == "ok"
    assert info.base.migraciones_aplicadas == len(list(config.DIRECTORIO_MIGRACIONES.glob("*.sql")))
    assert info.base.ultima_migracion.endswith(".sql") and not info.base.migraciones_pendientes
    assert info.base.contadores["productos_activos"] == 1 and info.base.contadores["ventas"] == 0
    assert info.ultimo_backup is None and info.alerta_backup is None
    # Solo avisos (WARNING/ERROR) y con los secretos enmascarados.
    assert len(info.ultimos_avisos) == 2
    assert "token=***" in info.ultimos_avisos[1] and "password=***" in info.ultimos_avisos[1]
    assert "abc123" not in " ".join(info.ultimos_avisos) and "hola" not in " ".join(info.ultimos_avisos)
    assert "Alfajor secreto" not in json.dumps(info.__dict__, default=str)


def test_sin_log_ni_base_sigue_informando(tmp_path, monkeypatch):
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", tmp_path / "inexistente.db")
    monkeypatch.setattr(config, "DIRECTORIO_LOGS", tmp_path / "sin_logs")

    info = servicio_soporte.obtener_informacion()

    assert info.base.existe is False and info.base.integridad == "sin base"
    assert info.log_existe is False and info.ultimos_avisos == []


def test_base_danada_se_informa_sin_fallar(tmp_path, monkeypatch):
    ruta = tmp_path / "rota.db"
    ruta.write_bytes(b"esto no es una base sqlite" * 100)
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
    monkeypatch.setattr(config, "DIRECTORIO_LOGS", tmp_path / "logs")

    info = servicio_soporte.obtener_informacion()

    assert info.base.existe and not info.base.integra
    assert info.base.error is not None


def test_diagnostico_es_un_zip_sanitizado_sin_la_base(base_datos_temporal, tmp_path, monkeypatch):
    _log_con_secretos(tmp_path, monkeypatch)

    contenido = servicio_soporte.exportar_diagnostico()

    with zipfile.ZipFile(io.BytesIO(contenido)) as zip_diagnostico:
        nombres = set(zip_diagnostico.namelist())
        assert nombres == {"diagnostico.json", "migraciones.txt", "registro_enmascarado.log"}
        diagnostico = json.loads(zip_diagnostico.read("diagnostico.json"))
        registro = zip_diagnostico.read("registro_enmascarado.log").decode("utf-8")
        migraciones = zip_diagnostico.read("migraciones.txt").decode("utf-8")
    assert diagnostico["version"] == VERSION and diagnostico["base"]["integridad"] == "ok"
    assert "arrancó" in registro and "abc123" not in registro and "token=***" in registro
    # El access log lleva búsquedas de clientes y productos en la URL: no viaja en el diagnóstico.
    assert "uvicorn.access" not in registro and "Gomez" not in registro
    assert migraciones.strip().endswith(".sql")
    assert servicio_soporte.nombre_de_diagnostico().startswith(f"diagnostico_twinx_{VERSION}_")
