"""Cliente ASGI mínimo para tests HTTP end-to-end, sin agregar `httpx`.

`httpx` no está instalado en este entorno (se verificó antes de
escribir esto: `import httpx` falla, y por eso también falla
`starlette.testclient.TestClient`, que lo requiere). En vez de
instalarlo, este helper llama directamente al callable ASGI de la app
real (`interfaces.web.app.app`) armando a mano un scope HTTP mínimo —
es lo mismo que hace cualquier servidor ASGI, solo que en memoria.
Usa únicamente `asyncio` y `urllib.parse` de la librería estándar.

No dispara el `lifespan` de la app (`inicializar_base_datos()`): los
tests que lo necesitan ya usan la fixture `base_datos_temporal`, que
inicializa el esquema de forma independiente del lifespan de FastAPI.

Nombre con `_` inicial a propósito: pytest no lo recolecta como
archivo de tests, es un helper compartido.
"""

import asyncio
import json
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

from interfaces.web.app import app


def _construir_cuerpo_multipart(
    formulario: dict[str, str] | None,
    archivos: dict[str, tuple[str, bytes]] | None,
) -> tuple[bytes, str]:
    """Arma un cuerpo `multipart/form-data` a mano, sin dependencias:
    campos de texto normales + archivos (nombre de campo -> (nombre de
    archivo, contenido)). Necesario para probar uploads de imagen
    contra la app real sin `httpx`."""
    boundary = "----testboundary" + secrets.token_hex(8)
    partes: list[bytes] = []
    for nombre, valor in (formulario or {}).items():
        partes.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{nombre}"\r\n\r\n{valor}\r\n'.encode()
        )
    for nombre, (nombre_archivo, contenido) in (archivos or {}).items():
        encabezado = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{nombre}"; '
            f'filename="{nombre_archivo}"\r\nContent-Type: application/octet-stream\r\n\r\n'
        ).encode()
        partes.append(encabezado + contenido + b"\r\n")
    partes.append(f"--{boundary}--\r\n".encode())
    return b"".join(partes), f"multipart/form-data; boundary={boundary}"


@dataclass
class RespuestaHttp:
    status: int
    headers: list[tuple[bytes, bytes]]
    cuerpo: bytes

    def header(self, nombre: str) -> str | None:
        """Primer valor del header `nombre` (case-insensitive), o `None`."""
        nombre_bytes = nombre.lower().encode()
        for clave, valor in self.headers:
            if clave.lower() == nombre_bytes:
                return valor.decode()
        return None

    def headers_multiples(self, nombre: str) -> list[str]:
        """Todos los valores del header `nombre` (ej. varios `Set-Cookie`)."""
        nombre_bytes = nombre.lower().encode()
        return [valor.decode() for clave, valor in self.headers if clave.lower() == nombre_bytes]

    @property
    def texto(self) -> str:
        return self.cuerpo.decode("utf-8")

    def json(self):
        """Parsea el cuerpo como JSON (respuestas de la API del POS)."""
        return json.loads(self.cuerpo.decode("utf-8"))


def solicitud(
    method: str,
    path: str,
    *,
    cookies: dict[str, str] | None = None,
    formulario: dict[str, str | list[str]] | None = None,
    archivos: dict[str, tuple[str, bytes]] | None = None,
    json_body: object | None = None,
) -> RespuestaHttp:
    """Ejecuta una request HTTP real contra la app ASGI, en memoria.

    `archivos` (nombre de campo -> (nombre de archivo, contenido))
    arma un `multipart/form-data` real, combinando los campos de
    `formulario` como texto plano dentro del mismo cuerpo -- para
    probar rutas que reciben `UploadFile` (alta/edición de producto
    con imagen), igual que un navegador con un `<form enctype=
    "multipart/form-data">`.

    Un valor de `formulario` puede ser una lista (ej.
    `{"producto_id": ["1", "2"]}`) para representar el mismo campo
    repetido varias veces -- así se arma un `<form>` con varias líneas
    del mismo nombre, como el de alta de una compra con múltiples
    productos (ver `interfaces.web.rutas.compras`). `urlencode(...,
    doseq=True)` expande cada lista a pares `clave=valor` repetidos;
    los valores escalares (`str`) se comportan exactamente igual que
    antes.

    `json_body` (cualquier valor serializable) arma un cuerpo JSON real
    con `Content-Type: application/json` -- necesario para probar
    `POST /api/ventas` (Fase 5A), que recibe un `VentaEntrada` por
    JSON, no un `<form>`. Es mutuamente excluyente con `formulario`/
    `archivos`: si se pasa `json_body`, se usa ese cuerpo.
    """
    ruta, _, query = path.partition("?")

    headers: list[tuple[bytes, bytes]] = []
    if cookies:
        valor_cookie = "; ".join(f"{clave}={valor}" for clave, valor in cookies.items())
        headers.append((b"cookie", valor_cookie.encode()))

    cuerpo = b""
    if json_body is not None:
        cuerpo = json.dumps(json_body).encode("utf-8")
        headers.append((b"content-type", b"application/json"))
    elif archivos is not None:
        cuerpo, content_type = _construir_cuerpo_multipart(formulario, archivos)
        headers.append((b"content-type", content_type.encode()))
    elif formulario is not None:
        cuerpo = urlencode(formulario, doseq=True).encode()
        headers.append((b"content-type", b"application/x-www-form-urlencoded"))
    headers.append((b"content-length", str(len(cuerpo)).encode()))

    scope = {
        "type": "http",
        "method": method,
        "path": ruta,
        "raw_path": ruta.encode(),
        "query_string": query.encode(),
        "headers": headers,
        "client": ("test", 1),
        "server": ("test", 80),
        "scheme": "http",
        "http_version": "1.1",
    }

    resultado: dict = {"status": None, "headers": [], "cuerpo": b""}
    ya_enviado = False

    async def receive():
        nonlocal ya_enviado
        if ya_enviado:
            return {"type": "http.request", "body": b"", "more_body": False}
        ya_enviado = True
        return {"type": "http.request", "body": cuerpo, "more_body": False}

    async def send(mensaje):
        if mensaje["type"] == "http.response.start":
            resultado["status"] = mensaje["status"]
            resultado["headers"] = mensaje["headers"]
        elif mensaje["type"] == "http.response.body":
            resultado["cuerpo"] += mensaje.get("body", b"")

    asyncio.run(app(scope, receive, send))
    return RespuestaHttp(status=resultado["status"], headers=resultado["headers"], cuerpo=resultado["cuerpo"])
