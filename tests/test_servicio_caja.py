"""Pruebas de integración de services.servicio_caja: secuencia lógica de
una caja física (apertura, movimientos, cierre) contra una base de
datos SQLite temporal (ver tests/conftest.py)."""

import pytest

from domain.venta import ItemVenta
from excepciones import CajaError, DatosInvalidosError
from services import servicio_caja, servicio_stock, servicio_ventas


def test_abrir_caja_registra_movimiento_de_apertura(base_datos_temporal):
    movimiento = servicio_caja.abrir_caja(100000, "Apertura turno mañana")

    assert movimiento.tipo == "APERTURA"
    assert movimiento.monto_centavos == 100000


def test_no_se_puede_abrir_una_caja_ya_abierta(base_datos_temporal):
    servicio_caja.abrir_caja(100000)

    with pytest.raises(CajaError):
        servicio_caja.abrir_caja(50000)


def test_no_se_puede_cerrar_sin_una_caja_abierta(base_datos_temporal):
    with pytest.raises(CajaError):
        servicio_caja.cerrar_caja(100000)


def test_no_se_puede_registrar_ingreso_sin_caja_abierta(base_datos_temporal):
    with pytest.raises(CajaError):
        servicio_caja.registrar_ingreso(5000, "cambio inicial")


def test_no_se_puede_registrar_egreso_sin_caja_abierta(base_datos_temporal):
    with pytest.raises(CajaError):
        servicio_caja.registrar_egreso(5000, "pago a proveedor")


def test_ingreso_requiere_descripcion(base_datos_temporal):
    servicio_caja.abrir_caja(100000)

    with pytest.raises(DatosInvalidosError):
        servicio_caja.registrar_ingreso(5000, "")


def test_egreso_requiere_descripcion(base_datos_temporal):
    servicio_caja.abrir_caja(100000)

    with pytest.raises(DatosInvalidosError):
        servicio_caja.registrar_egreso(5000, "   ")


def test_ciclo_completo_apertura_movimientos_y_cierre(base_datos_temporal):
    servicio_caja.abrir_caja(100000, "Apertura")
    servicio_caja.registrar_ingreso(5000, "Cambio adicional")
    servicio_caja.registrar_egreso(2000, "Pago a proveedor")

    cierre = servicio_caja.cerrar_caja(103000, "Cierre")

    assert cierre.tipo == "CIERRE"
    assert cierre.monto_centavos == 103000


def test_se_puede_reabrir_la_caja_despues_de_un_cierre(base_datos_temporal):
    servicio_caja.abrir_caja(100000)
    servicio_caja.cerrar_caja(100000)

    movimiento = servicio_caja.abrir_caja(50000, "Nuevo turno")

    assert movimiento.tipo == "APERTURA"


def test_no_se_puede_cerrar_dos_veces_seguidas(base_datos_temporal):
    servicio_caja.abrir_caja(100000)
    servicio_caja.cerrar_caja(100000)

    with pytest.raises(CajaError):
        servicio_caja.cerrar_caja(100000)


def test_monto_negativo_es_rechazado(base_datos_temporal):
    with pytest.raises(DatosInvalidosError):
        servicio_caja.abrir_caja(-100)


def test_arqueo_del_dia_sin_movimientos_ni_ventas_da_todo_en_cero(base_datos_temporal):
    arqueo = servicio_caja.calcular_arqueo_del_dia()

    assert arqueo.total_vendido_centavos == 0
    assert arqueo.total_efectivo_ventas_centavos == 0
    assert arqueo.efectivo_estimado_centavos == 0
    assert arqueo.cantidad_ventas == 0
    assert arqueo.ventas == []


def test_arqueo_del_dia_combina_apertura_ventas_e_ingresos_egresos(base_datos_temporal):
    servicio_caja.abrir_caja(100000, "Apertura turno mañana")
    servicio_caja.registrar_ingreso(5000, "Cambio adicional")
    servicio_caja.registrar_egreso(2000, "Pago a proveedor")

    producto = servicio_stock.registrar_producto(
        "7790000000001", "Alfajor", 100, 20000, stock_actual=10, stock_minimo=1
    )
    servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
    servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "TARJETA")

    arqueo = servicio_caja.calcular_arqueo_del_dia()

    assert arqueo.cantidad_ventas == 2
    assert arqueo.total_vendido_centavos == 40000  # 20000 efectivo + 20000 tarjeta
    assert arqueo.total_efectivo_ventas_centavos == 20000
    # 100000 (apertura) + 5000 (ingreso) - 2000 (egreso) + 20000 (venta efectivo)
    assert arqueo.efectivo_estimado_centavos == 123000
