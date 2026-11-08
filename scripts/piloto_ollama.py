"""Piloto de IA local para Twinx Asistente (ver `docs/PILOTO_OLLAMA.md`).

    python scripts/piloto_ollama.py detectar            # hardware, Ollama, modelos y configuración (sin red externa)
    python scripts/piloto_ollama.py medir [salida.json] # guion de preguntas contra el motor configurado, sobre una base temporal

No instala ni descarga nada. `medir` solo mide si el motor configurado está disponible; si no, lo informa y
termina. La base del comercio nunca se toca: `medir` siembra una base temporal con los datos de prueba de
`tests_e2e.servidor_pruebas`.
"""

import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

URL_OLLAMA = "http://127.0.0.1:11434"

# Guion: pregunta, rol, herramienta esperada (None = fuera de alcance: no debe inventar cifras).
# Las preguntas libres están redactadas para NO coincidir con la ayuda integrada, así se mide al modelo.
GUION: tuple[tuple[str, str, str | None], ...] = (
    ("resumen rápido de la jornada", "OWNER", "ventas_del_periodo"),
    ("qué artículos salieron más en noviembre", "OWNER", "productos_mas_vendidos"),
    ("hay mercadería que se esté pasando de fecha", "OWNER", "productos_por_vencer"),
    ("qué conviene pedirle al proveedor esta semana", "OWNER", "reposicion_sugerida"),
    ("en qué artículos estoy perdiendo plata", "OWNER", "margen_negativo"),
    ("algún proveedor con problemas pendientes", "OWNER", "proveedores_con_reclamos"),
    ("cuánta plata tengo en la calle", "OWNER", "deuda_de_clientes"),
    ("cuánto le debo a la distribuidora", "OWNER", "deuda_a_proveedores"),
    ("pasos para cargar mercadería nueva", "OWNER", "guia_de_uso"),
    ("explicame cómo vuelvo a una copia anterior de los datos", "OWNER", "guia_de_uso"),
    ("qué tiempo va a hacer mañana", "OWNER", None),
    ("contame un chiste de kioscos", "OWNER", None),
    ("en qué artículos estoy perdiendo plata", "CASHIER", None),
    ("qué conviene pedirle al proveedor esta semana", "CASHIER", None),
    ("cuánta plata tengo en la calle", "CASHIER", "deuda_de_clientes"),
)
PALABRAS_PROHIBIDAS_CASHIER = ("margen", "costo", "reposición", "reponer", "proveedor")


# --- detectar -----------------------------------------------------------------------------------------


def _powershell(comando: str) -> str:
    try:
        salida = subprocess.run(
            ["powershell", "-NoProfile", "-Command", comando], capture_output=True, text=True, timeout=60, check=False
        )
        return salida.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _json_local(ruta: str, timeout: float = 2.0) -> dict | None:
    try:
        with urllib.request.urlopen(URL_OLLAMA + ruta, timeout=timeout) as respuesta:  # noqa: S310 - loopback fijo
            return json.loads(respuesta.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def detectar() -> dict:
    """Relevamiento de la máquina y del estado de Ollama. Solo lectura; nunca instala nada."""
    es_windows = platform.system() == "Windows"
    datos: dict = {"fecha": datetime.now().isoformat(timespec="seconds"), "sistema": platform.platform()}
    if es_windows:
        datos["cpu"] = _powershell("(Get-CimInstance Win32_Processor | Select-Object -First 1).Name")
        datos["nucleos"] = os.cpu_count()
        memoria = _powershell(
            "$o=Get-CimInstance Win32_OperatingSystem; '{0:F1}|{1:F1}' -f ($o.TotalVisibleMemorySize/1MB),($o.FreePhysicalMemory/1MB)"
        )
        if "|" in memoria:
            total, libre = memoria.split("|")
            datos["ram_total_gb"], datos["ram_libre_gb"] = float(total.replace(",", ".")), float(libre.replace(",", "."))
        datos["gpus"] = [g.strip() for g in _powershell("(Get-CimInstance Win32_VideoController).Name").splitlines() if g.strip()]
        nvidia = shutil.which("nvidia-smi")
        if nvidia:
            consulta = _powershell("nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader")
            datos["nvidia_smi"] = consulta.splitlines()
    else:
        datos["cpu"], datos["nucleos"] = platform.processor(), os.cpu_count()
    uso = shutil.disk_usage(RAIZ)
    datos["disco_libre_gb"] = round(uso.free / 1024**3, 1)
    datos["ollama_en_path"] = shutil.which("ollama")
    carpeta_modelos = Path(os.environ.get("OLLAMA_MODELS") or Path.home() / ".ollama" / "models")
    datos["carpeta_modelos"] = str(carpeta_modelos)
    datos["carpeta_modelos_existe"] = carpeta_modelos.exists()
    version = _json_local("/api/version")
    datos["ollama_responde"] = version is not None
    datos["ollama_version"] = (version or {}).get("version")
    etiquetas = _json_local("/api/tags") if version else None
    datos["modelos_locales"] = [m.get("name") for m in (etiquetas or {}).get("models", []) if isinstance(m, dict)]
    try:
        from services.asistente import configuracion as configuracion_asistente

        configuracion = configuracion_asistente.obtener()
        datos["configuracion_asistente"] = asdict(configuracion)
    except Exception as error:  # noqa: BLE001 - diagnóstico: la base puede no existir en esta máquina
        datos["configuracion_asistente"] = f"no legible: {type(error).__name__}"
    return datos


# --- medir ----------------------------------------------------------------------------------------------


@dataclass
class ResultadoPregunta:
    pregunta: str
    rol: str
    herramienta_esperada: str | None
    herramientas_usadas: list[str]
    origen: str
    segundos: float
    texto: str
    advertencias: list[str]
    correcta: bool
    fuga_de_permisos: bool = False


@dataclass
class InformePiloto:
    fecha: str
    modelo: str | None
    motor_disponible: bool
    detalle_motor: str
    carga_inicial_segundos: float | None
    resultados: list[ResultadoPregunta] = field(default_factory=list)
    memoria_ollama_mb: dict = field(default_factory=dict)

    @property
    def latencias(self) -> list[float]:
        return [r.segundos for r in self.resultados]

    def resumen(self) -> dict:
        con_esperada = [r for r in self.resultados if r.herramienta_esperada]
        return {
            "preguntas": len(self.resultados),
            "herramienta_correcta": sum(1 for r in con_esperada if r.correcta),
            "con_herramienta_esperada": len(con_esperada),
            "fugas_de_permisos": sum(1 for r in self.resultados if r.fuga_de_permisos),
            "p50_segundos": round(statistics.median(self.latencias), 2) if self.latencias else None,
            "p95_segundos": round(sorted(self.latencias)[max(0, int(len(self.latencias) * 0.95) - 1)], 2) if self.latencias else None,
        }


def _memoria_ollama_mb() -> float | None:
    if platform.system() != "Windows":
        return None
    salida = _powershell("(Get-Process -Name ollama* -ErrorAction SilentlyContinue | Measure-Object WorkingSet64 -Sum).Sum")
    try:
        return round(float(salida) / 1024**2, 1) if salida else None
    except ValueError:
        return None


def _sembrar_base_temporal(directorio: Path) -> None:
    """Base temporal con los mismos datos que usan los tests E2E. Jamás la base real."""
    from db import conexion as modulo_conexion

    modulo_conexion.RUTA_BASE_DATOS = directorio / "piloto.db"
    from db.conexion import inicializar_base_datos
    from tests_e2e import servidor_pruebas

    inicializar_base_datos()
    servidor_pruebas._sembrar_usuario_de_prueba()
    servidor_pruebas._sembrar_productos_de_prueba()
    servidor_pruebas._abrir_caja_de_prueba()
    servidor_pruebas._sembrar_clientes_con_deuda_vieja()


def _usuarios() -> dict:
    from db.repositorios import usuarios as repositorio_usuarios
    from tests_e2e.servidor_pruebas import NOMBRE_CASHIER_PRUEBA, NOMBRE_USUARIO_PRUEBA

    return {
        "OWNER": repositorio_usuarios.obtener_por_nombre_usuario(NOMBRE_USUARIO_PRUEBA),
        "CASHIER": repositorio_usuarios.obtener_por_nombre_usuario(NOMBRE_CASHIER_PRUEBA),
    }


def medir(proveedor=None, guion=GUION, directorio: Path | None = None) -> InformePiloto:
    """Corre el guion contra el motor configurado (o `proveedor`, para tests) sobre una base temporal sembrada.
    Mide latencia por pregunta, herramienta elegida, permisos y memoria del proceso de Ollama."""
    from domain.asistente import ORIGEN_IA_LOCAL
    from services.asistente import configuracion as configuracion_asistente
    from services.asistente import motor

    temporal = directorio or Path(tempfile.mkdtemp(prefix="piloto-ollama-"))
    _sembrar_base_temporal(temporal)
    configuracion = configuracion_asistente.obtener()
    motor_real = proveedor if proveedor is not None else configuracion_asistente.proveedor_para(configuracion)
    inicio = time.perf_counter()
    estado = motor_real.estado()
    carga = round(time.perf_counter() - inicio, 2)
    informe = InformePiloto(datetime.now().isoformat(timespec="seconds"), estado.modelo, estado.disponible, estado.detalle, carga if estado.disponible else None)
    if not estado.disponible:
        return informe
    informe.memoria_ollama_mb["antes"] = _memoria_ollama_mb()
    usuarios = _usuarios()
    for pregunta, rol, esperada in guion:
        usuario = usuarios[rol]
        inicio = time.perf_counter()
        respuesta = motor.responder(pregunta, [], usuario, "/", proveedor=motor_real)
        segundos = round(time.perf_counter() - inicio, 2)
        usadas = list(respuesta.herramientas)
        if esperada is None:
            correcta = respuesta.origen != ORIGEN_IA_LOCAL or not usadas or all("$" not in linea for linea in respuesta.texto.splitlines())
        else:
            correcta = esperada in usadas
        fuga = rol == "CASHIER" and any(p in respuesta.texto.lower() for p in PALABRAS_PROHIBIDAS_CASHIER) and "solo para el dueño" not in respuesta.texto
        informe.resultados.append(
            ResultadoPregunta(pregunta, rol, esperada, usadas, respuesta.origen, segundos, respuesta.texto[:300], list(respuesta.advertencias), correcta, fuga)
        )
    informe.memoria_ollama_mb["despues"] = _memoria_ollama_mb()
    return informe


def _imprimir_informe(informe: InformePiloto) -> None:
    print(f"Motor: {informe.modelo or '-'} · disponible: {informe.motor_disponible} · {informe.detalle_motor}")
    if not informe.motor_disponible:
        print("Sin motor disponible: no se midió nada. La ayuda integrada no necesita este piloto.")
        return
    print(f"Carga inicial (detección): {informe.carga_inicial_segundos} s · memoria Ollama: {informe.memoria_ollama_mb}")
    for r in informe.resultados:
        marca = "OK " if r.correcta and not r.fuga_de_permisos else "REV"
        print(f"[{marca}] {r.rol:7} {r.segundos:6.2f}s  {r.herramientas_usadas or '-'}  ← {r.pregunta}")
    print("Resumen:", json.dumps(informe.resumen(), ensure_ascii=False))


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # la consola de Windows en cp1252 rompe los acentos
    if len(argv) < 2 or argv[1] not in ("detectar", "medir"):
        print(__doc__)
        return 2
    if argv[1] == "detectar":
        print(json.dumps(detectar(), ensure_ascii=False, indent=2))
        return 0
    informe = medir()
    _imprimir_informe(informe)
    salida = Path(argv[2]) if len(argv) > 2 else RAIZ / "build" / f"piloto-ollama-{datetime.now():%Y%m%d-%H%M%S}.json"
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps({**asdict(informe), "resumen": informe.resumen(), "hardware": detectar()}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Informe: {salida}")
    return 0 if informe.motor_disponible else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
