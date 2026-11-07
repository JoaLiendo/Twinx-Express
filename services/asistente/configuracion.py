"""Configuración del motor de IA del asistente (V1.18), persistida en la tabla `configuracion` como el resto
de los ajustes del dueño. Sin motor configurado el asistente funciona igual con la ayuda integrada."""

import logging
from dataclasses import dataclass, replace

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import configuracion as repositorio_configuracion
from domain.asistente import MODO_INTEGRADO, MODO_OLLAMA, MODOS_MOTOR
from excepciones import MotorIAError
from services.asistente.proveedores import (
    URL_OLLAMA_POR_DEFECTO,
    ProveedorIA,
    ProveedorIntegrado,
    ProveedorOllama,
    validar_nombre_modelo,
    validar_url_local,
)

logger = logging.getLogger(__name__)

CLAVE_MOTOR = "asistente.motor"
CLAVE_URL = "asistente.ollama_url"
CLAVE_MODELO = "asistente.ollama_modelo"
CLAVE_TIMEOUT = "asistente.timeout_segundos"
CLAVE_NUM_CTX = "asistente.num_ctx"

MODELO_POR_DEFECTO = "llama3.2:3b"
TIMEOUT_POR_DEFECTO = 30
TIMEOUT_MINIMO, TIMEOUT_MAXIMO = 5, 120
NUM_CTX_POR_DEFECTO = 4096
NUM_CTX_MINIMO, NUM_CTX_MAXIMO = 1024, 32768


@dataclass(frozen=True)
class ConfiguracionAsistente:
    motor: str = MODO_INTEGRADO
    url: str = URL_OLLAMA_POR_DEFECTO
    modelo: str = MODELO_POR_DEFECTO
    timeout_segundos: int = TIMEOUT_POR_DEFECTO
    num_ctx: int = NUM_CTX_POR_DEFECTO

    @property
    def usa_ia(self) -> bool:
        return self.motor == MODO_OLLAMA


def _entero(texto: object, minimo: int, maximo: int, nombre: str) -> int:
    try:
        valor = int(str(texto).strip())
    except (TypeError, ValueError) as error:
        raise MotorIAError(f"{nombre} debe ser un número entero.") from error
    if not minimo <= valor <= maximo:
        raise MotorIAError(f"{nombre} debe estar entre {minimo} y {maximo}.")
    return valor


def validar(motor: str, url: str, modelo: str, timeout_segundos: object, num_ctx: object) -> ConfiguracionAsistente:
    """Valida lo que viene del formulario. La URL solo puede ser local; el modelo, un nombre de Ollama."""
    motor = (motor or MODO_INTEGRADO).strip()
    if motor not in MODOS_MOTOR:
        raise MotorIAError("Motor desconocido.")
    return ConfiguracionAsistente(
        motor=motor,
        url=validar_url_local(url or URL_OLLAMA_POR_DEFECTO),
        modelo=validar_nombre_modelo(modelo or MODELO_POR_DEFECTO),
        timeout_segundos=_entero(timeout_segundos or TIMEOUT_POR_DEFECTO, TIMEOUT_MINIMO, TIMEOUT_MAXIMO, "El tiempo de espera"),
        num_ctx=_entero(num_ctx or NUM_CTX_POR_DEFECTO, NUM_CTX_MINIMO, NUM_CTX_MAXIMO, "El contexto del modelo"),
    )


def _desde_valores(valores: dict[str, str]) -> ConfiguracionAsistente:
    """Lo guardado, saneado: un valor corrupto en la base vuelve al valor por defecto en vez de romper el
    arranque del asistente."""
    configuracion = ConfiguracionAsistente()
    try:
        configuracion = validar(
            valores.get(CLAVE_MOTOR, configuracion.motor),
            valores.get(CLAVE_URL, configuracion.url),
            valores.get(CLAVE_MODELO, configuracion.modelo),
            valores.get(CLAVE_TIMEOUT, configuracion.timeout_segundos),
            valores.get(CLAVE_NUM_CTX, configuracion.num_ctx),
        )
    except MotorIAError as error:
        logger.warning("Configuración del asistente inválida en la base, se usan los valores por defecto: %s", error)
        configuracion = replace(configuracion, motor=MODO_INTEGRADO)
    return configuracion


def obtener() -> ConfiguracionAsistente:
    return _desde_valores(repositorio_configuracion.obtener_todas())


def guardar(configuracion: ConfiguracionAsistente, usuario_id: int) -> ConfiguracionAsistente:
    """Guarda la configuración (solo OWNER: lo exige la ruta) y la audita. Nunca guarda secretos: Ollama
    local no usa claves."""
    valores = {
        CLAVE_MOTOR: configuracion.motor,
        CLAVE_URL: configuracion.url,
        CLAVE_MODELO: configuracion.modelo,
        CLAVE_TIMEOUT: str(configuracion.timeout_segundos),
        CLAVE_NUM_CTX: str(configuracion.num_ctx),
    }
    with obtener_conexion(inmediata=True) as conexion:
        for clave, valor in valores.items():
            repositorio_configuracion.guardar_en_conexion(conexion, clave, valor)
        detalle = (
            f"Asistente: motor {configuracion.motor}"
            + (f", modelo {configuracion.modelo} en {configuracion.url}" if configuracion.usa_ia else "")
        )
        repositorio_auditoria.registrar_en_conexion(conexion, usuario_id, "CONFIGURACION_ASISTENTE_CAMBIADA", "CONFIGURACION", None, detalle)
    logger.info("Configuración del asistente actualizada: motor=%s", configuracion.motor)
    return configuracion


def proveedor_para(configuracion: ConfiguracionAsistente) -> ProveedorIA:
    """El motor que corresponde a la configuración. Con `integrado` no se abre ninguna conexión jamás."""
    if configuracion.usa_ia:
        return ProveedorOllama(
            url=configuracion.url,
            modelo=configuracion.modelo,
            timeout_segundos=float(configuracion.timeout_segundos),
            num_ctx=configuracion.num_ctx,
        )
    return ProveedorIntegrado()
