"""V1.12-A: orden FEFO y asignación de consumos entre partidas (reglas puras)."""

import pytest

from domain.partida import Partida, asignar_consumo, orden_fefo
from excepciones import StockInsuficienteError

HOY = "2026-11-10"


def _p(id_, disponible, vence=None, origen="COMPRA"):
    return Partida(id_, 1, origen, disponible, disponible, vence, None, "2026-01-01" if origen != "HEREDADA" else None)


def test_orden_fefo_vigentes_por_vencimiento_luego_sin_fecha_luego_vencidas():
    partidas = [
        _p(1, 5, origen="HEREDADA"),
        _p(2, 5, "2026-12-15"),
        _p(3, 5, "2026-11-20"),
        _p(4, 5, "2026-11-01"),  # vencida
        _p(5, 5),
        _p(6, 5, "2026-11-10"),  # vence hoy: todavía vigente
    ]
    assert [p.id for p in orden_fefo(partidas, HOY)] == [6, 3, 2, 1, 5, 4]


def test_empate_de_vencimiento_desempata_por_id():
    assert [p.id for p in orden_fefo([_p(9, 1, "2026-12-01"), _p(4, 1, "2026-12-01")], HOY)] == [4, 9]


def test_consumo_que_cruza_varias_partidas():
    asignaciones = asignar_consumo([_p(1, 3, "2026-12-01"), _p(2, 10, "2026-11-20"), _p(3, 4)], 12, HOY)
    assert [(a.partida_id, a.cantidad, a.criterio) for a in asignaciones] == [(2, 10, "FEFO"), (1, 2, "FEFO")]


def test_una_sola_partida_con_disponible_es_unica():
    asignaciones = asignar_consumo([_p(1, 0, "2026-12-01"), _p(2, 6)], 4, HOY)
    assert [(a.partida_id, a.cantidad, a.criterio) for a in asignaciones] == [(2, 4, "UNICA")]


def test_partida_elegida():
    asignaciones = asignar_consumo([_p(1, 3, "2026-12-01"), _p(2, 10)], 3, HOY, partida_elegida_id=2)
    assert [(a.partida_id, a.cantidad, a.criterio) for a in asignaciones] == [(2, 3, "ELEGIDA")]


@pytest.mark.parametrize("cantidad, elegida", [(14, None), (4, 1), (1, 99)])
def test_insuficiente(cantidad, elegida):
    with pytest.raises(StockInsuficienteError):
        asignar_consumo([_p(1, 3), _p(2, 10)], cantidad, HOY, partida_elegida_id=elegida)


def test_vencida_y_su_dia():
    assert _p(1, 1, "2026-11-09").vencida(HOY) and not _p(1, 1, "2026-11-10").vencida(HOY)
    assert not _p(1, 1).vencida(HOY)
