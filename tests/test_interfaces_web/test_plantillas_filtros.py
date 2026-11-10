"""Filtros de presentación registrados en `interfaces.web.plantillas`."""

import pytest

from interfaces.web.plantillas import formato_porcentaje


@pytest.mark.parametrize(
    "valor, esperado",
    [
        (None, "—"),
        (12.5, "12,5 %"),
        (-3.0, "-3,0 %"),
        (0.0, "0,0 %"),
        # Auditoría visual del cierre: los miles van con punto, como en los importes (antes «-2499960,0 %»).
        (-2499960.0, "-2.499.960,0 %"),
        (1250.0, "1.250,0 %"),
    ],
)
def test_formato_porcentaje(valor, esperado):
    assert formato_porcentaje(valor) == esperado
