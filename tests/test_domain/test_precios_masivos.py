"""V1.2 Fase 2: reglas puras de la actualización masiva (aritmética entera)."""

import pytest

from domain.precios_masivos import (
    ESTADO_INVALIDO,
    ESTADO_OK,
    ESTADO_SIN_CAMBIO,
    CriterioActualizacion,
    calcular_precio_nuevo,
    crear_criterio,
    evaluar,
    monto_a_centavos_positivo,
    porcentaje_a_basis_points,
)
from excepciones import DatosInvalidosError


class TestPorcentaje:
    @pytest.mark.parametrize(
        "texto, esperado", [("10", 1000), ("10,5", 1050), ("10.25", 1025), (" 5 ", 500), ("0,01", 1), ("1000", 100_000)]
    )
    def test_convierte_a_puntos_basicos(self, texto, esperado):
        assert porcentaje_a_basis_points(texto) == esperado

    @pytest.mark.parametrize("texto", ["", "abc", "0", "-5", "10,123", "1000,01", "nan", "inf", "1e3x"])
    def test_rechaza_valores_invalidos(self, texto):
        with pytest.raises(DatosInvalidosError):
            porcentaje_a_basis_points(texto)


class TestMonto:
    def test_monto_positivo(self):
        assert monto_a_centavos_positivo("15,50") == 1550

    @pytest.mark.parametrize("texto", ["0", "-3", "abc", ""])
    def test_rechaza_cero_negativo_o_invalido(self, texto):
        with pytest.raises(DatosInvalidosError):
            monto_a_centavos_positivo(texto)


class TestCalculo:
    def test_aumento_porcentual_exacto(self):
        criterio = CriterioActualizacion("PORCENTAJE", "AUMENTAR", 1000)  # 10 %

        assert calcular_precio_nuevo(20000, criterio) == 22000

    def test_disminucion_porcentual(self):
        criterio = CriterioActualizacion("PORCENTAJE", "DISMINUIR", 2500)  # 25 %

        assert calcular_precio_nuevo(20000, criterio) == 15000

    def test_el_porcentaje_redondea_el_medio_centavo_hacia_arriba(self):
        criterio = CriterioActualizacion("PORCENTAJE", "AUMENTAR", 1050)  # 10,5 %

        assert calcular_precio_nuevo(1001, criterio) == 1106  # 1001 * 1,105 = 1106,105

    def test_monto_fijo_aumenta_y_disminuye(self):
        assert calcular_precio_nuevo(20000, CriterioActualizacion("MONTO", "AUMENTAR", 1500)) == 21500
        assert calcular_precio_nuevo(20000, CriterioActualizacion("MONTO", "DISMINUIR", 1500)) == 18500

    def test_redondeo_al_peso_medio_peso_hacia_arriba(self):
        criterio = CriterioActualizacion("PORCENTAJE", "AUMENTAR", 1000, "ENTERO")

        assert calcular_precio_nuevo(15050, criterio) == 16600  # 16555 -> 16600
        assert calcular_precio_nuevo(14999, criterio) == 16500  # 16498,9 -> 16499 -> 16500

    def test_sin_redondeo_conserva_los_centavos(self):
        criterio = CriterioActualizacion("PORCENTAJE", "AUMENTAR", 1000, "NINGUNO")

        assert calcular_precio_nuevo(15050, criterio) == 16555

    def test_nunca_usa_floats(self):
        criterio = CriterioActualizacion("PORCENTAJE", "AUMENTAR", 1)  # 0,01 %

        assert isinstance(calcular_precio_nuevo(123456789, criterio), int)


class TestEvaluar:
    def test_ok_si_cambia(self):
        assert evaluar(20000, CriterioActualizacion("MONTO", "AUMENTAR", 100)) == (20100, ESTADO_OK)

    def test_sin_cambio_si_el_redondeo_lo_deja_igual(self):
        criterio = CriterioActualizacion("MONTO", "AUMENTAR", 10, "ENTERO")  # 20010 -> 20000

        assert evaluar(20000, criterio) == (20000, ESTADO_SIN_CAMBIO)

    def test_invalido_si_el_precio_quedaria_en_cero_o_negativo(self):
        assert evaluar(1000, CriterioActualizacion("MONTO", "DISMINUIR", 1000))[1] == ESTADO_INVALIDO
        assert evaluar(1000, CriterioActualizacion("MONTO", "DISMINUIR", 5000))[1] == ESTADO_INVALIDO
        assert evaluar(1000, CriterioActualizacion("PORCENTAJE", "DISMINUIR", 10_000))[1] == ESTADO_INVALIDO

    def test_el_redondeo_nunca_convierte_un_precio_positivo_en_cero(self):
        criterio = CriterioActualizacion("MONTO", "DISMINUIR", 970, "ENTERO")  # 1000 - 970 = 30 -> 0

        nuevo, estado = evaluar(1000, criterio)

        assert estado != ESTADO_OK or nuevo > 0


class TestCriterio:
    def test_crear_desde_texto(self):
        assert crear_criterio("PORCENTAJE", "AUMENTAR", "12,5") == CriterioActualizacion("PORCENTAJE", "AUMENTAR", 1250)
        assert crear_criterio("MONTO", "DISMINUIR", "3", "ENTERO") == CriterioActualizacion("MONTO", "DISMINUIR", 300, "ENTERO")

    @pytest.mark.parametrize(
        "args",
        [
            ("OTRO", "AUMENTAR", "1"),
            ("PORCENTAJE", "SUBIR", "1"),
            ("PORCENTAJE", "AUMENTAR", "1", "TRUNCAR"),
            ("PORCENTAJE", "AUMENTAR", "0"),
        ],
    )
    def test_rechaza_criterios_invalidos(self, args):
        with pytest.raises(DatosInvalidosError):
            crear_criterio(*args)
