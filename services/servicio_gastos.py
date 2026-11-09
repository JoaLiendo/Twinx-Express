"""Casos de uso de los gastos operativos (migración 048): registrar, corregir lo descriptivo, anular y
consultar. Solo el OWNER administra gastos (lo decide la ruta); acá se garantiza la coherencia con la caja:
un gasto pagado con efectivo de la caja nace junto con su EGRESO en una sola transacción, un reenvío del
formulario no duplica nada y una anulación registra el reintegro a la caja solo si el OWNER lo indica.
"""

import logging
from dataclasses import dataclass
from datetime import date

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import caja as repositorio_caja
from db.repositorios import gastos as repositorio_gastos
from db.repositorios.gastos import FiltrosGastos, TotalCategoria
from domain.caja import MovimientoCaja
from domain.dinero import centavos_a_texto_localizado
from domain.fechas import limites_de_fechas
from domain.gasto import (
    CATEGORIAS_VALIDAS,
    ESTADO_ANULADO,
    ESTADO_VIGENTE,
    MEDIOS_PAGO_VALIDOS,
    DatosGasto,
    Gasto,
    describir_cambios,
    descripcion_para_caja,
    validar_cambios_gasto,
    validar_datos_gasto,
    validar_motivo_anulacion,
    validar_reintegro_a_caja,
)
from excepciones import (
    MENSAJE_FORMULARIO_REENVIADO_CON_OTROS_DATOS,
    CajaCerradaError,
    ClaveIdempotenciaReutilizadaError,
    DatosInvalidosError,
    GastoNoEncontradoError,
    GastoYaAnuladoError,
)
from services.servicio_reportes import limites_del_periodo

logger = logging.getLogger(__name__)

GASTOS_POR_PAGINA = 50
ESTADOS_FILTRO = (ESTADO_VIGENTE, ESTADO_ANULADO)


def _mismo_gasto(existente: Gasto, datos: DatosGasto) -> bool:
    return (
        existente.fecha,
        existente.categoria,
        existente.descripcion,
        existente.importe_centavos,
        existente.medio_pago,
    ) == (datos.fecha, datos.categoria, datos.descripcion, datos.importe_centavos, datos.medio_pago)


def _resolver_reintento(existente: Gasto, datos: DatosGasto) -> Gasto:
    """Una clave ya usada devuelve el gasto original solo si es el mismo pedido; con otros datos se
    rechaza en vez de dar por registrado algo distinto."""
    if not _mismo_gasto(existente, datos):
        raise ClaveIdempotenciaReutilizadaError(MENSAJE_FORMULARIO_REENVIADO_CON_OTROS_DATOS)
    return existente


def registrar_gasto(
    *,
    usuario_id: int,
    fecha: str | None,
    categoria: str | None,
    descripcion: str | None,
    importe_centavos: int,
    medio_pago: str | None,
    referencia: str | None = None,
    responsable: str | None = None,
    observaciones: str | None = None,
    clave_idempotencia: str | None = None,
    hoy: date | None = None,
) -> Gasto:
    """Registra un gasto operativo. Si se paga con efectivo de la caja, exige una caja abierta y crea el
    EGRESO en la misma transacción (si algo falla no queda ni el gasto ni el egreso).

    Raises:
        DatosInvalidosError: ante un campo inválido.
        CajaCerradaError: si se paga con la caja y no hay una caja abierta.
        ClaveIdempotenciaReutilizadaError: si la clave ya se usó con otros datos.
    """
    datos = validar_datos_gasto(
        fecha=fecha,
        categoria=categoria,
        descripcion=descripcion,
        importe_centavos=importe_centavos,
        medio_pago=medio_pago,
        referencia=referencia,
        responsable=responsable,
        observaciones=observaciones,
        hoy=hoy,
    )
    with obtener_conexion(inmediata=True) as conexion:
        if clave_idempotencia:
            existente = repositorio_gastos.obtener_por_clave_idempotencia_en_conexion(conexion, clave_idempotencia)
            if existente is not None:
                return _resolver_reintento(existente, datos)
        caja_movimiento_id = None
        if datos.paga_con_caja:
            if repositorio_caja.obtener_sesion_abierta_en_conexion(conexion) is None:
                raise CajaCerradaError(
                    "No hay una caja abierta para pagar este gasto con efectivo de la caja: abrí la caja o elegí otro medio de pago."
                )
            egreso = repositorio_caja.registrar_movimiento_en_conexion(
                conexion,
                MovimientoCaja(tipo="EGRESO", monto_centavos=datos.importe_centavos, descripcion=descripcion_para_caja(datos)),
                usuario_id=usuario_id,
                auditar=False,
            )
            caja_movimiento_id = egreso.id
        gasto = repositorio_gastos.registrar_en_conexion(conexion, datos, usuario_id, caja_movimiento_id, clave_idempotencia or None)
        resumen = (
            f"Gasto #{gasto.id} {gasto.categoria_etiqueta} por ${centavos_a_texto_localizado(gasto.importe_centavos)} "
            f"({gasto.medio_pago_etiqueta}, {gasto.fecha})"
        )
        if caja_movimiento_id is not None:
            resumen += f"; egreso de caja #{caja_movimiento_id}"
        repositorio_auditoria.registrar_en_conexion(conexion, usuario_id, "GASTO_REGISTRADO", "GASTO", gasto.id, resumen)
    logger.info("Gasto registrado: #%s %s por %s centavos", gasto.id, gasto.categoria, gasto.importe_centavos)
    return gasto


def obtener_gasto(gasto_id: int) -> Gasto:
    gasto = repositorio_gastos.obtener_por_id(gasto_id)
    if gasto is None:
        raise GastoNoEncontradoError(f"No existe un gasto con id {gasto_id}.")
    return gasto


def modificar_gasto(
    gasto_id: int,
    *,
    usuario_id: int,
    categoria: str | None,
    descripcion: str | None,
    referencia: str | None,
    responsable: str | None,
    observaciones: str | None,
) -> Gasto:
    """Corrige lo descriptivo de un gasto vigente (categoría, descripción, referencia, responsable,
    observaciones). Lo económico (fecha, importe, medio de pago) no se edita: se anula y se carga otro.

    Raises:
        GastoNoEncontradoError, GastoYaAnuladoError, DatosInvalidosError.
    """
    cambios = validar_cambios_gasto(
        categoria=categoria,
        descripcion=descripcion,
        referencia=referencia,
        responsable=responsable,
        observaciones=observaciones,
    )
    with obtener_conexion(inmediata=True) as conexion:
        gasto = repositorio_gastos.obtener_por_id_en_conexion(conexion, gasto_id)
        if gasto is None:
            raise GastoNoEncontradoError(f"No existe un gasto con id {gasto_id}.")
        if not gasto.vigente:
            raise GastoYaAnuladoError(f"El gasto {gasto_id} está anulado y no se modifica.")
        diferencias = describir_cambios(gasto, cambios)
        if not diferencias:
            return gasto
        repositorio_gastos.modificar_en_conexion(conexion, gasto_id, cambios)
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, "GASTO_MODIFICADO", "GASTO", gasto_id, f"Gasto #{gasto_id}: " + "; ".join(diferencias)
        )
        actualizado = repositorio_gastos.obtener_por_id_en_conexion(conexion, gasto_id)
    assert actualizado is not None
    return actualizado


def anular_gasto(gasto_id: int, *, usuario_id: int, motivo: str | None, reintegro_a_caja: bool | None = None) -> Gasto:
    """Anula un gasto vigente con motivo. Si se pagó con la caja, el OWNER indica si el efectivo vuelve:
    con `True` se crea un INGRESO vinculado en la caja abierta (exige caja abierta); con `False` la caja
    queda como está. El egreso original nunca se borra.

    Raises:
        GastoNoEncontradoError, GastoYaAnuladoError, DatosInvalidosError, CajaCerradaError.
    """
    motivo_valido = validar_motivo_anulacion(motivo)
    with obtener_conexion(inmediata=True) as conexion:
        gasto = repositorio_gastos.obtener_por_id_en_conexion(conexion, gasto_id)
        if gasto is None:
            raise GastoNoEncontradoError(f"No existe un gasto con id {gasto_id}.")
        if not gasto.vigente:
            raise GastoYaAnuladoError(f"El gasto {gasto_id} ya fue anulado anteriormente.")
        reintegra = validar_reintegro_a_caja(gasto.medio_pago, reintegro_a_caja)
        reintegro_id = None
        if reintegra:
            if repositorio_caja.obtener_sesion_abierta_en_conexion(conexion) is None:
                raise CajaCerradaError(
                    "No hay una caja abierta para recibir el efectivo de vuelta: abrí la caja o anulá sin reintegro."
                )
            ingreso = repositorio_caja.registrar_movimiento_en_conexion(
                conexion,
                MovimientoCaja(
                    tipo="INGRESO",
                    monto_centavos=gasto.importe_centavos,
                    descripcion=f"Reintegro por anulación del gasto #{gasto_id}"[:250],
                ),
                usuario_id=usuario_id,
                auditar=False,
            )
            reintegro_id = ingreso.id
        repositorio_gastos.anular_en_conexion(conexion, gasto_id, usuario_id, motivo_valido, reintegro_id)
        caja = ""
        if gasto.paga_con_caja:
            caja = f"; reintegro a caja: ingreso #{reintegro_id}" if reintegro_id else "; sin reintegro a caja"
        repositorio_auditoria.registrar_en_conexion(
            conexion,
            usuario_id,
            "GASTO_ANULADO",
            "GASTO",
            gasto_id,
            f"Gasto #{gasto_id} {gasto.categoria_etiqueta} por ${centavos_a_texto_localizado(gasto.importe_centavos)} "
            f"anulado: {motivo_valido}{caja}",
        )
        anulado = repositorio_gastos.obtener_por_id_en_conexion(conexion, gasto_id)
    assert anulado is not None
    logger.info("Gasto anulado: #%s (%s)", gasto_id, motivo_valido)
    return anulado


# --- Consultas -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ListadoGastos:
    gastos: list[Gasto]
    pagina: int
    total_paginas: int
    cantidad: int
    total_vigente_centavos: int
    filtros: FiltrosGastos


def filtros_del_listado(
    fecha_desde: str | None,
    fecha_hasta: str | None,
    categoria: str | None,
    medio_pago: str | None,
    estado: str | None,
    texto: str | None,
) -> FiltrosGastos:
    """Normaliza los filtros del listado (un valor desconocido se rechaza, el vacío se ignora)."""
    inicio, fin = limites_de_fechas(fecha_desde or None, fecha_hasta or None, "Las fechas del filtro deben ser AAAA-MM-DD.")
    if categoria and categoria not in CATEGORIAS_VALIDAS:
        raise DatosInvalidosError("Categoría de gasto desconocida.")
    if medio_pago and medio_pago not in MEDIOS_PAGO_VALIDOS:
        raise DatosInvalidosError("Medio de pago desconocido.")
    if estado and estado not in ESTADOS_FILTRO:
        raise DatosInvalidosError("Estado de gasto desconocido.")
    texto_limpio = (texto or "").strip()[:100] or None
    return FiltrosGastos(inicio, fin, categoria or None, medio_pago or None, estado or None, texto_limpio)


def listar_gastos(filtros: FiltrosGastos, pagina: int = 1) -> ListadoGastos:
    cantidad, total = repositorio_gastos.contar(filtros)
    total_paginas = max(1, -(-cantidad // GASTOS_POR_PAGINA))
    pagina = min(max(1, pagina), total_paginas)
    gastos = repositorio_gastos.listar(filtros, GASTOS_POR_PAGINA, (pagina - 1) * GASTOS_POR_PAGINA)
    return ListadoGastos(gastos, pagina, total_paginas, cantidad, total, filtros)


def listar_para_exportar(filtros: FiltrosGastos) -> list[Gasto]:
    return repositorio_gastos.listar_todos(filtros)


@dataclass(frozen=True)
class ResumenGastosPeriodo:
    fecha_desde: str
    fecha_hasta: str
    total_centavos: int
    por_categoria: list[TotalCategoria]


def resumir_periodo(fecha_desde: str | None = None, fecha_hasta: str | None = None) -> ResumenGastosPeriodo:
    """Gastos vigentes del período (últimos 30 días por defecto) con su desglose por categoría."""
    desde, hasta, inicio, fin = limites_del_periodo(fecha_desde, fecha_hasta)
    por_categoria = repositorio_gastos.totales_por_categoria(inicio, fin)
    return ResumenGastosPeriodo(desde, hasta, sum(t.importe_centavos for t in por_categoria), por_categoria)
