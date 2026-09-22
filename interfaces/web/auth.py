"""Infraestructura web de autenticación: resolver el usuario autenticado
a partir de un `Request` y una dependencia reutilizable (`requiere_rol`)
para restringir endpoints por rol.

Autenticación en funcionamiento, no solo infraestructura:
- `interfaces.web.rutas.autenticacion` expone `/login` y `/logout` y es
  el único lugar que escribe/borra la cookie de sesión, con el mismo
  nombre centralizado acá (`NOMBRE_COOKIE_SESION`).
- `requiere_rol` está conectada como dependencia en los routers de
  `interfaces/web/rutas/` (productos, ventas, caja, compras,
  proveedores, empleados, backup, dashboard, y los cascarones de
  pedidos/precios/reportes), restringiendo accesos reales por rol
  (`OWNER`, `CASHIER`).
- No hay redirecciones ni respuestas HTTP acá: solo se devuelve `None`
  o se levanta una excepción de dominio (`NoAutenticadoError`,
  `PermisoDenegadoError`). Traducir eso a una respuesta HTTP concreta
  (redirect a `/login`, 403, etc.) es responsabilidad de
  `interfaces.web.app`, que traduce `ErrorAplicacion` a una respuesta
  en un único lugar (ver su exception_handler).

Reutiliza `services.servicio_auth` para todo lo que ya resuelve
(sesión válida, expiración, usuario activo): este módulo no vuelve a
implementar nada de eso, solo conecta un `Request` con esa lógica.
"""

from fastapi import Request

from domain.usuario import Usuario
from excepciones import NoAutenticadoError, PermisoDenegadoError
from services import servicio_auth

# Nombre de la cookie de sesión. Centralizado acá porque este es el único
# lugar de `interfaces/web/` que debe leerla (mismo principio que
# `db/conexion.py` siendo el único lugar que abre conexiones sqlite3);
# quien escriba la cookie (fase 2E, `interfaces.web.rutas.autenticacion`)
# debe usar este mismo nombre, no inventar otro.
NOMBRE_COOKIE_SESION = "session_token"

# La app corre en http://127.0.0.1 sin TLS (ver lanzador.py/KioscoApp.spec:
# es una app de escritorio empaquetada, no un servicio expuesto en red).
# Un cookie `Secure=True` no se enviaría nunca sobre HTTP y rompería el
# login. Si en el futuro esto se sirve detrás de HTTPS, este es el único
# valor que hay que cambiar.
COOKIE_SEGURA = False


def obtener_usuario_actual(request: Request) -> Usuario | None:
    """Resuelve el usuario autenticado a partir de la cookie de sesión
    del `Request`, o `None` si no hay una sesión válida.

    Nunca lanza: falta de cookie, token inexistente, sesión expirada o
    usuario desactivado se tratan todos por igual como "no
    autenticado" (la distinción entre esos casos ya la hace
    `servicio_auth.obtener_usuario_de_token`, que es quien de verdad
    la resuelve).
    """
    token = request.cookies.get(NOMBRE_COOKIE_SESION)
    if not token:
        return None
    return servicio_auth.obtener_usuario_de_token(token)


def requiere_rol(*roles: str):
    """Devuelve una dependencia de FastAPI que exige un usuario
    autenticado con alguno de `roles`.

    Se usa como `Depends(requiere_rol("OWNER"))` a nivel de router o de
    ruta puntual (ver `interfaces/web/rutas/`). También se prueba
    llamando a la dependencia devuelta directamente con un `Request`
    (ver `tests/test_interfaces_web/test_auth.py`).

    Raises:
        NoAutenticadoError: no hay usuario autenticado en el request
            (incluye el caso de un usuario que fue desactivado
            después de haber iniciado esta sesión: para quien llama,
            es indistinguible de no estar autenticado).
        PermisoDenegadoError: hay un usuario autenticado, pero su rol
            no está en `roles`.
    """

    def dependencia(request: Request) -> Usuario:
        usuario = obtener_usuario_actual(request)
        if usuario is None:
            raise NoAutenticadoError("Esta acción requiere haber iniciado sesión.")
        if usuario.rol not in roles:
            raise PermisoDenegadoError(f"El rol '{usuario.rol}' no tiene permiso para esta acción.")
        return usuario

    return dependencia
