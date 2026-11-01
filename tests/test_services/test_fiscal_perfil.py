"""Servicios V1.16-A: perfil del emisor, clasificación de IVA y bloqueo de la anulación local."""

import pytest

from db.repositorios import auditoria as repositorio_auditoria
from excepciones import PermisoDenegadoError, VentaConComprobanteFiscalError
from services import servicio_ventas
from services.fiscal import servicio_perfil
from tests import utilidades_fiscales as uf

pytestmark = pytest.mark.usefixtures("caja_abierta")


def _acciones() -> list[str]:
    return [entrada.accion for entrada in repositorio_auditoria.listar(limite=50)]


class TestPerfil:
    def test_alta_y_edicion_auditadas_sin_valores(self):
        owner = uf.crear_owner()
        assert servicio_perfil.guardar_perfil(uf.perfil(), owner)
        assert servicio_perfil.guardar_perfil(uf.perfil(), owner) == []
        cambiados = servicio_perfil.guardar_perfil(uf.perfil(punto_venta=4), owner)
        assert cambiados == ["punto de venta"]
        resumenes = [e.resumen for e in repositorio_auditoria.listar(limite=10) if e.accion == "CONFIGURACION_FISCAL_CAMBIADA"]
        assert len(resumenes) == 2
        assert all("20111111112" not in r for r in resumenes)

    def test_cashier_no_configura(self):
        cashier = uf.crear_cashier()
        with pytest.raises(PermisoDenegadoError):
            servicio_perfil.guardar_perfil(uf.perfil(), cashier)
        assert servicio_perfil.obtener_perfil() is None

    def test_cambiar_punto_de_venta_pierde_la_validacion(self):
        owner = uf.crear_owner()
        uf.guardar_perfil_validado()
        assert servicio_perfil.obtener_perfil().punto_venta_cae_listo
        servicio_perfil.guardar_perfil(uf.perfil(punto_venta=9), owner)
        assert not servicio_perfil.obtener_perfil().punto_venta_cae_listo

    def test_guardar_nunca_cambia_el_ambiente(self):
        owner = uf.crear_owner()
        servicio_perfil.guardar_perfil(uf.perfil(ambiente="PRODUCCION"), owner)
        assert servicio_perfil.obtener_perfil().ambiente == "HOMOLOGACION"


class TestClasificacion:
    def test_producto_nace_sin_clasificar(self):
        producto = uf.producto()
        assert servicio_perfil.obtener_tratamiento(producto.id) is None
        assert servicio_perfil.contar_sin_clasificar() == 1

    def test_clasificar_audita_y_es_idempotente(self):
        owner = uf.crear_owner()
        producto = uf.producto()
        assert servicio_perfil.clasificar_productos({producto.id: uf.IVA_21}, owner) == 1
        assert servicio_perfil.clasificar_productos({producto.id: uf.IVA_21}, owner) == 0
        assert servicio_perfil.clasificar_productos({producto.id: uf.EXENTO}, owner) == 1
        assert _acciones().count("PRODUCTO_IVA_CLASIFICADO") == 2
        assert servicio_perfil.obtener_tratamiento(producto.id) == uf.EXENTO

    def test_cashier_no_clasifica(self):
        producto = uf.producto()
        with pytest.raises(PermisoDenegadoError):
            servicio_perfil.clasificar_productos({producto.id: uf.IVA_21}, uf.crear_cashier())

    def test_listado_filtra_sin_clasificar(self):
        owner = uf.crear_owner()
        a, b = uf.producto(nombre="Agua"), uf.producto(nombre="Chicle")
        servicio_perfil.clasificar_productos({a.id: uf.IVA_21}, owner)
        assert [p.nombre for p in servicio_perfil.listar_productos(True)] == ["Chicle"]
        assert {p.nombre for p in servicio_perfil.listar_productos(False)} == {"Agua", "Chicle"}


class TestAnulacion:
    def test_venta_con_comprobante_vivo_no_se_anula_localmente(self):
        owner = uf.crear_owner()
        venta = uf.vender([(uf.producto(1000, uf.IVA_21), 1)])
        uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))
        with pytest.raises(VentaConComprobanteFiscalError):
            servicio_ventas.anular_venta(venta.id, "ERROR_CARGA", None, owner)

    def test_venta_con_comprobante_rechazado_si_se_anula(self):
        owner = uf.crear_owner()
        venta = uf.vender([(uf.producto(1000, uf.IVA_21), 1)])
        cid = uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))
        uf.transicionar(cid, "ENVIANDO", numero=1, fecha_comprobante="2026-11-01")
        uf.transicionar(cid, "RECHAZADO", resultado="R")
        assert servicio_ventas.anular_venta(venta.id, "ERROR_CARGA", None, owner).estado == "ANULADA"
