"""Limitador de intentos de login: bloqueo temporal por usuario tras una
racha de fallos consecutivos.

Lógica pura de contadores y tiempo: no toca SQL, HTTP ni `logging` (eso
lo hace `services.servicio_auth`, que es quien conoce el contexto).

Decisiones de diseño:

- **Estado solo para usuarios existentes**, con clave `usuario.id`. El
  llamador nunca consulta el limitador con nombres inexistentes, así
  que el diccionario está acotado por la cantidad de filas de
  `usuarios` y un atacante no puede hacerlo crecer inventando nombres.
- **El intento se cuenta antes de verificar la contraseña**
  (`intentar`), no después: de N intentos simultáneos sobre la misma
  cuenta, solo los primeros `umbral` se permiten. Si se contara al
  fallar, varios podrían pasar a la vez el chequeo previo.
- **El bloqueo es fijo y no se extiende** con intentos posteriores: si
  se extendiera, bastaría un intento cada pocos minutos para dejar una
  cuenta bloqueada para siempre.
- **`time.monotonic`** como reloj por defecto, para que un cambio de
  hora del sistema no acorte ni alargue un bloqueo. Es inyectable para
  poder probar sin `sleep`.
- El estado vive solo en memoria y se pierde al reiniciar la app (el
  proceso es único; ver auditoría de fuerza bruta en login).
"""

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

UMBRAL_FALLOS = 5
BLOQUEO_SEGUNDOS = 300
VENTANA_SEGUNDOS = 900


@dataclass
class _Estado:
    intentos: int
    inicio_ventana: float
    bloqueado_hasta: float | None = None


@dataclass(frozen=True)
class DecisionIntento:
    """Resultado de `LimitadorIntentosLogin.intentar`.

    `intentos` es la cantidad contada en el ciclo actual (incluye este
    intento si fue permitido). `se_bloquea` es `True` solo para el
    intento que agota el umbral. `segundos_restantes` solo es mayor a
    cero si el intento fue rechazado por un bloqueo vigente.
    """

    permitido: bool
    intentos: int
    se_bloquea: bool
    segundos_restantes: int


class LimitadorIntentosLogin:
    def __init__(
        self,
        *,
        umbral: int = UMBRAL_FALLOS,
        ventana_segundos: float = VENTANA_SEGUNDOS,
        bloqueo_segundos: float = BLOQUEO_SEGUNDOS,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._umbral = umbral
        self._ventana_segundos = ventana_segundos
        self._bloqueo_segundos = bloqueo_segundos
        self._reloj = reloj
        self._estados: dict[int, _Estado] = {}
        self._lock = threading.Lock()

    def intentar(self, clave: int) -> DecisionIntento:
        """Registra un intento de login para `clave` y decide si puede
        proceder a verificar la contraseña.

        Si hay un bloqueo vigente, lo rechaza sin contarlo ni extenderlo.
        Si el bloqueo o la ventana de la racha ya vencieron, abre un
        ciclo nuevo. Al llegar al umbral, el bloqueo empieza en este
        mismo instante (antes de que termine la verificación).
        """
        with self._lock:
            ahora = self._reloj()
            estado = self._estados.get(clave)

            if estado is not None:
                if estado.bloqueado_hasta is not None:
                    if ahora < estado.bloqueado_hasta:
                        restantes = math.ceil(estado.bloqueado_hasta - ahora)
                        return DecisionIntento(False, estado.intentos, False, restantes)
                    estado = None
                elif ahora - estado.inicio_ventana >= self._ventana_segundos:
                    estado = None

            if estado is None:
                estado = _Estado(intentos=0, inicio_ventana=ahora)
                self._estados[clave] = estado

            estado.intentos += 1
            se_bloquea = estado.intentos >= self._umbral
            if se_bloquea:
                estado.bloqueado_hasta = ahora + self._bloqueo_segundos
            return DecisionIntento(True, estado.intentos, se_bloquea, 0)

    def registrar_exito(self, clave: int) -> int:
        """Borra el contador de `clave` tras un login correcto.

        Devuelve cuántos intentos fallidos previos tenía registrados
        (los de este mismo login no cuentan: ver `intentar`), o 0 si no
        había estado.
        """
        with self._lock:
            estado = self._estados.pop(clave, None)
        if estado is None:
            return 0
        return estado.intentos - 1
