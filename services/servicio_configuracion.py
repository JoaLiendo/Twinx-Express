"""Casos de uso de configuración (V1.2): datos comerciales del ticket."""

import logging

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import configuracion as repositorio_configuracion
from domain.comercio import CLAVES_CONFIGURACION, ETIQUETAS, DatosComercio

logger = logging.getLogger(__name__)


def _desde_valores(valores: dict[str, str]) -> DatosComercio:
    """Un campo sin configurar queda vacío (el nombre vacío usa el valor por defecto)."""
    return DatosComercio(**{campo: valores.get(clave, "") for campo, clave in CLAVES_CONFIGURACION.items()})


def obtener_datos_comercio() -> DatosComercio:
    """Datos comerciales configurados, o los valores por defecto si nunca se configuraron."""
    return _desde_valores(repositorio_configuracion.obtener_todas())


def guardar_datos_comercio(datos: DatosComercio, usuario_id: int | None = None) -> list[str]:
    """Guarda los datos comerciales y devuelve las etiquetas de los campos que
    cambiaron (lista vacía si no cambió nada: en ese caso no se escribe ni se
    audita). La validación ocurre al construir `DatosComercio`. Todo en una
    transacción: los campos se guardan juntos y, con `usuario_id`, el cambio
    queda en la auditoría (solo qué campos, no sus valores)."""
    with obtener_conexion(inmediata=True) as conexion:
        actuales = _desde_valores(repositorio_configuracion.obtener_todas_en_conexion(conexion))
        cambiados = [campo for campo in CLAVES_CONFIGURACION if getattr(actuales, campo) != getattr(datos, campo)]
        for campo in cambiados:
            repositorio_configuracion.guardar_en_conexion(conexion, CLAVES_CONFIGURACION[campo], getattr(datos, campo))
        if cambiados and usuario_id is not None:
            repositorio_auditoria.registrar_en_conexion(
                conexion,
                usuario_id,
                "CONFIGURACION_COMERCIO_CAMBIADA",
                "CONFIGURACION",
                None,
                "Datos del ticket: " + ", ".join(ETIQUETAS[campo] for campo in cambiados),
            )
    if cambiados:
        logger.info("Datos comerciales del ticket actualizados: %s", ", ".join(cambiados))
    return [ETIQUETAS[campo] for campo in cambiados]
