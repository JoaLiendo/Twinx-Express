"""Casos de uso de gestión de proveedores (Fase 4A).

Orquesta `domain.proveedor` y `db.repositorios.proveedores`. Es CRUD
simple: no hay ninguna regla de negocio más allá de "el nombre no
puede repetirse" y "no se puede borrar físicamente un proveedor con
compras asociadas" -- ambas ya las traduce el repositorio (la segunda
todavía no es ejercitable: ver docstring de
`db.repositorios.proveedores.eliminar_proveedor`).
"""

import logging

from db.repositorios import proveedores as repositorio_proveedores
from domain.proveedor import Proveedor

logger = logging.getLogger(__name__)


def crear_proveedor(
    nombre: str,
    contacto_nombre: str | None = None,
    telefono: str | None = None,
    email: str | None = None,
    direccion: str | None = None,
    notas: str | None = None,
) -> Proveedor:
    """Da de alta un nuevo proveedor.

    Raises:
        DatosInvalidosError: si el nombre está vacío.
        NombreProveedorDuplicadoError: si ya existe un proveedor con ese nombre.
    """
    proveedor = Proveedor(
        nombre=nombre,
        contacto_nombre=contacto_nombre or None,
        telefono=telefono or None,
        email=email or None,
        direccion=direccion or None,
        notas=notas or None,
    )
    proveedor_creado = repositorio_proveedores.crear_proveedor(proveedor)
    logger.info("Proveedor creado: %s (id=%s)", proveedor_creado.nombre, proveedor_creado.id)
    return proveedor_creado


def obtener_por_id(proveedor_id: int) -> Proveedor | None:
    """Busca un proveedor activo por id (pantallas de edición)."""
    return repositorio_proveedores.obtener_por_id(proveedor_id)


def listar_activos() -> list[Proveedor]:
    """Devuelve los proveedores activos (listado normal)."""
    return repositorio_proveedores.listar_activos()


def listar_todos() -> list[Proveedor]:
    """Devuelve todos los proveedores, activos e inactivos (vista "dados de baja")."""
    return repositorio_proveedores.listar_todos()


def actualizar_proveedor(
    proveedor_id: int,
    nombre: str,
    contacto_nombre: str | None = None,
    telefono: str | None = None,
    email: str | None = None,
    direccion: str | None = None,
    notas: str | None = None,
) -> Proveedor:
    """Actualiza los datos editables de un proveedor existente.

    Raises:
        ProveedorNoEncontradoError: si no existe un proveedor activo con ese id.
        DatosInvalidosError: si el nombre está vacío.
        NombreProveedorDuplicadoError: si el nombre ya pertenece a otro proveedor.
    """
    proveedor_actualizado = Proveedor(
        id=proveedor_id,
        nombre=nombre,
        contacto_nombre=contacto_nombre or None,
        telefono=telefono or None,
        email=email or None,
        direccion=direccion or None,
        notas=notas or None,
    )
    resultado = repositorio_proveedores.actualizar_datos(proveedor_actualizado)
    logger.info("Proveedor actualizado: %s (id=%s)", resultado.nombre, resultado.id)
    return resultado


def eliminar_proveedor(proveedor_id: int) -> bool:
    """Da de baja un proveedor. Devuelve `True` si la baja fue lógica.

    Raises:
        ProveedorNoEncontradoError: si no existe un proveedor activo con ese id.
    """
    baja_logica = repositorio_proveedores.eliminar_proveedor(proveedor_id)
    logger.info("Proveedor id=%s dado de baja (baja_logica=%s)", proveedor_id, baja_logica)
    return baja_logica


def reactivar_proveedor(proveedor_id: int) -> Proveedor:
    """Reactiva un proveedor dado de baja lógica.

    Raises:
        ProveedorNoEncontradoError: si no existe un proveedor inactivo con ese id.
    """
    proveedor = repositorio_proveedores.reactivar_proveedor(proveedor_id)
    logger.info("Proveedor reactivado: %s (id=%s)", proveedor.nombre, proveedor.id)
    return proveedor
