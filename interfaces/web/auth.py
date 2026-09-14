"""Infraestructura web de autenticación (Fase 2D): resolver el usuario
autenticado a partir de un `Request` y una dependencia reutilizable
para restringir endpoints por rol en fases posteriores.

Fase 2D es solo infraestructura, no autenticación en funcionamiento:
- No existe todavía ningún `/login` que fije la cookie de sesión (fase
  2E). En la aplicación real, `obtener_usuario_actual` hoy siempre
  devuelve `None`, porque ninguna respuesta llegó nunca a poner esa
  cookie — leerla acá no es "agregar cookies" como funcionalidad, es
  la mitad de lectura de un mecanismo que todavía no tiene mitad de
  escritura.
- `requiere_rol` no está conectada a ningún router (`Depends(...)`)
  todavía: queda lista para que 2E la use, pero hoy no protege nada.
- No hay redirecciones ni respuestas HTTP acá: solo se devuelve `None`
  o se levanta una excepción de dominio (`NoAutenticadoError`,
  `PermisoDenegadoError`). Traducir eso a una respuesta HTTP concreta
  (redirect a `/login`, 403, etc.) es responsabilidad de una fase
  posterior, con el mismo patrón que ya usa el resto de la app:
  `interfaces.web.app` traduce `ErrorAplicacion` a una respuesta en un
  único lugar (ver su exception_handler).

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

    Pensada para conectarse en una fase posterior como
    `Depends(requiere_rol("OWNER"))` en un router o una ruta puntual.
    Por ahora no está conectada a nada: se prueba llamando a la
    dependencia devuelta directamente con un `Request` (ver
    tests/test_interfaces_web/test_auth.py).

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
