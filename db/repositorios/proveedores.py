"""Repositorio de acceso a datos de proveedores (Fase 4A).

Contiene únicamente consultas SQL parametrizadas (nunca se concatenan
strings para construir SQL). No contiene reglas de negocio: la
validación de datos ocurre en `domain.proveedor.Proveedor` antes de
llegar acá. Cualquier caso de uso que necesite proveedores pasa por
`services.servicio_proveedores`, no por este módulo directamente.
"""

import sqlite3

from db.conexion import obtener_conexion
from domain.proveedor import Proveedor
from excepciones import ErrorBaseDatos, NombreProveedorDuplicadoError, ProveedorNoEncontradoError

_COLUMNAS = "id, nombre, contacto_nombre, telefono, email, direccion, notas, activo, fecha_creacion"


def _fila_a_proveedor(fila: sqlite3.Row) -> Proveedor:
    """Mapea una fila de la tabla `proveedores` a la entidad de dominio."""
    return Proveedor(
        id=fila["id"],
        nombre=fila["nombre"],
        contacto_nombre=fila["contacto_nombre"],
        telefono=fila["telefono"],
        email=fila["email"],
        direccion=fila["direccion"],
        notas=fila["notas"],
        activo=bool(fila["activo"]),
        fecha_creacion=fila["fecha_creacion"],
    )


def crear_proveedor(proveedor: Proveedor) -> Proveedor:
    """Inserta un nuevo proveedor y devuelve la entidad con su id asignado.

    Traduce la violación de UNIQUE sobre `nombre` en
    `NombreProveedorDuplicadoError`.
    """
    consulta = f"""
        INSERT INTO proveedores (nombre, contacto_nombre, telefono, email, direccion, notas)
        VALUES (?, ?, ?, ?, ?, ?)
        RETURNING {_COLUMNAS}
    """
    parametros = (
        proveedor.nombre,
        proveedor.contacto_nombre,
        proveedor.telefono,
        proveedor.email,
        proveedor.direccion,
        proveedor.notas,
    )
    try:
        with obtener_conexion() as conexion:
            fila = conexion.execute(consulta, parametros).fetchone()
    except ErrorBaseDatos as error:
        if isinstance(error.__cause__, sqlite3.IntegrityError):
            raise NombreProveedorDuplicadoError(
                f"Ya existe un proveedor con el nombre '{proveedor.nombre}'."
            ) from error
        raise
    return _fila_a_proveedor(fila)


def obtener_por_id_en_conexion(conexion: sqlite3.Connection, proveedor_id: int) -> Proveedor | None:
    """Busca un proveedor activo por id usando una conexión ya abierta.

    Pensada para componerse dentro de una transacción más amplia (ver
    `services.servicio_compras.registrar_compra`), donde hace falta
    validar que el proveedor exista y esté activo antes de registrar
    una compra, todo dentro de la misma transacción atómica.
    """
    consulta = f"SELECT {_COLUMNAS} FROM proveedores WHERE id = ? AND activo = 1"
    fila = conexion.execute(consulta, (proveedor_id,)).fetchone()
    return _fila_a_proveedor(fila) if fila is not None else None


def obtener_por_id(proveedor_id: int) -> Proveedor | None:
    """Busca un proveedor activo por id (pantallas de edición y baja).

    Un proveedor inactivo no se encuentra acá: para reactivarlo la
    pantalla correspondiente usa el listado de inactivos, no esta
    función (mismo criterio que `db.repositorios.productos.obtener_por_id`).
    """
    with obtener_conexion() as conexion:
        return obtener_por_id_en_conexion(conexion, proveedor_id)


def listar_activos() -> list[Proveedor]:
    """Devuelve los proveedores activos, ordenados por nombre."""
    consulta = f"SELECT {_COLUMNAS} FROM proveedores WHERE activo = 1 ORDER BY nombre"
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta).fetchall()
    return [_fila_a_proveedor(fila) for fila in filas]


def listar_todos() -> list[Proveedor]:
    """Devuelve todos los proveedores (activos e inactivos), ordenados
    por nombre (pantalla de gestión con "ver dados de baja")."""
    consulta = f"SELECT {_COLUMNAS} FROM proveedores ORDER BY nombre"
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta).fetchall()
    return [_fila_a_proveedor(fila) for fila in filas]


def actualizar_datos(proveedor: Proveedor) -> Proveedor:
    """Actualiza los datos editables de un proveedor existente (no su id ni `activo`)."""
    if proveedor.id is None:
        raise ProveedorNoEncontradoError("No se puede actualizar un proveedor sin id.")

    consulta = f"""
        UPDATE proveedores
        SET nombre = ?, contacto_nombre = ?, telefono = ?, email = ?, direccion = ?, notas = ?
        WHERE id = ?
        RETURNING {_COLUMNAS}
    """
    parametros = (
        proveedor.nombre,
        proveedor.contacto_nombre,
        proveedor.telefono,
        proveedor.email,
        proveedor.direccion,
        proveedor.notas,
        proveedor.id,
    )
    try:
        with obtener_conexion() as conexion:
            fila = conexion.execute(consulta, parametros).fetchone()
    except ErrorBaseDatos as error:
        if isinstance(error.__cause__, sqlite3.IntegrityError):
            raise NombreProveedorDuplicadoError(
                f"Ya existe un proveedor con el nombre '{proveedor.nombre}'."
            ) from error
        raise

    if fila is None:
        raise ProveedorNoEncontradoError(f"No existe un proveedor con id {proveedor.id}.")
    return _fila_a_proveedor(fila)


def eliminar_proveedor(proveedor_id: int) -> bool:
    """Elimina un proveedor activo. Devuelve `True` si la baja fue lógica.

    Intenta primero un `DELETE` físico. Hoy (Fase 4A) ninguna tabla
    referencia `proveedores`, así que este `DELETE` siempre tiene
    éxito y la rama de abajo queda sin ejercitar en la práctica: está
    escrita para activarse sola -- sin cambios acá -- el día que Fase
    4B agregue `compras.proveedor_id REFERENCES proveedores(id) ON
    DELETE RESTRICT` y falle con `sqlite3.IntegrityError` (mismo
    patrón que `db.repositorios.productos.eliminar_producto` y
    `db.repositorios.categorias.eliminar_categoria`).
    """
    try:
        with obtener_conexion() as conexion:
            cursor = conexion.execute(
                "DELETE FROM proveedores WHERE id = ? AND activo = 1", (proveedor_id,)
            )
            fue_eliminado = cursor.rowcount > 0
        baja_logica = False
    except ErrorBaseDatos as error:
        if not isinstance(error.__cause__, sqlite3.IntegrityError):
            raise
        with obtener_conexion() as conexion:
            cursor = conexion.execute(
                "UPDATE proveedores SET activo = 0 WHERE id = ? AND activo = 1", (proveedor_id,)
            )
            fue_eliminado = cursor.rowcount > 0
        baja_logica = True

    if not fue_eliminado:
        raise ProveedorNoEncontradoError(f"No existe un proveedor activo con id {proveedor_id}.")
    return baja_logica


def reactivar_proveedor(proveedor_id: int) -> Proveedor:
    """Revierte una baja lógica (`activo` de 0 a 1). No toca ninguna otra columna."""
    consulta = f"""
        UPDATE proveedores
        SET activo = 1
        WHERE id = ? AND activo = 0
        RETURNING {_COLUMNAS}
    """
    with obtener_conexion() as conexion:
        fila = conexion.execute(consulta, (proveedor_id,)).fetchone()
    if fila is None:
        raise ProveedorNoEncontradoError(f"No existe un proveedor inactivo con id {proveedor_id}.")
    return _fila_a_proveedor(fila)
