"""Estructura de navegación del panel, en un único lugar.

`contexto_base()` la agrega ya filtrada por el rol del usuario actual
a todas las páginas (ver `interfaces.web.utilidades`). La regla de qué
rol ve qué sección vive **acá y solo acá** (campo `roles` de cada
item): ningún template decide esto por su cuenta, así se evita
duplicar la misma regla de permisos en cada `.html`.

Matriz aprobada en la auditoría de Fase 2: OWNER ve las 9 secciones;
CASHIER ve Dashboard, Ventas, Caja y Stock (Stock en modo consulta,
algo que decide la ruta en una fase posterior, no la navegación).
"""

_SOLO_OWNER = ("OWNER",)

NAV_ITEMS: list[dict[str, object]] = [
    {"href": "/", "etiqueta": "Dashboard", "icono": "dashboard", "roles": None},
    {"href": "/ventas", "etiqueta": "Ventas", "icono": "ventas", "roles": None},
    {"href": "/pedidos", "etiqueta": "Pedidos", "icono": "pedidos", "roles": _SOLO_OWNER, "proximamente": True},
    {"href": "/precios", "etiqueta": "Precios", "icono": "precios", "roles": _SOLO_OWNER},
    {"href": "/caja", "etiqueta": "Caja", "icono": "caja", "roles": None},
    {"href": "/productos", "etiqueta": "Stock", "icono": "productos", "roles": None},
    {"href": "/proveedores", "etiqueta": "Proveedores", "icono": "proveedores", "roles": _SOLO_OWNER},
    {"href": "/compras", "etiqueta": "Compras", "icono": "compras", "roles": _SOLO_OWNER},
    {"href": "/reposicion", "etiqueta": "Reposición", "icono": "pedidos", "roles": _SOLO_OWNER},
    {"href": "/reportes", "etiqueta": "Reportes", "icono": "reportes", "roles": _SOLO_OWNER},
    {"href": "/empleados", "etiqueta": "Empleados", "icono": "empleados", "roles": _SOLO_OWNER},
    {"href": "/auditoria", "etiqueta": "Auditoría", "icono": "auditoria", "roles": _SOLO_OWNER},
    {"href": "/configuracion", "etiqueta": "Configuración", "icono": "configuracion", "roles": _SOLO_OWNER},
]


def navegacion_visible_para(rol: str | None) -> list[dict[str, object]]:
    """Filtra `NAV_ITEMS` según el rol del usuario actual.

    Responsabilidad exacta de esta función, y nada más: decidir qué
    enlaces del menú se muestran. **No es autorización.** No decide si
    una acción está permitida ni qué debe responder el servidor cuando
    no hay sesión — eso vive en otras dos piezas, deliberadamente
    separadas:

    - `interfaces.web.auth.requiere_rol` — autorización real de
      backend (protege la ruta en sí, no solo el link del menú).
    - Fase 2E (todavía sin implementar) — qué respuesta HTTP dar
      cuando no hay autenticación (redirigir a `/login`, 401, etc.).

    Un item con `roles=None` es visible para cualquier usuario
    autenticado, sea cual sea su rol. Un item con una tupla de roles
    solo es visible si `rol` está en ella (deny-by-default: un rol
    desconocido no hereda secciones restringidas).

    DECISIÓN TEMPORAL DE LA FASE 2D (no una regla de autorización que
    deba perdurar): `rol=None` (nadie autenticado) devuelve hoy la
    navegación **completa**, porque todavía no existe ningún login
    real y restringir acá dejaría cualquier página de la Fase 1 sin
    sidebar antes de que exista una forma de iniciar sesión. Cuando la
    Fase 2E agregue login y protección de rutas, lo esperable es que
    esta función deje de llamarse con `rol=None` para páginas
    protegidas (porque `requiere_rol` ya habrá cortado el request
    antes de llegar a renderizar nada) — en ese momento hay que
    revisar si este `if` sigue haciendo falta. Ver el test
    `test_none_es_una_decision_temporal_de_2d_no_una_regla_de_autorizacion`
    en `tests/test_interfaces_web/test_navegacion.py`, que deja esto
    registrado para que no se vuelva una regla permanente por inercia.
    """
    # V1.1: una sección marcada `proximamente` todavía no tiene funcionalidad
    # real (sus rutas siguen existiendo, pero son un cascarón): no se ofrece en
    # ningún menú para que nadie la confunda con algo utilizable.
    disponibles = [item for item in NAV_ITEMS if not item.get("proximamente")]
    if rol is None:
        return disponibles
    return [item for item in disponibles if item["roles"] is None or rol in item["roles"]]
