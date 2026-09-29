"""Pruebas del motor puro de cuentas a pagar (V1.10-B): replay FIFO causal, clasificación de
vencimientos y buckets. Sin base de datos: todo entra y sale por parámetros, y `hoy` siempre se inyecta."""

from datetime import date, timedelta

import pytest

from domain.cuentas_a_pagar import (
    ESTADO_PAGADA,
    ESTADO_PENDIENTE,
    ESTADO_PROXIMA_A_VENCER,
    ESTADO_SIN_VENCIMIENTO,
    ESTADO_VENCIDA,
    ESTADO_ANULADA,
    TIPO_CARGO_COMPRA,
    TIPO_PAGO,
    TIPO_REVERSA_COMPRA,
    CompraParaVencimiento,
    EstimacionFifo,
    MovimientoLibro,
    ResumenVencimientos,
    VencimientoCompra,
    clasificar_compras,
    clasificar_vencimiento,
    estimar_pendientes_fifo,
    resumir_vencimientos,
)
from excepciones import LibroProveedorInconsistenteError

HOY = date(2026, 6, 15)


def cargo(id_: int, compra_id: int, monto: int) -> MovimientoLibro:
    return MovimientoLibro(id_, TIPO_CARGO_COMPRA, compra_id, monto)


def pago(id_: int, monto: int) -> MovimientoLibro:
    return MovimientoLibro(id_, TIPO_PAGO, None, monto)


def reversa(id_: int, compra_id: int, monto: int) -> MovimientoLibro:
    return MovimientoLibro(id_, TIPO_REVERSA_COMPRA, compra_id, monto)


def pendientes(libro: list[MovimientoLibro]) -> dict[int, int]:
    return dict(estimar_pendientes_fifo(libro).pendientes_estimados)


# --------------------------------------------------------------------------- FIFO básico


def test_libro_vacio_no_tiene_saldo_ni_pendientes():
    estimacion = estimar_pendientes_fifo([])
    assert estimacion.saldo_centavos == 0
    assert dict(estimacion.pendientes_estimados) == {}
    assert estimacion.compras_reversadas == frozenset()


def test_un_cargo_sin_pago_queda_completo():
    estimacion = estimar_pendientes_fifo([cargo(1, 10, 100)])
    assert dict(estimacion.pendientes_estimados) == {10: 100}
    assert estimacion.saldo_centavos == 100


def test_cargo_con_pago_parcial_reduce_el_residual():
    assert pendientes([cargo(1, 10, 100), pago(2, 30)]) == {10: 70}


def test_cargo_con_pago_total_queda_en_cero():
    estimacion = estimar_pendientes_fifo([cargo(1, 10, 100), pago(2, 100)])
    assert dict(estimacion.pendientes_estimados) == {10: 0}
    assert estimacion.saldo_centavos == 0


def test_dos_cargos_sin_pagos_quedan_completos():
    assert pendientes([cargo(1, 10, 100), cargo(2, 11, 50)]) == {10: 100, 11: 50}


def test_pago_menor_al_primer_cargo_solo_toca_al_mas_antiguo():
    assert pendientes([cargo(1, 10, 100), cargo(2, 11, 50), pago(3, 40)]) == {10: 60, 11: 50}


def test_pago_que_cruza_dos_cargos_consume_el_primero_y_sigue_con_el_segundo():
    assert pendientes([cargo(1, 10, 100), cargo(2, 11, 100), pago(3, 130)]) == {10: 0, 11: 70}


def test_varios_pagos_se_acumulan_sobre_la_cola():
    libro = [cargo(1, 10, 100), pago(2, 30), pago(3, 30), cargo(4, 11, 100), pago(5, 60)]
    assert pendientes(libro) == {10: 0, 11: 80}


def test_tres_cargos_con_varios_pagos():
    libro = [
        cargo(1, 10, 100),
        cargo(2, 11, 200),
        pago(3, 150),
        cargo(4, 12, 50),
        pago(5, 120),
    ]
    # 150 cierra C10 y deja C11 en 150; 120 deja C11 en 30.
    assert pendientes(libro) == {10: 0, 11: 30, 12: 50}


def test_pago_total_de_todo_el_libro_deja_todos_los_residuales_en_cero():
    estimacion = estimar_pendientes_fifo([cargo(1, 10, 100), cargo(2, 11, 50), pago(3, 150)])
    assert dict(estimacion.pendientes_estimados) == {10: 0, 11: 0}
    assert estimacion.saldo_centavos == 0


def test_caso_causal_un_pago_no_cubre_un_cargo_que_todavia_no_aparecio():
    """Caso obligatorio: C1=100, PAGO=100, C2=100 deja C1 en 0 y C2 en 100 (el pago no puede cubrir
    un cargo que todavía no apareció en el libro)."""
    assert pendientes([cargo(1, 10, 100), pago(2, 100), cargo(3, 11, 100)]) == {10: 0, 11: 100}


def test_caso_causal_pago_parcial_intermedio_no_se_aplica_al_cargo_futuro():
    assert pendientes([cargo(1, 10, 100), pago(2, 60), cargo(3, 11, 100), pago(4, 10)]) == {10: 30, 11: 100}


def test_el_resultado_conserva_el_orden_de_los_cargos():
    estimacion = estimar_pendientes_fifo([cargo(1, 12, 5), cargo(2, 10, 5), cargo(3, 11, 5)])
    assert list(estimacion.pendientes_estimados) == [12, 10, 11]


def test_el_resultado_es_inmutable():
    estimacion = estimar_pendientes_fifo([cargo(1, 10, 100)])
    with pytest.raises(TypeError):
        estimacion.pendientes_estimados[10] = 0  # type: ignore[index]


def test_replay_es_determinista():
    libro = [cargo(1, 10, 100), pago(2, 30), cargo(3, 11, 70), pago(4, 90)]
    assert estimar_pendientes_fifo(libro) == estimar_pendientes_fifo(list(libro))


# --------------------------------------------------------------------------- reversas


def test_cargo_mas_reversa_deja_el_libro_en_cero_y_sin_obligaciones():
    estimacion = estimar_pendientes_fifo([cargo(1, 10, 100), reversa(2, 10, 100)])
    assert dict(estimacion.pendientes_estimados) == {10: 0}
    assert estimacion.compras_reversadas == frozenset({10})
    assert estimacion.saldo_centavos == 0


def test_dos_cargos_y_reversa_del_primero():
    estimacion = estimar_pendientes_fifo([cargo(1, 10, 100), cargo(2, 11, 50), reversa(3, 10, 100)])
    assert dict(estimacion.pendientes_estimados) == {10: 0, 11: 50}
    assert estimacion.saldo_centavos == 50


def test_dos_cargos_y_reversa_del_segundo():
    estimacion = estimar_pendientes_fifo([cargo(1, 10, 100), cargo(2, 11, 50), reversa(3, 11, 50)])
    assert dict(estimacion.pendientes_estimados) == {10: 100, 11: 0}
    assert estimacion.saldo_centavos == 100


def test_pago_posterior_a_una_reversa_no_toca_la_compra_reversada():
    libro = [cargo(1, 10, 100), cargo(2, 11, 50), reversa(3, 10, 100), pago(4, 20)]
    assert pendientes(libro) == {10: 0, 11: 30}


def test_reversa_del_segundo_cargo_es_valida_si_el_pago_previo_solo_toco_al_primero():
    libro = [cargo(1, 10, 100), cargo(2, 11, 50), pago(3, 40), reversa(4, 11, 50)]
    assert pendientes(libro) == {10: 60, 11: 0}


def test_reversa_sin_cargo_es_inconsistente():
    with pytest.raises(LibroProveedorInconsistenteError):
        estimar_pendientes_fifo([reversa(1, 10, 100)])


def test_reversa_de_otra_compra_es_inconsistente():
    with pytest.raises(LibroProveedorInconsistenteError):
        estimar_pendientes_fifo([cargo(1, 10, 100), reversa(2, 11, 100)])


def test_reversa_duplicada_es_inconsistente():
    with pytest.raises(LibroProveedorInconsistenteError):
        estimar_pendientes_fifo([cargo(1, 10, 100), cargo(2, 11, 100), reversa(3, 10, 100), reversa(4, 10, 100)])


def test_reversa_despues_de_pago_parcial_es_inconsistente():
    with pytest.raises(LibroProveedorInconsistenteError):
        estimar_pendientes_fifo([cargo(1, 10, 100), pago(2, 10), reversa(3, 10, 100)])


def test_reversa_despues_de_pago_total_es_inconsistente():
    with pytest.raises(LibroProveedorInconsistenteError):
        estimar_pendientes_fifo([cargo(1, 10, 100), pago(2, 100), reversa(3, 10, 100)])


def test_reversa_con_monto_distinto_al_cargo_es_inconsistente():
    with pytest.raises(LibroProveedorInconsistenteError):
        estimar_pendientes_fifo([cargo(1, 10, 100), reversa(2, 10, 60)])


def test_cargo_duplicado_de_la_misma_compra_es_inconsistente():
    with pytest.raises(LibroProveedorInconsistenteError):
        estimar_pendientes_fifo([cargo(1, 10, 100), cargo(2, 10, 100)])


def test_cargo_de_compra_ya_reversada_es_inconsistente():
    with pytest.raises(LibroProveedorInconsistenteError):
        estimar_pendientes_fifo([cargo(1, 10, 100), reversa(2, 10, 100), cargo(3, 10, 100)])


# --------------------------------------------------------------------------- hostiles


def test_pago_antes_de_cualquier_cargo_es_inconsistente():
    with pytest.raises(LibroProveedorInconsistenteError):
        estimar_pendientes_fifo([pago(1, 10), cargo(2, 10, 100)])


def test_pago_mayor_al_saldo_disponible_es_inconsistente():
    with pytest.raises(LibroProveedorInconsistenteError):
        estimar_pendientes_fifo([cargo(1, 10, 100), pago(2, 101)])


def test_pago_mayor_al_saldo_causal_es_inconsistente_aunque_el_total_final_alcance():
    with pytest.raises(LibroProveedorInconsistenteError):
        estimar_pendientes_fifo([cargo(1, 10, 100), pago(2, 150), cargo(3, 11, 100)])


def test_id_duplicado_es_inconsistente():
    with pytest.raises(LibroProveedorInconsistenteError):
        estimar_pendientes_fifo([cargo(1, 10, 100), pago(1, 10)])


def test_movimientos_fuera_de_orden_por_id_son_inconsistentes():
    with pytest.raises(LibroProveedorInconsistenteError):
        estimar_pendientes_fifo([cargo(2, 10, 100), pago(1, 10)])


@pytest.mark.parametrize("id_", [0, -1])
def test_id_no_positivo_es_inconsistente(id_):
    with pytest.raises(LibroProveedorInconsistenteError):
        estimar_pendientes_fifo([cargo(id_, 10, 100)])


def test_tipo_desconocido_es_inconsistente():
    with pytest.raises(LibroProveedorInconsistenteError):
        estimar_pendientes_fifo([MovimientoLibro(1, "AJUSTE", 10, 100)])


@pytest.mark.parametrize("monto", [0, -5, 1.5, True, "100", None])
def test_monto_que_no_es_entero_positivo_es_inconsistente(monto):
    with pytest.raises(LibroProveedorInconsistenteError):
        estimar_pendientes_fifo([cargo(1, 10, 100), MovimientoLibro(2, TIPO_PAGO, None, monto)])


@pytest.mark.parametrize("monto", [0, -100])
def test_cargo_con_monto_no_positivo_es_inconsistente(monto):
    with pytest.raises(LibroProveedorInconsistenteError):
        estimar_pendientes_fifo([cargo(1, 10, monto)])


@pytest.mark.parametrize("tipo", [TIPO_CARGO_COMPRA, TIPO_REVERSA_COMPRA])
def test_cargo_y_reversa_sin_compra_id_son_inconsistentes(tipo):
    with pytest.raises(LibroProveedorInconsistenteError):
        estimar_pendientes_fifo([cargo(1, 10, 100), MovimientoLibro(2, tipo, None, 100)])


def test_pago_con_compra_id_es_inconsistente():
    with pytest.raises(LibroProveedorInconsistenteError):
        estimar_pendientes_fifo([cargo(1, 10, 100), MovimientoLibro(2, TIPO_PAGO, 10, 50)])


def test_id_no_entero_es_inconsistente():
    with pytest.raises(LibroProveedorInconsistenteError):
        estimar_pendientes_fifo([MovimientoLibro(True, TIPO_CARGO_COMPRA, 10, 100)])  # type: ignore[arg-type]


# --------------------------------------------------------------------------- clasificación


@pytest.mark.parametrize(
    ("desplazamiento_dias", "esperado"),
    [
        (-1, ESTADO_VENCIDA),
        (0, ESTADO_PROXIMA_A_VENCER),
        (1, ESTADO_PROXIMA_A_VENCER),
        (7, ESTADO_PROXIMA_A_VENCER),
        (8, ESTADO_PENDIENTE),
    ],
)
def test_bordes_de_fecha_de_vencimiento(desplazamiento_dias, esperado):
    vencimiento = HOY + timedelta(days=desplazamiento_dias)
    assert clasificar_vencimiento("ACTIVA", 100, vencimiento, HOY) == esperado


def test_hoy_no_es_vencida():
    assert clasificar_vencimiento("ACTIVA", 100, HOY, HOY) != ESTADO_VENCIDA


def test_vencimiento_lejano_es_pendiente():
    assert clasificar_vencimiento("ACTIVA", 100, HOY + timedelta(days=400), HOY) == ESTADO_PENDIENTE


def test_sin_fecha_de_vencimiento_es_sin_vencimiento():
    assert clasificar_vencimiento("ACTIVA", 100, None, HOY) == ESTADO_SIN_VENCIMIENTO


def test_residual_cero_es_pagada_aunque_este_vencida():
    assert clasificar_vencimiento("ACTIVA", 0, HOY - timedelta(days=30), HOY) == ESTADO_PAGADA


def test_residual_cero_sin_vencimiento_es_pagada():
    assert clasificar_vencimiento("ACTIVA", 0, None, HOY) == ESTADO_PAGADA


def test_anulada_tiene_prioridad_absoluta():
    assert clasificar_vencimiento("ANULADA", 100, HOY - timedelta(days=30), HOY) == ESTADO_ANULADA
    assert clasificar_vencimiento("ANULADA", 0, None, HOY) == ESTADO_ANULADA


def test_estado_de_compra_desconocido_es_error():
    with pytest.raises(LibroProveedorInconsistenteError):
        clasificar_vencimiento("BORRADOR", 100, None, HOY)


def test_residual_negativo_es_error():
    with pytest.raises(LibroProveedorInconsistenteError):
        clasificar_vencimiento("ACTIVA", -1, None, HOY)


def compra(compra_id, monto, vencimiento=None, estado="ACTIVA") -> CompraParaVencimiento:
    return CompraParaVencimiento(
        compra_id=compra_id, fecha_vencimiento=vencimiento, estado=estado, monto_original_centavos=monto
    )


def test_clasificar_compras_combina_replay_y_fechas_por_compra():
    estimacion = estimar_pendientes_fifo([cargo(1, 10, 100), cargo(2, 11, 200), pago(3, 130)])
    compras = [
        compra(10, 100, HOY - timedelta(days=5)),
        compra(11, 200, HOY + timedelta(days=3)),
    ]
    resultado = clasificar_compras(compras, estimacion, HOY)
    assert resultado == (
        VencimientoCompra(10, 100, 0, HOY - timedelta(days=5), ESTADO_PAGADA),
        VencimientoCompra(11, 200, 170, HOY + timedelta(days=3), ESTADO_PROXIMA_A_VENCER),
    )


def test_clasificar_compras_marca_anulada_la_compra_reversada():
    estimacion = estimar_pendientes_fifo([cargo(1, 10, 100), reversa(2, 10, 100)])
    (resultado,) = clasificar_compras([compra(10, 100, HOY, estado="ANULADA")], estimacion, HOY)
    assert resultado.estado_vencimiento == ESTADO_ANULADA
    assert resultado.pendiente_estimado_centavos == 0


def test_clasificar_compras_rechaza_compra_activa_reversada_en_el_libro():
    estimacion = estimar_pendientes_fifo([cargo(1, 10, 100), reversa(2, 10, 100)])
    with pytest.raises(LibroProveedorInconsistenteError):
        clasificar_compras([compra(10, 100)], estimacion, HOY)


def test_clasificar_compras_rechaza_compra_anulada_sin_reversa_en_el_libro():
    estimacion = estimar_pendientes_fifo([cargo(1, 10, 100)])
    with pytest.raises(LibroProveedorInconsistenteError):
        clasificar_compras([compra(10, 100, estado="ANULADA")], estimacion, HOY)


def test_clasificar_compras_rechaza_compra_sin_cargo_en_el_libro():
    estimacion = estimar_pendientes_fifo([cargo(1, 10, 100)])
    with pytest.raises(LibroProveedorInconsistenteError):
        clasificar_compras([compra(99, 100)], estimacion, HOY)


def test_clasificar_compras_rechaza_cargo_sin_compra_informada():
    estimacion = estimar_pendientes_fifo([cargo(1, 10, 100), cargo(2, 11, 100)])
    with pytest.raises(LibroProveedorInconsistenteError):
        clasificar_compras([compra(10, 100)], estimacion, HOY)


def test_clasificar_compras_rechaza_monto_de_compra_distinto_al_del_cargo():
    estimacion = estimar_pendientes_fifo([cargo(1, 10, 100)])
    with pytest.raises(LibroProveedorInconsistenteError):
        clasificar_compras([compra(10, 90)], estimacion, HOY)


def test_clasificar_compras_rechaza_compra_repetida():
    estimacion = estimar_pendientes_fifo([cargo(1, 10, 100)])
    with pytest.raises(LibroProveedorInconsistenteError):
        clasificar_compras([compra(10, 100), compra(10, 100)], estimacion, HOY)


# --------------------------------------------------------------------------- buckets


def vencimiento(estado: str, pendiente: int, compra_id: int = 1) -> VencimientoCompra:
    return VencimientoCompra(compra_id, pendiente, pendiente, None, estado)


def test_resumen_reparte_el_saldo_en_los_cuatro_buckets():
    items = (
        vencimiento(ESTADO_VENCIDA, 10, 1),
        vencimiento(ESTADO_PROXIMA_A_VENCER, 20, 2),
        vencimiento(ESTADO_PENDIENTE, 30, 3),
        vencimiento(ESTADO_SIN_VENCIMIENTO, 40, 4),
    )
    resumen = resumir_vencimientos(items, 100)
    assert resumen == ResumenVencimientos(vencida=10, proxima=20, vigente=30, sin_vencimiento=40)
    assert resumen.total_centavos == 100


def test_resumen_suma_varias_compras_del_mismo_bucket():
    items = (vencimiento(ESTADO_VENCIDA, 10, 1), vencimiento(ESTADO_VENCIDA, 5, 2))
    assert resumir_vencimientos(items, 15).vencida == 15


def test_resumen_pagadas_y_anuladas_aportan_cero():
    items = (
        vencimiento(ESTADO_PAGADA, 0, 1),
        vencimiento(ESTADO_ANULADA, 0, 2),
        vencimiento(ESTADO_SIN_VENCIMIENTO, 25, 3),
    )
    resumen = resumir_vencimientos(items, 25)
    assert resumen == ResumenVencimientos(vencida=0, proxima=0, vigente=0, sin_vencimiento=25)


def test_resumen_de_nada_es_cero():
    assert resumir_vencimientos((), 0).total_centavos == 0


def test_resumen_falla_si_los_buckets_no_suman_el_saldo():
    with pytest.raises(LibroProveedorInconsistenteError):
        resumir_vencimientos((vencimiento(ESTADO_PENDIENTE, 30),), 31)


def test_resumen_falla_si_sin_vencimiento_no_coincide_con_el_saldo():
    with pytest.raises(LibroProveedorInconsistenteError):
        resumir_vencimientos((vencimiento(ESTADO_SIN_VENCIMIENTO, 40),), 0)


def test_resumen_rechaza_pagada_o_anulada_con_pendiente():
    with pytest.raises(LibroProveedorInconsistenteError):
        resumir_vencimientos((vencimiento(ESTADO_PAGADA, 5),), 5)


def test_resumen_rechaza_estado_desconocido():
    with pytest.raises(LibroProveedorInconsistenteError):
        resumir_vencimientos((vencimiento("OTRO", 5),), 5)


def test_estimacion_es_un_valor_comparable():
    assert isinstance(estimar_pendientes_fifo([]), EstimacionFifo)
