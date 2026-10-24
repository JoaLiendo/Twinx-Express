"""Casos de uso de reclamos e incidentes con proveedores (V1.13-C): registrar un reclamo (solo OWNER),
avanzar su estado con historial auditable y consultarlos.

Un reclamo nunca toca la evaluación del proveedor ni su cuenta a pagar: son dimensiones distintas.
Sus relaciones (producto, compra, línea, ajuste FALLA_FABRICA) se validan contra los datos reales y,
desde un ajuste, el proveedor solo se propone si se conoce de verdad (ver `domain.reclamo_proveedor`).
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from db.conexion import ConexionBD, obtener_conexion
from db.repositorios import ajustes_stock as repositorio_ajustes_stock
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import proveedores as repositorio_proveedores
from db.repositorios import reclamos_proveedor as repositorio_reclamos
from db.repositorios import usuarios as repositorio_usuarios
from domain.ajuste_stock import AjusteStock
from domain.reclamo_proveedor import (
    ESTADOS_ABIERTOS,
    OrigenIdentificado,
    ReclamoProveedor,
    identificar_origen,
    validar_cantidad,
    validar_texto,
    validar_tipo,
    validar_transicion,
)
from domain.usuario import exigir_rol
from excepciones import (
    MENSAJE_FORMULARIO_REENVIADO_CON_OTROS_DATOS,
    AjusteNoEncontradoError,
    AjusteYaReclamadoError,
    ClaveIdempotenciaReutilizadaError,
    CompraNoEncontradaError,
    DatosInvalidosError,
    ProductoNoEncontradoError,
    ProveedorNoEncontradoError,
    ReclamoDesactualizadoError,
    ReclamoNoEncontradoError,
)

logger = logging.getLogger(__name__)

_SOLO_OWNER = frozenset({"OWNER"})
MOTIVO_RECLAMABLE = "FALLA_FABRICA"


@dataclass(frozen=True)
class AjusteReclamable:
    """Lo que se sabe de un ajuste FALLA_FABRICA para proponer un reclamo: `origen` es `None` si el
    proveedor no se puede afirmar (lo elige el OWNER)."""

    ajuste: AjusteStock
    origen: OrigenIdentificado | None
    reclamo_existente_id: int | None


def _ajuste_reclamable_en_conexion(conexion: ConexionBD, ajuste_id: int) -> AjusteReclamable:
    ajuste = repositorio_ajustes_stock.obtener_por_id_en_conexion(conexion, ajuste_id)
    if ajuste is None:
        raise AjusteNoEncontradoError(f"No existe un ajuste de stock con id {ajuste_id}.")
    if ajuste.motivo != MOTIVO_RECLAMABLE or ajuste.delta >= 0:
        raise DatosInvalidosError("Solo un ajuste por falla de fábrica puede originar un reclamo al proveedor.")
    if repositorio_ajustes_stock.obtener_reversa_de_en_conexion(conexion, ajuste_id) is not None:
        raise DatosInvalidosError("Ese ajuste fue revertido: ya no hay una pérdida que reclamar.")
    return AjusteReclamable(
        ajuste=ajuste,
        origen=identificar_origen(repositorio_reclamos.consumos_de_ajuste_en_conexion(conexion, ajuste_id)),
        reclamo_existente_id=repositorio_reclamos.obtener_por_ajuste_en_conexion(conexion, ajuste_id),
    )


def ajuste_reclamable(ajuste_id: int) -> AjusteReclamable:
    """Datos para el formulario de reclamo desde un ajuste FALLA_FABRICA.

    Raises:
        AjusteNoEncontradoError: si no existe.
        DatosInvalidosError: si no es una baja por falla de fábrica o fue revertido.
    """
    with obtener_conexion() as conexion:
        return _ajuste_reclamable_en_conexion(conexion, ajuste_id)


def _resolver_relaciones(
    conexion: ConexionBD,
    proveedor_id: int,
    tipo: str,
    producto_id: int | None,
    compra_id: int | None,
    ajuste_id: int | None,
    cantidad: int | None,
) -> tuple[int | None, int | None, int | None]:
    """Valida las relaciones opcionales y devuelve `(producto_id, compra_id, detalle_compra_id)` finales."""
    detalle_compra_id = None
    if ajuste_id is not None:
        reclamable = _ajuste_reclamable_en_conexion(conexion, ajuste_id)
        if reclamable.reclamo_existente_id is not None:
            raise AjusteYaReclamadoError(f"Ese ajuste ya tiene el reclamo #{reclamable.reclamo_existente_id}.")
        if tipo != MOTIVO_RECLAMABLE:
            raise DatosInvalidosError("Un reclamo originado en una falla de fábrica es de tipo Falla de fábrica.")
        if producto_id is not None and producto_id != reclamable.ajuste.producto_id:
            raise DatosInvalidosError("El producto no coincide con el del ajuste.")
        producto_id = reclamable.ajuste.producto_id
        if cantidad is not None and cantidad > -reclamable.ajuste.delta:
            raise DatosInvalidosError(f"La cantidad no puede superar la dada de baja ({-reclamable.ajuste.delta}).")
        origen = reclamable.origen
        if origen is not None:
            if origen.proveedor_id != proveedor_id:
                otro = repositorio_proveedores.obtener_por_id_incluyendo_inactivos_en_conexion(conexion, origen.proveedor_id)
                raise DatosInvalidosError(
                    f"La mercadería dada de baja provino de {otro.nombre if otro else 'otro proveedor'}: "
                    "el reclamo es para ese proveedor."
                )
            if compra_id is None:
                compra_id, detalle_compra_id = origen.compra_id, origen.detalle_compra_id
    if producto_id is not None and not repositorio_reclamos.producto_existe_en_conexion(conexion, producto_id):
        raise ProductoNoEncontradoError(f"No existe un producto con id {producto_id}.")
    if compra_id is not None:
        proveedor_de_compra = repositorio_reclamos.proveedor_de_compra_en_conexion(conexion, compra_id)
        if proveedor_de_compra is None:
            raise CompraNoEncontradaError(f"No existe una compra con id {compra_id}.")
        if proveedor_de_compra != proveedor_id:
            raise DatosInvalidosError(f"La compra #{compra_id} no es de este proveedor.")
        if producto_id is not None and detalle_compra_id is None:
            detalle_compra_id = repositorio_reclamos.linea_de_compra_en_conexion(conexion, compra_id, producto_id)
            if detalle_compra_id is None:
                raise DatosInvalidosError(f"El producto elegido no está en la compra #{compra_id}.")
    return producto_id, compra_id, detalle_compra_id


def crear_reclamo(
    proveedor_id: int,
    usuario_id: int,
    tipo: str,
    descripcion: str | None,
    *,
    producto_id: int | None = None,
    compra_id: int | None = None,
    ajuste_id: int | None = None,
    cantidad: int | None = None,
    clave_idempotencia: str | None = None,
) -> ReclamoProveedor:
    """Registra un reclamo PENDIENTE al proveedor (activo o dado de baja: puede reclamarse por una
    compra histórica). Con `compra_id` y `producto_id`, la línea de compra se resuelve sola; con
    `ajuste_id`, el producto sale del ajuste y, si el proveedor se conoce de verdad, tiene que ser ese.

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        DatosInvalidosError: datos o relaciones incoherentes (compra de otro proveedor, producto que no
            está en la compra, ajuste que no es de falla de fábrica o fue revertido, proveedor distinto
            del identificado, cantidad mayor a la dada de baja...).
        ProveedorNoEncontradoError / ProductoNoEncontradoError / CompraNoEncontradaError /
            AjusteNoEncontradoError: si alguna referencia no existe.
        AjusteYaReclamadoError: si el ajuste ya originó un reclamo.
        ClaveIdempotenciaReutilizadaError: si la clave ya se usó con otro contenido.
    """
    validar_tipo(tipo)
    descripcion_limpia = validar_texto(descripcion, "La descripción del reclamo")
    validar_cantidad(cantidad)
    with obtener_conexion(inmediata=True) as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _SOLO_OWNER)
        if clave_idempotencia is not None:
            existente = repositorio_reclamos.obtener_por_clave_en_conexion(conexion, clave_idempotencia)
            if existente is not None:
                mismo = (existente.proveedor_id, existente.tipo, existente.descripcion, existente.ajuste_id,
                         existente.cantidad) == (proveedor_id, tipo, descripcion_limpia, ajuste_id, cantidad)
                if not mismo:
                    raise ClaveIdempotenciaReutilizadaError(MENSAJE_FORMULARIO_REENVIADO_CON_OTROS_DATOS)
                return existente
        proveedor = repositorio_proveedores.obtener_por_id_incluyendo_inactivos_en_conexion(conexion, proveedor_id)
        if proveedor is None:
            raise ProveedorNoEncontradoError(f"No existe un proveedor con id {proveedor_id}.")
        producto_id, compra_id, detalle_compra_id = _resolver_relaciones(
            conexion, proveedor_id, tipo, producto_id, compra_id, ajuste_id, cantidad
        )
        reclamo = repositorio_reclamos.crear_en_conexion(
            conexion,
            proveedor_id=proveedor_id,
            tipo=tipo,
            descripcion=descripcion_limpia,
            producto_id=producto_id,
            compra_id=compra_id,
            detalle_compra_id=detalle_compra_id,
            ajuste_id=ajuste_id,
            cantidad=cantidad,
            usuario_id=usuario_id,
            clave_idempotencia=clave_idempotencia,
        )
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, "RECLAMO_PROVEEDOR_CREADO", "RECLAMO_PROVEEDOR", reclamo.id,
            f"Reclamo #{reclamo.id} a {proveedor.nombre} ({reclamo.tipo_etiqueta})",
        )
    logger.info("Reclamo %s registrado al proveedor %s por usuario %s", reclamo.id, proveedor_id, usuario_id)
    return reclamo


def cambiar_estado(
    reclamo_id: int,
    usuario_id: int,
    estado_nuevo: str,
    observacion: str | None,
    estado_esperado: str,
    clave_idempotencia: str | None = None,
) -> ReclamoProveedor:
    """Registra un cambio de estado del reclamo con su observación obligatoria.

    `estado_esperado` es el estado que el usuario estaba viendo: si otro cambio se registró entretanto,
    no se aplica uno pensado sobre un estado ya superado. Un reenvío del mismo formulario (misma clave y
    contenido) no registra un segundo cambio.

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        ReclamoNoEncontradoError: si no existe.
        ReclamoDesactualizadoError: si su estado ya no es `estado_esperado`.
        TransicionReclamoInvalidaError: si el cambio no está permitido (incluye repetir el estado).
        DatosInvalidosError: si falta la observación.
        ClaveIdempotenciaReutilizadaError: si la clave ya se usó con otro contenido.
    """
    observacion_limpia = validar_texto(observacion, "La observación del cambio")
    with obtener_conexion(inmediata=True) as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _SOLO_OWNER)
        if clave_idempotencia is not None:
            cambio = repositorio_reclamos.obtener_cambio_por_clave_en_conexion(conexion, clave_idempotencia)
            if cambio is not None:
                if (cambio.reclamo_id, cambio.estado_anterior, cambio.estado_nuevo, cambio.observacion) != (
                    reclamo_id, estado_esperado, estado_nuevo, observacion_limpia
                ):
                    raise ClaveIdempotenciaReutilizadaError(MENSAJE_FORMULARIO_REENVIADO_CON_OTROS_DATOS)
                return repositorio_reclamos.obtener_en_conexion(conexion, reclamo_id)
        reclamo = repositorio_reclamos.obtener_en_conexion(conexion, reclamo_id)
        if reclamo is None:
            raise ReclamoNoEncontradoError(f"No existe un reclamo con id {reclamo_id}.")
        if reclamo.estado != estado_esperado:
            raise ReclamoDesactualizadoError(
                f"El reclamo ya está {reclamo.estado_etiqueta.lower()}: revisá su historial antes de cambiarlo."
            )
        validar_transicion(reclamo.estado, estado_nuevo)
        repositorio_reclamos.registrar_cambio_en_conexion(
            conexion, reclamo_id, reclamo.estado, estado_nuevo, observacion_limpia, usuario_id, clave_idempotencia
        )
        actualizado = repositorio_reclamos.obtener_en_conexion(conexion, reclamo_id)
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, "RECLAMO_PROVEEDOR_ESTADO", "RECLAMO_PROVEEDOR", reclamo_id,
            f"Reclamo #{reclamo_id} a {reclamo.proveedor_nombre}: {reclamo.estado_etiqueta} → {actualizado.estado_etiqueta}",
        )
    logger.info("Reclamo %s: %s -> %s (usuario %s)", reclamo_id, reclamo.estado, estado_nuevo, usuario_id)
    return actualizado


def obtener(reclamo_id: int, proveedor_id: int) -> ReclamoProveedor:
    """El reclamo con su historial, verificando que sea del proveedor indicado (rutas anidadas).

    Raises:
        ReclamoNoEncontradoError: si no existe o es de otro proveedor.
    """
    reclamo = obtener_por_id(reclamo_id)
    if reclamo.proveedor_id != proveedor_id:
        raise ReclamoNoEncontradoError(f"No existe el reclamo #{reclamo_id} para este proveedor.")
    return reclamo


def obtener_por_id(reclamo_id: int) -> ReclamoProveedor:
    """Raises:
        ReclamoNoEncontradoError: si no existe."""
    reclamo = repositorio_reclamos.obtener(reclamo_id)
    if reclamo is None:
        raise ReclamoNoEncontradoError(f"No existe el reclamo #{reclamo_id}.")
    return reclamo


def listar_de_proveedor(proveedor_id: int) -> list[ReclamoProveedor]:
    return repositorio_reclamos.listar_de_proveedor(proveedor_id)


def abiertos_por_proveedor() -> dict[int, int]:
    """Reclamos abiertos (pendientes o reconocidos) por proveedor; los que no tienen no aparecen."""
    return repositorio_reclamos.abiertos_por_proveedor(ESTADOS_ABIERTOS)


def reclamos_de_ajustes(ajuste_ids: Iterable[int]) -> dict[int, int]:
    """Reclamo originado por cada ajuste que ya tiene uno (para enlazarlo desde el historial de ajustes)."""
    return repositorio_reclamos.reclamos_de_ajustes(ajuste_ids)
