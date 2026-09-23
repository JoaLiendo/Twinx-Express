"""Pruebas de services.limitador_login: contadores, bloqueo temporal,
ventana y concurrencia, con reloj controlado (sin `sleep`)."""

import threading

from services.limitador_login import (
    BLOQUEO_SEGUNDOS,
    UMBRAL_FALLOS,
    VENTANA_SEGUNDOS,
    LimitadorIntentosLogin,
)


def _limitador(reloj_falso) -> LimitadorIntentosLogin:
    return LimitadorIntentosLogin(reloj=reloj_falso)


def test_constantes_del_diseno_aprobado():
    assert UMBRAL_FALLOS == 5
    assert BLOQUEO_SEGUNDOS == 300
    assert VENTANA_SEGUNDOS == 900


def test_los_primeros_intentos_se_permiten_y_solo_el_ultimo_bloquea(reloj_falso):
    limitador = _limitador(reloj_falso)

    decisiones = [limitador.intentar(1) for _ in range(UMBRAL_FALLOS)]

    assert all(d.permitido for d in decisiones)
    assert [d.intentos for d in decisiones] == [1, 2, 3, 4, 5]
    assert [d.se_bloquea for d in decisiones] == [False, False, False, False, True]


def test_intento_posterior_al_umbral_se_rechaza_con_segundos_restantes(reloj_falso):
    limitador = _limitador(reloj_falso)
    for _ in range(UMBRAL_FALLOS):
        limitador.intentar(1)

    decision = limitador.intentar(1)

    assert decision.permitido is False
    assert decision.se_bloquea is False
    assert decision.segundos_restantes == BLOQUEO_SEGUNDOS


def test_el_bloqueo_no_se_extiende_con_nuevos_intentos(reloj_falso):
    limitador = _limitador(reloj_falso)
    for _ in range(UMBRAL_FALLOS):
        limitador.intentar(1)

    reloj_falso.avanzar(BLOQUEO_SEGUNDOS - 1)
    assert limitador.intentar(1).permitido is False  # sigue bloqueado, sin extender

    reloj_falso.avanzar(1)  # se cumplen exactamente BLOQUEO_SEGUNDOS desde el bloqueo
    assert limitador.intentar(1).permitido is True


def test_al_expirar_el_bloqueo_se_abre_un_ciclo_nuevo(reloj_falso):
    limitador = _limitador(reloj_falso)
    for _ in range(UMBRAL_FALLOS):
        limitador.intentar(1)
    reloj_falso.avanzar(BLOQUEO_SEGUNDOS)

    decision = limitador.intentar(1)

    assert decision.permitido is True
    assert decision.intentos == 1
    assert decision.se_bloquea is False


def test_fuera_de_la_ventana_la_racha_se_reinicia(reloj_falso):
    limitador = _limitador(reloj_falso)
    for _ in range(UMBRAL_FALLOS - 1):
        limitador.intentar(1)
    reloj_falso.avanzar(VENTANA_SEGUNDOS)

    decision = limitador.intentar(1)

    assert decision.intentos == 1
    assert decision.se_bloquea is False


def test_dentro_de_la_ventana_la_racha_sigue_acumulando(reloj_falso):
    limitador = _limitador(reloj_falso)
    for _ in range(UMBRAL_FALLOS - 1):
        limitador.intentar(1)
    reloj_falso.avanzar(VENTANA_SEGUNDOS - 1)

    assert limitador.intentar(1).se_bloquea is True


def test_registrar_exito_reinicia_el_contador(reloj_falso):
    limitador = _limitador(reloj_falso)
    for _ in range(UMBRAL_FALLOS - 1):
        limitador.intentar(1)
    limitador.intentar(1)  # el intento que termina siendo correcto
    limitador.registrar_exito(1)

    decisiones = [limitador.intentar(1) for _ in range(UMBRAL_FALLOS)]

    assert all(d.permitido for d in decisiones)
    assert decisiones[0].intentos == 1


def test_registrar_exito_devuelve_los_fallos_previos(reloj_falso):
    limitador = _limitador(reloj_falso)
    limitador.intentar(1)
    limitador.intentar(1)
    limitador.intentar(1)  # tercer intento: correcto, 2 fallos previos

    assert limitador.registrar_exito(1) == 2


def test_registrar_exito_sin_fallos_previos_devuelve_cero(reloj_falso):
    limitador = _limitador(reloj_falso)

    assert limitador.registrar_exito(1) == 0
    limitador.intentar(1)
    assert limitador.registrar_exito(1) == 0


def test_claves_distintas_estan_aisladas(reloj_falso):
    limitador = _limitador(reloj_falso)
    for _ in range(UMBRAL_FALLOS):
        limitador.intentar(1)

    assert limitador.intentar(1).permitido is False
    assert limitador.intentar(2).permitido is True


def test_concurrencia_solo_el_umbral_de_intentos_es_permitido(reloj_falso):
    limitador = _limitador(reloj_falso)
    cantidad_hilos = 50
    barrera = threading.Barrier(cantidad_hilos)
    decisiones = []

    def intentar():
        barrera.wait()
        decisiones.append(limitador.intentar(1))

    hilos = [threading.Thread(target=intentar) for _ in range(cantidad_hilos)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    assert len(decisiones) == cantidad_hilos
    assert sum(1 for d in decisiones if d.permitido) == UMBRAL_FALLOS
    assert sum(1 for d in decisiones if d.se_bloquea) == 1
