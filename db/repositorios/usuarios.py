"""Repositorio de acceso a datos de usuarios (Fase 2B).

Contiene únicamente consultas SQL parametrizadas sobre `usuarios`. No
contiene reglas de negocio: la validación de los datos ocurre en
`domain.usuario.Usuario`. El login/autenticación (hashing,
verificación de contraseña, sesiones) se implementa en `services/` en
una fase posterior.
"""

import sqlite3

from db.conexion import obtener_conexion
from domain.usuario import Usuario
from excepciones import ErrorBaseDatos, NombreUsuarioDuplicadoError, UsuarioNoEncontradoError

_COLUMNAS = "id, nombre_usuario, nombre_completo, password_hash, rol, activo, fecha_creacion"


def _fila_a_usuario(fila: sqlite3.Row) -> Usuario:
    """Mapea una fila de la tabla `usuarios` a la entidad de dominio."""
    return Usuario(
        id=fila["id"],
        nombre_usuario=fila["nombre_usuario"],
        nombre_completo=fila["nombre_completo"],
        password_hash=fila["password_hash"],
        rol=fila["rol"],
        activo=bool(fila["activo"]),
        fecha_creacion=fila["fecha_creacion"],
    )


def crear_usuario(usuario: Usuario) -> Usuario:
    """Inserta un nuevo usuario y devuelve la entidad con su id asignado.

    Traduce la violación de UNIQUE sobre `nombre_usuario` en
    `NombreUsuarioDuplicadoError`, para que la capa de servicios pueda
    reaccionar a ese caso puntual sin inspeccionar mensajes de error.
    """
    consulta = f"""
        INSERT INTO usuarios (nombre_usuario, nombre_completo, password_hash, rol, activo)
        VALUES (?, ?, ?, ?, ?)
        RETURNING {_COLUMNAS}
    """
    parametros = (
        usuario.nombre_usuario,
        usuario.nombre_completo,
        usuario.password_hash,
        usuario.rol,
        int(usuario.activo),
    )
    try:
        with obtener_conexion() as conexion:
            fila = conexion.execute(consulta, parametros).fetchone()
    except ErrorBaseDatos as error:
        if isinstance(error.__cause__, sqlite3.IntegrityError):
            raise NombreUsuarioDuplicadoError(
                f"Ya existe un usuario con el nombre de usuario '{usuario.nombre_usuario}'."
            ) from error
        raise
    return _fila_a_usuario(fila)


def obtener_por_id(usuario_id: int) -> Usuario | None:
    """Busca un usuario por id, o `None` si no existe."""
    consulta = f"SELECT {_COLUMNAS} FROM usuarios WHERE id = ?"
    with obtener_conexion() as conexion:
        fila = conexion.execute(consulta, (usuario_id,)).fetchone()
    return _fila_a_usuario(fila) if fila is not None else None


def obtener_por_nombre_usuario(nombre_usuario: str) -> Usuario | None:
    """Busca un usuario por su nombre de usuario, o `None` si no existe."""
    consulta = f"SELECT {_COLUMNAS} FROM usuarios WHERE nombre_usuario = ?"
    with obtener_conexion() as conexion:
        fila = conexion.execute(consulta, (nombre_usuario,)).fetchone()
    return _fila_a_usuario(fila) if fila is not None else None


def listar_todos() -> list[Usuario]:
    """Devuelve todos los usuarios, ordenados por nombre de usuario."""
    consulta = f"SELECT {_COLUMNAS} FROM usuarios ORDER BY nombre_usuario"
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta).fetchall()
    return [_fila_a_usuario(fila) for fila in filas]


def actualizar_nombre_completo(usuario_id: int, nombre_completo: str) -> Usuario:
    """Actualiza únicamente `nombre_completo`. `nombre_usuario` es
    inmutable después de creado el usuario (Etapa B.1 del MVP): no
    existe ninguna operación que lo modifique."""
    consulta = f"""
        UPDATE usuarios SET nombre_completo = ? WHERE id = ?
        RETURNING {_COLUMNAS}
    """
    with obtener_conexion() as conexion:
        fila = conexion.execute(consulta, (nombre_completo, usuario_id)).fetchone()
    if fila is None:
        raise UsuarioNoEncontradoError(f"No existe un usuario con id {usuario_id}.")
    return _fila_a_usuario(fila)


def actualizar_rol(usuario_id: int, rol: str) -> Usuario:
    """Actualiza el rol de un usuario. La regla de negocio de "nunca
    dejar el sistema sin ningún OWNER activo" se valida en
    `services.servicio_usuarios`, no acá: este repositorio solo
    ejecuta el `UPDATE` ya decidido."""
    consulta = f"""
        UPDATE usuarios SET rol = ? WHERE id = ?
        RETURNING {_COLUMNAS}
    """
    with obtener_conexion() as conexion:
        fila = conexion.execute(consulta, (rol, usuario_id)).fetchone()
    if fila is None:
        raise UsuarioNoEncontradoError(f"No existe un usuario con id {usuario_id}.")
    return _fila_a_usuario(fila)


def actualizar_activo(usuario_id: int, activo: bool) -> Usuario:
    """Activa o desactiva un usuario (baja lógica: nunca hay
    eliminación física de usuarios). La regla de "nunca dejar el
    sistema sin ningún OWNER activo" se valida en
    `services.servicio_usuarios`."""
    consulta = f"""
        UPDATE usuarios SET activo = ? WHERE id = ?
        RETURNING {_COLUMNAS}
    """
    with obtener_conexion() as conexion:
        fila = conexion.execute(consulta, (int(activo), usuario_id)).fetchone()
    if fila is None:
        raise UsuarioNoEncontradoError(f"No existe un usuario con id {usuario_id}.")
    return _fila_a_usuario(fila)


def actualizar_password(usuario_id: int, password_hash: str) -> Usuario:
    """Fija un `password_hash` nuevo (reset por un OWNER, o autocambio
    de la propia contraseña). Quien llama ya calculó el hash con
    `services.servicio_auth.hashear_password` -- este repositorio nunca
    hashea ni recibe una contraseña en texto plano."""
    consulta = f"""
        UPDATE usuarios SET password_hash = ? WHERE id = ?
        RETURNING {_COLUMNAS}
    """
    with obtener_conexion() as conexion:
        fila = conexion.execute(consulta, (password_hash, usuario_id)).fetchone()
    if fila is None:
        raise UsuarioNoEncontradoError(f"No existe un usuario con id {usuario_id}.")
    return _fila_a_usuario(fila)


def crear_primer_owner(nombre_usuario: str, nombre_completo: str, password_hash: str) -> Usuario | None:
    """Crea el primer usuario OWNER de una instalación limpia, de forma
    atómica (ver auditoría de Stage D: configuración inicial).

    A diferencia de `crear_usuario`, esta función nunca falla porque ya
    exista un OWNER: en ese caso simplemente no inserta nada y
    devuelve `None`. La comprobación "no hay ningún OWNER activo" y la
    inserción ocurren en una única sentencia SQL (`INSERT ... SELECT
    ... WHERE NOT EXISTS`), por lo que es segura ante dos llamadas
    concurrentes -- nunca pueden crearse dos "primeros" OWNER a la vez,
    sin necesidad de un lock explícito ni de cambiar `db/conexion.py`:
    alcanza con que SQLite serialice la ejecución real de dos INSERT
    concurrentes sobre el mismo archivo, como ya hace por defecto.
    """
    consulta = f"""
        INSERT INTO usuarios (nombre_usuario, nombre_completo, password_hash, rol, activo)
        SELECT ?, ?, ?, 'OWNER', 1
        WHERE NOT EXISTS (
            SELECT 1 FROM usuarios WHERE rol = 'OWNER' AND activo = 1
        )
        RETURNING {_COLUMNAS}
    """
    parametros = (nombre_usuario, nombre_completo, password_hash)
    try:
        with obtener_conexion() as conexion:
            fila = conexion.execute(consulta, parametros).fetchone()
    except ErrorBaseDatos as error:
        if isinstance(error.__cause__, sqlite3.IntegrityError):
            raise NombreUsuarioDuplicadoError(
                f"Ya existe un usuario con el nombre de usuario '{nombre_usuario}'."
            ) from error
        raise
    return _fila_a_usuario(fila) if fila is not None else None


def contar_owners_activos() -> int:
    """Cantidad de usuarios con rol OWNER y `activo = 1`.

    Usado por `services.servicio_usuarios` para impedir cualquier
    operación (desactivar, cambiar rol) que dejaría al sistema sin
    ningún OWNER activo."""
    consulta = "SELECT COUNT(*) AS total FROM usuarios WHERE rol = 'OWNER' AND activo = 1"
    with obtener_conexion() as conexion:
        fila = conexion.execute(consulta).fetchone()
    return fila["total"]
