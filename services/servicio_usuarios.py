"""Casos de uso de gestión de usuarios/empleados (Etapa B.1 del MVP).

Concentra las reglas de negocio propias de administrar usuarios,
además de la autenticación ya resuelta en `services.servicio_auth`
(login, sesiones, hashing) -- este módulo reutiliza ese hashing, nunca
lo reimplementa.

Reglas de negocio de esta etapa (decididas junto al usuario, no
inventadas acá):

- `nombre_usuario` es inmutable después de creado: no existe ninguna
  operación que lo modifique (`editar_datos` solo toca
  `nombre_completo`).
- La baja es siempre lógica (`activo = 0`); no hay eliminación física.
- Nunca se puede dejar el sistema sin ningún OWNER activo: se bloquea
  tanto desactivar al último OWNER activo como cambiarle el rol a
  CASHIER (`UltimoOwnerActivoError`).
- La autorización por rol (que un CASHIER no pueda llamar a estas
  operaciones) se aplica en `interfaces/web/` vía
  `interfaces.web.auth.requiere_rol`, igual que en el resto de la
  aplicación -- este módulo, como todo `services/`, no conoce roles de
  quien lo llama.
"""

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from excepciones import CredencialesInvalidasError, UltimoOwnerActivoError, UsuarioNoEncontradoError
from services import servicio_auth


def crear_usuario(
    nombre_usuario: str, nombre_completo: str, password: str, rol: str, actor_id: int | None = None
) -> Usuario:
    """Da de alta un nuevo usuario, hasheando su contraseña.

    La validación de negocio (campos obligatorios, rol válido) ocurre
    al construir `Usuario`; `NombreUsuarioDuplicadoError` se propaga
    desde el repositorio si `nombre_usuario` ya existe.
    """
    password_hash = servicio_auth.hashear_password(password)  # costoso: antes de tomar el lock
    usuario = Usuario(
        nombre_usuario=nombre_usuario,
        nombre_completo=nombre_completo,
        password_hash=password_hash,
        rol=rol,
    )
    with obtener_conexion(inmediata=True) as conexion:
        creado = repositorio_usuarios.crear_usuario_en_conexion(conexion, usuario)
        _auditar(conexion, actor_id, "USUARIO_CREADO", creado.id, f"{creado.nombre_usuario} ({creado.rol})")
    return creado


def _auditar(conexion, actor_id: int | None, accion: str, usuario_id: int, resumen: str) -> None:
    """Deja el rastro en la MISMA transacción que la operación sobre el usuario:
    si esta escritura falla, la operación se revierte. Solo con un actor
    autenticado. Nunca recibe contraseñas ni hashes."""
    if actor_id is not None:
        repositorio_auditoria.registrar_en_conexion(conexion, actor_id, accion, "USUARIO", usuario_id, resumen)


def _obtener_o_fallar_en_conexion(conexion, usuario_id: int) -> Usuario:
    usuario = repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id)
    if usuario is None:
        raise UsuarioNoEncontradoError(f"No existe un usuario con id {usuario_id}.")
    return usuario


def hay_algun_owner_activo() -> bool:
    """True si existe al menos un usuario OWNER activo.

    Usado para decidir si una instalación necesita el flujo de
    configuración inicial (ver `interfaces.web.rutas.configuracion_inicial`
    e `interfaces.web.rutas.autenticacion`)."""
    return repositorio_usuarios.contar_owners_activos() > 0


def crear_primer_owner(nombre_usuario: str, nombre_completo: str, password: str) -> Usuario | None:
    """Crea el primer OWNER de una instalación limpia.

    Devuelve `None` (sin lanzar) si ya existía un OWNER activo: la
    comprobación es atómica a nivel de
    `db.repositorios.usuarios.crear_primer_owner`, nunca un simple
    chequeo previo en Python (ver auditoría de concurrencia de Stage D).
    """
    password_hash = servicio_auth.hashear_password(password)
    # Solo para validar los datos (campos no vacíos) antes de persistir,
    # mismo patrón que `crear_usuario`.
    Usuario(
        nombre_usuario=nombre_usuario,
        nombre_completo=nombre_completo,
        password_hash=password_hash,
        rol="OWNER",
    )
    return repositorio_usuarios.crear_primer_owner(
        nombre_usuario=nombre_usuario,
        nombre_completo=nombre_completo,
        password_hash=password_hash,
    )


def listar_todos() -> list[Usuario]:
    """Devuelve todos los usuarios (activos e inactivos), para el panel de empleados."""
    return repositorio_usuarios.listar_todos()


def obtener_por_id(usuario_id: int) -> Usuario | None:
    """Busca un usuario por id (pantalla de edición)."""
    return repositorio_usuarios.obtener_por_id(usuario_id)


def _obtener_o_fallar(usuario_id: int) -> Usuario:
    usuario = repositorio_usuarios.obtener_por_id(usuario_id)
    if usuario is None:
        raise UsuarioNoEncontradoError(f"No existe un usuario con id {usuario_id}.")
    return usuario


def editar_datos(usuario_id: int, nombre_completo: str) -> Usuario:
    """Actualiza `nombre_completo`. `nombre_usuario` nunca se toca acá:
    es inmutable después de creado el usuario."""
    usuario_actual = _obtener_o_fallar(usuario_id)
    # Reconstruye la entidad con el dato nuevo para reutilizar la
    # validación de dominio (no vacío) antes de persistir.
    Usuario(
        nombre_usuario=usuario_actual.nombre_usuario,
        nombre_completo=nombre_completo,
        password_hash=usuario_actual.password_hash,
        rol=usuario_actual.rol,
        id=usuario_actual.id,
        activo=usuario_actual.activo,
        fecha_creacion=usuario_actual.fecha_creacion,
    )
    return repositorio_usuarios.actualizar_nombre_completo(usuario_id, nombre_completo)


def cambiar_rol(usuario_id: int, nuevo_rol: str, actor_id: int | None = None) -> Usuario:
    """Cambia el rol de un usuario.

    Raises:
        UltimoOwnerActivoError: si el usuario es el único OWNER activo
            y `nuevo_rol` no es OWNER (dejaría al sistema sin ningún
            OWNER activo).
    """
    with obtener_conexion(inmediata=True) as conexion:
        usuario_actual = _obtener_o_fallar_en_conexion(conexion, usuario_id)
        # Reconstruye la entidad para validar que `nuevo_rol` sea uno de
        # los roles válidos (mismo mecanismo que `editar_datos`).
        Usuario(
            nombre_usuario=usuario_actual.nombre_usuario,
            nombre_completo=usuario_actual.nombre_completo,
            password_hash=usuario_actual.password_hash,
            rol=nuevo_rol,
            id=usuario_actual.id,
            activo=usuario_actual.activo,
            fecha_creacion=usuario_actual.fecha_creacion,
        )
        es_el_ultimo_owner_activo = (
            usuario_actual.rol == "OWNER"
            and usuario_actual.activo
            and nuevo_rol != "OWNER"
            and repositorio_usuarios.contar_owners_activos_en_conexion(conexion) <= 1
        )
        if es_el_ultimo_owner_activo:
            raise UltimoOwnerActivoError(
                "No se puede cambiar el rol del último OWNER activo: el sistema quedaría sin ningún OWNER."
            )
        actualizado = repositorio_usuarios.actualizar_rol_en_conexion(conexion, usuario_id, nuevo_rol)
        _auditar(conexion, actor_id, "USUARIO_ROL_CAMBIADO", usuario_id, f"{actualizado.nombre_usuario}: rol {nuevo_rol}")
    return actualizado


def activar(usuario_id: int, actor_id: int | None = None) -> Usuario:
    """Reactiva un usuario dado de baja lógica."""
    with obtener_conexion(inmediata=True) as conexion:
        _obtener_o_fallar_en_conexion(conexion, usuario_id)
        activado = repositorio_usuarios.actualizar_activo_en_conexion(conexion, usuario_id, True)
        _auditar(conexion, actor_id, "USUARIO_ACTIVADO", usuario_id, activado.nombre_usuario)
    return activado


def desactivar(usuario_id: int, actor_id: int | None = None) -> Usuario:
    """Da de baja lógica a un usuario (`activo = 0`); nunca elimina el registro.

    Raises:
        UltimoOwnerActivoError: si el usuario es el único OWNER activo
            (dejaría al sistema sin ningún OWNER activo).
    """
    with obtener_conexion(inmediata=True) as conexion:
        usuario_actual = _obtener_o_fallar_en_conexion(conexion, usuario_id)
        es_el_ultimo_owner_activo = (
            usuario_actual.rol == "OWNER"
            and usuario_actual.activo
            and repositorio_usuarios.contar_owners_activos_en_conexion(conexion) <= 1
        )
        if es_el_ultimo_owner_activo:
            raise UltimoOwnerActivoError(
                "No se puede desactivar al último OWNER activo: el sistema quedaría sin ningún OWNER."
            )
        desactivado = repositorio_usuarios.actualizar_activo_en_conexion(conexion, usuario_id, False)
        _auditar(conexion, actor_id, "USUARIO_DESACTIVADO", usuario_id, desactivado.nombre_usuario)
    return desactivado


def resetear_password(usuario_id: int, password_nueva: str, actor_id: int | None = None) -> Usuario:
    """Fija una contraseña nueva para otro usuario, sin verificar la
    anterior (acción de un OWNER sobre un tercero). La autorización de
    que quien llama sea OWNER se aplica en la capa web, no acá."""
    password_hash = servicio_auth.hashear_password(password_nueva)  # costoso: antes de tomar el lock
    with obtener_conexion(inmediata=True) as conexion:
        _obtener_o_fallar_en_conexion(conexion, usuario_id)
        reseteado = repositorio_usuarios.actualizar_password_en_conexion(conexion, usuario_id, password_hash)
        # Solo se audita QUE se reseteó, nunca la contraseña ni su hash.
        _auditar(conexion, actor_id, "USUARIO_PASSWORD_RESETEADA", usuario_id, reseteado.nombre_usuario)
    return reseteado


def cambiar_password_propia(usuario_id: int, password_actual: str, password_nueva: str) -> Usuario:
    """Cambia la propia contraseña de un usuario autenticado, verificando
    primero la contraseña actual.

    Raises:
        CredencialesInvalidasError: si `password_actual` no coincide
            con la contraseña vigente del usuario.
    """
    usuario_actual = _obtener_o_fallar(usuario_id)
    if not servicio_auth.verificar_password(password_actual, usuario_actual.password_hash):
        raise CredencialesInvalidasError("La contraseña actual es incorrecta.")
    password_hash = servicio_auth.hashear_password(password_nueva)
    return repositorio_usuarios.actualizar_password(usuario_id, password_hash)
