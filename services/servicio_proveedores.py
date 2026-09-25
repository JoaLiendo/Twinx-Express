"""Casos de uso de gestión de proveedores (Fase 4A) y de su relación con los productos (V1.4).

Orquesta `domain.proveedor`, `domain.producto_proveedor` y sus repositorios. Las reglas
son pocas: el nombre no puede repetirse, un proveedor con compras o productos vinculados
no se borra físicamente (se desactiva) y los vínculos producto-proveedor solo los
gestiona un OWNER activo. Cuando se informa `usuario_id`, cada operación deja su registro
de auditoría en la misma transacción que el cambio.
"""

import logging
from dataclasses import dataclass

from db.conexion import ConexionBD, obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import compras as repositorio_compras
from db.repositorios import producto_proveedor as repositorio_producto_proveedor
from db.repositorios import productos as repositorio_productos
from db.repositorios import proveedores as repositorio_proveedores
from db.repositorios import usuarios as repositorio_usuarios
from domain.compra import ResumenCompra
from domain.producto import Producto
from domain.producto_proveedor import ProductoDeProveedor, VinculoProveedor, normalizar_codigo_proveedor
from domain.proveedor import Proveedor
from domain.usuario import exigir_rol
from excepciones import (
    ProductoNoEncontradoError,
    ProveedorNoEncontradoError,
    VinculoProveedorExistenteError,
    VinculoProveedorNoEncontradoError,
)

logger = logging.getLogger(__name__)

_SOLO_OWNER = frozenset({"OWNER"})


@dataclass(frozen=True)
class FichaProveedor:
    """Un proveedor con sus productos vinculados y su historial de compras (más recientes primero)."""

    proveedor: Proveedor
    productos: list[ProductoDeProveedor]
    compras: list[ResumenCompra]


def _auditar(
    conexion: ConexionBD, usuario_id: int | None, accion: str, entidad: str, entidad_id: int, resumen: str
) -> None:
    if usuario_id is not None:
        repositorio_auditoria.registrar_en_conexion(conexion, usuario_id, accion, entidad, entidad_id, resumen)


def crear_proveedor(
    nombre: str,
    contacto_nombre: str | None = None,
    telefono: str | None = None,
    email: str | None = None,
    direccion: str | None = None,
    notas: str | None = None,
    usuario_id: int | None = None,
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
    with obtener_conexion(inmediata=True) as conexion:
        proveedor_creado = repositorio_proveedores.crear_proveedor_en_conexion(conexion, proveedor)
        _auditar(
            conexion, usuario_id, "PROVEEDOR_CREADO", "PROVEEDOR", proveedor_creado.id,
            f"Proveedor creado: {proveedor_creado.nombre}",
        )
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


def buscar_proveedores(texto: str, incluir_inactivos: bool = False) -> list[Proveedor]:
    """Proveedores cuyo nombre, contacto, teléfono o email contienen `texto`. Un texto vacío
    devuelve el listado normal (activos, o todos con `incluir_inactivos`)."""
    if not texto.strip():
        return listar_todos() if incluir_inactivos else listar_activos()
    return repositorio_proveedores.buscar(texto, incluir_inactivos)


def obtener_ficha(proveedor_id: int) -> FichaProveedor:
    """Ficha de un proveedor (activo o dado de baja): datos, productos vinculados e historial de compras.

    Raises:
        ProveedorNoEncontradoError: si no existe un proveedor con ese id.
    """
    proveedor = repositorio_proveedores.obtener_por_id_incluyendo_inactivos(proveedor_id)
    if proveedor is None:
        raise ProveedorNoEncontradoError(f"No existe un proveedor con id {proveedor_id}.")
    return FichaProveedor(
        proveedor=proveedor,
        productos=repositorio_producto_proveedor.listar_por_proveedor(proveedor_id),
        compras=repositorio_compras.listar_resumen(proveedor_id),
    )


def actualizar_proveedor(
    proveedor_id: int,
    nombre: str,
    contacto_nombre: str | None = None,
    telefono: str | None = None,
    email: str | None = None,
    direccion: str | None = None,
    notas: str | None = None,
    usuario_id: int | None = None,
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
    with obtener_conexion(inmediata=True) as conexion:
        resultado = repositorio_proveedores.actualizar_datos_en_conexion(conexion, proveedor_actualizado)
        _auditar(
            conexion, usuario_id, "PROVEEDOR_EDITADO", "PROVEEDOR", resultado.id,
            f"Proveedor editado: {resultado.nombre}",
        )
    logger.info("Proveedor actualizado: %s (id=%s)", resultado.nombre, resultado.id)
    return resultado


def eliminar_proveedor(proveedor_id: int, usuario_id: int | None = None) -> bool:
    """Da de baja un proveedor. Devuelve `True` si la baja fue lógica (tenía compras o productos vinculados).

    Raises:
        ProveedorNoEncontradoError: si no existe un proveedor activo con ese id.
    """
    with obtener_conexion(inmediata=True) as conexion:
        proveedor = repositorio_proveedores.obtener_por_id_en_conexion(conexion, proveedor_id)
        if proveedor is None:
            raise ProveedorNoEncontradoError(f"No existe un proveedor activo con id {proveedor_id}.")
        baja_logica = repositorio_proveedores.eliminar_proveedor_en_conexion(conexion, proveedor_id)
        detalle = "baja lógica (conserva su historial)" if baja_logica else "eliminado"
        _auditar(conexion, usuario_id, "PROVEEDOR_BAJA", "PROVEEDOR", proveedor_id, f"{proveedor.nombre}: {detalle}")
    logger.info("Proveedor id=%s dado de baja (baja_logica=%s)", proveedor_id, baja_logica)
    return baja_logica


def reactivar_proveedor(proveedor_id: int, usuario_id: int | None = None) -> Proveedor:
    """Reactiva un proveedor dado de baja lógica.

    Raises:
        ProveedorNoEncontradoError: si no existe un proveedor inactivo con ese id.
    """
    with obtener_conexion(inmediata=True) as conexion:
        proveedor = repositorio_proveedores.reactivar_proveedor_en_conexion(conexion, proveedor_id)
        _auditar(
            conexion, usuario_id, "PROVEEDOR_REACTIVADO", "PROVEEDOR", proveedor_id,
            f"Proveedor reactivado: {proveedor.nombre}",
        )
    logger.info("Proveedor reactivado: %s (id=%s)", proveedor.nombre, proveedor.id)
    return proveedor


# --- relación producto-proveedor (V1.4) ------------------------------------------------------------


def _resolver_para_vinculo(
    conexion: ConexionBD, usuario_id: int, proveedor_id: int, producto_id: int
) -> tuple[Proveedor, Producto]:
    """Exige un OWNER activo y resuelve proveedor y producto (ambos activos) dentro de la transacción."""
    exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _SOLO_OWNER)
    proveedor = repositorio_proveedores.obtener_por_id_en_conexion(conexion, proveedor_id)
    if proveedor is None:
        raise ProveedorNoEncontradoError(f"No existe un proveedor activo con id {proveedor_id}.")
    producto = repositorio_productos.obtener_por_id_en_conexion(conexion, producto_id)
    if producto is None:
        raise ProductoNoEncontradoError(f"No existe un producto activo con id {producto_id}.")
    return proveedor, producto


def vincular_producto(
    proveedor_id: int, producto_id: int, usuario_id: int, codigo_proveedor: str | None = None
) -> VinculoProveedor:
    """Vincula un producto activo a un proveedor activo. Queda principal solo si el producto
    todavía no tenía un principal.

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        ProveedorNoEncontradoError / ProductoNoEncontradoError: si no existen o están inactivos.
        VinculoProveedorExistenteError: si ya estaban vinculados.
        DatosInvalidosError: si el código del proveedor es demasiado largo.
    """
    codigo = normalizar_codigo_proveedor(codigo_proveedor)
    with obtener_conexion(inmediata=True) as conexion:
        proveedor, producto = _resolver_para_vinculo(conexion, usuario_id, proveedor_id, producto_id)
        if repositorio_producto_proveedor.obtener_vinculo_en_conexion(conexion, producto_id, proveedor_id):
            raise VinculoProveedorExistenteError(
                f"'{producto.nombre}' ya está vinculado al proveedor '{proveedor.nombre}'."
            )
        vinculo = repositorio_producto_proveedor.crear_vinculo_en_conexion(
            conexion, producto_id, proveedor_id, codigo
        )
        principal = " (principal)" if vinculo.es_principal else ""
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, "PRODUCTO_PROVEEDOR_VINCULADO", "PRODUCTO_PROVEEDOR", vinculo.id,
            f"{producto.nombre} vinculado a {proveedor.nombre}{principal}",
        )
    logger.info("Producto %s vinculado al proveedor %s por usuario %s", producto_id, proveedor_id, usuario_id)
    return vinculo


def quitar_vinculo(proveedor_id: int, producto_id: int, usuario_id: int) -> None:
    """Quita el vínculo producto-proveedor. No promueve a otro proveedor como principal.

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        VinculoProveedorNoEncontradoError: si no estaban vinculados.
    """
    with obtener_conexion(inmediata=True) as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _SOLO_OWNER)
        vinculo = repositorio_producto_proveedor.obtener_vinculo_en_conexion(conexion, producto_id, proveedor_id)
        if vinculo is None:
            raise VinculoProveedorNoEncontradoError("El producto no está vinculado a ese proveedor.")
        repositorio_producto_proveedor.quitar_vinculo_en_conexion(conexion, producto_id, proveedor_id)
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, "PRODUCTO_PROVEEDOR_QUITADO", "PRODUCTO_PROVEEDOR", vinculo.id,
            f"Producto {producto_id} desvinculado del proveedor {proveedor_id}",
        )
    logger.info("Producto %s desvinculado del proveedor %s por usuario %s", producto_id, proveedor_id, usuario_id)


def establecer_principal(proveedor_id: int, producto_id: int, usuario_id: int) -> VinculoProveedor:
    """Deja al proveedor como el único principal del producto (acción explícita del OWNER). Si ya
    lo era, no cambia nada ni audita.

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        VinculoProveedorNoEncontradoError: si no estaban vinculados.
    """
    with obtener_conexion(inmediata=True) as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _SOLO_OWNER)
        vinculo = repositorio_producto_proveedor.obtener_vinculo_en_conexion(conexion, producto_id, proveedor_id)
        if vinculo is None:
            raise VinculoProveedorNoEncontradoError("El producto no está vinculado a ese proveedor.")
        if vinculo.es_principal:
            return vinculo
        repositorio_producto_proveedor.establecer_principal_en_conexion(conexion, producto_id, proveedor_id)
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, "PRODUCTO_PROVEEDOR_PRINCIPAL", "PRODUCTO_PROVEEDOR", vinculo.id,
            f"Proveedor {proveedor_id} es ahora el principal del producto {producto_id}",
        )
        actualizado = repositorio_producto_proveedor.obtener_vinculo_en_conexion(conexion, producto_id, proveedor_id)
    logger.info("Proveedor %s principal del producto %s (usuario %s)", proveedor_id, producto_id, usuario_id)
    return actualizado
