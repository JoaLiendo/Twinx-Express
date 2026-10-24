"""Auditoría de cierre de V1.12: casos adversariales que faltaban sobre partidas, costos y bajas.

Al final de cada test se exige la conciliación global y que el libro explique cada disponible.
"""

from datetime import date, timedelta

import pytest

from db.conexion import obtener_conexion
from db.repositorios import ajustes_stock as repositorio_ajustes_stock
from db.repositorios import productos as repositorio_productos
from domain.ajuste_stock import AjusteStock
from domain.compra import ItemCompra, PartidaIngreso
from domain.producto import Producto
from domain.venta import ItemVenta
from excepciones import ProductoNoEncontradoError, VentaRequiereAutorizacionVencidosError, VentaYaAnuladaError
from services import servicio_compras, servicio_partidas, servicio_proveedores, servicio_stock, servicio_ventas
from tests.test_interfaces_web._asgi_cliente import solicitud
from tests.utilidades_clientes import consultar, heredar_existencia_en_conexion, usuario_logueado

pytestmark = pytest.mark.usefixtures("caja_abierta")

HOY = date.today()


def _dia(dias: int) -> str:
    return (HOY + timedelta(days=dias)).isoformat()


@pytest.fixture
def e(base_datos_temporal):
    owner, co = usuario_logueado("OWNER", "duenio")
    prov = servicio_proveedores.crear_proveedor("Lácteos")
    yield type("E", (), {"ruta": base_datos_temporal, "owner": owner, "co": co, "prov": prov})
    assert servicio_partidas.productos_no_conciliados() == []
    assert consultar(
        base_datos_temporal,
        "SELECT COUNT(*) FROM partidas_stock s WHERE s.cantidad_disponible <> s.cantidad_original"
        " + (SELECT COALESCE(SUM(delta), 0) FROM movimientos_partida m WHERE m.partida_id = s.id)",
    )[0][0] == 0


def _heredado(codigo: str, stock: int, costo: int = 77) -> Producto:
    """Producto anterior a V1.12 (sin historial) con su existencia heredada, como lo deja el upgrade."""
    with obtener_conexion() as conexion:
        producto = repositorio_productos.crear_producto_en_conexion(
            conexion, Producto(codigo, f"Viejo {codigo}", costo, 200, stock_actual=stock)
        )
        heredar_existencia_en_conexion(conexion, producto.id, stock)
    return producto


def _partidas(producto_id):
    return [(p.origen, p.cantidad_disponible, p.fecha_vencimiento, p.costo_unitario_centavos)
            for p in servicio_partidas.listar_partidas(producto_id)]


class TestCostoDePerdidas:
    def test_con_varias_partidas_sin_elegir_el_costo_queda_desconocido(self, e):
        producto = servicio_stock.registrar_producto("1", "Yogur", 100, 300, stock_actual=2, usuario_id=e.owner.id)
        servicio_compras.registrar_compra(e.prov.id, e.owner.id, [ItemCompra(producto.id, 5, 140)])
        rotura = servicio_stock.ajustar_stock(producto.id, -3, "ROTURA", e.owner.id)
        assert rotura.costo_unitario_centavos is None  # ni 140 (vigente) ni el de una partida supuesta
        assert [tuple(f) for f in consultar(e.ruta, "SELECT delta, criterio FROM movimientos_partida WHERE ajuste_id = ?",
                                            (rotura.id,))] == [(-2, "FEFO"), (-1, "FEFO")]

    def test_con_una_sola_partida_usa_su_costo_real_y_no_el_vigente(self, e):
        producto = servicio_stock.registrar_producto("1", "Yogur", 100, 300, stock_actual=4, usuario_id=e.owner.id)
        servicio_stock.actualizar_producto(producto.id, "1", "Yogur", 150, 300, 0, usuario_id=e.owner.id)
        assert servicio_stock.ajustar_stock(producto.id, -1, "ROBO", e.owner.id).costo_unitario_centavos == 100

    def test_existencia_heredada_no_inventa_costo(self, e):
        producto = _heredado("9", 5)
        assert servicio_stock.ajustar_stock(producto.id, -1, "FALLA_FABRICA", e.owner.id).costo_unitario_centavos is None


def test_reversa_de_una_baja_anterior_a_v112_no_inventa_costo_de_partida(e):
    with obtener_conexion() as conexion:  # V1.11: 5 u., ROTURA -2 (snapshot = costo vigente 77), upgrade hereda 3
        producto = repositorio_productos.crear_producto_en_conexion(conexion, Producto("9", "Viejo", 77, 200, stock_actual=3))
        rotura = repositorio_ajustes_stock.registrar_ajuste_en_conexion(conexion, AjusteStock(
            producto_id=producto.id, usuario_id=e.owner.id, motivo="ROTURA", delta=-2, stock_anterior=5,
            stock_resultante=3, costo_unitario_centavos=77,
        ))
        heredar_existencia_en_conexion(conexion, producto.id, 3)

    reversa = servicio_stock.revertir_ajuste(rotura.id, "no se había roto", e.owner.id)

    assert reversa.costo_unitario_centavos == 77  # el ajuste conserva la referencia histórica de su original
    assert _partidas(producto.id) == [("HEREDADA", 3, None, None), ("AJUSTE", 2, None, None)]  # la partida no la hereda


class TestProductoInactivoConVencidos:
    @pytest.fixture
    def inactivo(self, e):
        producto = servicio_stock.registrar_producto("1", "Yogur", 100, 300, usuario_id=e.owner.id)
        servicio_compras.registrar_compra(e.prov.id, e.owner.id, [ItemCompra(producto.id, 5, 120, (PartidaIngreso(5, _dia(3)),))])
        assert servicio_stock.eliminar_producto(producto.id, e.owner.id) is True  # tiene historial: baja lógica
        return producto

    def test_se_puede_retirar_lo_vencido_desde_su_pantalla(self, e, inactivo):
        partida = servicio_partidas.listar_partidas(inactivo.id)[0]
        html = solicitud("GET", f"/productos/{inactivo.id}/vencimientos", cookies=e.co).texto
        assert f'data-partida-id="{partida.id}"' in html
        respuesta = solicitud("POST", f"/productos/{inactivo.id}/vencimientos/{partida.id}/baja", cookies=e.co,
                              formulario={"cantidad": "5", "clave_idempotencia": "k-inactivo"})
        assert "tipo=success" in (respuesta.header("location") or "")
        assert repositorio_productos.obtener_por_id_incluyendo_inactivos(inactivo.id).stock_actual == 0
        assert servicio_partidas.resumenes_de_vencimiento(10).get(inactivo.id) is None  # la alerta se apaga

    def test_el_listado_de_dados_de_baja_ofrece_vencimientos(self, e, inactivo):
        html = solicitud("GET", "/productos?mostrar_inactivos=true", cookies=e.co).texto
        assert f'href="/productos/{inactivo.id}/vencimientos"' in html

    def test_otras_perdidas_siguen_exigiendo_producto_activo(self, e, inactivo):
        with pytest.raises(ProductoNoEncontradoError):
            servicio_stock.ajustar_stock(inactivo.id, -1, "ROTURA", e.owner.id)


def test_baja_de_un_producto_con_existencia_heredada_y_sin_otro_historial_es_logica(e):
    producto = _heredado("9", 5)
    assert servicio_stock.eliminar_producto(producto.id, e.owner.id) is True
    assert servicio_stock.motivos_de_conservacion(producto.id) == ["existencia heredada"]
    auditoria = consultar(e.ruta, "SELECT resumen FROM auditoria WHERE accion = 'PRODUCTO_BAJA'")
    assert "baja lógica" in auditoria[0][0]


class TestVentasYAnulaciones:
    @pytest.fixture
    def yogur(self, e):
        """3 vencidas (costo 110), 4 vigentes con fecha (120), 2 heredadas (sin costo): FEFO = vigente, heredada, vencida."""
        producto = _heredado("1", 2, costo=100)
        servicio_compras.registrar_compra(e.prov.id, e.owner.id, [ItemCompra(producto.id, 3, 110, (PartidaIngreso(3, _dia(1)),))])
        servicio_compras.registrar_compra(e.prov.id, e.owner.id, [ItemCompra(producto.id, 4, 120, (PartidaIngreso(4, _dia(20)),))])
        vencida = next(p for p in servicio_partidas.listar_partidas(producto.id) if p.fecha_vencimiento == _dia(1))
        with obtener_conexion() as conexion:  # "pasa el tiempo": la de 1 día ya venció (saltando la guardia)
            trigger = conexion.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'trg_partidas_stock_origen_inmutable'"
            ).fetchone()[0]
            conexion.execute("DROP TRIGGER trg_partidas_stock_origen_inmutable")
            conexion.execute("UPDATE partidas_stock SET fecha_vencimiento = ? WHERE id = ?", (_dia(-1), vencida.id))
            conexion.execute(trigger)
        return producto

    def test_lineas_separadas_que_solo_juntas_tocan_lo_vencido_piden_autorizacion(self, e, yogur):
        with pytest.raises(VentaRequiereAutorizacionVencidosError, match=r"\(1 u.\)"):
            servicio_ventas.registrar_venta([ItemVenta(yogur.id, 4), ItemVenta(yogur.id, 3)], "EFECTIVO", usuario_id=e.owner.id)
        assert consultar(e.ruta, "SELECT COUNT(*) FROM movimientos_partida WHERE detalle_venta_id IS NOT NULL")[0][0] == 0

    def test_costo_real_de_lo_vendido_y_anulacion_exacta_e_idempotente(self, e, yogur):
        antes = _partidas(yogur.id)
        venta = servicio_ventas.registrar_venta([ItemVenta(yogur.id, 8)], "EFECTIVO", usuario_id=e.owner.id,
                                                autorizado_por_id=e.owner.id)
        consumos = [(c.origen, c.cantidad, c.costo_unitario_centavos) for c in servicio_partidas.consumos_de_venta(venta.id)]
        assert consumos == [("COMPRA", 4, 120), ("HEREDADA", 2, None), ("COMPRA", 2, 110)]
        # detalle_venta conserva el costo vigente (referencia), distinto del costo real del libro
        assert consultar(e.ruta, "SELECT costo_unitario_centavos FROM detalle_venta")[0][0] == 120

        servicio_ventas.anular_venta(venta.id, "ERROR_CARGA", None, e.owner.id)
        assert _partidas(yogur.id) == antes
        assert {f[0] for f in consultar(e.ruta, "SELECT criterio FROM movimientos_partida WHERE delta > 0")} == {"REVERSION"}
        with pytest.raises(VentaYaAnuladaError):
            servicio_ventas.anular_venta(venta.id, "ERROR_CARGA", None, e.owner.id)
        assert _partidas(yogur.id) == antes
