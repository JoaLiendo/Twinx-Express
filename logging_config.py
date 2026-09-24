"""Registro persistente en archivo de la aplicación (V1.1).

En el ejecutable, la consola es lo único que ve el operador y desaparece al
cerrarla: sin un archivo, los `logger.info/warning/error` de la aplicación no
dejaban rastro para diagnosticar un problema de arranque u operación. Este
módulo agrega un archivo rotativo local, sin ningún envío a servicios externos.
"""

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

NOMBRE_ARCHIVO_LOG = "twinx_express.log"
# 1 MB por archivo y 5 rotados: acota el disco usado a ~6 MB, suficiente para
# semanas de uso normal de un kiosco.
TAMANO_MAXIMO_BYTES = 1_000_000
ARCHIVOS_ROTADOS = 5
FORMATO = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# Loggers de uvicorn que no propagan al logger raíz (ver `uvicorn.config.LOGGING_CONFIG`).
_LOGGERS_DE_UVICORN = ("uvicorn", "uvicorn.access")
_NOMBRE_DEL_HANDLER = "twinx_express_archivo"

_PATRON_SENSIBLE = re.compile(
    r"(?i)\b(password|contrase[nñ]a|clave|passwd|token|cookie|session|sesion|hash)\b(\s*[=:]\s*)([^\s,;&]+)"
)


class FiltroDeDatosSensibles(logging.Filter):
    """Última defensa: enmascara valores de campos con nombre sensible
    (contraseña, token, cookie, sesión, hash) si algún mensaje llegara a
    incluirlos. La regla principal sigue siendo no registrarlos nunca."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _PATRON_SENSIBLE.sub(r"\1\2***", record.getMessage())
        record.args = None
        return True


def configurar_logging(directorio_logs: Path, nivel: int = logging.INFO) -> Path:
    """Agrega un archivo de log rotativo al logger raíz y a los de uvicorn.

    Idempotente: llamarla de nuevo no duplica el handler. Devuelve la ruta
    del archivo de log. Debe llamarse *después* de crear `uvicorn.Config`,
    que reconfigura los loggers de uvicorn y descartaría el handler.
    """
    directorio_logs.mkdir(parents=True, exist_ok=True)
    ruta_log = directorio_logs / NOMBRE_ARCHIVO_LOG

    raiz = logging.getLogger()
    raiz.setLevel(nivel)

    handler = next((h for h in raiz.handlers if h.get_name() == _NOMBRE_DEL_HANDLER), None)
    if handler is None:
        handler = RotatingFileHandler(
            ruta_log, maxBytes=TAMANO_MAXIMO_BYTES, backupCount=ARCHIVOS_ROTADOS, encoding="utf-8"
        )
        handler.set_name(_NOMBRE_DEL_HANDLER)
        handler.setLevel(nivel)
        handler.setFormatter(logging.Formatter(FORMATO))
        handler.addFilter(FiltroDeDatosSensibles())
        raiz.addHandler(handler)

    for nombre in _LOGGERS_DE_UVICORN:
        logger_uvicorn = logging.getLogger(nombre)
        if handler not in logger_uvicorn.handlers:
            logger_uvicorn.addHandler(handler)

    return ruta_log
