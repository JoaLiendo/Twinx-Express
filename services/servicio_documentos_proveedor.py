"""Casos de uso de la documentación fiscal declarada de proveedores (V1.13-D): registrar un documento
(solo OWNER) y listarlos. Sin archivos ni validaciones externas (ver `domain.documento_proveedor`)."""

import logging
from datetime import date

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import documentos_proveedor as repositorio_documentos
from db.repositorios import proveedores as repositorio_proveedores
from db.repositorios import usuarios as repositorio_usuarios
from domain.documento_proveedor import DocumentoProveedor, validar_documento
from domain.usuario import exigir_rol
from excepciones import (
    MENSAJE_FORMULARIO_REENVIADO_CON_OTROS_DATOS,
    ClaveIdempotenciaReutilizadaError,
    ProveedorNoEncontradoError,
)

logger = logging.getLogger(__name__)

_SOLO_OWNER = frozenset({"OWNER"})


def registrar_documento(
    proveedor_id: int,
    usuario_id: int,
    tipo: str,
    fecha_documento: str,
    referencia: str | None = None,
    observacion: str | None = None,
    clave_idempotencia: str | None = None,
    hoy: date | None = None,
) -> DocumentoProveedor:
    """Registra un documento fiscal declarado del proveedor (activo o dado de baja). Un reenvío del mismo
    formulario no lo duplica. `hoy` es solo para tests.

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        DatosInvalidosError: tipo, fecha o textos inválidos.
        ProveedorNoEncontradoError: si el proveedor no existe.
        ClaveIdempotenciaReutilizadaError: si la clave ya se usó con otro contenido.
    """
    fecha, referencia_limpia, observacion_limpia = validar_documento(
        tipo, fecha_documento, referencia, observacion, hoy or date.today()
    )
    with obtener_conexion(inmediata=True) as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _SOLO_OWNER)
        if clave_idempotencia is not None:
            existente = repositorio_documentos.obtener_por_clave_en_conexion(conexion, clave_idempotencia)
            if existente is not None:
                if (existente.proveedor_id, existente.tipo, existente.fecha_documento, existente.referencia,
                        existente.observacion) != (proveedor_id, tipo, fecha, referencia_limpia, observacion_limpia):
                    raise ClaveIdempotenciaReutilizadaError(MENSAJE_FORMULARIO_REENVIADO_CON_OTROS_DATOS)
                return existente
        proveedor = repositorio_proveedores.obtener_por_id_incluyendo_inactivos_en_conexion(conexion, proveedor_id)
        if proveedor is None:
            raise ProveedorNoEncontradoError(f"No existe un proveedor con id {proveedor_id}.")
        documento = repositorio_documentos.registrar_en_conexion(
            conexion, proveedor_id, tipo, fecha, referencia_limpia, observacion_limpia, usuario_id, clave_idempotencia
        )
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, "DOCUMENTO_PROVEEDOR_REGISTRADO", "PROVEEDOR", proveedor_id,
            f"{proveedor.nombre}: {documento.tipo_etiqueta} del {fecha}",
        )
    logger.info("Documento %s registrado para el proveedor %s por usuario %s", documento.id, proveedor_id, usuario_id)
    return documento


def listar_de_proveedor(proveedor_id: int) -> list[DocumentoProveedor]:
    return repositorio_documentos.listar_de_proveedor(proveedor_id)
