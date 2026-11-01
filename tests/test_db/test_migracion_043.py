"""Migración 043 (V1.16-F): invariantes de la cancelación con nota de crédito, exigidas por la base."""

import pytest

from db.conexion import obtener_conexion
from db.repositorios import fiscal as repositorio_fiscal
from excepciones import ErrorBaseDatos
from tests import utilidades_fiscales as uf


@pytest.fixture
def escenario(caja_abierta):
    owner = uf.crear_owner()
    venta = uf.vender([(uf.producto(12100, uf.IVA_21), 1)])
    factura = uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))
    uf.autorizar(factura, 1)
    nota = uf.insertar(uf.nuevo_comprobante_para_venta(venta.id, rol="NOTA_CREDITO", comprobante_asociado_id=factura))
    with obtener_conexion() as conexion:
        cancelacion = repositorio_fiscal.insertar_cancelacion_en_conexion(conexion, venta.id, factura, nota, "ERROR_CARGA", None, owner)
    return venta, factura, nota, cancelacion, owner


def _actualizar(cancelacion_id, estado):
    with obtener_conexion() as conexion:
        repositorio_fiscal.actualizar_cancelacion_en_conexion(conexion, cancelacion_id, estado)


def test_aplicada_exige_nc_autorizada_y_venta_anulada(escenario):
    _, _, nota, cancelacion, _ = escenario
    with pytest.raises(ErrorBaseDatos, match="solo se aplica"):
        _actualizar(cancelacion, "APLICADA")
    uf.transicionar(nota, "ENVIANDO", numero=1, fecha_comprobante="2026-11-01")
    uf.transicionar(nota, "AUTORIZADO", resultado="A", codigo_autorizacion="71234567890999", vencimiento_autorizacion="2026-11-11")
    with pytest.raises(ErrorBaseDatos, match="solo se aplica"):
        _actualizar(cancelacion, "APLICADA")


def test_una_sola_cancelacion_viva_por_venta(escenario):
    venta, factura, nota, _, owner = escenario
    with pytest.raises(ErrorBaseDatos):
        with obtener_conexion() as conexion:
            repositorio_fiscal.insertar_cancelacion_en_conexion(conexion, venta.id, factura, nota, "ERROR_CARGA", None, owner)


def test_transiciones_y_borrado(escenario):
    _, _, _, cancelacion, _ = escenario
    _actualizar(cancelacion, "RECHAZADA")
    with pytest.raises(ErrorBaseDatos, match="no permitida"):
        _actualizar(cancelacion, "PENDIENTE")
    with pytest.raises(ErrorBaseDatos):
        with obtener_conexion() as conexion:
            conexion.execute("DELETE FROM cancelaciones_fiscales")


def test_otro_exige_observaciones(escenario):
    venta, factura, nota, cancelacion, owner = escenario
    _actualizar(cancelacion, "RECHAZADA")
    with pytest.raises(ErrorBaseDatos):
        with obtener_conexion() as conexion:
            repositorio_fiscal.insertar_cancelacion_en_conexion(conexion, venta.id, factura, nota, "OTRO", None, owner)
