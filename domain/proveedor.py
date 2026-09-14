"""Entidad Proveedor y reglas de negocio asociadas (Fase 4A).

Representa un proveedor de mercadería tal como lo maneja el dominio,
sin depender de cómo se persiste (db/) ni de cómo se presenta
(interfaces/). Valida sus propios invariantes al construirse, mismo
patrón que `domain.categoria.Categoria`.

Es puramente informativo: no tiene ninguna relación con compras
todavía (esa lógica es de una fase posterior). Los datos de contacto
son todos opcionales -- lo único obligatorio es el nombre.
"""

from dataclasses import dataclass

from excepciones import DatosInvalidosError


@dataclass
class Proveedor:
    """Un proveedor de mercadería, validado al construirse.

    `id` y `fecha_creacion` quedan en `None` para un proveedor todavía
    no persistido; la capa de datos los completa al guardarlo.
    """

    nombre: str
    id: int | None = None
    contacto_nombre: str | None = None
    telefono: str | None = None
    email: str | None = None
    direccion: str | None = None
    notas: str | None = None
    activo: bool = True
    fecha_creacion: str | None = None

    def __post_init__(self) -> None:
        if not self.nombre or not self.nombre.strip():
            raise DatosInvalidosError("El nombre del proveedor no puede estar vacío.")
