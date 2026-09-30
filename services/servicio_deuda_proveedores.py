"""Reporte de deuda a proveedores (V1.10-D): saldo por proveedor y su reparto estimado por vencimiento.

Solo lectura. El saldo del libro es la verdad contable agregada; el reparto en vencida / próxima / vigente /
sin vencimiento es una **estimación por antigüedad (FIFO)** (ver `domain.cuentas_a_pagar`) y así debe
presentarse. Un proveedor cuyo libro es imposible se informa como inconsistente, sin cifras FIFO, y se
registra un warning; nunca se corrige nada.
"""

import logging
from datetime import date

from db.repositorios import cuentas_a_pagar as repositorio_cuentas_a_pagar
from domain.deuda_proveedores import (
    ReporteDeudaProveedores,
    construir_fila,
    cumple_situacion,
    es_listable,
    fila_inconsistente,
    validar_situacion,
)
from excepciones import LibroProveedorInconsistenteError

logger = logging.getLogger(__name__)

CRITERIO_FIFO = "Estimado por antigüedad (FIFO)"
MARCA_INCONSISTENTE = "Datos inconsistentes"


def generar_reporte_deuda_proveedores(
    proveedor_id: int | None = None, situacion: str | None = None, hoy: date | None = None
) -> ReporteDeudaProveedores:
    """Proveedores con deuda (saldo > 0) más los de libro inconsistente, con sus importes estimados por
    vencimiento. `proveedor_id` y `situacion` (`vencida`, `proxima`, `sin_vencimiento` o vacío = todas) se
    combinan; una fila inconsistente se conserva con cualquier situación porque no se puede descartar que
    la cumpla. `hoy` es solo para tests: por defecto, la fecha local.

    Raises:
        DatosInvalidosError: si la situación no es una de las admitidas.
    """
    situacion_valida = validar_situacion(situacion)
    hoy = hoy or date.today()
    filas = []
    for libro in repositorio_cuentas_a_pagar.leer_libros_de_proveedores(proveedor_id):
        try:
            fila = construir_fila(libro, hoy)
        except LibroProveedorInconsistenteError as error:
            logger.warning("Libro inconsistente del proveedor %s en el reporte de deuda: %s", libro.proveedor_id, error)
            fila = fila_inconsistente(libro)
        if es_listable(fila) and cumple_situacion(fila, situacion_valida):
            filas.append(fila)
    return ReporteDeudaProveedores(proveedor_id, situacion_valida, tuple(filas))
