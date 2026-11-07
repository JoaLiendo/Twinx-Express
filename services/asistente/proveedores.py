"""Motores de IA del asistente (V1.18): la interfaz común, el adaptador de Ollama local y un proveedor falso
determinista para los tests.

El adaptador habla con la API REST de Ollama (`/api/tags`, `/api/chat` con `tools`, sin streaming) usando
solo la biblioteca estándar: no suma dependencias ni servicios. Por diseño solo acepta direcciones de
loopback: ningún dato del comercio sale de la máquina. No hay telemetría ni claves.
"""

import json
import logging
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse

from domain.asistente import MODO_INTEGRADO, MODO_OLLAMA, EstadoMotor
from excepciones import MotorIAError

logger = logging.getLogger(__name__)

URL_OLLAMA_POR_DEFECTO = "http://127.0.0.1:11434"
HOSTS_LOCALES = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})
TIMEOUT_DETECCION_SEGUNDOS = 2.0
# Nombres de modelo de Ollama: `familia:etiqueta`, a veces con un espacio de nombres (`usuario/modelo`).
_NOMBRE_MODELO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,98}(:[A-Za-z0-9._-]{1,40})?$")


def validar_url_local(url: str) -> str:
    """La URL del motor debe ser local (loopback) y http(s) sin ruta ni credenciales. Devuelve la URL sin la
    barra final."""
    url = (url or "").strip().rstrip("/")
    partes = urlparse(url)
    if partes.scheme not in ("http", "https") or not partes.hostname or partes.username or partes.password:
        raise MotorIAError("La dirección del motor debe tener la forma http://127.0.0.1:11434.")
    if partes.hostname not in HOSTS_LOCALES:
        raise MotorIAError("El asistente solo se conecta a un motor local (127.0.0.1 o localhost).")
    if partes.path not in ("", "/") or partes.query or partes.fragment:
        raise MotorIAError("La dirección del motor no lleva ruta: solo esquema, host y puerto.")
    return url


def validar_nombre_modelo(nombre: str) -> str:
    nombre = (nombre or "").strip()
    if not _NOMBRE_MODELO.match(nombre):
        raise MotorIAError("El nombre del modelo no es válido (ejemplo: llama3.2:3b).")
    return nombre


@dataclass(frozen=True)
class LlamadaHerramienta:
    nombre: str
    argumentos: dict


@dataclass(frozen=True)
class RespuestaModelo:
    """Un turno del modelo: texto final o pedidos de herramientas (nunca ambos a la vez para el asistente)."""

    texto: str
    llamadas: tuple[LlamadaHerramienta, ...] = ()
    modelo: str | None = None


@dataclass(frozen=True)
class MensajeModelo:
    """Un mensaje en el formato del motor: `system`, `user`, `assistant` o `tool`."""

    rol: str
    contenido: str
    nombre_herramienta: str | None = None
    llamadas: tuple[LlamadaHerramienta, ...] = ()


class ProveedorIA(Protocol):
    def estado(self) -> EstadoMotor: ...

    def conversar(self, mensajes: list[MensajeModelo], herramientas: list[dict]) -> RespuestaModelo: ...


@dataclass(frozen=True)
class ProveedorIntegrado:
    """Sin motor de IA: solo la ayuda integrada. Existe para que el servicio no tenga un caso `None`."""

    def estado(self) -> EstadoMotor:
        return EstadoMotor(MODO_INTEGRADO, False, None, "Sin motor de IA configurado.")

    def conversar(self, mensajes: list[MensajeModelo], herramientas: list[dict]) -> RespuestaModelo:
        raise MotorIAError("No hay un motor de IA configurado.")


@dataclass(frozen=True)
class ProveedorOllama:
    """Adaptador de la API local de Ollama. `num_ctx` y `num_predict` acotan la memoria y el tiempo de
    cada respuesta; `keep_alive` corto libera la RAM del modelo cuando el asistente deja de usarse."""

    url: str = URL_OLLAMA_POR_DEFECTO
    modelo: str = "llama3.2:3b"
    timeout_segundos: float = 30.0
    num_ctx: int = 4096
    num_predict: int = 400
    keep_alive: str = "5m"
    temperatura: float = 0.1

    def estado(self) -> EstadoMotor:
        """Consulta `/api/tags` con un timeout corto: conectado solo si el servidor responde y el modelo está
        descargado. Nunca lanza: el estado es información, no un error."""
        try:
            respuesta = self._get("/api/tags", TIMEOUT_DETECCION_SEGUNDOS)
        except MotorIAError as error:
            return EstadoMotor(MODO_OLLAMA, False, self.modelo, str(error))
        disponibles = [m.get("name") or m.get("model") for m in respuesta.get("models", []) if isinstance(m, dict)]
        if self.modelo in disponibles or f"{self.modelo}:latest" in disponibles:
            return EstadoMotor(MODO_OLLAMA, True, self.modelo, f"Ollama en {self.url}.")
        lista = ", ".join(str(m) for m in disponibles[:5]) or "ninguno"
        return EstadoMotor(
            MODO_OLLAMA, False, self.modelo, f"Ollama responde pero el modelo {self.modelo} no está descargado (hay: {lista})."
        )

    def conversar(self, mensajes: list[MensajeModelo], herramientas: list[dict]) -> RespuestaModelo:
        cuerpo = {
            "model": self.modelo,
            "messages": [_a_mensaje_ollama(m) for m in mensajes],
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"num_ctx": self.num_ctx, "num_predict": self.num_predict, "temperature": self.temperatura},
        }
        if herramientas:
            cuerpo["tools"] = herramientas
        datos = self._post("/api/chat", cuerpo, self.timeout_segundos)
        mensaje = datos.get("message") or {}
        llamadas = []
        for llamada in mensaje.get("tool_calls") or []:
            funcion = (llamada or {}).get("function") or {}
            nombre = funcion.get("name")
            argumentos = funcion.get("arguments")
            if isinstance(argumentos, str):
                try:
                    argumentos = json.loads(argumentos)
                except ValueError:
                    argumentos = {}
            if isinstance(nombre, str):
                llamadas.append(LlamadaHerramienta(nombre, argumentos if isinstance(argumentos, dict) else {}))
        texto = mensaje.get("content") if isinstance(mensaje.get("content"), str) else ""
        return RespuestaModelo(texto, tuple(llamadas), datos.get("model") or self.modelo)

    # -- HTTP ---------------------------------------------------------------------------------------

    def _get(self, ruta: str, timeout: float) -> dict:
        return self._solicitar(urllib.request.Request(self.url + ruta, method="GET"), timeout)

    def _post(self, ruta: str, cuerpo: dict, timeout: float) -> dict:
        datos = json.dumps(cuerpo).encode("utf-8")
        solicitud = urllib.request.Request(
            self.url + ruta, data=datos, method="POST", headers={"Content-Type": "application/json"}
        )
        return self._solicitar(solicitud, timeout)

    def _solicitar(self, solicitud: urllib.request.Request, timeout: float) -> dict:
        """Una llamada HTTP a Ollama traducida a `MotorIAError` con un mensaje para el usuario. Se registra
        el motivo técnico, nunca el contenido de la conversación."""
        try:
            with urllib.request.urlopen(solicitud, timeout=timeout) as respuesta:  # noqa: S310 - URL validada como loopback
                crudo = respuesta.read()
        except urllib.error.HTTPError as error:
            detalle = _detalle_de_error_http(error)
            logger.warning("Ollama respondió %s en %s: %s", error.code, solicitud.selector, detalle)
            if error.code == 404:
                raise MotorIAError(f"Ollama no encuentra el modelo {self.modelo}: descargalo con `ollama pull`.") from error
            raise MotorIAError(f"El motor de IA respondió con un error ({error.code}).") from error
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as error:
            motivo = getattr(error, "reason", error)
            if isinstance(motivo, (socket.timeout, TimeoutError)) or "timed out" in str(motivo):
                logger.warning("Ollama no respondió a tiempo (%ss) en %s", timeout, solicitud.selector)
                raise MotorIAError("El motor de IA tardó demasiado en responder.") from error
            logger.info("Ollama no está disponible en %s: %s", self.url, motivo)
            raise MotorIAError("No hay un motor de IA local escuchando.") from error
        try:
            datos = json.loads(crudo.decode("utf-8"))
        except ValueError as error:
            raise MotorIAError("La respuesta del motor de IA no se pudo interpretar.") from error
        if not isinstance(datos, dict):
            raise MotorIAError("La respuesta del motor de IA no tiene la forma esperada.")
        if "error" in datos and isinstance(datos["error"], str):
            raise MotorIAError(f"El motor de IA informó un error: {datos['error'][:120]}")
        return datos


def _detalle_de_error_http(error: urllib.error.HTTPError) -> str:
    try:
        return error.read(400).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _a_mensaje_ollama(mensaje: MensajeModelo) -> dict:
    """Mensajes del asistente → formato de `/api/chat`: los resultados de herramientas van como `tool`."""
    if mensaje.rol == "tool":
        return {"role": "tool", "content": mensaje.contenido, "tool_name": mensaje.nombre_herramienta}
    salida: dict = {"role": mensaje.rol, "content": mensaje.contenido}
    if mensaje.llamadas:
        salida["tool_calls"] = [
            {"function": {"name": llamada.nombre, "arguments": llamada.argumentos}} for llamada in mensaje.llamadas
        ]
    return salida


@dataclass
class ProveedorFalso:
    """Motor determinista para tests y demostraciones: responde con un guion fijo, turno a turno.

    `guion` es la lista de respuestas que dará en orden; cuando se agota repite la última. `disponible`
    simula un motor apagado. Registra los mensajes recibidos para que un test verifique qué vio el modelo
    (por ejemplo, que un CASHIER nunca recibe herramientas de costos)."""

    guion: list[RespuestaModelo] = field(default_factory=list)
    disponible: bool = True
    modelo: str = "falso:test"
    recibidos: list[tuple[list[MensajeModelo], list[dict]]] = field(default_factory=list)
    fallar_con: str | None = None

    def estado(self) -> EstadoMotor:
        if self.disponible:
            return EstadoMotor(MODO_OLLAMA, True, self.modelo, "Motor de prueba.")
        return EstadoMotor(MODO_OLLAMA, False, self.modelo, "Motor de prueba apagado.")

    def conversar(self, mensajes: list[MensajeModelo], herramientas: list[dict]) -> RespuestaModelo:
        self.recibidos.append((list(mensajes), list(herramientas)))
        if self.fallar_con:
            raise MotorIAError(self.fallar_con)
        if not self.disponible:
            raise MotorIAError("No hay un motor de IA local escuchando.")
        indice = min(len(self.recibidos) - 1, len(self.guion) - 1)
        if indice < 0:
            return RespuestaModelo("", (), self.modelo)
        respuesta = self.guion[indice]
        return RespuestaModelo(respuesta.texto, respuesta.llamadas, respuesta.modelo or self.modelo)
