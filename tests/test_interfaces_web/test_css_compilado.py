"""Design system compilado (V1.17-B): la aplicación no depende de ningún CDN y el CSS versionado contiene
las clases de componentes que usan los templates.

Si este test falla después de tocar `twinx.src.css` o de usar una clase de componente nueva en un
template, falta correr `npm run build:css` y versionar `twinx.css`: el ejecutable no tiene Node."""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
TEMPLATES = RAIZ / "interfaces" / "web" / "templates"
CSS = RAIZ / "interfaces" / "web" / "static" / "css" / "twinx.css"
PREFIJOS_COMPONENTES = ("btn", "badge", "card", "kpi", "alert", "nav-pill", "input", "data-table", "segmented",
                        "empty-state", "page", "label", "field-error", "select", "textarea", "table-wrap", "muted",
                        "section-title")
_CLASES = re.compile(r'class="([^"]*)"')
_CLASE_COMPONENTE = re.compile(r"^(?:%s)(?:-[a-z0-9-]+)?$" % "|".join(re.escape(p) for p in PREFIJOS_COMPONENTES))


def _clases_de_componentes_usadas() -> set[str]:
    usadas: set[str] = set()
    for plantilla in TEMPLATES.rglob("*.html"):
        for atributo in _CLASES.findall(plantilla.read_text(encoding="utf-8")):
            for token in atributo.split():
                if _CLASE_COMPONENTE.match(token):
                    usadas.add(token)
    return usadas


HOSTS_CDN = ("cdn.tailwindcss.com", "fonts.googleapis.com", "fonts.gstatic.com", "unpkg.com", "cdn.jsdelivr.net",
             "cdnjs.cloudflare.com", "@import url(http")


def test_ningun_template_css_ni_js_carga_recursos_de_un_cdn():
    archivos = [*TEMPLATES.rglob("*.html"), CSS, *(RAIZ / "interfaces" / "web" / "static" / "js").glob("*.js")]
    con_cdn = [p.name for p in archivos if any(host in p.read_text(encoding="utf-8") for host in HOSTS_CDN)]
    assert con_cdn == []


def test_todos_los_templates_de_pagina_enlazan_el_css_compilado():
    for nombre in ("base.html", "login.html", "configuracion_inicial.html"):
        assert '/static/css/twinx.css' in (TEMPLATES / nombre).read_text(encoding="utf-8"), nombre


def test_el_css_compilado_existe_y_define_las_clases_de_componentes_usadas():
    assert CSS.exists(), "Falta interfaces/web/static/css/twinx.css: correr `npm run build:css`."
    css = CSS.read_text(encoding="utf-8")
    assert ".dark" in css and "--color-primary" in css
    faltantes = sorted(clase for clase in _clases_de_componentes_usadas() if f".{clase}" not in css)
    assert faltantes == [], f"Clases usadas en templates sin definir en twinx.css (¿falta `npm run build:css`?): {faltantes}"


def test_en_oscuro_la_pestana_activa_se_distingue_y_los_controles_nativos_son_oscuros():
    """Auditoría visual del cierre: `.nav-pill` en oscuro tapaba el color de la pestaña activa, y los íconos de
    calendario/reloj de los `<input type="date|time">` quedaban negros sobre fondo oscuro."""
    css = CSS.read_text(encoding="utf-8")
    assert ".nav-pill-active:is(.dark *){" in css
    assert re.search(r"\.dark\{[^}]*color-scheme:dark", css)
