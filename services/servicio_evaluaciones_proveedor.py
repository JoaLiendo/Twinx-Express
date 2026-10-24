"""Casos de uso de evaluación de proveedores (V1.13-B): registrar una evaluación nueva (solo OWNER) y
consultar el historial y la calificación actual.

Nunca modifica una evaluación anterior: corregir es registrar otra. La calificación actual se deriva
siempre de la evaluación más reciente (ver `db.repositorios.evaluaciones_proveedor`).
"""

import logging
from collections.abc import Iterable

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import evaluaciones_proveedor as repositorio_evaluaciones
from db.repositorios import proveedores as repositorio_proveedores
from db.repositorios import usuarios as repositorio_usuarios
from domain.evaluacion_proveedor import EvaluacionProveedor, validar_evaluacion
from domain.usuario import exigir_rol
from excepciones import (
    MENSAJE_FORMULARIO_REENVIADO_CON_OTROS_DATOS,
    ClaveIdempotenciaReutilizadaError,
    ProveedorNoEncontradoError,
)

logger = logging.getLogger(__name__)

_SOLO_OWNER = frozenset({"OWNER"})


def registrar_evaluacion(
    proveedor_id: int,
    usuario_id: int,
    estrellas: int,
    comentario: str | None,
    criterios: dict[str, int | None] | None = None,
    clave_idempotencia: str | None = None,
) -> EvaluacionProveedor:
    """Registra una evaluación nueva de un proveedor (activo o dado de baja: evaluarlo no cambia su
    estado ni su historia). No recalcula nada: la estrella general es la que eligió el usuario.

    Un reenvío del mismo formulario (misma `clave_idempotencia`, mismo contenido) devuelve la evaluación
    ya registrada en vez de duplicarla.

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        DatosInvalidosError: estrellas/criterios fuera de 1..5 o comentario vacío.
        ProveedorNoEncontradoError: si el proveedor no existe.
        ClaveIdempotenciaReutilizadaError: si la clave ya se usó con otro contenido.
    """
    estrellas, comentario_limpio, completados = validar_evaluacion(estrellas, comentario, criterios or {})
    with obtener_conexion(inmediata=True) as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _SOLO_OWNER)
        if clave_idempotencia is not None:
            existente = repositorio_evaluaciones.obtener_por_clave_en_conexion(conexion, clave_idempotencia)
            if existente is not None:
                if (existente.proveedor_id, existente.estrellas, existente.comentario, existente.criterios) != (
                    proveedor_id, estrellas, comentario_limpio, completados
                ):
                    raise ClaveIdempotenciaReutilizadaError(MENSAJE_FORMULARIO_REENVIADO_CON_OTROS_DATOS)
                return existente
        proveedor = repositorio_proveedores.obtener_por_id_incluyendo_inactivos_en_conexion(conexion, proveedor_id)
        if proveedor is None:
            raise ProveedorNoEncontradoError(f"No existe un proveedor con id {proveedor_id}.")
        evaluacion = repositorio_evaluaciones.registrar_en_conexion(
            conexion, proveedor_id, estrellas, comentario_limpio, completados, usuario_id, clave_idempotencia
        )
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, "PROVEEDOR_EVALUADO", "PROVEEDOR", proveedor_id,
            f"{proveedor.nombre}: {estrellas} estrella(s) (evaluación #{evaluacion.id})",
        )
    logger.info("Proveedor %s evaluado con %s estrellas por usuario %s", proveedor_id, estrellas, usuario_id)
    return evaluacion


def historial(proveedor_id: int) -> list[EvaluacionProveedor]:
    """Evaluaciones del proveedor, de la más reciente (la calificación actual) a la más antigua."""
    return repositorio_evaluaciones.listar_de_proveedor(proveedor_id)


def calificaciones_actuales(proveedor_ids: Iterable[int] | None = None) -> dict[int, int]:
    """Estrellas actuales por proveedor; los nunca evaluados no aparecen (sin calificación)."""
    return repositorio_evaluaciones.calificaciones_actuales(proveedor_ids)
