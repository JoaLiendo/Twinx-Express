"""V1.16-G: contingencia CAEA contra el ARCA falso. Nunca se emite bajo CAEA ante un resultado incierto;
lo emitido queda pendiente de informar (persistido) hasta que ARCA lo acepte."""

from datetime import date

import pytest

from db.conexion import obtener_conexion
from db.repositorios import fiscal as repositorio_fiscal
from domain.fiscal import caea as dominio_caea
from domain.fiscal import qr
from excepciones import ErrorBaseDatos, FiscalizacionNoDisponibleError, PermisoDenegadoError
from services.fiscal import servicio_caea, servicio_facturacion, servicio_panel, servicio_parametros
from tests import utilidades_fiscales as uf

HOY = date.today()


class TestPeriodos:
    def test_periodo_de(self):
        assert dominio_caea.periodo_de(date(2026, 11, 15)) == (202611, 1)
        assert dominio_caea.periodo_de(date(2026, 11, 16)) == (202611, 2)

    def test_rango_y_bisiesto(self):
        assert dominio_caea.rango(202802, 2) == (date(2028, 2, 16), date(2028, 2, 29))
        assert dominio_caea.rango(202611, 1) == (date(2026, 11, 1), date(2026, 11, 15))

    def test_siguiente_cruza_el_anio(self):
        assert dominio_caea.siguiente(202612, 2) == (202701, 1)
        assert dominio_caea.siguiente(202612, 1) == (202612, 2)

    def test_solicitables_incluye_el_proximo_en_los_5_dias_previos(self):
        assert dominio_caea.solicitables(date(2026, 11, 10)) == [(202611, 1)]
        assert dominio_caea.solicitables(date(2026, 11, 11)) == [(202611, 1), (202611, 2)]
        assert dominio_caea.solicitables(date(2026, 12, 27)) == [(202612, 2), (202701, 1)]


@pytest.fixture
def owner(base_datos_temporal):
    return uf.crear_owner()


@pytest.fixture
def contingencia(caja_abierta, arca_falso, owner):
    uf.guardar_perfil_validado(punto_venta_caea=4, contingencia_caea=True)
    servicio_parametros.refrescar(arca_falso)
    servicio_caea.solicitar(owner)
    return arca_falso


def _venta():
    return uf.vender([(uf.producto(12100, uf.IVA_21), 1)])


class TestSolicitud:
    def test_obtiene_y_es_idempotente(self, contingencia, owner):
        llamadas = contingencia.llamadas.count("FECAEASolicitar")
        assert servicio_caea.solicitar(owner)[0].codigo.startswith("31")
        assert contingencia.llamadas.count("FECAEASolicitar") == llamadas

    def test_recupera_el_que_arca_ya_habia_otorgado(self, caja_abierta, arca_falso, owner):
        uf.guardar_perfil_validado(punto_venta_caea=4, contingencia_caea=True)
        periodo, orden = dominio_caea.periodo_de(HOY)
        otorgado = arca_falso.solicitar_caea(periodo, orden)  # pedido antes (p. ej. base restaurada)
        assert servicio_caea.solicitar(owner)[0].codigo == otorgado.codigo
        assert "FECAEAConsultar" in arca_falso.llamadas

    def test_cashier_no_solicita(self, contingencia):
        with pytest.raises(PermisoDenegadoError):
            servicio_caea.solicitar(uf.crear_cashier())


class TestEmisionEnContingencia:
    def test_arca_caido_emite_bajo_caea(self, contingencia, owner):
        contingencia.caido = True
        venta = _venta()
        comprobante = servicio_caea.fiscalizar_con_contingencia(venta.id, owner)
        assert comprobante.estado == "CAEA_PENDIENTE_INFORMAR" and comprobante.modalidad == "CAEA"
        assert comprobante.punto_venta == 4 and comprobante.numero == 1 and comprobante.codigo_autorizacion.startswith("31")
        assert comprobante.fecha_hora_generacion and comprobante.autorizado
        assert qr.datos_qr(comprobante)["tipoCodAut"] == "A"
        estados_venta = [c.estado for c in servicio_facturacion.comprobantes_de_venta(venta.id)]
        assert estados_venta == ["DESCARTADO", "CAEA_PENDIENTE_INFORMAR"]

    def test_incierto_nunca_cae_a_caea(self, contingencia, owner):
        contingencia.caer_tras_cae = True
        venta = _venta()
        comprobante = servicio_caea.fiscalizar_con_contingencia(venta.id, owner)
        assert comprobante.estado == "INCIERTO"
        assert all(c.modalidad == "CAE" for c in servicio_facturacion.comprobantes_de_venta(venta.id))

    def test_sin_caea_vigente_no_hay_factura(self, caja_abierta, arca_falso, owner):
        uf.guardar_perfil_validado(punto_venta_caea=4, contingencia_caea=True)
        servicio_parametros.refrescar(arca_falso)
        servicio_caea.solicitar(owner, hoy=date(2026, 10, 1))  # CAEA vencido: solo cubre del 1 al 15/10
        arca_falso.caido = True
        venta = _venta()
        with pytest.raises(FiscalizacionNoDisponibleError, match="no disponible"):
            servicio_caea.fiscalizar_con_contingencia(venta.id, owner)
        assert [c.estado for c in servicio_facturacion.comprobantes_de_venta(venta.id)] == ["PENDIENTE"]

    def test_contingencia_deshabilitada(self, caja_abierta, arca_falso, owner):
        uf.guardar_perfil_validado(punto_venta_caea=4, contingencia_caea=False)
        servicio_parametros.refrescar(arca_falso)
        arca_falso.caido = True
        with pytest.raises(FiscalizacionNoDisponibleError):
            servicio_caea.fiscalizar_con_contingencia(_venta().id, owner)

    def test_numeracion_continua_desde_la_base_de_arca(self, caja_abierta, arca_falso, owner):
        uf.guardar_perfil_validado(punto_venta_caea=4, contingencia_caea=True)
        servicio_parametros.refrescar(arca_falso)
        arca_falso.emitir_externo(4, 6)  # ARCA ya tenía el 1 en el punto de venta CAEA
        servicio_caea.solicitar(owner)
        arca_falso.caido = True
        numeros = [servicio_caea.fiscalizar_con_contingencia(_venta().id, owner).numero for _ in range(2)]
        assert numeros == [2, 3]

    def test_doble_click_en_contingencia_no_duplica(self, contingencia, owner):
        contingencia.caido = True
        venta = _venta()
        primero = servicio_caea.fiscalizar_con_contingencia(venta.id, owner)
        segundo = servicio_caea.fiscalizar_con_contingencia(venta.id, owner)
        assert primero.id == segundo.id


class TestInforme:
    def test_informa_al_volver_arca_y_sobrevive_reinicio(self, contingencia, owner):
        contingencia.caido = True
        comprobante = servicio_caea.fiscalizar_con_contingencia(_venta().id, owner)
        assert servicio_caea.informar_pendientes() == 0  # ARCA sigue caído: queda pendiente
        assert servicio_facturacion.obtener(comprobante.id).estado == "CAEA_PENDIENTE_INFORMAR"
        assert any("CAEA" in aviso for aviso in servicio_panel.alerta_fiscal())
        contingencia.caido = False
        # "Reinicio": nada vive en memoria, todo sale de la base.
        assert servicio_caea.informar_pendientes() == 1
        assert servicio_facturacion.obtener(comprobante.id).estado == "CAEA_INFORMADO"
        assert contingencia.registro(4, 6, 1).emision == "CAEA"
        assert not any("CAEA" in aviso for aviso in servicio_panel.alerta_fiscal())

    def test_informe_rechazado_y_reintento(self, contingencia, owner):
        contingencia.caido = True
        comprobante = servicio_caea.fiscalizar_con_contingencia(_venta().id, owner)
        contingencia.caido = False
        puntos = contingencia.puntos
        contingencia.puntos = [p for p in puntos if p.numero != 4]
        servicio_caea.informar_pendientes()
        assert servicio_facturacion.obtener(comprobante.id).estado == "CAEA_INFORME_RECHAZADO"
        contingencia.puntos = puntos
        servicio_caea.informar_pendientes()
        assert servicio_facturacion.obtener(comprobante.id).estado == "CAEA_INFORMADO"

    def test_ya_informado_se_reconoce(self, contingencia, owner):
        contingencia.caido = True
        comprobante = servicio_caea.fiscalizar_con_contingencia(_venta().id, owner)
        contingencia.caido = False
        import dataclasses
        from datetime import datetime

        solicitud = dataclasses.replace(
            servicio_facturacion.solicitud_de(comprobante, 1, date.fromisoformat(comprobante.fecha_comprobante)),
            caea=comprobante.codigo_autorizacion,
            fecha_hora_generacion=datetime.now().strftime("%Y%m%d%H%M%S"),
        )
        assert contingencia.informar_caea(solicitud).resultado == "A"  # se informó y se perdió la respuesta
        servicio_caea.informar_pendientes()
        assert servicio_facturacion.obtener(comprobante.id).estado == "CAEA_INFORMADO"

    def test_sin_movimiento(self, caja_abierta, arca_falso, owner):
        uf.guardar_perfil_validado(punto_venta_caea=4, contingencia_caea=True)
        servicio_parametros.refrescar(arca_falso)
        servicio_caea.solicitar(owner, hoy=date(2026, 10, 1))
        assert servicio_caea.informar_sin_movimiento() == 1
        assert servicio_caea.informar_sin_movimiento() == 0
        assert arca_falso.llamadas.count("FECAEASinMovimientoInformar") == 1


class TestInvariantesDB:
    def test_comprobante_caea_exige_el_codigo_del_caea(self, contingencia, owner):
        venta = _venta()
        vigente = servicio_caea.contingencia_disponible(repositorio_fiscal.obtener_perfil())
        nuevo = uf.nuevo_comprobante_para_venta(
            venta.id, modalidad="CAEA", caea_id=vigente.id, estado="CAEA_PENDIENTE_INFORMAR", numero=1,
            fecha_comprobante=HOY.isoformat(), fecha_hora_generacion="2026-11-01 10:00:00",
            codigo_autorizacion="39999999999999", vencimiento_autorizacion=vigente.vigente_hasta,
        )
        with pytest.raises(ErrorBaseDatos, match="CAEA"):
            uf.insertar(nuevo)

    def test_caea_inmutable(self, contingencia):
        with pytest.raises(ErrorBaseDatos):
            with obtener_conexion() as conexion:
                conexion.execute("UPDATE caea_autorizaciones SET codigo = '31111111111111'")
        with pytest.raises(ErrorBaseDatos):
            with obtener_conexion() as conexion:
                conexion.execute("DELETE FROM caea_autorizaciones")
