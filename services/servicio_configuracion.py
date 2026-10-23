"""Casos de uso de configuración: datos comerciales del ticket (V1.2), aviso de vencimientos y parámetros de liquidación (V1.12)."""

import logging

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import configuracion as repositorio_configuracion
from domain.comercio import CLAVES_CONFIGURACION, ETIQUETAS, DatosComercio
from domain.liquidacion import DESCUENTO_OBJETIVO_POR_DEFECTO, MARGEN_MINIMO_POR_DEFECTO, ParametrosLiquidacion
from domain.partida import DIAS_ALERTA_POR_DEFECTO, validar_dias_alerta

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


CLAVE_DIAS_ALERTA_VENCIMIENTO = "vencimientos.dias_alerta"


def obtener_dias_alerta_vencimiento() -> int:
    """Días de anticipación de la alerta "Próximo a vencer" (V1.12-D); 10 si nunca se configuró."""
    valor = repositorio_configuracion.obtener_todas().get(CLAVE_DIAS_ALERTA_VENCIMIENTO)
    return validar_dias_alerta(valor) if valor is not None else DIAS_ALERTA_POR_DEFECTO


def guardar_dias_alerta_vencimiento(texto: str, usuario_id: int) -> int:
    """Guarda los días de alerta (configuración global, solo OWNER: lo exige la ruta) y lo audita si cambió.

    Raises:
        DatosInvalidosError: si no es un entero entre 0 y `DIAS_ALERTA_MAXIMO`.
    """
    dias = validar_dias_alerta(texto)
    with obtener_conexion(inmediata=True) as conexion:
        actual = repositorio_configuracion.obtener_todas_en_conexion(conexion).get(CLAVE_DIAS_ALERTA_VENCIMIENTO)
        anterior = int(actual) if actual is not None else DIAS_ALERTA_POR_DEFECTO
        if dias != anterior:
            repositorio_configuracion.guardar_en_conexion(conexion, CLAVE_DIAS_ALERTA_VENCIMIENTO, str(dias))
            repositorio_auditoria.registrar_en_conexion(
                conexion, usuario_id, "CONFIGURACION_VENCIMIENTOS_CAMBIADA", "CONFIGURACION", None,
                f"Alerta de vencimiento: {anterior} -> {dias} días",
            )
            logger.info("Días de alerta de vencimiento: %s -> %s", anterior, dias)
    return dias


CLAVE_LIQUIDACION_DESCUENTO = "liquidacion.descuento_objetivo"
CLAVE_LIQUIDACION_MARGEN = "liquidacion.margen_minimo"


def obtener_parametros_liquidacion() -> ParametrosLiquidacion:
    """Descuento objetivo y margen mínimo de la liquidación por vencimiento (V1.12-E); 20 % y 10 % si
    nunca se configuraron."""
    valores = repositorio_configuracion.obtener_todas()
    return ParametrosLiquidacion.desde_texto(
        valores.get(CLAVE_LIQUIDACION_DESCUENTO, str(DESCUENTO_OBJETIVO_POR_DEFECTO)),
        valores.get(CLAVE_LIQUIDACION_MARGEN, str(MARGEN_MINIMO_POR_DEFECTO)),
    )


def guardar_parametros_liquidacion(descuento: str, margen: str, usuario_id: int) -> ParametrosLiquidacion:
    """Raises: DatosInvalidosError si algún porcentaje es inválido."""
    nuevos = ParametrosLiquidacion.desde_texto(descuento, margen)
    actuales = obtener_parametros_liquidacion()
    if nuevos != actuales:
        with obtener_conexion(inmediata=True) as conexion:
            repositorio_configuracion.guardar_en_conexion(conexion, CLAVE_LIQUIDACION_DESCUENTO, str(nuevos.descuento_objetivo))
            repositorio_configuracion.guardar_en_conexion(conexion, CLAVE_LIQUIDACION_MARGEN, str(nuevos.margen_minimo))
            repositorio_auditoria.registrar_en_conexion(
                conexion, usuario_id, "CONFIGURACION_VENCIMIENTOS_CAMBIADA", "CONFIGURACION", None,
                f"Liquidación: descuento {actuales.descuento_objetivo} -> {nuevos.descuento_objetivo} %,"
                f" margen mínimo {actuales.margen_minimo} -> {nuevos.margen_minimo} %",
            )
    return nuevos
