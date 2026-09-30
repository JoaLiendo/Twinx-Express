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
    CuentaProveedor,
    ReporteDeudaProveedores,
    construir_cuenta,
    construir_fila,
    cuenta_inconsistente,
    cuenta_vacia,
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
    excluidos = 0
    for libro in repositorio_cuentas_a_pagar.leer_libros_de_proveedores(proveedor_id):
        try:
            fila = construir_fila(libro, hoy)
        except LibroProveedorInconsistenteError as error:
            logger.warning("Libro inconsistente del proveedor %s en el reporte de deuda: %s", libro.proveedor_id, error)
            fila = fila_inconsistente(libro)
        if not es_listable(fila):
            continue
        if cumple_situacion(fila, situacion_valida):
            filas.append(fila)
        elif not fila.consistente:
            excluidos += 1
    return ReporteDeudaProveedores(proveedor_id, situacion_valida, tuple(filas), excluidos)


def generar_cuenta_proveedor(proveedor_id: int, hoy: date | None = None) -> CuentaProveedor:
    """Cuenta a pagar de un proveedor (ficha y detalle de compra): saldo real del libro y, como estimación
    por antigüedad (FIFO), su reparto por vencimiento, sus compras a crédito abiertas y su próximo
    vencimiento. Con un libro inconsistente devuelve solo el saldo agregado y registra un warning.

    Lee libro y compras en una misma transacción (`leer_libros_de_proveedores`), el mismo motor que el
    reporte. `hoy` es solo para tests."""
    libros = repositorio_cuentas_a_pagar.leer_libros_de_proveedores(proveedor_id)
    if not libros:
        return cuenta_vacia()
    libro = libros[0]
    try:
        return construir_cuenta(libro, hoy or date.today())
    except LibroProveedorInconsistenteError as error:
        logger.warning("Libro inconsistente del proveedor %s en su cuenta a pagar: %s", proveedor_id, error)
        return cuenta_inconsistente(libro.saldo_centavos)
