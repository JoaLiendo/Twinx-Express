"""Cierre operativo F4: backup diario a una hora fija, solo con la aplicación abierta. Regla pura, servicio,
configuración auditada, hilo que se detiene y pantalla."""

import threading
import zipfile
from datetime import datetime, time, timedelta

import pytest

import services.servicio_backup as modulo_backup
from db.repositorios import auditoria as repositorio_auditoria
from excepciones import DatosInvalidosError
from services import servicio_backup_programado as programado
from services.control_escrituras import ControlEscrituras
from tests.test_interfaces_web._asgi_cliente import solicitud
from tests.utilidades_clientes import usuario_logueado


class TestRegla:
    def test_sin_hora_nunca_corresponde(self):
        assert not programado.corresponde(None, datetime(2026, 11, 9, 23, 0), None)

    def test_antes_de_la_hora_no_corresponde(self):
        assert not programado.corresponde(time(21, 30), datetime(2026, 11, 9, 21, 29), None)

    def test_a_la_hora_sin_backup_corresponde(self):
        assert programado.corresponde(time(21, 30), datetime(2026, 11, 9, 21, 30), None)
        assert programado.corresponde(time(21, 30), datetime(2026, 11, 9, 23, 59), datetime(2026, 11, 8, 22, 0))

    def test_un_backup_posterior_a_la_hora_de_hoy_lo_cubre(self):
        assert not programado.corresponde(time(21, 30), datetime(2026, 11, 9, 22, 0), datetime(2026, 11, 9, 21, 45))
        # Uno anterior a la hora de hoy (por ejemplo, el automático de la mañana) no lo cubre.
        assert programado.corresponde(time(21, 30), datetime(2026, 11, 9, 22, 0), datetime(2026, 11, 9, 9, 0))

    @pytest.mark.parametrize("texto, esperado", [("21:30", time(21, 30)), (" 07:05 ", time(7, 5)), ("", None), (None, None)])
    def test_validar_hora(self, texto, esperado):
        assert programado.validar_hora(texto) == esperado

    @pytest.mark.parametrize("texto", ["25:00", "7:5", "2130", "21:60", "abc"])
    def test_hora_invalida(self, texto):
        with pytest.raises(DatosInvalidosError):
            programado.validar_hora(texto)


class TestConfiguracion:
    def test_guardar_audita_y_quitar_desactiva(self, base_datos_temporal):
        usuario, _ = usuario_logueado("OWNER", "duenio")
        assert programado.obtener_hora_programada() is None
        assert programado.guardar_hora_programada("21:30", usuario.id) == time(21, 30)
        assert programado.obtener_hora_programada() == time(21, 30)
        programado.guardar_hora_programada("21:30", usuario.id)  # sin cambios: no audita de nuevo
        assert programado.guardar_hora_programada("", usuario.id) is None
        entradas = repositorio_auditoria.listar(accion="CONFIGURACION_BACKUP_CAMBIADA")
        assert [e.resumen for e in entradas][:2] == ["Backup diario programado: desactivado", "Backup diario programado: 21:30"]

    def test_pantalla_y_formulario(self, base_datos_temporal):
        usuario, cookies = usuario_logueado("OWNER", "duenio")
        html = solicitud("GET", "/backup", cookies=cookies).texto
        assert 'id="backup-programado"' in html and "Desactivado" in html and "Solo funciona con Twinx abierto" in html
        respuesta = solicitud("POST", "/backup/hora-programada", cookies=cookies, formulario={"hora": "20:00"})
        assert respuesta.status == 303 and "tipo=success" in respuesta.header("location")
        assert 'value="20:00"' in solicitud("GET", "/backup", cookies=cookies).texto
        invalida = solicitud("POST", "/backup/hora-programada", cookies=cookies, formulario={"hora": "99:99"})
        assert invalida.status < 500 and programado.obtener_hora_programada() == time(20, 0)


class TestEjecucion:
    def test_ejecuta_a_la_hora_y_no_repite(self, base_datos_temporal, tmp_path, monkeypatch):
        usuario, _ = usuario_logueado("OWNER", "duenio")
        ahora = datetime.now()
        if ahora.hour == 0 and ahora.minute < 2:
            pytest.skip("alrededor de medianoche la hora programada caería en el día anterior")
        # La hora programada ya pasó hace un minuto (la fecha del backup sale del nombre real del archivo).
        hora = (ahora - timedelta(minutes=1)).strftime("%H:%M")
        programado.guardar_hora_programada(hora, usuario.id)
        destino = tmp_path / "backups"
        antes = ahora - timedelta(minutes=2)
        assert programado.ejecutar_si_corresponde(destino, ControlEscrituras(), ahora=antes) is None
        ruta = programado.ejecutar_si_corresponde(destino, ControlEscrituras())
        assert ruta is not None and zipfile.is_zipfile(ruta) and ruta.name.startswith("KioscoApp_backup_")
        assert programado.ejecutar_si_corresponde(destino, ControlEscrituras()) is None
        assert len(list(destino.glob("*.zip"))) == 1
        assert any(f"programado {hora}" in e.resumen for e in repositorio_auditoria.listar(accion="BACKUP_AUTOMATICO_OK"))

    def test_sin_hora_configurada_no_hace_nada(self, base_datos_temporal, tmp_path):
        destino = tmp_path / "backups"
        assert programado.ejecutar_si_corresponde(destino, ControlEscrituras()) is None
        assert not destino.exists()

    def test_un_error_del_backup_no_propaga_y_queda_auditado(self, base_datos_temporal, tmp_path, monkeypatch):
        usuario, _ = usuario_logueado("OWNER", "duenio")
        programado.guardar_hora_programada("00:00", usuario.id)

        def falla(*_a, **_k):
            from excepciones import ErrorBackup

            raise ErrorBackup("disco lleno")

        monkeypatch.setattr(modulo_backup, "crear_backup", falla)
        assert programado.ejecutar_si_corresponde(tmp_path / "backups", ControlEscrituras()) is None
        assert any("disco lleno" in e.resumen for e in repositorio_auditoria.listar(accion="BACKUP_ERROR"))

    def test_el_hilo_sondea_y_se_detiene(self, base_datos_temporal, tmp_path, monkeypatch):
        llamadas = []
        monkeypatch.setattr(programado, "ejecutar_si_corresponde", lambda *a, **k: llamadas.append(1))
        detener = threading.Event()
        hilo = threading.Thread(target=programado.bucle, args=(tmp_path, ControlEscrituras(), detener, 0.01), daemon=True)
        hilo.start()
        for _ in range(200):
            if len(llamadas) >= 2:
                break
            threading.Event().wait(0.01)
        detener.set()
        hilo.join(timeout=2)
        assert not hilo.is_alive() and len(llamadas) >= 2
