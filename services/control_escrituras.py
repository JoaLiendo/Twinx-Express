"""Coordinación central para permitir un backup consistente sin tocar
cada servicio de negocio individualmente.

Separa deliberadamente dos conceptos (corrección de un deadlock real:
la operación de backup es también un `POST` HTTP, y si se la contara
como una "escritura de negocio" más, terminaría esperando a que un
contador que ella misma incrementó llegue a cero):

- **Escritura de negocio**: cualquier request que modifica datos de
  negocio (ventas, productos, caja, etc.). El middleware de
  `interfaces/web/app.py` la registra con `permitir_escritura_de_negocio`
  / `finalizar_escritura_de_negocio`.
- **Backup**: `iniciar_backup`/`finalizar_backup` es una operación
  atómica aparte, nunca contada como escritura de negocio. En una
  única sección crítica: rechaza un segundo backup concurrente, deja
  de aceptar escrituras de negocio nuevas, y espera a que las que ya
  estaban en curso (de otros requests) terminen.
"""

import threading
import time


class ControlEscrituras:
    """Los handlers de ruta son funciones sincrónicas que Starlette
    ejecuta en un threadpool (no en el event loop), por eso la
    sincronización es con `threading`, no con `asyncio`."""

    def __init__(self) -> None:
        self._condicion = threading.Condition()
        self._contador_escrituras_de_negocio = 0
        self._backup_activo = False

    def permitir_escritura_de_negocio(self) -> bool:
        """Registra el inicio de una escritura de negocio.

        Devuelve `False` (sin registrar nada) si hay un backup activo;
        en ese caso el llamador no debe ejecutar la escritura.
        """
        with self._condicion:
            if self._backup_activo:
                return False
            self._contador_escrituras_de_negocio += 1
            return True

    def finalizar_escritura_de_negocio(self) -> None:
        """Marca el fin de una escritura ya registrada con
        `permitir_escritura_de_negocio`. Debe llamarse siempre, incluso
        si la escritura terminó con una excepción (ver uso en el
        middleware, dentro de un `finally`)."""
        with self._condicion:
            self._contador_escrituras_de_negocio -= 1
            if self._contador_escrituras_de_negocio == 0:
                self._condicion.notify_all()

    def iniciar_backup(self, timeout: float | None = None) -> bool:
        """Operación atómica equivalente a: si ya hay un backup activo,
        rechazar; si no, marcar backup activo, impedir escrituras de
        negocio nuevas, y esperar a que las que ya estaban en curso
        drenen. Nunca se cuenta a sí misma como escritura de negocio.

        Devuelve `False` de inmediato (sin esperar nada) si ya había
        otro backup activo -- así nunca corren dos backups a la vez.

        Si se agota `timeout` mientras todavía quedan escrituras de
        negocio en curso, **nunca** devuelve `True`: libera el turno
        que había tomado (deja `backup_activo` en `False` de nuevo, sin
        tocar el contador de escrituras, que sigue reflejando lo que
        realmente sigue en curso) y devuelve `False`, para que la
        aplicación pueda seguir funcionando con normalidad. El tiempo
        total respeta un deadline monotónico (`time.monotonic()`), no
        se reinicia en cada `wait()` interno.
        """
        with self._condicion:
            if self._backup_activo:
                return False
            self._backup_activo = True

            limite = None if timeout is None else time.monotonic() + timeout
            while self._contador_escrituras_de_negocio > 0:
                if limite is None:
                    self._condicion.wait()
                    continue
                restante = limite - time.monotonic()
                if restante <= 0 or not self._condicion.wait(restante):
                    self._backup_activo = False
                    self._condicion.notify_all()
                    return False
            return True

    def finalizar_backup(self) -> None:
        """Vuelve a aceptar escrituras de negocio y libera el turno
        para un próximo backup. Debe llamarse siempre, incluso si el
        backup falló (ver uso en `services.servicio_backup`, dentro de
        un `finally`)."""
        with self._condicion:
            self._backup_activo = False
            self._condicion.notify_all()

    @property
    def escrituras_de_negocio_en_curso(self) -> int:
        with self._condicion:
            return self._contador_escrituras_de_negocio

    @property
    def backup_activo(self) -> bool:
        with self._condicion:
            return self._backup_activo


control_escrituras = ControlEscrituras()
