"""Entidad Usuario y reglas de negocio asociadas (Fase 2B).

Representa un usuario del sistema de autenticación tal como lo maneja
el dominio, sin depender de cómo se persiste (db/) ni de cómo se
presenta (interfaces/). Valida sus propios invariantes al construirse,
igual que `domain.producto.Producto`.

Esta entidad modela únicamente los datos del usuario. El login, el
hashing/verificación de contraseñas y la autorización por rol se
implementan en `services/` en una fase posterior (ver auditoría de
Fase 2): acá no hay ninguna lógica de autenticación.
"""

from dataclasses import dataclass

from excepciones import DatosInvalidosError, PermisoDenegadoError

ROLES_VALIDOS = frozenset({"OWNER", "CASHIER"})


@dataclass
class Usuario:
    """Usuario del sistema.

    `id` y `fecha_creacion` quedan en `None` para un usuario todavía
    no persistido; la capa de datos los completa al guardarlo.
    `password_hash` nunca es la contraseña en texto plano: quien
    construye un `Usuario` ya debe traer el hash calculado (ver
    `services.servicio_auth`, fase 2C).
    """

    nombre_usuario: str
    nombre_completo: str
    password_hash: str
    rol: str
    id: int | None = None
    activo: bool = True
    fecha_creacion: str | None = None

    def __post_init__(self) -> None:
        self._validar()

    def _validar(self) -> None:
        if not self.nombre_usuario or not self.nombre_usuario.strip():
            raise DatosInvalidosError("El nombre de usuario no puede estar vacío.")
        if not self.nombre_completo or not self.nombre_completo.strip():
            raise DatosInvalidosError("El nombre completo no puede estar vacío.")
        if not self.password_hash or not self.password_hash.strip():
            raise DatosInvalidosError("El usuario debe tener un password_hash.")
        if self.rol not in ROLES_VALIDOS:
            raise DatosInvalidosError(
                f"Rol inválido: {self.rol!r}. Debe ser uno de {sorted(ROLES_VALIDOS)}."
            )

    @property
    def es_owner(self) -> bool:
        """True si el usuario tiene rol OWNER."""
        return self.rol == "OWNER"


def exigir_rol(usuario: Usuario | None, roles: frozenset[str]) -> None:
    """Exige que el usuario exista, esté activo y tenga uno de los `roles`.

    Los servicios de clientes y cuenta corriente la aplican además del control
    de la interfaz web, para que la regla no dependa de quién invoque el caso de uso.

    Raises:
        PermisoDenegadoError: si `usuario` es `None`, está inactivo o su rol no alcanza.
    """
    if usuario is None or not usuario.activo:
        raise PermisoDenegadoError("La operación requiere un usuario activo.")
    if usuario.rol not in roles:
        raise PermisoDenegadoError(f"El rol {usuario.rol} no tiene permiso para esta operación.")
