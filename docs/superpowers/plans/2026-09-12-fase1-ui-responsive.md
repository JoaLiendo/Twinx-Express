# Fase 1 — Arquitectura visual y responsive de Stock-app

> **Para quien ejecute este plan:** se ejecuta en la misma sesión que lo escribió (sin handoff a otro agente), así que las tareas no repiten código completo que ya está descripto en el texto — el detalle vive en el archivo real al implementarlo. NO se hacen commits (instrucción explícita del usuario): cada tarea termina con "marcar hecho" en vez de `git commit`. NO hay TDD clásico: la web no tiene suite de tests (`tests/` solo cubre `db/`, `domain/`, `services/`) y no se puede agregar `httpx`/`TestClient` sin sumar una dependencia nueva (prohibido). La verificación de cada tarea es: `pytest` sigue en verde + chequeo manual (curl / lectura del HTML renderizado / revisión visual).

**Goal:** Rediseñar la capa de presentación (`interfaces/web/templates/`, `base.html`, navegación) para que sea responsive (375/768/1024/1440) y prepare la navegación completa del producto (Ventas, Pedidos, Precios, Caja, Stock, Proveedores, Reportes, Empleados), sin tocar lógica de negocio, sin autenticación real, sin dependencias nuevas.

**Architecture:** Todo vive en `interfaces/web/` (capa de presentación, ver CLAUDE.md sección 3). Se agrega un archivo de macros Jinja (`templates/_componentes.html`) para botón/card/badge/estado vacío/input. La lista de navegación se centraliza en un módulo Python nuevo (`interfaces/web/navegacion.py`) para que en el futuro un filtro por rol sea un cambio de una función, no de N templates — hoy esa función no filtra nada. Se agregan 5 routers nuevos (uno por sección cascarón) que solo renderizan un template estático, siguiendo el mismo patrón que `dashboard.py`.

**Tech Stack:** FastAPI + Jinja2 (vía `Jinja2Templates`), Tailwind CDN (`<script src="https://cdn.tailwindcss.com">`), JavaScript vanilla inline. Cero dependencias nuevas.

**Spec:** instrucciones del usuario en el mensaje "AUTORIZO LA IMPLEMENTACIÓN DE LA FASE 1" (2026-09-12), resumidas en `## Global Constraints` abajo.

## Global Constraints

- NO autenticación/login real, NO roles/permisos reales, NO modelos de usuario nuevos, NO cambios de esquema de base de datos.
- NO lógica de negocio nueva (POS, pedidos, proveedores, reportes, precios, caja funcional siguen sin implementarse).
- NO HTMX, NO React/Vue/Next.js, NO dependencias nuevas (`requirements.txt` no cambia), NO Node/build system, NO librerías de íconos (íconos = SVG inline, como ya se hace).
- Mantener Inter, slate, indigo, emerald, rose, amber, sky y el tono visual actual — evolucionar, no reemplazar.
- No modificar `services/`, `domain/`, `db/` salvo estrictamente necesario (no se prevé necesidad — el dashboard usa funciones que ya existen).
- Solo cambiar la etiqueta visible "Productos" → "Stock" en el menú; no tocar rutas (`/productos`), nombres de módulos, ni tests.
- Responsive verificado en 375px, 768px, 1024px, 1440px.
- NO commit al finalizar.
- Si aparece una decisión arquitectónica no cubierta acá, PARAR y preguntar en vez de improvisar.

---

## Contexto relevado (para no releer todo el repo)

- Stack real: **FastAPI**, no Flask. Rutas en `interfaces/web/rutas/*.py`, templates Jinja2 en `interfaces/web/templates/`, instancia compartida en `interfaces/web/plantillas.py` (filtro `dinero`).
- `interfaces/web/utilidades.py::contexto_base()` ya inyecta `caja_abierta` en todas las páginas (se usa en el header). Es el lugar natural para agregar `nav_items`.
- `base.html` actual: sidebar fija `w-64` sin ningún comportamiento responsive (no hay `hidden`/`md:`/`lg:` en el aside). 4 items de nav hardcodeados con SVGs inline. Toasts vía query params (`?msg=&tipo=`), JS vanilla inline al final del `<body>`.
- No existe ninguna suite de tests para la capa web (`tests/` solo tiene `test_db/`, `test_domain/`, `test_services/`). `httpx` no está instalado (necesario para `TestClient` de FastAPI) → no se agrega, así que no hay tests automáticos nuevos para rutas/templates.
- Datos reales ya disponibles para el dashboard (sin lógica nueva):
  - `services.servicio_caja.consultar_estado()` → bool (ya usado).
  - `services.servicio_caja.calcular_arqueo_del_dia()` → `ArqueoCaja(total_vendido_centavos, total_efectivo_ventas_centavos, efectivo_estimado_centavos, cantidad_ventas, ventas)`.
  - `services.servicio_caja.listar_movimientos()` → lista cronológica de `MovimientoCaja` (hay que invertir e ir a los últimos N, igual que hace `rutas/caja.py`).
  - `services.servicio_stock.listar_stock_critico()` → `list[Producto]` (ya usado).
  - No existe nada de "pedidos" → placeholder explícito.
- Patrones visuales ya establecidos a formalizar en macros: botón primario (`bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg font-semibold`), botón secundario (`bg-slate-100 hover:bg-slate-200 text-slate-700`), botón peligro (`bg-rose-50 hover:bg-rose-100 text-rose-700`), badge de estado (pill con dot: `rounded-full ring-1 ring-inset` + color semántico), card (`rounded-2xl border border-slate-200 bg-white shadow-sm`), estado vacío (`rounded-2xl border border-dashed border-slate-300 text-center py-16`), input (`rounded-lg border border-slate-300 px-3.5 py-2.5 focus:ring-2`).
- Anchos fijos que rompen en 375px y hay que revisar: inputs con `w-64`/`w-56`/`w-96` en formularios de búsqueda (`productos/lista.html`, `ventas/pos.html`), el aside del carrito `w-96` en `ventas/pos.html`, el padding del `<main>` (`p-8`) y del header (`px-8`).

---

## File Structure

**Crear:**
- `interfaces/web/navegacion.py` — lista `NAV_ITEMS` (dicts: `href`, `etiqueta`, `icono`) usada por `base.html`. Único lugar para agregar/quitar secciones o (a futuro) filtrar por rol.
- `interfaces/web/templates/_componentes.html` — macros Jinja: `boton()`, `badge()`, `card()` (call-macro), `estado_vacio()`, `campo_input()`.
- `interfaces/web/rutas/pedidos.py`, `precios.py`, `proveedores.py`, `reportes.py`, `empleados.py` — un router por sección, cada uno con un único `GET` que renderiza su template cascarón.
- `interfaces/web/templates/pedidos.html`, `precios.html`, `proveedores.html`, `reportes.html`, `empleados.html` — cascarones visuales (`estado_vacio` + descripción de lo que vendrá).

**Modificar:**
- `interfaces/web/templates/base.html` — reescritura responsive completa (sidebar desktop 256px / rail tablet ~64px / drawer mobile), navegación desde `NAV_ITEMS`, header adaptado, main con padding responsive.
- `interfaces/web/utilidades.py` — `contexto_base()` agrega `"nav_items": NAV_ITEMS`.
- `interfaces/web/app.py` — registrar los 5 routers nuevos.
- `interfaces/web/rutas/dashboard.py` — agregar `arqueo` y `movimientos_recientes` al contexto.
- `interfaces/web/templates/dashboard.html` — nuevas secciones (ventas de hoy, pedidos pendientes placeholder, movimientos recientes, alertas, accesos rápidos) usando las macros nuevas.
- `interfaces/web/templates/productos/lista.html`, `ventas/pos.html`, `caja/panel.html` — solo ajustes responsive (anchos fijos → fluidos, stacking en mobile, padding/touch targets). Sin tocar lógica ni estructura de datos.
- `interfaces/web/templates/productos/nuevo.html`, `productos/editar.html`, `productos/importar.html`, `caja/arqueo.html` — verificación y, si hace falta, mismo tipo de ajuste responsive menor.

**No se toca:** `services/`, `domain/`, `db/`, `excepciones.py`, `config.py`, `static/js/pos.js` (su lógica de carrito no depende del layout), tests existentes.

---

## Tareas

### Tarea 1 — Módulo de navegación centralizado

**Archivos:** Crear `interfaces/web/navegacion.py`. Modificar `interfaces/web/utilidades.py`.

- [ ] Crear `NAV_ITEMS`: lista de 9 dicts (Dashboard, Ventas, Pedidos, Precios, Caja, Stock, Proveedores, Reportes, Empleados) con `href`, `etiqueta`, `icono` (clave string para el SVG que dibuja `base.html`). "Stock" apunta a `/productos` (no se renombra la ruta).
- [ ] En `utilidades.py`, importar `NAV_ITEMS` y agregarlo a `contexto_base()` como `"nav_items": NAV_ITEMS`.
- [ ] Verificar: `python -c "from interfaces.web.utilidades import contexto_base; print(contexto_base())"` desde `stock_app/` no lanza excepción y devuelve `nav_items` con 9 elementos.

### Tarea 2 — Macros Jinja reutilizables

**Archivos:** Crear `interfaces/web/templates/_componentes.html`.

- [ ] Macro `boton(texto, href=none, tipo='button', variante='primario', icono_svg=none, extra='', name=none, value=none)`: variantes `primario` (indigo), `secundario` (slate-100), `peligro` (rose-50), `oscuro` (slate-900, el que hoy se usa para "Buscar"/"Filtrar"). Renderiza `<a>` si hay `href`, si no `<button>`.
- [ ] Macro `badge(texto, variante='neutral')`: variantes `exito` (emerald), `alerta` (rose), `advertencia` (amber), `info` (indigo), `neutral` (slate) — mismo patrón pill+dot que ya existe en dashboard/productos/caja.
- [ ] Macro `card()` como call-macro (`{% macro card(extra='') %}<div class="rounded-2xl border border-slate-200 bg-white shadow-sm {{ extra }}">{{ caller() }}</div>{% endmacro %}`).
- [ ] Macro `estado_vacio(titulo, subtitulo=none, icono_svg=none)`: el borde punteado ya usado en productos/ventas, pero parametrizado.
- [ ] Macro `campo_input(label, name, tipo='text', valor='', placeholder='', requerido=false, extra='', anillo='indigo')`: el patrón de label+input ya repetido en caja/productos.
- [ ] Verificar: no hay chequeo automático posible (son macros Jinja, no Python) — se valida al usarlas en las tareas siguientes; si el server no levanta o tira `TemplateSyntaxError`, se corrige acá.

### Tarea 3 — `base.html` responsive (sidebar / rail / drawer)

**Archivos:** Modificar `interfaces/web/templates/base.html`.

- [ ] Reemplazar el `<aside>` fijo por tres presentaciones controladas con clases responsive de Tailwind (sin JS de por medio para desktop/tablet, solo para el drawer mobile):
  - **Desktop (`lg:` ≥1024px):** sidebar `lg:w-64 lg:flex` con logo, label + ícono por item, footer "Fase Beta local".
  - **Tablet (`md:` 768–1023px):** rail `md:w-16 md:flex lg:hidden`, mismos items solo ícono, con `title="{{ etiqueta }}"` para accesibilidad (no hay tooltip custom, es nativo del navegador).
  - **Mobile (`<768px`):** aside oculto (`hidden md:flex` en el contenedor que agrupa rail+sidebar). En su lugar, un botón hamburguesa en el header abre un drawer off-canvas (`<div id="drawer-nav">`, fixed, `inset-0`, backdrop semitransparente + panel deslizante con los mismos `NAV_ITEMS`, ancho `w-72 max-w-[80vw]`).
- [ ] Iterar `NAV_ITEMS` (viene en el contexto vía `contexto_base()`) en vez de la lista hardcodeada `{% set nav_items = [...] %}` actual.
- [ ] Agregar 4 SVGs inline nuevos (mismo estilo `stroke-width="1.6"` que los existentes) para `pedidos` (clipboard), `precios` (etiqueta/tag), `proveedores` (camión), `reportes` (barras), `empleados` (personas). Reusar el bloque `{% if/elif %}` existente, ahora con 9 ramas.
- [ ] JS vanilla inline (junto al script de toasts existente) para abrir/cerrar el drawer: toggle de una clase (ej. `translate-x-full` ↔ `translate-x-0`) al click del botón hamburguesa, y al click en el backdrop o en un link del drawer.
- [ ] Header: mostrar el botón hamburguesa solo `md:hidden`; en tablet/desktop no se ve.
- [ ] Ajustar padding responsive: header `px-4 md:px-6 lg:px-8`, `<main>` `p-4 md:p-6 lg:p-8`.
- [ ] Verificar arrancando el servidor (`python -m interfaces.web.app` en background) y con `curl -s http://127.0.0.1:8000/ | grep -c 'drawer-nav'` (debe ser 1) y `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/` (debe ser 200).

### Tarea 4 — Etiqueta "Productos" → "Stock"

**Archivos:** ya cubierto por `navegacion.py` (Tarea 1) — este ítem es solo de verificación.

- [ ] Confirmar que en `NAV_ITEMS` el item con `href="/productos"` tiene `etiqueta="Stock"`.
- [ ] Confirmar que ninguna ruta, redirect, nombre de archivo o test menciona el texto "Stock" como identificador (solo debe cambiar el texto visible del link). `grep -rn "Productos" interfaces/web/rutas/ services/ tests/` no debe verse afectado por este cambio (es solo texto en el nav).

### Tarea 5 — Secciones cascarón: Pedidos, Precios, Proveedores, Reportes, Empleados

**Archivos:** Crear los 5 pares router+template listados en File Structure. Modificar `interfaces/web/app.py`.

- [ ] Cada router (`pedidos.py`, etc.) sigue el patrón exacto de `dashboard.py`: un `APIRouter()`, un `@router.get("/pedidos")` (idem `/precios`, `/proveedores`, `/reportes`, `/empleados`) que arma `contexto_base()` y renderiza su template. Sin parámetros, sin `services`.
- [ ] Cada template extiende `base.html`, define `titulo`/`header`, y en `content` usa `card()` + `estado_vacio(titulo="...", subtitulo="Esta sección todavía no tiene funcionalidad. Está preparada como parte de la Fase 1 de rediseño visual.")` con una breve lista (3-4 bullets, texto plano) de lo que se planea para esa sección más adelante — sin botones que simulen acciones reales (nada de "Crear pedido" funcional).
- [ ] En `app.py`, importar los 5 módulos nuevos junto a los existentes e incluir sus routers (`app.include_router(...)` x5).
- [ ] Verificar: con el server levantado, `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/pedidos http://127.0.0.1:8000/precios http://127.0.0.1:8000/proveedores http://127.0.0.1:8000/reportes http://127.0.0.1:8000/empleados` → los 5 devuelven `200`.

### Tarea 6 — Dashboard: datos reales + placeholders explícitos

**Archivos:** Modificar `interfaces/web/rutas/dashboard.py`, `interfaces/web/templates/dashboard.html`.

- [ ] En `dashboard.py`, importar `servicio_caja` y agregar al contexto: `"arqueo": servicio_caja.calcular_arqueo_del_dia()` y `"movimientos_recientes": list(reversed(servicio_caja.listar_movimientos()))[:5]`. `productos_criticos` y `caja_abierta` ya existen.
- [ ] En `dashboard.html`, agregar (manteniendo el hero y las dos cards existentes):
  - Card "Ventas de hoy": `arqueo.cantidad_ventas` y `${{ arqueo.total_vendido_centavos | dinero }}` (dato real).
  - Card "Pedidos pendientes": placeholder explícito — badge `neutral` con texto "Próximamente" y `estado_vacio` chico, sin número inventado.
  - Sección "Movimientos recientes": tabla/lista compacta con `movimientos_recientes` (mismo formato que `caja/panel.html`, reutilizando `badge()` para el tipo de movimiento).
  - Sección "Alertas": deriva de datos ya existentes, sin lógica nueva — si `productos_criticos` no está vacío, alerta rose "N productos con stock crítico"; si `not caja_abierta`, alerta amber "La caja está cerrada". Si no hay ninguna condición, mostrar `estado_vacio("Todo en orden", "No hay alertas activas.")`.
  - Sección "Accesos rápidos": grid de tiles (ícono + etiqueta) linkeando a las 8 secciones de `NAV_ITEMS` (excluye Dashboard).
- [ ] Verificar: `curl -s http://127.0.0.1:8000/ | grep -c "Accesos rápidos"` → 1. Abrir con caja cerrada y con caja abierta (usar los endpoints existentes `/caja/abrir` y `/caja/cerrar` contra la DB de desarrollo, o inspección visual) para confirmar que la alerta de caja aparece/desaparece.

### Tarea 7 — Responsive de páginas existentes (POS, Caja, Stock, formularios)

**Archivos:** Modificar `interfaces/web/templates/ventas/pos.html`, `caja/panel.html`, `productos/lista.html`, y — solo si al probar aparece un problema real — `productos/nuevo.html`, `productos/editar.html`, `productos/importar.html`, `caja/arqueo.html`.

- [ ] `ventas/pos.html`: el contenedor raíz pasa de `flex gap-6` fijo a `flex flex-col lg:flex-row gap-6`; el aside del carrito pasa de `w-96 shrink-0 sticky top-0 ... max-h-[calc(100vh-8.5rem)]` a `w-full lg:w-96 lg:sticky lg:top-0 lg:max-h-[calc(100vh-8.5rem)]` (en mobile/tablet el carrito queda debajo de la grilla, sin sticky ni límite de alto). El input de código de barras y el de filtro pasan de anchos fijos (`min-w-[220px]`, `w-56`) a `w-full sm:w-56` donde corresponda, dentro de un contenedor que permita wrap (`flex-wrap` ya está).
- [ ] `caja/panel.html`: sin cambios estructurales grandes (ya usa `grid-cols-1 md:grid-cols-3` y `overflow-x-auto` en la tabla); solo confirmar que los botones mantienen alto ≥40px (touch target) en mobile — ya lo cumplen (`py-2.5`).
- [ ] `productos/lista.html`: el input de búsqueda `w-64` → `w-full sm:w-64`; el `<form>` que lo envuelve pasa a `flex flex-wrap` (ya lo es) pero el contenedor exterior (`flex flex-wrap items-center justify-between`) necesita `gap-y-3` para no apretar en mobile.
- [ ] Revisar (sin reescribir salvo que se detecte rotura real) `productos/nuevo.html` / `editar.html` (ya usan `grid-cols-2` sin `sm:` — en 375px dos columnas de inputs numéricos angostos pueden quedar apretadas; si se ve mal, cambiar a `grid-cols-1 sm:grid-cols-2`) y `caja/arqueo.html` (`grid-cols-1 sm:grid-cols-3` ya es responsive, solo confirmar).
- [ ] Verificar con curl que las páginas siguen devolviendo 200 después de los cambios: `/ventas`, `/caja`, `/caja/arqueo`, `/productos`, `/productos/nuevo`.

### Tarea 8 — Verificación final y diff

**Archivos:** ninguno (solo comandos).

- [ ] `python -m pytest` desde `stock_app/` → todo en verde (no debería cambiar nada: no se tocó `db/`, `domain/`, `services/`).
- [ ] Levantar el server (`python -m interfaces.web.app` en background) y hacer `curl` a **todas** las rutas GET existentes + nuevas: `/`, `/ventas`, `/productos`, `/productos/nuevo`, `/productos/importar`, `/productos/{id}/editar` (con un id real sembrado), `/caja`, `/caja/arqueo`, `/pedidos`, `/precios`, `/proveedores`, `/reportes`, `/empleados` → todas `200`.
- [ ] Confirmar que el POS sigue funcional: abrir `/ventas`, revisar que `static/js/pos.js` sigue enganchando los mismos `data-producto-card`/`#input-codigo-barras`/`#carrito-*`/`#boton-cobrar` (no se renombró ningún id/clase que ese JS lea — `grep -n "getElementById\|querySelector" static/js/pos.js` contra los ids que quedaron en `pos.html`).
- [ ] Revisar responsive en 375/768/1024/1440 — si hay una herramienta de browser disponible (playwright-cli) usarla para capturar `/`, `/ventas`, `/productos`, `/pedidos` en los 4 anchos; si no está disponible en el entorno, dejarlo documentado como limitación en el informe (no bloquea la entrega).
- [ ] `git diff --stat` y revisión completa del diff (`git diff`) antes de reportar — confirmar que no se tocó nada fuera de `interfaces/web/` (excepto este plan en `docs/`).
- [ ] NO ejecutar `git commit`.

---

## Self-Review

- **Cobertura del pedido:** layout (T3), navegación 8 secciones (T1/T3/T4), componentes Jinja (T2), dashboard (T6), 5 cascarones (T5), etiqueta Stock (T4), responsive (T3/T7), diseño/paleta (sin tarea propia — se respeta en todas las tareas por reutilizar clases existentes), dependencias (sin tarea propia — ninguna tarea toca `requirements.txt`), verificación (T8).
- **Placeholders:** el único dato "inventado" es el badge "Próximamente" de pedidos pendientes en el dashboard, explícitamente marcado como tal — cumple la regla del usuario, no es un placeholder de plan sin implementar.
- **Login shell:** decidido NO construirlo — el layout/nav de Fase 1 no depende de una pantalla de login para definirse, y agregar una pantalla sin flujo real sería alcance extra no pedido explícitamente. Se deja como nota en el informe final, no como tarea.
