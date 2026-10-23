"""V1.12-C: ventas FEFO con vencimientos reales. El consumo de partidas es causal (cada movimiento apunta a
su línea de venta), ocurre en la misma transacción que la venta y no altera el costo congelado de
`detalle_venta`. La vista de partidas consumidas es solo para OWNER (muestra costos)."""

import pytest

from domain.compra import ItemCompra, PartidaIngreso
from domain.venta import ItemVenta
from excepciones import StockInsuficienteError
from services import servicio_compras, servicio_partidas, servicio_proveedores, servicio_stock, servicio_ventas
from tests.utilidades_clientes import consultar, crear_owner, usuario_logueado

from tests.test_interfaces_web._asgi_cliente import solicitud

pytestmark = pytest.mark.usefixtures("caja_abierta")


@pytest.fixture
def e(base_datos_temporal):
    owner = crear_owner()
    prov = servicio_proveedores.crear_proveedor("Lácteos")
    producto = servicio_stock.registrar_producto("1", "Yogur", 100, 300, stock_actual=2)  # stock inicial sin fecha
    servicio_compras.registrar_compra(prov.id, owner.id, [ItemCompra(producto.id, 9, 140, (
        PartidaIngreso(5, "2099-12-15"), PartidaIngreso(4, "2099-11-20"),
    ))])
    yield type("E", (), {"ruta": base_datos_temporal, "owner": owner, "prov": prov, "p": producto})
    assert servicio_partidas.productos_no_conciliados() == []


def _vender(e, cantidad, clave=None):
    return servicio_ventas.registrar_venta([ItemVenta(e.p.id, cantidad)], "EFECTIVO", clave, usuario_id=e.owner.id)


def _consumos(venta):
    return [(c.fecha_vencimiento, c.cantidad, c.costo_unitario_centavos) for c in servicio_partidas.consumos_de_venta(venta.id)]


def test_sale_primero_lo_que_vence_antes_y_lo_sin_fecha_al_final(e):
    venta = _vender(e, 10)
    assert _consumos(venta) == [("2099-11-20", 4, 140), ("2099-12-15", 5, 140), (None, 1, 100)]


def test_el_costo_congelado_de_la_venta_no_cambia(e):
    venta = _vender(e, 3)
    costo_detalle = consultar(e.ruta, "SELECT costo_unitario_centavos FROM detalle_venta WHERE venta_id = ?", (venta.id,))
    assert costo_detalle[0][0] == 140  # costo vigente del producto al vender (última compra), como siempre


def test_empate_de_vencimiento_sale_la_partida_mas_antigua(e):
    servicio_compras.registrar_compra(e.prov.id, e.owner.id, [ItemCompra(e.p.id, 3, 150, (PartidaIngreso(3, "2099-11-20"),))])
    venta = _vender(e, 6)
    consumos = servicio_partidas.consumos_de_venta(venta.id)
    assert [(c.fecha_vencimiento, c.cantidad, c.costo_unitario_centavos) for c in consumos] == [
        ("2099-11-20", 4, 140), ("2099-11-20", 2, 150),
    ]
    assert consumos[0].partida_id < consumos[1].partida_id


def test_existencia_heredada_va_despues_de_lo_que_tiene_fecha(e):
    from db.conexion import obtener_conexion
    from db.repositorios import productos as repositorio_productos
    from domain.producto import Producto
    from tests.utilidades_clientes import heredar_existencia_en_conexion

    with obtener_conexion() as conexion:
        viejo = repositorio_productos.crear_producto_en_conexion(conexion, Producto("9", "Viejo", 50, 90, stock_actual=3))
        heredar_existencia_en_conexion(conexion, viejo.id, 3)
    servicio_compras.registrar_compra(e.prov.id, e.owner.id, [ItemCompra(viejo.id, 2, 60, (PartidaIngreso(2, "2099-10-01"),))])
    venta = servicio_ventas.registrar_venta([ItemVenta(viejo.id, 4)], "EFECTIVO")
    assert [(c.origen, c.cantidad) for c in servicio_partidas.consumos_de_venta(venta.id)] == [("COMPRA", 2), ("HEREDADA", 2)]


def test_stock_insuficiente_no_registra_venta_ni_movimientos(e):
    movimientos_antes = consultar(e.ruta, "SELECT COUNT(*) FROM movimientos_partida")[0][0]
    with pytest.raises(StockInsuficienteError):
        _vender(e, 12)
    assert consultar(e.ruta, "SELECT COUNT(*) FROM ventas")[0][0] == 0
    assert consultar(e.ruta, "SELECT COUNT(*) FROM movimientos_partida")[0][0] == movimientos_antes


def test_doble_envio_no_duplica_consumos(e):
    primera = _vender(e, 2, clave="k-v")
    _vender(e, 2, clave="k-v")
    assert _consumos(primera) == [("2099-11-20", 2, 140)]
    assert consultar(e.ruta, "SELECT COUNT(*) FROM movimientos_partida WHERE detalle_venta_id IS NOT NULL")[0][0] == 1


def test_anulada_queda_sin_consumos_netos_y_la_partida_vuelve_a_salir_primero(e):
    venta = _vender(e, 4)
    servicio_ventas.anular_venta(venta.id, "ERROR_CARGA", None, e.owner.id)
    assert servicio_partidas.consumos_de_venta(venta.id) == []
    otra = _vender(e, 1)
    assert _consumos(otra) == [("2099-11-20", 1, 140)]


def test_vista_de_partidas_consumidas_solo_para_owner(e):
    venta = _vender(e, 5)
    _, owner = usuario_logueado("OWNER", "duenio2")
    _, cajera = usuario_logueado("CASHIER", "cajera")
    html_owner = solicitud("GET", f"/ventas/{venta.id}", cookies=owner).texto
    html_cajera = solicitud("GET", f"/ventas/{venta.id}", cookies=cajera).texto
    assert 'id="partidas-consumidas"' in html_owner and "vence 20/11/2099" in html_owner and "$5,60" in html_owner
    assert 'id="partidas-consumidas"' not in html_cajera and "$5,60" not in html_cajera
