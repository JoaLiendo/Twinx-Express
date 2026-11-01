"""Acciones de técnico sobre el ambiente fiscal (consola del lanzador, nunca desde la interfaz web).

Activar producción es deliberadamente incómodo: exige escribir el CUIT del emisor, que el perfil esté
completo y que haya un certificado de PRODUCCIÓN vigente de ese mismo CUIT. Queda registrado en la
base (habilitación append-only, condición del trigger) y en la auditoría.
"""

import logging

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import fiscal as repositorio_fiscal
from domain.auditoria import ACCION_PRODUCCION_FISCAL_HABILITADA
from domain.fiscal.comprobante import AMBIENTE_PRODUCCION
from domain.proveedor import normalizar_cuit
from excepciones import ConfiguracionFiscalError, CredencialesFiscalesError, DatosInvalidosError
from services.fiscal import credenciales

logger = logging.getLogger(__name__)


def habilitar_produccion(cuit_confirmado: str) -> str:
    """Pasa el perfil a PRODUCCION. Devuelve un resumen para la consola.

    Raises:
        ConfiguracionFiscalError / CredencialesFiscalesError / DatosInvalidosError: con el motivo.
    """
    try:
        cuit = normalizar_cuit(cuit_confirmado)
    except DatosInvalidosError as error:
        raise ConfiguracionFiscalError(f"CUIT inválido: {error}") from error
    perfil = repositorio_fiscal.obtener_perfil()
    if perfil is None:
        raise ConfiguracionFiscalError("No hay perfil fiscal: configuralo desde el panel antes de habilitar producción.")
    if cuit != perfil.cuit:
        raise ConfiguracionFiscalError("El CUIT escrito no coincide con el del emisor configurado.")
    if perfil.ambiente == AMBIENTE_PRODUCCION:
        return "El perfil fiscal ya estaba en producción."
    info = credenciales.estado(AMBIENTE_PRODUCCION)
    if info.estado not in (credenciales.VIGENTE, credenciales.POR_VENCER):
        raise CredencialesFiscalesError("Falta un certificado de PRODUCCIÓN vigente: importalo desde el panel fiscal.")
    if info.cuit and info.cuit != perfil.cuit:
        raise CredencialesFiscalesError("El certificado de producción pertenece a otro CUIT.")
    with obtener_conexion(inmediata=True) as conexion:
        repositorio_fiscal.registrar_habilitacion_produccion_en_conexion(
            conexion, perfil.cuit, info.huella, "Habilitación de producción por consola del técnico"
        )
        repositorio_fiscal.cambiar_ambiente_en_conexion(conexion, AMBIENTE_PRODUCCION)
        repositorio_auditoria.registrar_en_conexion(
            conexion, None, ACCION_PRODUCCION_FISCAL_HABILITADA, "PERFIL_FISCAL", None,
            f"Producción fiscal habilitada por el técnico (certificado {info.huella[:16]}, vence {info.vence})",
        )
    logger.warning("Producción fiscal HABILITADA para el CUIT %s", perfil.cuit)
    return (
        "Producción fiscal habilitada. Validá el punto de venta de producción desde el panel fiscal antes de facturar."
    )
