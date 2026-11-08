"""Motores de IA del asistente (V1.18): la interfaz común, el adaptador de Ollama local y un proveedor falso
determinista para los tests.

El adaptador habla con la API REST de Ollama (`/api/tags`, `/api/show`, `/api/chat` con `tools`, sin streaming)
usando solo la biblioteca estándar: no suma dependencias ni servicios. Por diseño solo acepta direcciones de
loopback, no usa el proxy del sistema y no sigue redirecciones: ningún dato del comercio sale de la máquina.
No hay telemetría ni claves.
"""

import http.client
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
HOSTS_LOCALES = frozenset({"127.0.0.1", "localhost", "::1"})
TIMEOUT_DETECCION_SEGUNDOS = 3.0
# Tope de pedidos de herramientas que se aceptan por turno del modelo: más que eso es un modelo descontrolado.
MAX_LLAMADAS_POR_TURNO = 3
# Nombres de modelo de Ollama: `familia:etiqueta`, a veces con un espacio de nombres (`usuario/modelo`).
_NOMBRE_MODELO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,98}(:[A-Za-z0-9._-]{1,40})?$")
_CARACTER_RARO = re.compile(r"[\s\x00-\x1f\x7f]")


class _SinRedirecciones(urllib.request.HTTPRedirectHandler):
    """Un 3xx de "Ollama" no se sigue jamás: llevaría la conversación a otro host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - firma de urllib
        return None


# Opener propio: sin proxy (el opener por defecto lee HTTP_PROXY y el proxy de Windows, que NO excluyen
# 127.0.0.1) y sin redirecciones. Es la garantía técnica de "solo local".
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _SinRedirecciones)


def validar_url_local(url: str) -> str:
    """La URL del motor debe ser http(s) sobre loopback, con puerto válido y sin ruta, credenciales ni
    caracteres raros. Devuelve la URL normalizada (`esquema://host:puerto`)."""
    url = (url or "").strip()
    if not url or _CARACTER_RARO.search(url):
        raise MotorIAError("La dirección del motor debe tener la forma http://127.0.0.1:11434.")
    try:
        partes = urlparse(url)
        puerto = partes.port
    except ValueError as error:
        raise MotorIAError("La dirección del motor no es válida.") from error
    esquema = partes.scheme.lower()
    host = (partes.hostname or "").lower()
    if esquema not in ("http", "https") or not host or partes.username or partes.password:
        raise MotorIAError("La dirección del motor debe tener la forma http://127.0.0.1:11434.")
    if host not in HOSTS_LOCALES:
        raise MotorIAError("El asistente solo se conecta a un motor local (127.0.0.1 o localhost).")
    if partes.path not in ("", "/") or partes.params or partes.query or partes.fragment:
        raise MotorIAError("La dirección del motor no lleva ruta: solo esquema, host y puerto.")
    if puerto is None:
        puerto = 443 if esquema == "https" else 80
    if not 1 <= puerto <= 65535:
        raise MotorIAError("El puerto del motor debe estar entre 1 y 65535.")
    host_url = f"[{host}]" if ":" in host else host
    return f"{esquema}://{host_url}:{puerto}"


def validar_nombre_modelo(nombre: str) -> str:
    nombre = (nombre or "").strip()
    if not _NOMBRE_MODELO.match(nombre):
        raise MotorIAError("El nombre del modelo no es válido (ejemplo: llama3.2:3b).")
    return nombre


@dataclass(frozen=True)
class LlamadaHerramienta:
    """`argumentos` llega tal cual lo mandó el modelo: si no es un objeto, `herramientas.validar_argumentos`
    lo rechaza. Nunca se reemplaza en silencio por `{}`."""

    nombre: str
    argumentos: object


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


def _nombres_de_modelos(datos: dict) -> list[str]:
    modelos = datos.get("models")
    if not isinstance(modelos, list):
        raise MotorIAError("La respuesta del motor de IA no tiene la forma esperada.")
    return [str(m.get("name") or m.get("model") or "") for m in modelos if isinstance(m, dict)]


def _llamadas_de(mensaje: dict) -> tuple[LlamadaHerramienta, ...]:
    """Las `tool_calls` del turno, con tipos verificados y acotadas a `MAX_LLAMADAS_POR_TURNO`."""
    crudas = mensaje.get("tool_calls") or []
    if not isinstance(crudas, list):
        raise MotorIAError("La respuesta del motor de IA no tiene la forma esperada.")
    llamadas = []
    for llamada in crudas[:MAX_LLAMADAS_POR_TURNO]:
        funcion = llamada.get("function") if isinstance(llamada, dict) else None
        if not isinstance(funcion, dict) or not isinstance(funcion.get("name"), str):
            continue
        argumentos = funcion.get("arguments")
        if isinstance(argumentos, str):
            try:
                argumentos = json.loads(argumentos)
            except ValueError:
                pass  # queda el texto crudo y la validación de argumentos lo rechaza
        llamadas.append(LlamadaHerramienta(funcion["name"], {} if argumentos is None else argumentos))
    return tuple(llamadas)


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
        """Conectado solo si el servidor responde, el modelo está descargado y admite herramientas (`tools`,
        según `/api/show`; un Ollama viejo sin ese dato se acepta). Nunca lanza: el estado es información."""
        try:
            disponibles = _nombres_de_modelos(self._get("/api/tags", TIMEOUT_DETECCION_SEGUNDOS))
            if self.modelo not in disponibles and f"{self.modelo}:latest" not in disponibles:
                lista = ", ".join(m for m in disponibles[:5] if m) or "ninguno"
                return EstadoMotor(MODO_OLLAMA, False, self.modelo, f"Ollama responde pero el modelo {self.modelo} no está descargado (hay: {lista}).")
            detalle = self._post("/api/show", {"model": self.modelo}, TIMEOUT_DETECCION_SEGUNDOS)
        except MotorIAError as error:
            return EstadoMotor(MODO_OLLAMA, False, self.modelo, str(error))
        capacidades = detalle.get("capabilities")
        if isinstance(capacidades, list) and "tools" not in capacidades:
            return EstadoMotor(MODO_OLLAMA, False, self.modelo, f"El modelo {self.modelo} no admite herramientas (tools): elegí otro.")
        return EstadoMotor(MODO_OLLAMA, True, self.modelo, f"Ollama en {self.url}.")

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
        mensaje = datos.get("message")
        if not isinstance(mensaje, dict):
            raise MotorIAError("La respuesta del motor de IA no tiene la forma esperada.")
        texto = mensaje.get("content") if isinstance(mensaje.get("content"), str) else ""
        modelo = datos.get("model") if isinstance(datos.get("model"), str) else self.modelo
        return RespuestaModelo(texto, _llamadas_de(mensaje), modelo)

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
        """Una llamada HTTP a Ollama traducida a `MotorIAError` con un mensaje para el usuario. El log guarda
        solo el código y la ruta: nunca el cuerpo, que puede repetir texto del modelo o del comercio."""
        ruta = solicitud.selector
        try:
            with _OPENER.open(solicitud, timeout=timeout) as respuesta:
                crudo = respuesta.read()
        except urllib.error.HTTPError as error:
            cuerpo = _cuerpo_de_error(error)
            logger.warning("Ollama respondió %s en %s", error.code, ruta)
            if error.code == 404:
                raise MotorIAError(f"Ollama no encuentra el modelo {self.modelo}: descargalo con `ollama pull`.") from error
            if error.code == 400 and "does not support tools" in cuerpo:
                raise MotorIAError(f"El modelo {self.modelo} no admite herramientas (tools): elegí otro.") from error
            raise MotorIAError(f"El motor de IA respondió con un error ({error.code}).") from error
        except (socket.timeout, TimeoutError) as error:
            logger.warning("Ollama no respondió a tiempo (%ss) en %s", timeout, ruta)
            raise MotorIAError("El motor de IA tardó demasiado en responder.") from error
        except urllib.error.URLError as error:
            motivo = error.reason
            if isinstance(motivo, (socket.timeout, TimeoutError)):
                logger.warning("Ollama no respondió a tiempo (%ss) en %s", timeout, ruta)
                raise MotorIAError("El motor de IA tardó demasiado en responder.") from error
            logger.info("Ollama no está disponible en %s: %s", self.url, type(motivo).__name__)
            raise MotorIAError("No hay un motor de IA local escuchando.") from error
        except (http.client.HTTPException, ConnectionError, OSError, ValueError) as error:
            logger.warning("Respuesta inválida o conexión cortada de Ollama en %s: %s", ruta, type(error).__name__)
            raise MotorIAError("El motor de IA cortó la conexión o respondió mal.") from error
        try:
            datos = json.loads(crudo.decode("utf-8"))
        except ValueError as error:
            raise MotorIAError("La respuesta del motor de IA no se pudo interpretar.") from error
        if not isinstance(datos, dict):
            raise MotorIAError("La respuesta del motor de IA no tiene la forma esperada.")
        if isinstance(datos.get("error"), str):
            raise MotorIAError("El motor de IA informó un error.")
        return datos


def _cuerpo_de_error(error: urllib.error.HTTPError) -> str:
    try:
        return error.read(400).decode("utf-8", errors="replace")
    except (OSError, http.client.HTTPException):
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
