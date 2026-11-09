"""Cierre operativo E5: custodia de la historia fiscal fuera de `data/`. Demuestra la vía de pérdida
silenciosa (la base se reemplaza por una copia anterior por fuera del restore seguro) y que la aplicación
no arranca sobre esa base, sin inventar nada y con salida auditada para el técnico."""

import asyncio
import json
import sqlite3

import pytest

import config
import db.conexion as modulo_conexion
import interfaces.web.app as modulo_app
import services.servicio_backup as modulo_servicio_backup
from db.repositorios import auditoria as repositorio_auditoria
from domain.fiscal.historia import ComprobanteConocido, HistoriaFiscal
from excepciones import HistoriaFiscalPerdidaError
from services.fiscal import servicio_custodia
from tests import utilidades_fiscales as uf

CAE = "71234567890123"


@pytest.fixture
def con_comprobante(caja_abierta):
    uf.guardar_perfil_validado()
    venta = uf.vender([(uf.producto(12100, uf.IVA_21), 1)])
    comprobante_id = uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))
    uf.autorizar(comprobante_id, 7, CAE)
    return comprobante_id


def _reemplazar_base_por_una_vacia(tmp_path, monkeypatch):
    """Simula el bypass: una copia anterior de la base (sin los comprobantes) puesta en el lugar de la actual
    por fuera de `servicio_restore` (versión vieja del programa, copia manual)."""
    nueva = tmp_path / "data_vieja" / "kiosco.db"
    nueva.parent.mkdir()
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", nueva)
    modulo_conexion.inicializar_base_datos()


class TestCustodia:
    def test_sin_historia_no_se_crea_archivo_ni_se_bloquea(self, base_datos_temporal):
        assert servicio_custodia.registrar_historia_actual() is None
        assert not servicio_custodia.ruta_custodia().exists()
        servicio_custodia.verificar_al_iniciar()

    def test_registra_la_historia_fuera_de_data_y_la_verificacion_pasa(self, con_comprobante):
        historia = servicio_custodia.registrar_historia_actual()
        ruta = servicio_custodia.ruta_custodia()
        assert ruta.is_file() and ruta.parent == config.DIRECTORIO_SECRETOS_FISCALES
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        assert datos["comprobantes"][0]["numero"] == 7 and datos["comprobantes"][0]["codigo_autorizacion"] == CAE
        assert servicio_custodia.leer_custodia() == historia
        servicio_custodia.verificar_al_iniciar()

    def test_la_base_reemplazada_por_una_copia_anterior_no_arranca(self, con_comprobante, tmp_path, monkeypatch):
        servicio_custodia.registrar_historia_actual()
        _reemplazar_base_por_una_vacia(tmp_path, monkeypatch)
        with pytest.raises(HistoriaFiscalPerdidaError, match="1 comprobante\\(s\\) autorizado\\(s\\) con CAE"):
            servicio_custodia.verificar_al_iniciar()
        # El lifespan de la app corta el arranque con ese mismo error y deja la causa en app.state.
        monkeypatch.setattr(modulo_servicio_backup, "ejecutar_backup_automatico_si_corresponde", lambda *a, **k: None)

        async def _arrancar():
            async with modulo_app.lifespan(modulo_app.app):
                pass

        with pytest.raises(HistoriaFiscalPerdidaError):
            asyncio.run(_arrancar())
        assert isinstance(modulo_app.app.state.error_de_inicio, HistoriaFiscalPerdidaError)
        assert "no inicia" in str(modulo_app.app.state.error_de_inicio)

    def test_un_autorizado_degradado_a_incierto_tambien_bloquea(self, con_comprobante):
        custodiada = servicio_custodia.registrar_historia_actual()
        degradada = HistoriaFiscal(
            comprobantes=tuple(
                ComprobanteConocido(c.ambiente, c.cuit_emisor, c.punto_venta, c.tipo_comprobante, c.numero, "INCIERTO", None)
                for c in custodiada.comprobantes
            )
        )
        # La base sigue AUTORIZADA pero la custodia dice INCIERTO: eso es conocimiento nuevo, no pérdida.
        servicio_custodia._escribir(degradada)
        servicio_custodia.verificar_al_iniciar()
        # Al revés (custodia AUTORIZADO, base INCIERTO) sí se perdió el CAE conocido.
        servicio_custodia._escribir(custodiada)
        with modulo_conexion.obtener_conexion() as conexion:
            conexion.execute("PRAGMA foreign_keys = OFF")
        con = sqlite3.connect(modulo_conexion.RUTA_BASE_DATOS)
        try:
            con.execute("UPDATE comprobantes_fiscales SET estado = 'INCIERTO', codigo_autorizacion = NULL, vencimiento_autorizacion = NULL, fecha_proceso = NULL")
            con.commit()
        except sqlite3.DatabaseError:
            con.close()
            pytest.skip("la base no admite degradar un autorizado por SQL directo (trigger): la custodia se prueba con la base vacía")
        con.close()
        with pytest.raises(HistoriaFiscalPerdidaError):
            servicio_custodia.verificar_al_iniciar()

    def test_el_tecnico_acepta_la_perdida_y_queda_auditado(self, con_comprobante, tmp_path, monkeypatch):
        servicio_custodia.registrar_historia_actual()
        _reemplazar_base_por_una_vacia(tmp_path, monkeypatch)
        mensaje = servicio_custodia.aceptar_historia_actual()
        assert "1 comprobante(s) autorizado(s) con CAE" in mensaje
        entradas = repositorio_auditoria.listar(accion="HISTORIA_FISCAL_ACEPTADA")
        assert len(entradas) == 1 and "dada por perdida" in entradas[0].resumen
        servicio_custodia.verificar_al_iniciar()  # ya no bloquea
        assert servicio_custodia.leer_custodia() is not None and servicio_custodia.leer_custodia().vacia

    def test_una_custodia_ilegible_no_impide_el_arranque(self, con_comprobante):
        ruta = servicio_custodia.ruta_custodia()
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text("{no es json", encoding="utf-8")
        assert servicio_custodia.leer_custodia() is None
        servicio_custodia.verificar_al_iniciar()
        servicio_custodia.registrar_historia_actual()
        assert servicio_custodia.leer_custodia() is not None

    def test_actualizar_sin_fallar_nunca_lanza(self, con_comprobante, monkeypatch):
        monkeypatch.setattr(servicio_custodia, "_escribir", lambda *_: (_ for _ in ()).throw(OSError("disco lleno")))
        servicio_custodia.actualizar_sin_fallar()

    def test_el_lanzador_reconoce_el_comando(self):
        from lanzador import _comando_de_mantenimiento

        assert _comando_de_mantenimiento(["exe", "--aceptar-historia-fiscal"]) == ("--aceptar-historia-fiscal", None)
