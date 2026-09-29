"""Property tests deterministas del motor de cuentas a pagar (V1.10-B), sin dependencias externas.

Generan libros válidos con `random.Random(semilla)` y comprueban invariantes contra un oráculo acumulativo
independiente del replay (sobre libros válidos, los pagos totales se aplican en orden de antigüedad a las
compras no reversadas). Después corrompen los libros con mutaciones pequeñas y exigen que se detecten."""

import random
from datetime import date, timedelta

import pytest

from domain.cuentas_a_pagar import (
    ESTADO_COMPRA_ACTIVA,
    ESTADO_COMPRA_ANULADA,
    TIPO_CARGO_COMPRA,
    TIPO_PAGO,
    TIPO_REVERSA_COMPRA,
    CompraParaVencimiento,
    MovimientoLibro,
    clasificar_compras,
    estimar_pendientes_fifo,
    resumir_vencimientos,
)
from excepciones import LibroProveedorInconsistenteError

HOY = date(2026, 6, 15)
SEMILLAS = range(300)


def generar_libro_valido(semilla: int) -> list[MovimientoLibro]:
    """Libro válido: cada pago cabe en el saldo de ese punto y solo se reversa una compra intacta."""
    azar = random.Random(semilla)
    libro: list[MovimientoLibro] = []
    montos: dict[int, int] = {}  # compra_id -> monto de las compras vigentes (no reversadas), en orden
    pagado = 0
    proximo_id = 0
    proxima_compra = 100
    for _ in range(azar.randint(1, 25)):
        proximo_id += azar.randint(1, 4)
        saldo = sum(montos.values()) - pagado
        opcion = azar.random()
        if opcion < 0.45 or (opcion < 0.85 and saldo == 0 and not montos):
            libro.append(MovimientoLibro(proximo_id, TIPO_CARGO_COMPRA, proxima_compra, azar.randint(1, 500)))
            montos[proxima_compra] = libro[-1].monto_centavos
            proxima_compra += 1
        elif opcion < 0.85 and saldo > 0:
            monto = azar.randint(1, saldo) if azar.random() < 0.8 else saldo
            libro.append(MovimientoLibro(proximo_id, TIPO_PAGO, None, monto))
            pagado += monto
        else:
            antes = 0
            intactas = []
            for compra_id, monto in montos.items():
                if pagado <= antes:
                    intactas.append(compra_id)
                antes += monto
            if not intactas:
                continue
            compra_id = azar.choice(intactas)
            libro.append(MovimientoLibro(proximo_id, TIPO_REVERSA_COMPRA, compra_id, montos.pop(compra_id)))
    return libro


def oraculo(libro: list[MovimientoLibro]) -> tuple[dict[int, int], int]:
    """Pendientes esperados por fórmula acumulativa y saldo, sin simular la cola."""
    cargos = {m.compra_id: m.monto_centavos for m in libro if m.tipo == TIPO_CARGO_COMPRA}
    reversadas = {m.compra_id for m in libro if m.tipo == TIPO_REVERSA_COMPRA}
    pagos = sum(m.monto_centavos for m in libro if m.tipo == TIPO_PAGO)
    esperado: dict[int, int] = {}
    antes = 0
    for compra_id, monto in cargos.items():
        if compra_id in reversadas:
            esperado[compra_id] = 0
            continue
        esperado[compra_id] = monto - min(monto, max(0, pagos - antes))
        antes += monto
    return esperado, sum(esperado.values())


def compras_de(libro: list[MovimientoLibro], azar: random.Random) -> list[CompraParaVencimiento]:
    reversadas = {m.compra_id for m in libro if m.tipo == TIPO_REVERSA_COMPRA}
    resultado = []
    for m in libro:
        if m.tipo != TIPO_CARGO_COMPRA:
            continue
        vencimiento = None if azar.random() < 0.3 else HOY + timedelta(days=azar.randint(-20, 30))
        estado = ESTADO_COMPRA_ANULADA if m.compra_id in reversadas else ESTADO_COMPRA_ACTIVA
        resultado.append(CompraParaVencimiento(m.compra_id, vencimiento, estado, m.monto_centavos))
    return resultado


@pytest.mark.parametrize("semilla", SEMILLAS)
def test_libros_validos_cumplen_los_invariantes(semilla):
    libro = generar_libro_valido(semilla)
    estimacion = estimar_pendientes_fifo(libro)
    esperado, saldo_esperado = oraculo(libro)

    assert dict(estimacion.pendientes_estimados) == esperado
    assert all(residual >= 0 for residual in estimacion.pendientes_estimados.values())
    assert sum(estimacion.pendientes_estimados.values()) == estimacion.saldo_centavos == saldo_esperado
    reversadas = {m.compra_id for m in libro if m.tipo == TIPO_REVERSA_COMPRA}
    assert estimacion.compras_reversadas == reversadas
    assert all(estimacion.pendientes_estimados[compra_id] == 0 for compra_id in reversadas)
    # Todo pago quedó absorbido: lo cargado menos lo reversado menos lo pagado es justo el saldo.
    cargado = sum(m.monto_centavos for m in libro if m.tipo == TIPO_CARGO_COMPRA)
    pagado = sum(m.monto_centavos for m in libro if m.tipo == TIPO_PAGO)
    reversado = sum(m.monto_centavos for m in libro if m.tipo == TIPO_REVERSA_COMPRA)
    assert cargado - reversado - pagado == estimacion.saldo_centavos


@pytest.mark.parametrize("semilla", SEMILLAS)
def test_buckets_suman_el_saldo_y_el_replay_es_determinista(semilla):
    libro = generar_libro_valido(semilla)
    compras = compras_de(libro, random.Random(semilla))

    estimacion = estimar_pendientes_fifo(libro)
    vencimientos = clasificar_compras(compras, estimacion, HOY)
    resumen = resumir_vencimientos(vencimientos, estimacion.saldo_centavos)

    assert resumen.total_centavos == estimacion.saldo_centavos
    assert estimar_pendientes_fifo(list(libro)) == estimacion
    assert clasificar_compras(compras, estimar_pendientes_fifo(list(libro)), HOY) == vencimientos


def test_el_generador_produce_libros_variados():
    libros = [generar_libro_valido(semilla) for semilla in SEMILLAS]
    tipos = {m.tipo for libro in libros for m in libro}
    assert tipos == {TIPO_CARGO_COMPRA, TIPO_PAGO, TIPO_REVERSA_COMPRA}
    assert any(estimar_pendientes_fifo(libro).saldo_centavos == 0 and libro for libro in libros)
    assert any(estimar_pendientes_fifo(libro).saldo_centavos > 0 for libro in libros)


# --------------------------------------------------------------------------- mutaciones hostiles


def renumerar(libro: list[MovimientoLibro]) -> list[MovimientoLibro]:
    return [MovimientoLibro(i, m.tipo, m.compra_id, m.monto_centavos) for i, m in enumerate(libro, start=1)]


def _saldo_antes(libro: list[MovimientoLibro], indice: int) -> int:
    signo = {TIPO_CARGO_COMPRA: 1, TIPO_PAGO: -1, TIPO_REVERSA_COMPRA: -1}
    return sum(signo[m.tipo] * m.monto_centavos for m in libro[:indice])


def borrar_cargo_reversado(libro):
    for indice, m in enumerate(libro):
        if m.tipo == TIPO_CARGO_COMPRA and any(
            r.tipo == TIPO_REVERSA_COMPRA and r.compra_id == m.compra_id for r in libro
        ):
            return libro[:indice] + libro[indice + 1 :]
    return None


def duplicar_cargo(libro):
    for indice, m in enumerate(libro):
        if m.tipo == TIPO_CARGO_COMPRA:
            return renumerar(libro[: indice + 1] + [m] + libro[indice + 1 :])
    return None


def aumentar_pago(libro):
    for indice, m in enumerate(libro):
        if m.tipo == TIPO_PAGO:
            excesivo = MovimientoLibro(m.id, TIPO_PAGO, None, _saldo_antes(libro, indice) + 1)
            return libro[:indice] + [excesivo] + libro[indice + 1 :]
    return None


def mover_pago_antes_del_cargo(libro):
    for indice, m in enumerate(libro):
        if m.tipo == TIPO_PAGO:
            return renumerar([m] + libro[:indice] + libro[indice + 1 :])
    return None


def insertar_reversa_tras_pago_parcial(libro):
    estimacion = estimar_pendientes_fifo(libro)
    for compra_id, pendiente in estimacion.pendientes_estimados.items():
        original = estimacion.montos_originales[compra_id]
        if compra_id not in estimacion.compras_reversadas and pendiente < original:
            return renumerar(libro + [MovimientoLibro(0, TIPO_REVERSA_COMPRA, compra_id, original)])
    return None


def duplicar_reversa(libro):
    for m in libro:
        if m.tipo == TIPO_REVERSA_COMPRA:
            return renumerar(libro + [m])
    return None


MUTACIONES = [
    borrar_cargo_reversado,
    duplicar_cargo,
    aumentar_pago,
    mover_pago_antes_del_cargo,
    insertar_reversa_tras_pago_parcial,
    duplicar_reversa,
]


@pytest.mark.parametrize("mutacion", MUTACIONES, ids=lambda f: f.__name__)
def test_toda_mutacion_hostil_aplicable_se_detecta(mutacion):
    aplicadas = 0
    for semilla in SEMILLAS:
        corrupto = mutacion(generar_libro_valido(semilla))
        if corrupto is None:
            continue
        aplicadas += 1
        with pytest.raises(LibroProveedorInconsistenteError):
            estimar_pendientes_fifo(corrupto)
    assert aplicadas >= 20, "la mutación casi nunca aplica: el generador no ejercita este caso"


def test_pago_desordenado_dentro_del_libro_se_detecta():
    libro = generar_libro_valido(3)
    assert len(libro) >= 2
    desordenado = [libro[1], libro[0]] + libro[2:]
    with pytest.raises(LibroProveedorInconsistenteError):
        estimar_pendientes_fifo(desordenado)
