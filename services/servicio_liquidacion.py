"""Liquidación por vencimiento (V1.12-E): propuesta -> confirmación del OWNER -> cambio de precio auditado.

Nunca cambia un precio por su cuenta. La propuesta se calcula al mostrarla (`domain.liquidacion`); aplicar
pasa por el punto único de escritura de precios (`cambiar_precio_con_evento_en_conexion`), así el cambio
queda en el historial de precios como una edición manual del OWNER, más una entrada de auditoría que dice
que fue una liquidación y por qué vencimiento.
"""

import logging
from dataclasses import dataclass

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import partidas as repositorio_partidas
from db.repositorios import productos as repositorio_productos
from domain.dinero import centavos_a_texto
from domain.liquidacion import PropuestaLiquidacion, proponer_precio
from domain.partida import ESTADO_PROXIMO, Partida, ResumenVencimientos, estado_de_vencimiento, resumir_vencimientos
from domain.producto import Producto
from excepciones import DatosInvalidosError, ProductoNoEncontradoError
from services import servicio_configuracion, servicio_stock

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CandidatoLiquidacion:
    producto: Producto
    vencimientos: ResumenVencimientos
    propuesta: PropuestaLiquidacion


def listar_candidatos() -> list[CandidatoLiquidacion]:
    """Productos activos con mercadería próxima a vencer (no vencida: esa se retira, no se liquida), el
    que vence antes primero, con su propuesta. El costo relevante es el mayor costo conocido de las
    partidas próximas a vencer (piso de margen conservador); si ninguna lo tiene, el costo vigente."""
    dias = servicio_configuracion.obtener_dias_alerta_vencimiento()
    parametros = servicio_configuracion.obtener_parametros_liquidacion()
    hoy, partidas = repositorio_partidas.listar_con_disponible()
    por_producto: dict[int, list[Partida]] = {}
    for partida in partidas:
        por_producto.setdefault(partida.producto_id, []).append(partida)
    candidatos = []
    for producto in servicio_stock.listar_todos():
        propias = por_producto.get(producto.id, [])
        resumen = resumir_vencimientos(propias, hoy, dias)
        if resumen.estado != ESTADO_PROXIMO:
            continue
        costos = [
            p.costo_unitario_centavos for p in propias
            if p.costo_unitario_centavos is not None
            and estado_de_vencimiento(p.fecha_vencimiento, hoy, dias) == ESTADO_PROXIMO
        ]
        costo = max(costos) if costos else (producto.precio_costo_centavos or None)
        propuesta = proponer_precio(producto.precio_venta_centavos, costo, bool(costos), parametros)
        candidatos.append(CandidatoLiquidacion(producto, resumen, propuesta))
    return sorted(candidatos, key=lambda c: (c.vencimientos.proximo_vencimiento, c.producto.nombre))


def aplicar_precio(producto_id: int, precio_centavos: int, precio_anterior_centavos: int, usuario_id: int) -> Producto:
    """Aplica el precio que el OWNER confirmó (el propuesto o uno modificado).

    `precio_anterior_centavos` es el que vio al decidir: si cambió mientras tanto, no se pisa. Reenviar la
    misma confirmación es inocuo (el precio ya es ese: no se registra nada de nuevo).

    Raises:
        ProductoNoEncontradoError, DatosInvalidosError.
    """
    if precio_centavos <= 0:
        raise DatosInvalidosError("El precio de liquidación debe ser mayor a cero.")
    with obtener_conexion(inmediata=True) as conexion:
        producto = repositorio_productos.obtener_por_id_en_conexion(conexion, producto_id)
        if producto is None:
            raise ProductoNoEncontradoError(f"No existe un producto activo con id {producto_id}.")
        if producto.precio_venta_centavos == precio_centavos:
            return producto
        if producto.precio_venta_centavos != precio_anterior_centavos:
            raise DatosInvalidosError("El precio del producto cambió mientras tanto: revisá la propuesta de nuevo.")
        repositorio_productos.cambiar_precio_con_evento_en_conexion(
            conexion, producto_id, "VENTA", precio_centavos, usuario_id, "EDICION"
        )
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, "PRECIO_LIQUIDACION_APLICADO", "PRODUCTO", producto_id,
            f"{producto.nombre}: ${centavos_a_texto(precio_anterior_centavos)} -> ${centavos_a_texto(precio_centavos)}"
            " (liquidación por vencimiento)",
        )
        actualizado = repositorio_productos.obtener_por_id_en_conexion(conexion, producto_id)
    logger.info("Precio de liquidación aplicado: producto_id=%s %s -> %s", producto_id, precio_anterior_centavos, precio_centavos)
    return actualizado
