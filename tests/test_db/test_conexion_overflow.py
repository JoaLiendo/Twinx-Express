"""V1.5-A: un entero que SQLite no puede representar (`OverflowError` al enlazar el parámetro) se traduce a un
error de dato inválido con mensaje propio, la transacción se revierte y la conexión/base siguen utilizables."""

import pytest

from db.conexion import obtener_conexion
from excepciones import DatosInvalidosError, ErrorBaseDatos

ENTERO_ENORME = 2**63  # un bit más de lo que guarda SQLite


def _contar_categorias() -> int:
    with obtener_conexion() as conexion:
        return conexion.execute("SELECT COUNT(*) FROM categorias").fetchone()[0]


def test_el_overflow_se_traduce_a_dato_invalido_con_su_propio_mensaje(base_datos_temporal):
    with pytest.raises(DatosInvalidosError, match="fuera del rango admitido") as error:
        with obtener_conexion() as conexion:
            conexion.execute("SELECT ?", (ENTERO_ENORME,))

    assert isinstance(error.value.__cause__, OverflowError)  # se preserva la causa original
    assert "otra operación en curso" not in str(error.value)  # no es el mensaje de base ocupada


@pytest.mark.parametrize("inmediata", [False, True])
def test_el_overflow_revierte_lo_escrito_antes_en_la_misma_transaccion(base_datos_temporal, inmediata):
    with pytest.raises(DatosInvalidosError):
        with obtener_conexion(inmediata=inmediata) as conexion:
            conexion.execute("INSERT INTO categorias (nombre) VALUES ('no debe quedar')")
            conexion.execute("SELECT ?", (ENTERO_ENORME,))

    assert _contar_categorias() == 0


def test_tras_el_overflow_una_operacion_valida_funciona_de_inmediato(base_datos_temporal):
    with pytest.raises(DatosInvalidosError):
        with obtener_conexion(inmediata=True) as conexion:
            conexion.execute("INSERT INTO categorias (nombre) VALUES ('x')")
            conexion.execute("SELECT ?", (-ENTERO_ENORME - 1,))  # menor que -(2**63)

    with obtener_conexion(inmediata=True) as conexion:  # el lock de escritura se liberó
        conexion.execute("INSERT INTO categorias (nombre) VALUES ('valida')")

    assert _contar_categorias() == 1


def test_el_maximo_entero_representable_no_se_ve_afectado(base_datos_temporal):
    with obtener_conexion() as conexion:
        assert conexion.execute("SELECT ?", (2**63 - 1,)).fetchone()[0] == 2**63 - 1
        assert conexion.execute("SELECT ?", (-(2**63),)).fetchone()[0] == -(2**63)


@pytest.mark.parametrize("error", [RuntimeError("otro problema"), ValueError("otro problema"), ZeroDivisionError("otro problema"), ArithmeticError("otro problema")])
def test_solo_se_traduce_overflow_los_demas_errores_no_se_ocultan(base_datos_temporal, error):
    with pytest.raises(type(error), match="otro problema"):
        with obtener_conexion() as conexion:
            conexion.execute("INSERT INTO categorias (nombre) VALUES ('x')")
            raise error

    assert _contar_categorias() == 0


def test_un_error_de_sqlite_sigue_siendo_error_de_base_no_dato_invalido(base_datos_temporal):
    with pytest.raises(ErrorBaseDatos):
        with obtener_conexion() as conexion:
            conexion.execute("SELECT * FROM tabla_inexistente")
