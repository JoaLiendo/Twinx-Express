"""Entidad Sesión: infraestructura de persistencia para autenticación (Fase 2B).

Modela una sesión ya emitida (token + usuario + expiración) tal como
se guarda en la tabla `sesiones`. Solo valida que los datos sean
coherentes antes de persistirlos: la lógica de cuándo crear una
sesión, verificar su expiración o invalidarla en el login/logout se
implementa en `services.servicio_auth` en una fase posterior (ver
auditoría de Fase 2). Acá no hay ninguna regla de negocio de
autenticación.
"""

from dataclasses import dataclass

from excepciones import DatosInvalidosError


@dataclass
class Sesion:
    """Una sesión de autenticación, validada al construirse."""

    token: str
    usuario_id: int
    fecha_expiracion: str
    fecha_creacion: str | None = None

    def __post_init__(self) -> None:
        self._validar()

    def _validar(self) -> None:
        if not self.token or not self.token.strip():
            raise DatosInvalidosError("El token de sesión no puede estar vacío.")
        if self.usuario_id <= 0:
            raise DatosInvalidosError("La sesión debe estar asociada a un usuario válido.")
        if not self.fecha_expiracion or not self.fecha_expiracion.strip():
            raise DatosInvalidosError("La sesión debe tener una fecha de expiración.")
