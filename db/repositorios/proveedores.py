"""Repositorio de acceso a datos de proveedores (Fase 4A) y de su clasificación por categorías (V1.13-A).

Contiene únicamente consultas SQL parametrizadas (nunca se concatenan
strings para construir SQL). No contiene reglas de negocio: la
validación de datos ocurre en `domain.proveedor.Proveedor` antes de
llegar acá. Cualquier caso de uso que necesite proveedores pasa por
`services.servicio_proveedores`, no por este módulo directamente.
"""

import sqlite3
from collections.abc import Iterable

from db.conexion import obtener_conexion
from domain.proveedor import Proveedor
from excepciones import (
    CuitProveedorDuplicadoError,
    DatosInvalidosError,
    NombreProveedorDuplicadoError,
    ProveedorNoEncontradoError,
)

_COLUMNAS = (
    "id, nombre, contacto_nombre, telefono, email, direccion, notas, activo, fecha_creacion,"
    " razon_social, cuit, condicion_fiscal, ingresos_brutos, convenio_multilateral"
)


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
        razon_social=fila["razon_social"],
        cuit=fila["cuit"],
        condicion_fiscal=fila["condicion_fiscal"],
        ingresos_brutos=fila["ingresos_brutos"],
        convenio_multilateral=bool(fila["convenio_multilateral"]),
    )


def _parametros_de_datos(proveedor: Proveedor) -> tuple:
    return (
        proveedor.nombre,
        proveedor.contacto_nombre,
        proveedor.telefono,
        proveedor.email,
        proveedor.direccion,
        proveedor.notas,
        proveedor.razon_social,
        proveedor.cuit,
        proveedor.condicion_fiscal,
        proveedor.ingresos_brutos,
        int(proveedor.convenio_multilateral),
    )


def _traducir_integridad(error: sqlite3.IntegrityError, proveedor: Proveedor) -> Exception:
    """Traduce los UNIQUE de `nombre` y `cuit`; cualquier otra violación (un CHECK que el dominio ya
    valida) es un dato inválido."""
    texto = str(error)
    if "proveedores.cuit" in texto:
        return CuitProveedorDuplicadoError(f"Ya existe un proveedor con el CUIT {proveedor.cuit_formateado}.")
    if "proveedores.nombre" in texto:
        return NombreProveedorDuplicadoError(f"Ya existe un proveedor con el nombre '{proveedor.nombre}'.")
    return DatosInvalidosError("Los datos del proveedor no son válidos.")


def crear_proveedor_en_conexion(conexion: sqlite3.Connection, proveedor: Proveedor) -> Proveedor:
    """Inserta un nuevo proveedor dentro de la transacción recibida.

    Traduce la violación de UNIQUE sobre `nombre` en `NombreProveedorDuplicadoError` y sobre `cuit` en
    `CuitProveedorDuplicadoError`.
    """
    consulta = f"""
        INSERT INTO proveedores (nombre, contacto_nombre, telefono, email, direccion, notas,
                                 razon_social, cuit, condicion_fiscal, ingresos_brutos, convenio_multilateral)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING {_COLUMNAS}
    """
    try:
        fila = conexion.execute(consulta, _parametros_de_datos(proveedor)).fetchone()
    except sqlite3.IntegrityError as error:
        raise _traducir_integridad(error, proveedor) from error
    return _fila_a_proveedor(fila)


def crear_proveedor(proveedor: Proveedor) -> Proveedor:
    """Inserta un nuevo proveedor en su propia transacción y devuelve la entidad con su id."""
    with obtener_conexion() as conexion:
        return crear_proveedor_en_conexion(conexion, proveedor)


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


def obtener_por_id_incluyendo_inactivos_en_conexion(
    conexion: sqlite3.Connection, proveedor_id: int
) -> Proveedor | None:
    """Igual que `obtener_por_id_en_conexion`, pero también encuentra proveedores dados de baja
    (ficha e historial: un proveedor inactivo conserva su historial de compras)."""
    fila = conexion.execute(f"SELECT {_COLUMNAS} FROM proveedores WHERE id = ?", (proveedor_id,)).fetchone()
    return _fila_a_proveedor(fila) if fila is not None else None


def obtener_por_id_incluyendo_inactivos(proveedor_id: int) -> Proveedor | None:
    with obtener_conexion() as conexion:
        return obtener_por_id_incluyendo_inactivos_en_conexion(conexion, proveedor_id)


def _escapar_comodines_like(texto: str) -> str:
    """Escapa `\\`, `%` y `_` para que lo que tipea el usuario no se interprete como patrón LIKE."""
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def buscar(
    texto: str, incluir_inactivos: bool = False, categoria_ids: Iterable[int] | None = None
) -> list[Proveedor]:
    """Proveedores cuyo nombre, razón social, contacto, teléfono, email o CUIT contienen `texto` (sin
    distinguir mayúsculas; el CUIT se compara sin guiones), ordenados por nombre. Con `categoria_ids`,
    solo los clasificados en alguna de esas categorías. Los comodines LIKE se buscan como texto literal."""
    condiciones = [] if incluir_inactivos else ["activo = 1"]
    parametros: list[object] = []
    texto = texto.strip()
    if texto:
        patron = f"%{_escapar_comodines_like(texto)}%"
        digitos = "".join(caracter for caracter in texto if caracter.isdigit())
        condiciones.append(
            "(nombre LIKE ? ESCAPE '\\' OR razon_social LIKE ? ESCAPE '\\' OR contacto_nombre LIKE ? ESCAPE '\\'"
            " OR telefono LIKE ? ESCAPE '\\' OR email LIKE ? ESCAPE '\\' OR (? <> '' AND cuit LIKE ?))"
        )
        parametros += [patron] * 5 + [digitos, f"%{digitos}%"]
    if categoria_ids is not None:
        ids = sorted(set(categoria_ids))
        marcadores = ", ".join("?" for _ in ids) or "NULL"
        condiciones.append(
            f"id IN (SELECT proveedor_id FROM proveedor_categorias WHERE categoria_id IN ({marcadores}))"
        )
        parametros += ids
    donde = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    consulta = f"SELECT {_COLUMNAS} FROM proveedores {donde} ORDER BY nombre"
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta, parametros).fetchall()
    return [_fila_a_proveedor(fila) for fila in filas]


def actualizar_datos_en_conexion(conexion: sqlite3.Connection, proveedor: Proveedor) -> Proveedor:
    """Actualiza los datos editables de un proveedor existente (no su id ni `activo`)."""
    if proveedor.id is None:
        raise ProveedorNoEncontradoError("No se puede actualizar un proveedor sin id.")

    consulta = f"""
        UPDATE proveedores
        SET nombre = ?, contacto_nombre = ?, telefono = ?, email = ?, direccion = ?, notas = ?,
            razon_social = ?, cuit = ?, condicion_fiscal = ?, ingresos_brutos = ?, convenio_multilateral = ?
        WHERE id = ?
        RETURNING {_COLUMNAS}
    """
    try:
        fila = conexion.execute(consulta, (*_parametros_de_datos(proveedor), proveedor.id)).fetchone()
    except sqlite3.IntegrityError as error:
        raise _traducir_integridad(error, proveedor) from error

    if fila is None:
        raise ProveedorNoEncontradoError(f"No existe un proveedor con id {proveedor.id}.")
    return _fila_a_proveedor(fila)


def actualizar_datos(proveedor: Proveedor) -> Proveedor:
    """Actualiza los datos editables de un proveedor en su propia transacción."""
    with obtener_conexion() as conexion:
        return actualizar_datos_en_conexion(conexion, proveedor)


def eliminar_proveedor_en_conexion(conexion: sqlite3.Connection, proveedor_id: int) -> bool:
    """Da de baja un proveedor activo dentro de la transacción recibida. Devuelve `True` si la
    baja fue lógica.

    Intenta primero un `DELETE` físico. Si el proveedor tiene compras, productos vinculados o, desde
    V1.13, evaluaciones, reclamos o documentos, la FK `ON DELETE RESTRICT` lo impide
    (`sqlite3.IntegrityError`; el `DELETE` fallido no deja ningún efecto) y se lo desactiva (mismo patrón
    que `db.repositorios.productos.eliminar_producto_en_conexion`). Su clasificación por categorías no es
    historia: se borra en cascada con él.
    """
    try:
        cursor = conexion.execute("DELETE FROM proveedores WHERE id = ? AND activo = 1", (proveedor_id,))
        fue_eliminado = cursor.rowcount > 0
        baja_logica = False
    except sqlite3.IntegrityError:
        cursor = conexion.execute("UPDATE proveedores SET activo = 0 WHERE id = ? AND activo = 1", (proveedor_id,))
        fue_eliminado = cursor.rowcount > 0
        baja_logica = True

    if not fue_eliminado:
        raise ProveedorNoEncontradoError(f"No existe un proveedor activo con id {proveedor_id}.")
    return baja_logica


def eliminar_proveedor(proveedor_id: int) -> bool:
    """Elimina un proveedor activo en su propia transacción. Devuelve `True` si la baja fue lógica."""
    with obtener_conexion() as conexion:
        return eliminar_proveedor_en_conexion(conexion, proveedor_id)


def reactivar_proveedor_en_conexion(conexion: sqlite3.Connection, proveedor_id: int) -> Proveedor:
    """Revierte una baja lógica (`activo` de 0 a 1). No toca ninguna otra columna."""
    consulta = f"""
        UPDATE proveedores
        SET activo = 1
        WHERE id = ? AND activo = 0
        RETURNING {_COLUMNAS}
    """
    fila = conexion.execute(consulta, (proveedor_id,)).fetchone()
    if fila is None:
        raise ProveedorNoEncontradoError(f"No existe un proveedor inactivo con id {proveedor_id}.")
    return _fila_a_proveedor(fila)


def reactivar_proveedor(proveedor_id: int) -> Proveedor:
    """Reactiva un proveedor dado de baja lógica en su propia transacción."""
    with obtener_conexion() as conexion:
        return reactivar_proveedor_en_conexion(conexion, proveedor_id)


# --- clasificación por categorías (V1.13-A) --------------------------------------------------------


def ids_categorias_en_conexion(conexion: sqlite3.Connection, proveedor_id: int) -> set[int]:
    filas = conexion.execute(
        "SELECT categoria_id FROM proveedor_categorias WHERE proveedor_id = ?", (proveedor_id,)
    ).fetchall()
    return {fila["categoria_id"] for fila in filas}


def reemplazar_categorias_en_conexion(
    conexion: sqlite3.Connection, proveedor_id: int, agregar: Iterable[int], quitar: Iterable[int]
) -> None:
    """Agrega y quita vínculos proveedor-categoría. Nunca toca las categorías en sí."""
    conexion.executemany(
        "DELETE FROM proveedor_categorias WHERE proveedor_id = ? AND categoria_id = ?",
        [(proveedor_id, categoria_id) for categoria_id in quitar],
    )
    conexion.executemany(
        "INSERT INTO proveedor_categorias (proveedor_id, categoria_id) VALUES (?, ?)",
        [(proveedor_id, categoria_id) for categoria_id in agregar],
    )


def categorias_por_proveedor(proveedor_ids: Iterable[int] | None = None) -> dict[int, list[int]]:
    """Ids de categoría de cada proveedor (todos, o solo los indicados), en una única consulta."""
    if proveedor_ids is None:
        consulta, parametros = "SELECT proveedor_id, categoria_id FROM proveedor_categorias", []
    else:
        ids = sorted(set(proveedor_ids))
        if not ids:
            return {}
        marcadores = ", ".join("?" for _ in ids)
        consulta = f"SELECT proveedor_id, categoria_id FROM proveedor_categorias WHERE proveedor_id IN ({marcadores})"
        parametros = ids
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta, parametros).fetchall()
    resultado: dict[int, list[int]] = {}
    for fila in filas:
        resultado.setdefault(fila["proveedor_id"], []).append(fila["categoria_id"])
    return resultado
