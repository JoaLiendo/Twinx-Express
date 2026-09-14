"""Tests de `services/control_escrituras.py`: coordinación central de
escrituras de negocio en vuelo vs. la operación de backup.

Separación deliberada de conceptos (corrección de un deadlock real:
ver auditoría de distribución/backup) -- una escritura de negocio
(`permitir_escritura_de_negocio`/`finalizar_escritura_de_negocio`) es
lo que cuenta el middleware para cada request que sí modifica datos de
negocio. `iniciar_backup`/`finalizar_backup` es una operación atómica
aparte: nunca se cuenta a sí misma como una escritura de negocio, así
que esperar a que esas escrituras drenen no puede esperarse a sí
misma. `iniciar_backup` también resuelve, en una única sección
crítica, que dos backups no puedan arrancar a la vez.
"""

import threading
import time

from services.control_escrituras import ControlEscrituras


def test_permite_escritura_de_negocio_normal():
    control = ControlEscrituras()

    assert control.permitir_escritura_de_negocio() is True
    assert control.escrituras_de_negocio_en_curso == 1


def test_finalizar_escritura_de_negocio_decrementa_el_contador():
    control = ControlEscrituras()
    control.permitir_escritura_de_negocio()

    control.finalizar_escritura_de_negocio()

    assert control.escrituras_de_negocio_en_curso == 0


def test_iniciar_backup_rechaza_escrituras_de_negocio_nuevas():
    control = ControlEscrituras()
    assert control.iniciar_backup() is True

    assert control.permitir_escritura_de_negocio() is False
    assert control.escrituras_de_negocio_en_curso == 0


def test_iniciar_backup_espera_a_que_termine_una_escritura_de_negocio_en_curso():
    control = ControlEscrituras()
    control.permitir_escritura_de_negocio()  # escritura de negocio ya en curso
    orden = []

    def escritura_en_curso():
        time.sleep(0.05)
        orden.append("escritura_terminada")
        control.finalizar_escritura_de_negocio()

    hilo = threading.Thread(target=escritura_en_curso)
    hilo.start()

    assert control.iniciar_backup(timeout=2) is True
    orden.append("backup_continua")
    hilo.join()

    assert orden == ["escritura_terminada", "backup_continua"]
    assert control.escrituras_de_negocio_en_curso == 0


def test_finalizar_backup_permite_escrituras_de_negocio_de_nuevo():
    control = ControlEscrituras()
    control.iniciar_backup()

    control.finalizar_backup()

    assert control.permitir_escritura_de_negocio() is True


def test_iniciar_backup_no_espera_a_si_mismo():
    """Regresión del deadlock: `iniciar_backup` nunca debe contarse a sí
    mismo como una escritura de negocio -- si lo hiciera, esperaría por
    siempre a que un contador que él mismo incrementó llegue a cero."""
    control = ControlEscrituras()

    resultado = control.iniciar_backup(timeout=1)

    assert resultado is True
    assert control.escrituras_de_negocio_en_curso == 0
    assert control.backup_activo is True


def test_dos_backups_simultaneos_solo_uno_gana():
    control = ControlEscrituras()
    resultados = {}

    def intentar(nombre):
        resultados[nombre] = control.iniciar_backup(timeout=2)

    # El primero toma el turno y se queda "activo" (no lo libera) para
    # que el segundo, al llegar, todavía lo encuentre ocupado.
    hilo_a = threading.Thread(target=intentar, args=("A",))
    hilo_a.start()
    hilo_a.join()

    hilo_b = threading.Thread(target=intentar, args=("B",))
    hilo_b.start()
    hilo_b.join()

    assert sorted(resultados.values()) == [False, True]


def test_iniciar_backup_con_timeout_agotado_no_permite_backup_con_escrituras_pendientes():
    """El timeout de `iniciar_backup` nunca puede devolver `True` con
    escrituras de negocio todavía en curso -- y debe dejar todo en un
    estado normal (sin backup activo, sin escrituras perdidas) para
    que la aplicación pueda seguir funcionando."""
    control = ControlEscrituras()
    control.permitir_escritura_de_negocio()  # (1) escritura de negocio en curso, nunca se libera en este test

    resultado = control.iniciar_backup(timeout=0.05)  # (2)+(3) timeout corto que se agota

    assert resultado is False  # (4) el backup no comienza
    assert control.backup_activo is False  # (5)

    assert control.permitir_escritura_de_negocio() is True  # (6) las escrituras vuelven a poder entrar

    # (7) se libera la escritura original (la de (1)) y la nueva (la de (6));
    # los contadores quedan en cero.
    control.finalizar_escritura_de_negocio()
    control.finalizar_escritura_de_negocio()
    assert control.escrituras_de_negocio_en_curso == 0


def test_excepcion_durante_escritura_de_negocio_no_impide_liberar_el_contador():
    control = ControlEscrituras()
    control.permitir_escritura_de_negocio()

    try:
        try:
            raise RuntimeError("fallo simulado durante la escritura")
        finally:
            control.finalizar_escritura_de_negocio()
    except RuntimeError:
        pass

    assert control.escrituras_de_negocio_en_curso == 0
