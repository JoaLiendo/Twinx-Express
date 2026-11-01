"""Perfil fiscal del emisor y tratamiento de IVA de los productos (V1.16-A). Solo OWNER."""

import logging

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import fiscal as repositorio_fiscal
from db.repositorios import productos as repositorio_productos
from db.repositorios import usuarios as repositorio_usuarios
from domain.auditoria import ACCION_CONFIGURACION_FISCAL_CAMBIADA, ACCION_PRODUCTO_IVA_CLASIFICADO
from domain.fiscal.emisor import PerfilFiscal
from domain.fiscal.iva import TratamientoIva
from domain.usuario import exigir_rol
from excepciones import ProductoNoEncontradoError

logger = logging.getLogger(__name__)

_SOLO_OWNER = frozenset({"OWNER"})

_ETIQUETAS_CAMPOS = {
    "cuit": "CUIT",
    "razon_social": "razón social",
    "condicion_iva": "condición IVA",
    "domicilio": "domicilio",
    "ingresos_brutos": "IIBB",
    "inicio_actividades": "inicio de actividades",
    "habilitacion_clase_a": "habilitación clase A",
    "punto_venta_cae": "punto de venta",
    "punto_venta_caea": "punto de venta CAEA",
    "contingencia_caea": "contingencia CAEA",
}


def obtener_perfil() -> PerfilFiscal | None:
    return repositorio_fiscal.obtener_perfil()


def guardar_perfil(perfil: PerfilFiscal, usuario_id: int) -> list[str]:
    """Guarda los datos del emisor (nunca el ambiente: eso es del técnico). Devuelve los campos que
    cambiaron, auditados por nombre (sin valores). Un punto de venta o CUIT que cambia pierde su validación.

    Raises:
        PermisoDenegadoError: si no lo hace un OWNER activo.
    """
    with obtener_conexion(inmediata=True) as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _SOLO_OWNER)
        actual = repositorio_fiscal.obtener_perfil_en_conexion(conexion)
        cambiados = [
            etiqueta
            for campo, etiqueta in _ETIQUETAS_CAMPOS.items()
            if actual is None or getattr(actual, campo) != getattr(perfil, campo)
        ]
        if not cambiados:
            return []
        repositorio_fiscal.guardar_perfil_en_conexion(conexion, perfil)
        repositorio_auditoria.registrar_en_conexion(
            conexion,
            usuario_id,
            ACCION_CONFIGURACION_FISCAL_CAMBIADA,
            "PERFIL_FISCAL",
            None,
            ("Alta del perfil fiscal" if actual is None else "Perfil fiscal: " + ", ".join(cambiados)),
        )
    logger.info("Perfil fiscal actualizado: %s", ", ".join(cambiados))
    return cambiados


def clasificar_productos(tratamientos: dict[int, TratamientoIva], usuario_id: int) -> int:
    """Asigna el tratamiento de IVA a cada producto indicado (en una transacción) y devuelve cuántos
    cambiaron. Clasificar es una decisión explícita del OWNER: Twinx nunca supone una alícuota.

    Raises:
        PermisoDenegadoError: si no lo hace un OWNER activo.
        ProductoNoEncontradoError: si algún producto no existe.
    """
    cambiados = 0
    with obtener_conexion(inmediata=True) as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _SOLO_OWNER)
        for producto_id, tratamiento in tratamientos.items():
            producto = repositorio_productos.obtener_por_id_en_conexion_incluyendo_inactivos(conexion, producto_id)
            if producto is None:
                raise ProductoNoEncontradoError(f"No existe un producto con id {producto_id}.")
            anterior = repositorio_fiscal.obtener_tratamiento_en_conexion(conexion, producto_id)
            if anterior == tratamiento:
                continue
            repositorio_fiscal.guardar_tratamiento_en_conexion(conexion, producto_id, tratamiento, usuario_id)
            repositorio_auditoria.registrar_en_conexion(
                conexion,
                usuario_id,
                ACCION_PRODUCTO_IVA_CLASIFICADO,
                "PRODUCTO",
                producto_id,
                f"«{producto.nombre}»: {anterior.etiqueta if anterior else 'Sin clasificar'} -> {tratamiento.etiqueta}",
            )
            cambiados += 1
    if cambiados:
        logger.info("Tratamiento de IVA asignado a %s producto(s)", cambiados)
    return cambiados


def obtener_tratamiento(producto_id: int) -> TratamientoIva | None:
    return repositorio_fiscal.obtener_tratamiento(producto_id)


def listar_productos(solo_sin_clasificar: bool, texto: str = "", limite: int = 300):
    return repositorio_fiscal.listar_productos_con_tratamiento(solo_sin_clasificar, texto.strip(), limite)


def contar_sin_clasificar() -> int:
    return repositorio_fiscal.contar_productos_activos_sin_clasificar()
