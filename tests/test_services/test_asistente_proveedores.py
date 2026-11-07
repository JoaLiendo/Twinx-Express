"""Adaptador de Ollama local (V1.18) contra un servidor HTTP falso en loopback: detección, chat con
herramientas, errores, timeouts y la regla de "solo local". Sin Ollama instalado ni red."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from excepciones import MotorIAError
from services.asistente.proveedores import (
    LlamadaHerramienta,
    MensajeModelo,
    ProveedorFalso,
    ProveedorOllama,
    RespuestaModelo,
    validar_nombre_modelo,
    validar_url_local,
)


class _OllamaFalso(BaseHTTPRequestHandler):
    """Responde como Ollama según el `guion` del servidor: por ruta, un (status, cuerpo) o una demora."""

    guion: dict = {}
    recibidos: list = []

    def log_message(self, *_):  # silencio en los tests
        pass

    def _responder(self, ruta: str, cuerpo_pedido: dict | None):
        respuesta = self.guion.get(ruta)
        if callable(respuesta):
            respuesta = respuesta(cuerpo_pedido)
        if respuesta is None:
            self.send_response(404)
            self.end_headers()
            return
        estado, cuerpo = respuesta
        if estado == "demora":
            time.sleep(cuerpo)
            estado, cuerpo = 200, {"message": {"role": "assistant", "content": "tarde"}, "done": True}
        datos = json.dumps(cuerpo).encode("utf-8") if not isinstance(cuerpo, bytes) else cuerpo
        self.send_response(estado)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def do_GET(self):
        self._responder(self.path, None)

    def do_POST(self):
        largo = int(self.headers.get("Content-Length") or 0)
        cuerpo = json.loads(self.rfile.read(largo) or b"{}")
        self.recibidos.append((self.path, cuerpo))
        self._responder(self.path, cuerpo)


@pytest.fixture
def servidor():
    """Un Ollama falso en 127.0.0.1 con puerto libre; el test configura `guion` antes de llamar."""
    _OllamaFalso.guion = {}
    _OllamaFalso.recibidos = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaFalso)
    hilo = threading.Thread(target=httpd.serve_forever, daemon=True)
    hilo.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", _OllamaFalso
    finally:
        httpd.shutdown()
        httpd.server_close()


MODELOS = {"models": [{"name": "llama3.2:3b", "model": "llama3.2:3b"}, {"name": "otro:latest"}]}


class TestSoloLocal:
    @pytest.mark.parametrize("url", ["http://127.0.0.1:11434", "http://localhost:11434/", "http://[::1]:11434"])
    def test_acepta_loopback(self, url):
        assert validar_url_local(url).startswith("http")

    @pytest.mark.parametrize(
        "url", ["http://192.168.0.10:11434", "https://api.openai.com", "ftp://127.0.0.1", "http://user:x@127.0.0.1:11434", "http://127.0.0.1:11434/v1", ""]
    )
    def test_rechaza_todo_lo_que_no_sea_local_y_limpio(self, url):
        with pytest.raises(MotorIAError):
            validar_url_local(url)

    def test_nombre_de_modelo(self):
        assert validar_nombre_modelo(" llama3.2:3b ") == "llama3.2:3b"
        assert validar_nombre_modelo("usuario/modelo:q4") == "usuario/modelo:q4"
        for malo in ("", "modelo con espacios", "x" * 200, "../etc", "a:b:c"):
            with pytest.raises(MotorIAError):
                validar_nombre_modelo(malo)


class TestEstado:
    def test_conectado_cuando_el_modelo_esta_descargado(self, servidor):
        url, falso = servidor
        falso.guion["/api/tags"] = (200, MODELOS)
        estado = ProveedorOllama(url=url, modelo="llama3.2:3b").estado()
        assert (estado.disponible, estado.modelo) == (True, "llama3.2:3b")
        assert estado.etiqueta == "IA local conectada · llama3.2:3b"

    def test_modelo_sin_descargar_no_esta_disponible_y_lo_explica(self, servidor):
        url, falso = servidor
        falso.guion["/api/tags"] = (200, MODELOS)
        estado = ProveedorOllama(url=url, modelo="mistral:7b").estado()
        assert estado.disponible is False and "no está descargado" in estado.detalle and "llama3.2:3b" in estado.detalle

    def test_sin_servidor_no_hay_motor_y_no_se_lanza(self):
        estado = ProveedorOllama(url="http://127.0.0.1:9", modelo="llama3.2:3b").estado()
        assert estado.disponible is False and estado.detalle


class TestConversar:
    def test_manda_herramientas_y_limites_y_lee_las_llamadas(self, servidor):
        url, falso = servidor
        falso.guion["/api/chat"] = (
            200,
            {
                "model": "llama3.2:3b",
                "message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "ventas_del_periodo", "arguments": {"periodo": "hoy"}}}]},
                "done": True,
            },
        )
        proveedor = ProveedorOllama(url=url, modelo="llama3.2:3b", num_ctx=2048, num_predict=99, timeout_segundos=5)
        herramientas = [{"type": "function", "function": {"name": "ventas_del_periodo", "parameters": {}}}]
        respuesta = proveedor.conversar([MensajeModelo("system", "reglas"), MensajeModelo("user", "¿y hoy?")], herramientas)

        assert respuesta.llamadas == (LlamadaHerramienta("ventas_del_periodo", {"periodo": "hoy"}),)
        assert respuesta.modelo == "llama3.2:3b"
        ruta, cuerpo = falso.recibidos[0]
        assert ruta == "/api/chat" and cuerpo["stream"] is False and cuerpo["tools"] == herramientas
        assert cuerpo["options"] == {"num_ctx": 2048, "num_predict": 99, "temperature": 0.1}
        assert cuerpo["keep_alive"] == "5m"
        assert cuerpo["messages"][0] == {"role": "system", "content": "reglas"}

    def test_los_resultados_de_herramientas_viajan_como_tool_y_las_llamadas_previas_como_tool_calls(self, servidor):
        url, falso = servidor
        falso.guion["/api/chat"] = (200, {"message": {"role": "assistant", "content": "Vendiste $ 10,00."}, "done": True})
        mensajes = [
            MensajeModelo("assistant", "", llamadas=(LlamadaHerramienta("ventas_del_periodo", {"periodo": "hoy"}),)),
            MensajeModelo("tool", '{"total": 1000}', nombre_herramienta="ventas_del_periodo"),
        ]
        respuesta = ProveedorOllama(url=url).conversar(mensajes, [])
        assert respuesta.texto == "Vendiste $ 10,00." and respuesta.llamadas == ()
        _, cuerpo = falso.recibidos[0]
        assert cuerpo["messages"][0]["tool_calls"] == [{"function": {"name": "ventas_del_periodo", "arguments": {"periodo": "hoy"}}}]
        assert cuerpo["messages"][1] == {"role": "tool", "content": '{"total": 1000}', "tool_name": "ventas_del_periodo"}
        assert "tools" not in cuerpo

    def test_argumentos_como_texto_json_se_interpretan(self, servidor):
        url, falso = servidor
        falso.guion["/api/chat"] = (
            200,
            {"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "guia_de_uso", "arguments": '{"tema": "compras"}'}}]}, "done": True},
        )
        respuesta = ProveedorOllama(url=url).conversar([MensajeModelo("user", "x")], [])
        assert respuesta.llamadas[0].argumentos == {"tema": "compras"}

    def test_modelo_inexistente_404_da_un_mensaje_claro(self, servidor):
        url, falso = servidor
        falso.guion["/api/chat"] = (404, {"error": "model 'nada' not found"})
        with pytest.raises(MotorIAError, match="ollama pull"):
            ProveedorOllama(url=url, modelo="nada").conversar([MensajeModelo("user", "x")], [])

    def test_error_500_y_cuerpo_no_json_se_traducen(self, servidor):
        url, falso = servidor
        falso.guion["/api/chat"] = (500, {"error": "boom"})
        with pytest.raises(MotorIAError, match="500"):
            ProveedorOllama(url=url).conversar([MensajeModelo("user", "x")], [])
        falso.guion["/api/chat"] = (200, b"esto no es json")
        with pytest.raises(MotorIAError, match="interpretar"):
            ProveedorOllama(url=url).conversar([MensajeModelo("user", "x")], [])

    def test_timeout_se_informa_sin_colgar(self, servidor):
        url, falso = servidor
        falso.guion["/api/chat"] = ("demora", 1.5)
        inicio = time.monotonic()
        with pytest.raises(MotorIAError, match="tardó demasiado"):
            ProveedorOllama(url=url, timeout_segundos=0.3).conversar([MensajeModelo("user", "x")], [])
        assert time.monotonic() - inicio < 1.4

    def test_sin_servidor_se_informa_como_motor_ausente(self):
        with pytest.raises(MotorIAError, match="No hay un motor"):
            ProveedorOllama(url="http://127.0.0.1:9").conversar([MensajeModelo("user", "x")], [])


class TestProveedorFalso:
    def test_sigue_el_guion_y_registra_lo_que_vio(self):
        falso = ProveedorFalso([RespuestaModelo("", (LlamadaHerramienta("guia_de_uso", {"tema": "caja"}),)), RespuestaModelo("Listo.")])
        assert falso.conversar([MensajeModelo("user", "a")], [{"x": 1}]).llamadas[0].nombre == "guia_de_uso"
        assert falso.conversar([MensajeModelo("user", "b")], []).texto == "Listo."
        assert falso.conversar([MensajeModelo("user", "c")], []).texto == "Listo."
        assert len(falso.recibidos) == 3 and falso.recibidos[0][1] == [{"x": 1}]
        assert ProveedorFalso(disponible=False).estado().disponible is False
