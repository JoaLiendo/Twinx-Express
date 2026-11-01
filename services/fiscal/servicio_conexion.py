"""Conexión con ARCA desde el panel fiscal: validar el punto de venta, probar conectividad y consultar
el último comprobante autorizado. Solo OWNER; ninguna de estas operaciones autoriza nada."""

import logging

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import fiscal as repositorio_fiscal
from db.repositorios import usuarios as repositorio_usuarios
from domain.auditoria import ACCION_CONFIGURACION_FISCAL_CAMBIADA
from domain.fiscal import reglas, tablas_arca
from domain.fiscal.emisor import PerfilFiscal
from domain.fiscal.protocolo import EstadoServidores
from domain.usuario import exigir_rol
from excepciones import ConfiguracionFiscalError
from services.fiscal import servicio_parametros
from services.fiscal.gateway import gateway_para

logger = logging.getLogger(__name__)

_SOLO_OWNER = frozenset({"OWNER"})


def exigir_perfil() -> PerfilFiscal:
    perfil = repositorio_fiscal.obtener_perfil()
    if perfil is None:
        raise ConfiguracionFiscalError("Configurá primero el perfil fiscal del emisor.")
    return perfil


def _exigir_owner(usuario_id: int) -> None:
    with obtener_conexion() as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _SOLO_OWNER)


def _emision_esperada(caea: bool) -> str:
    return tablas_arca.EMISION_CAEA if caea else tablas_arca.EMISION_CAE


def _coincide_emision(emision_tipo: str, caea: bool) -> bool:
    """ARCA informa `EmisionTipo` por punto de venta (p. ej. «CAE» o «CAEA», a veces con sufijo)."""
    normalizado = emision_tipo.upper().replace(" ", "")
    if caea:
        return normalizado.startswith(tablas_arca.EMISION_CAEA)
    return normalizado.startswith(tablas_arca.EMISION_CAE) and not normalizado.startswith(tablas_arca.EMISION_CAEA)


def validar_punto_venta(usuario_id: int, caea: bool = False) -> list[str]:
    """Confirma con `FEParamGetPtosVenta` que el punto de venta configurado existe en ARCA, está habilitado
    y es de la modalidad correcta (CAE o CAEA); refresca las tablas de parámetros. Devuelve las tablas que
    no pudieron refrescarse.

    Raises:
        ConfiguracionFiscalError: si ARCA no lo confirma (el punto de venta queda sin validar).
    """
    _exigir_owner(usuario_id)
    perfil = exigir_perfil()
    numero = perfil.punto_venta_caea if caea else perfil.punto_venta_cae
    nombre = "punto de venta CAEA" if caea else "punto de venta"
    if numero is None:
        raise ConfiguracionFiscalError(f"Indicá el {nombre} antes de validarlo.")
    gateway = gateway_para(perfil)
    puntos = {punto.numero: punto for punto in gateway.puntos_venta()}
    punto = puntos.get(numero)
    problema = None
    if punto is None:
        problema = f"ARCA no tiene el {nombre} {numero} dado de alta para este CUIT en {perfil.ambiente.lower()}."
    elif not punto.habilitado:
        problema = f"El {nombre} {numero} está bloqueado o dado de baja en ARCA."
    elif not _coincide_emision(punto.emision_tipo, caea):
        problema = (
            f"El {nombre} {numero} es de emisión «{punto.emision_tipo}» en ARCA; se necesita uno de "
            f"«{_emision_esperada(caea)}» por web service."
        )
    with obtener_conexion(inmediata=True) as conexion:
        repositorio_fiscal.marcar_punto_venta_validado_en_conexion(conexion, caea, None if problema else perfil.ambiente)
        repositorio_auditoria.registrar_en_conexion(
            conexion,
            usuario_id,
            ACCION_CONFIGURACION_FISCAL_CAMBIADA,
            "PERFIL_FISCAL",
            None,
            f"Validación del {nombre} {numero} en {perfil.ambiente.lower()}: {'rechazada' if problema else 'confirmada'}",
        )
    if problema:
        raise ConfiguracionFiscalError(problema)
    fallidas = servicio_parametros.refrescar(gateway)
    logger.info("Punto de venta %s validado en %s (caea=%s)", numero, perfil.ambiente, caea)
    return fallidas


def probar_conexion(usuario_id: int) -> EstadoServidores:
    _exigir_owner(usuario_id)
    return gateway_para(exigir_perfil()).dummy()


def ultimos_autorizados(usuario_id: int) -> dict[str, int]:
    """Último número autorizado en ARCA por cada tipo que el emisor puede emitir con su punto de venta CAE."""
    _exigir_owner(usuario_id)
    perfil = exigir_perfil()
    if perfil.punto_venta_cae is None:
        raise ConfiguracionFiscalError("Indicá el punto de venta.")
    gateway = gateway_para(perfil)
    clases = ("C",) if perfil.condicion_iva != "RESPONSABLE_INSCRIPTO" else ("A", "B")
    resultado = {}
    for clase in clases:
        for rol in (tablas_arca.ROL_PRINCIPAL, tablas_arca.ROL_NOTA_CREDITO):
            tipo = reglas.tipo_comprobante(clase, rol)
            resultado[tablas_arca.NOMBRE_TIPO_COMPROBANTE[tipo]] = gateway.ultimo_autorizado(perfil.punto_venta_cae, tipo)
    return resultado
