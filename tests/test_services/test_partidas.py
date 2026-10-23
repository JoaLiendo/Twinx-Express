"""V1.12-A: las partidas acompañan, en la misma transacción, a cada operación que mueve stock.

Al final de cada test se exige la conciliación global (`stock_actual = SUM(partidas)` en TODOS los
productos) y que el libro explique cada disponible (`original + SUM(movimientos)`).
"""

import threading

import pytest

from db.conexion import obtener_conexion
from db.repositorios import caja as repositorio_caja
from db.repositorios import productos as repositorio_productos
from db.repositorios import ventas as repositorio_ventas
from domain.compra import ItemCompra
from domain.producto import Producto
from domain.venta import ItemVenta
from excepciones import PrecioVentaNoConfiguradoError, StockInsuficienteError
from services import (
    servicio_compras,
    servicio_inventario,
    servicio_partidas,
    servicio_proveedores,
    servicio_stock,
    servicio_ventas,
)
from tests.utilidades_clientes import consultar, crear_owner, heredar_existencia_en_conexion

pytestmark = pytest.mark.usefixtures("caja_abierta")


@pytest.fixture
def e(base_datos_temporal):
    owner = crear_owner()
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora")
    producto = servicio_stock.registrar_producto("1", "Yogur", 100, 300, stock_actual=4, usuario_id=owner.id)
    yield type("E", (), {"ruta": base_datos_temporal, "owner": owner, "prov": proveedor, "p": producto})
    assert servicio_partidas.productos_no_conciliados() == []
    assert consultar(
        base_datos_temporal,
        "SELECT COUNT(*) FROM partidas_stock s WHERE s.cantidad_disponible <> s.cantidad_original"
        " + (SELECT COALESCE(SUM(delta), 0) FROM movimientos_partida m WHERE m.partida_id = s.id)",
    )[0][0] == 0


def _partidas(producto):
    return [(p.origen, p.cantidad_original, p.cantidad_disponible, p.costo_unitario_centavos)
            for p in servicio_partidas.listar_partidas(producto.id)]


def _movimientos(e, columna, valor):
    return [tuple(f) for f in consultar(
        e.ruta, f"SELECT partida_id, delta, criterio FROM movimientos_partida WHERE {columna} = ? ORDER BY id", (valor,)
    )]


def _comprar(e, cantidad, costo=120):
    return servicio_compras.registrar_compra(e.prov.id, e.owner.id, [ItemCompra(e.p.id, cantidad, costo)])


def _vender(e, cantidad, clave=None):
    return servicio_ventas.registrar_venta([ItemVenta(e.p.id, cantidad)], "EFECTIVO", clave, usuario_id=e.owner.id)


def test_alta_con_stock_crea_su_partida_con_el_costo_del_ajuste(e):
    assert _partidas(e.p) == [("STOCK_INICIAL", 4, 4, 100)]
    sin_stock = servicio_stock.registrar_producto("2", "Vacío", 10, 20)
    assert servicio_partidas.listar_partidas(sin_stock.id) == []


def test_compra_crea_partida_con_el_costo_real_de_la_linea(e):
    _comprar(e, 10, costo=135)
    assert _partidas(e.p) == [("STOCK_INICIAL", 4, 4, 100), ("COMPRA", 10, 10, 135)]


def test_venta_cruza_partidas_por_fefo_y_su_anulacion_devuelve_exactamente_lo_mismo(e):
    _comprar(e, 10)
    venta = _vender(e, 6)
    detalle_id = consultar(e.ruta, "SELECT id FROM detalle_venta WHERE venta_id = ?", (venta.id,))[0][0]
    assert _movimientos(e, "detalle_venta_id", detalle_id) == [(1, -4, "FEFO"), (2, -2, "FEFO")]
    assert _partidas(e.p) == [("STOCK_INICIAL", 4, 0, 100), ("COMPRA", 10, 8, 120)]

    servicio_ventas.anular_venta(venta.id, "ERROR_CARGA", None, e.owner.id)

    assert _movimientos(e, "detalle_venta_id", detalle_id)[2:] == [(1, 4, "REVERSION"), (2, 2, "REVERSION")]
    assert _partidas(e.p) == [("STOCK_INICIAL", 4, 4, 100), ("COMPRA", 10, 10, 120)]


def test_una_venta_con_una_linea_invalida_no_toca_ninguna_partida(e):
    sin_precio = servicio_stock.registrar_producto("3", "Sin precio", 10, 0, stock_actual=5)
    with pytest.raises(PrecioVentaNoConfiguradoError):
        servicio_ventas.registrar_venta([ItemVenta(e.p.id, 2), ItemVenta(sin_precio.id, 1)], "EFECTIVO")
    with pytest.raises(StockInsuficienteError):
        _vender(e, 5)
    assert consultar(e.ruta, "SELECT COUNT(*) FROM movimientos_partida")[0][0] == 0
    assert _partidas(e.p) == [("STOCK_INICIAL", 4, 4, 100)]


def test_el_reintento_idempotente_no_vuelve_a_consumir(e):
    _vender(e, 1, clave="k-1")
    _vender(e, 1, clave="k-1")
    assert consultar(e.ruta, "SELECT COUNT(*) FROM movimientos_partida")[0][0] == 1
    assert servicio_stock.obtener_por_id(e.p.id).stock_actual == 3


def test_varias_lineas_del_mismo_producto(e):
    _comprar(e, 3)
    venta = servicio_ventas.registrar_venta([ItemVenta(e.p.id, 3), ItemVenta(e.p.id, 2)], "EFECTIVO")
    detalles = [f[0] for f in consultar(e.ruta, "SELECT id FROM detalle_venta WHERE venta_id = ? ORDER BY id", (venta.id,))]
    assert _movimientos(e, "detalle_venta_id", detalles[0]) == [(1, -3, "FEFO")]
    assert _movimientos(e, "detalle_venta_id", detalles[1]) == [(1, -1, "FEFO"), (2, -1, "FEFO")]


def test_baja_y_su_reversa_vuelven_a_la_misma_partida(e):
    _comprar(e, 10)
    rotura = servicio_stock.ajustar_stock(e.p.id, -5, "ROTURA", e.owner.id)
    assert _movimientos(e, "ajuste_id", rotura.id) == [(1, -4, "FEFO"), (2, -1, "FEFO")]
    reversa = servicio_stock.revertir_ajuste(rotura.id, "se cargó de más", e.owner.id)
    assert _movimientos(e, "ajuste_id", reversa.id) == [(1, 4, "REVERSION"), (2, 1, "REVERSION")]
    assert _partidas(e.p) == [("STOCK_INICIAL", 4, 4, 100), ("COMPRA", 10, 10, 120)]


def test_baja_con_una_sola_partida_es_unica(e):
    rotura = servicio_stock.ajustar_stock(e.p.id, -1, "ROTURA", e.owner.id)
    assert _movimientos(e, "ajuste_id", rotura.id) == [(1, -1, "UNICA")]


def test_recuento_positivo_no_inventa_vencimiento_ni_costo_y_negativo_consume(e):
    inventario = servicio_inventario.crear_inventario(e.owner.id, [e.p.id])
    servicio_inventario.registrar_conteo(inventario.id, e.p.id, 6, e.owner.id)
    servicio_inventario.confirmar_inventario(inventario.id, e.owner.id, "k-inv-1")
    partidas = servicio_partidas.listar_partidas(e.p.id)
    assert [(p.origen, p.cantidad_disponible, p.fecha_vencimiento, p.costo_unitario_centavos) for p in partidas] == [
        ("STOCK_INICIAL", 4, None, 100), ("RECUENTO", 2, None, None),
    ]
    otro = servicio_inventario.crear_inventario(e.owner.id, [e.p.id])
    servicio_inventario.registrar_conteo(otro.id, e.p.id, 1, e.owner.id)
    servicio_inventario.confirmar_inventario(otro.id, e.owner.id, "k-inv-2")
    assert [p.cantidad_disponible for p in servicio_partidas.listar_partidas(e.p.id)] == [0, 1]


def test_anular_compra_descuenta_primero_su_propia_partida(e):
    compra = _comprar(e, 10)
    servicio_compras.anular_compra(compra.id, "ERROR_CARGA", None, e.owner.id)
    detalle_id = consultar(e.ruta, "SELECT id FROM detalle_compra WHERE compra_id = ?", (compra.id,))[0][0]
    assert _movimientos(e, "detalle_compra_id", detalle_id) == [(2, -10, "ELEGIDA")]
    assert _partidas(e.p) == [("STOCK_INICIAL", 4, 4, 100), ("COMPRA", 10, 0, 120)]


def test_anular_compra_ya_vendida_en_parte_completa_por_fefo(e):
    compra = _comprar(e, 10)
    servicio_stock.ajustar_stock(e.p.id, -12, "ROBO", e.owner.id)  # FEFO por id: 4 del alta + 8 de la compra
    servicio_compras.registrar_compra(e.prov.id, e.owner.id, [ItemCompra(e.p.id, 20, 90)])
    servicio_compras.anular_compra(compra.id, "ERROR_CARGA", None, e.owner.id)
    detalle_id = consultar(e.ruta, "SELECT id FROM detalle_compra WHERE compra_id = ?", (compra.id,))[0][0]
    assert _movimientos(e, "detalle_compra_id", detalle_id) == [(2, -2, "ELEGIDA"), (3, -8, "UNICA")]


def test_anular_una_venta_anterior_a_v112_devuelve_una_partida_sin_datos_inventados(e):
    with obtener_conexion(inmediata=True) as conexion:
        historico = repositorio_productos.crear_producto_en_conexion(conexion, Producto("9", "Viejo", 50, 80, stock_actual=10))
        sesion = repositorio_caja.obtener_sesion_abierta_en_conexion(conexion)
        venta = repositorio_ventas.registrar_venta_con_detalle(
            conexion, 160, "EFECTIVO", [(ItemVenta(historico.id, 2), 80)], sesion_caja_id=sesion.id
        )
        heredar_existencia_en_conexion(conexion, historico.id, 10)  # el upgrade heredó lo que quedó

    servicio_ventas.anular_venta(venta.id, "ERROR_CARGA", None, e.owner.id)

    partidas = servicio_partidas.listar_partidas(historico.id)
    assert [(p.origen, p.cantidad_disponible, p.fecha_vencimiento, p.costo_unitario_centavos) for p in partidas] == [
        ("HEREDADA", 10, None, None), ("ANULACION_VENTA", 2, None, None),
    ]


def test_ventas_concurrentes_mantienen_la_conciliacion(e):
    _comprar(e, 46)  # 50 en total
    errores = []

    def vender():
        try:
            for _ in range(5):
                _vender(e, 2)
        except Exception as error:  # noqa: BLE001 - se informa abajo
            errores.append(error)

    hilos = [threading.Thread(target=vender) for _ in range(4)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()
    assert errores == []
    assert servicio_stock.obtener_por_id(e.p.id).stock_actual == 10
    assert sum(p.cantidad_disponible for p in servicio_partidas.listar_partidas(e.p.id)) == 10
