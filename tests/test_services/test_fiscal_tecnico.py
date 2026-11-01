"""V1.16: producción fiscal solo por acción explícita del técnico (consola), nunca por defecto."""

import pytest

import lanzador
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import fiscal as repositorio_fiscal
from excepciones import ConfiguracionFiscalError, CredencialesFiscalesError
from services.fiscal import credenciales, servicio_tecnico
from tests import certificados_prueba as cp
from tests import utilidades_fiscales as uf

pytestmark = pytest.mark.usefixtures("base_datos_temporal")


def _certificado_produccion(cuit=cp.CUIT):
    credenciales.importar("PRODUCCION", cp.pem(cp.certificado(homologacion=False, cuit=cuit)), cp.clave_pem(), None, None, None)


def test_el_perfil_nace_en_homologacion():
    uf.guardar_perfil_validado()
    assert repositorio_fiscal.obtener_perfil().ambiente == "HOMOLOGACION"


def test_sin_certificado_de_produccion_no_se_habilita():
    uf.guardar_perfil_validado()
    with pytest.raises(CredencialesFiscalesError):
        servicio_tecnico.habilitar_produccion(uf.CUIT_EMISOR)
    assert repositorio_fiscal.obtener_perfil().ambiente == "HOMOLOGACION"


def test_cuit_escrito_distinto_no_se_habilita():
    uf.guardar_perfil_validado()
    _certificado_produccion()
    with pytest.raises(ConfiguracionFiscalError, match="no coincide"):
        servicio_tecnico.habilitar_produccion(uf.CUIT_RECEPTOR_RI)


def test_certificado_de_otro_cuit_no_se_habilita():
    uf.guardar_perfil_validado()
    _certificado_produccion(cuit=uf.CUIT_RECEPTOR_RI)
    with pytest.raises(CredencialesFiscalesError, match="otro CUIT"):
        servicio_tecnico.habilitar_produccion(uf.CUIT_EMISOR)


def test_habilitacion_completa_queda_auditada_y_exige_revalidar():
    uf.guardar_perfil_validado()
    _certificado_produccion()
    servicio_tecnico.habilitar_produccion("20-11111111-2")
    perfil = repositorio_fiscal.obtener_perfil()
    assert perfil.ambiente == "PRODUCCION" and not perfil.punto_venta_cae_listo
    assert repositorio_auditoria.listar(accion="PRODUCCION_FISCAL_HABILITADA")


def test_comando_de_consola():
    assert lanzador._comando_de_mantenimiento(["KioscoApp.exe", "--habilitar-produccion-fiscal", "20111111112"]) == (
        "--habilitar-produccion-fiscal", "20111111112",
    )
