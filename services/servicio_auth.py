"""Casos de uso de autenticación (Fase 2C): hashing de contraseñas, login,
resolución de usuario a partir de un token de sesión y logout.

Esto es exclusivamente lógica de servicio. No hay nada de `Request`,
cookies, rutas HTTP ni autorización por rol acá — eso se conecta en
una fase posterior (ver auditoría de Fase 2, fases 2D en adelante).

Decisiones de esta fase (documentadas también junto a cada constante):

- **Hashing**: `hashlib.pbkdf2_hmac('sha256', ...)` de la librería
  estándar, sin dependencias nuevas. Formato autodescriptivo
  `pbkdf2_sha256$<iteraciones>$<salt_hex>$<hash_hex>` para poder
  cambiar la cantidad de iteraciones (o el algoritmo) en el futuro sin
  invalidar los hashes ya guardados.
- **Tokens**: `secrets.token_urlsafe(32)` (256 bits de entropía),
  nunca UUID/timestamp/id incremental (ver `_generar_token`).
- **Sesiones múltiples**: permitidas a propósito. Este es un kiosco de
  un solo local que hoy ya se opera desde más de una pestaña/POS a la
  vez (ver `interfaces/web/templates/ventas/pos.html`); atar el login
  a "una sola sesión activa" cerraría de golpe la sesión de otra caja
  sin ningún beneficio de seguridad real en un sistema sin datos
  sensibles de terceros. Cada `iniciar_sesion` crea una fila nueva en
  `sesiones`; no se invalida ninguna sesión previa del mismo usuario.
- **Mensajes de error de login**: `CredencialesInvalidasError` se usa
  con el mismo mensaje tanto si el usuario no existe como si la
  contraseña es incorrecta (ver `_MENSAJE_CREDENCIALES_INVALIDAS`).
  `UsuarioInactivoError` es una excepción distinta *solo para uso
  interno* (permite, por ejemplo, loguear "intento de acceso a cuenta
  desactivada" en vez de "credenciales inválidas"); cualquier interfaz
  que la muestre al usuario final debe hacerlo con el mismo texto
  genérico que usaría para `CredencialesInvalidasError`, nunca con
  `str(excepcion)` tal cual, para no revelar que el usuario existe
  pero está desactivado.
- **Fuerza bruta**: tras `UMBRAL_FALLOS` intentos fallidos consecutivos
  sobre un usuario existente, ese usuario queda bloqueado un rato (ver
  `services.limitador_login`). El bloqueo no es visible desde afuera:
  se responde con el mismo error genérico y se hace igual una
  verificación PBKDF2 (contra `_HASH_SENUELO`), de modo que ni el
  mensaje ni el tiempo de respuesta distinguen "bloqueado" de
  "contraseña incorrecta" o "usuario inexistente". Los nombres
  inexistentes no crean estado. Los logs identifican al usuario solo
  por `usuario_id`, nunca por nombre, contraseña ni hash.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta

from db.repositorios import sesiones as repositorio_sesiones
from db.repositorios import usuarios as repositorio_usuarios
from domain.sesion import Sesion
from domain.usuario import Usuario
from excepciones import CredencialesInvalidasError, DatosInvalidosError, UsuarioInactivoError
from services.limitador_login import UMBRAL_FALLOS, LimitadorIntentosLogin

logger = logging.getLogger(__name__)

_ALGORITMO_HASH = "pbkdf2_sha256"
_DIGESTO_HASHLIB = "sha256"
_SALT_BYTES = 16  # 128 bits: suficiente para que dos salts jamás colisionen.

# OWASP Password Storage Cheat Sheet (revisión 2023) recomienda un piso de
# 600.000 iteraciones para PBKDF2-HMAC-SHA256. Se midió en esta máquina
# (ver auditoría de Fase 2C): ~220ms por hash a 600.000 iteraciones, un
# costo aceptable para un login de un solo usuario local (no es una API de
# alto tráfico) y suficiente para encarecer un ataque de fuerza bruta
# offline. Queda como constante única y con `iteraciones` como parámetro
# explícito en `hashear_password` para poder subirla en el futuro sin
# romper los hashes ya guardados (el número usado en cada hash viaja
# dentro del propio hash, ver formato en el docstring del módulo).
ITERACIONES_PBKDF2 = 600_000

# Duración de una sesión: 12 horas cubre un turno de trabajo del kiosco sin
# forzar un nuevo login a mitad de turno, y limita cuánto tiempo queda
# utilizable una sesión olvidada abierta. Centralizada acá (no repetida en
# cada llamada) para que cambiarla sea un solo lugar.
DURACION_SESION_SEGUNDOS = 12 * 60 * 60

_FORMATO_FECHA = "%Y-%m-%d %H:%M:%S"

# Mismo mensaje para "no existe" y "contraseña incorrecta": para quien
# intenta autenticarse, ambos casos deben ser indistinguibles.
_MENSAJE_CREDENCIALES_INVALIDAS = "Nombre de usuario o contraseña incorrectos."


def _ahora() -> str:
    """Fecha/hora local actual, en el mismo formato que las columnas de
    fecha de SQLite (`datetime('now', 'localtime')`), para poder comparar
    como texto sin tener que parsear fechas de vuelta."""
    return datetime.now().strftime(_FORMATO_FECHA)


def hashear_password(password: str, *, iteraciones: int = ITERACIONES_PBKDF2) -> str:
    """Calcula el hash de una contraseña con PBKDF2-HMAC-SHA256.

    Genera un salt aleatorio criptográficamente seguro (`secrets`,
    nunca reutilizado entre llamadas) y devuelve un string
    autodescriptivo: `pbkdf2_sha256$<iteraciones>$<salt_hex>$<hash_hex>`.
    La contraseña en texto plano no se guarda en ningún lado.

    Raises:
        DatosInvalidosError: si `password` está vacía.
    """
    if not password:
        raise DatosInvalidosError("La contraseña no puede estar vacía.")

    salt = secrets.token_bytes(_SALT_BYTES)
    hash_bytes = hashlib.pbkdf2_hmac(_DIGESTO_HASHLIB, password.encode("utf-8"), salt, iteraciones)
    return f"{_ALGORITMO_HASH}${iteraciones}${salt.hex()}${hash_bytes.hex()}"


def verificar_password(password: str, password_hash: str) -> bool:
    """Verifica una contraseña contra un hash con el formato de
    `hashear_password`, en tiempo constante (`hmac.compare_digest`).

    Nunca lanza una excepción por un `password_hash` corrupto o con
    formato inválido: en ese caso devuelve `False`, igual que si la
    contraseña fuera incorrecta. Usa las iteraciones y el salt
    guardados **dentro del hash**, no la constante actual, para que
    subir `ITERACIONES_PBKDF2` en el futuro no invalide los hashes
    existentes.
    """
    try:
        algoritmo, iteraciones_texto, salt_hex, hash_hex_esperado = password_hash.split("$")
        if algoritmo != _ALGORITMO_HASH:
            return False
        iteraciones = int(iteraciones_texto)
        salt = bytes.fromhex(salt_hex)
        hash_esperado = bytes.fromhex(hash_hex_esperado)
    except ValueError:
        # split() con una cantidad de partes distinta de 4, iteraciones no
        # numéricas, o salt/hash que no son hexadecimales válidos.
        return False

    hash_calculado = hashlib.pbkdf2_hmac(_DIGESTO_HASHLIB, password.encode("utf-8"), salt, iteraciones)
    return hmac.compare_digest(hash_calculado, hash_esperado)


# Hash "señuelo" contra el que se compara cuando el nombre de usuario no
# existe, para que ese intento de login tarde un tiempo similar al de un
# intento con contraseña incorrecta (mitiga que alguien deduzca, por el
# tiempo de respuesta, qué nombres de usuario existen). Se calcula una
# sola vez, en memoria, a partir de una contraseña aleatoria que nadie
# conoce: no es un secreto reutilizable en ningún sentido sensible.
_HASH_SENUELO = hashear_password(secrets.token_urlsafe(32))

_limitador = LimitadorIntentosLogin()


def _generar_token() -> str:
    """Genera un token de sesión con entropía criptográfica suficiente.

    `secrets.token_urlsafe(32)` da 256 bits de aleatoriedad real (el
    módulo `secrets` está pensado explícitamente para esto). Se evita
    a propósito cualquier alternativa predecible o de baja entropía:
    UUID (identifica, no está pensado como secreto), timestamps,
    nombre de usuario, un id incremental, o derivar el token de la
    contraseña.
    """
    return secrets.token_urlsafe(32)


def iniciar_sesion(
    nombre_usuario: str, password: str, *, duracion_segundos: int = DURACION_SESION_SEGUNDOS
) -> Sesion:
    """Autentica a un usuario y crea una nueva sesión para él.

    Pasos (en este orden): busca el usuario; si no existe, rechaza con
    un error genérico; consulta el limitador y, si el usuario está
    bloqueado, rechaza con el mismo error genérico sin mirar su
    contraseña; si está inactivo, rechaza con un error distinto *solo
    para uso interno*; verifica la contraseña; si es incorrecta,
    rechaza con el mismo error genérico que "no existe". Todo camino de
    rechazo ejecuta exactamente una verificación PBKDF2. Genera un
    token nuevo y persiste la sesión.

    No invalida sesiones anteriores del mismo usuario: se permiten
    varias sesiones simultáneas (ver docstring del módulo).

    Args:
        duracion_segundos: para qué tan lejos en el futuro se fija
            `fecha_expiracion`. Parametrizable (en vez de usar
            siempre `DURACION_SESION_SEGUNDOS`) para poder probar
            expiración sin depender de `time.sleep` en los tests.

    Raises:
        CredencialesInvalidasError: usuario inexistente, usuario
            bloqueado temporalmente o contraseña incorrecta (mismo
            mensaje en todos los casos).
        UsuarioInactivoError: el usuario existe y la contraseña sería
            válida, pero está desactivado.
    """
    usuario = repositorio_usuarios.obtener_por_nombre_usuario(nombre_usuario)

    if usuario is None:
        verificar_password(password, _HASH_SENUELO)  # mismo costo que una verificación real
        # No se registra el nombre: podría ser una contraseña tipeada por error.
        logger.warning("Login fallido: usuario inexistente.")
        raise CredencialesInvalidasError(_MENSAJE_CREDENCIALES_INVALIDAS)

    decision = _limitador.intentar(usuario.id)
    if not decision.permitido:
        verificar_password(password, _HASH_SENUELO)  # mismo costo; el resultado se ignora
        logger.warning(
            "Intento de login durante bloqueo temporal: usuario_id=%s, restan %s s.",
            usuario.id,
            decision.segundos_restantes,
        )
        raise CredencialesInvalidasError(_MENSAJE_CREDENCIALES_INVALIDAS)

    if not usuario.activo:
        verificar_password(password, _HASH_SENUELO)  # mismo costo que una verificación real
        raise UsuarioInactivoError(f"El usuario '{nombre_usuario}' está inactivo.")

    if not verificar_password(password, usuario.password_hash):
        logger.warning(
            "Login fallido: usuario_id=%s, intento %s/%s.", usuario.id, decision.intentos, UMBRAL_FALLOS
        )
        if decision.se_bloquea:
            logger.warning(
                "Usuario bloqueado temporalmente: usuario_id=%s, tras %s fallos consecutivos.",
                usuario.id,
                decision.intentos,
            )
        raise CredencialesInvalidasError(_MENSAJE_CREDENCIALES_INVALIDAS)

    fallos_previos = _limitador.registrar_exito(usuario.id)
    if fallos_previos:
        logger.info(
            "Login exitoso: contador de fallos reiniciado, usuario_id=%s (%s fallos previos).",
            usuario.id,
            fallos_previos,
        )

    fecha_expiracion = (datetime.now() + timedelta(seconds=duracion_segundos)).strftime(_FORMATO_FECHA)
    sesion = Sesion(token=_generar_token(), usuario_id=usuario.id, fecha_expiracion=fecha_expiracion)
    return repositorio_sesiones.crear_sesion(sesion)


def obtener_usuario_de_token(token: str) -> Usuario | None:
    """Resuelve el usuario autenticado a partir de un token de sesión.

    Devuelve `None` (nunca lanza) si el token no existe, si la sesión
    ya expiró, si el usuario asociado ya no existe, o si el usuario
    fue desactivado **después** de haber iniciado esta sesión — una
    sesión ya creada no alcanza para seguir autenticado si a alguien
    le desactivaron la cuenta mientras tanto.

    Si la sesión encontrada está vencida, se borra en el momento
    (limpieza perezosa, sin scheduler: ver docstring del módulo).
    """
    sesion = repositorio_sesiones.obtener_por_token(token)
    if sesion is None:
        return None

    if sesion.fecha_expiracion <= _ahora():
        repositorio_sesiones.eliminar_por_token(token)
        return None

    usuario = repositorio_usuarios.obtener_por_id(sesion.usuario_id)
    if usuario is None or not usuario.activo:
        return None

    return usuario


def cerrar_sesion(token: str) -> None:
    """Invalida una sesión por su token (logout).

    Idempotente: cerrar sesión sobre un token que ya fue cerrado, o
    que directamente no existe, no lanza ningún error.
    """
    repositorio_sesiones.eliminar_por_token(token)
