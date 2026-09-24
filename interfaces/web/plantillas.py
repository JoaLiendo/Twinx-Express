"""Instancia compartida de Jinja2Templates para todos los routers.

Registra el filtro `dinero` para formatear centavos (`int`) como texto
decimal en los templates (ej. `{{ producto.precio_venta_centavos | dinero }}`
-> "150.50"), reutilizando la única conversión permitida entre esas dos
representaciones (ver `domain.dinero`).
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from domain.dinero import centavos_a_texto, centavos_a_texto_localizado
from version import VERSION

DIRECTORIO_TEMPLATES = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(DIRECTORIO_TEMPLATES))
templates.env.filters["dinero"] = centavos_a_texto
# Solo para texto de solo lectura (listados, totales, tickets): nunca usar
# en el `value=` de un input editable, ver domain.dinero.centavos_a_texto_localizado.
templates.env.filters["dinero_ar"] = centavos_a_texto_localizado
# Pie de página (base.html y login.html): versión de la aplicación para soporte.
templates.env.globals["app_version"] = VERSION
