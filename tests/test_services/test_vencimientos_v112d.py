"""V1.12-D: alertas de vencimiento (10 días por defecto, configurable por OWNER), filtros del listado,
baja por vencimiento desde la partida con su costo real, y venta de mercadería vencida solo con
autorización auditada de un OWNER (el CASHIER no puede ignorar la advertencia solo)."""

from datetime import date, timedelta

import pytest

from domain.compra import ItemCompra, PartidaIngreso
from domain.partida import Partida, estado_de_vencimiento, resumir_vencimientos, validar_dias_alerta
from domain.venta import ItemVenta
from excepciones import DatosInvalidosError, PermisoDenegadoError, VentaRequiereAutorizacionVencidosError
from services import (
    servicio_compras,
    servicio_configuracion,
    servicio_partidas,
    servicio_proveedores,
    servicio_stock,
    servicio_ventas,
)
from tests.utilidades_clientes import conectar, consultar, usuario_logueado

from tests.test_interfaces_web._asgi_cliente import solicitud

pytestmark = pytest.mark.usefixtures("caja_abierta")

HOY = date.today()


def _dia(dias: int) -> str:
    return (HOY + timedelta(days=dias)).isoformat()


def _vencer_partida(ruta, partida_id, fecha):
    """Hace "pasar el tiempo": una partida con vencimiento futuro queda vencida (fecha en el pasado). La
    aplicación nunca modifica un vencimiento; acá se salta esa guardia solo para fabricar el escenario."""
    conexion = conectar(ruta)
    try:
        trigger = conexion.execute("SELECT sql FROM sqlite_master WHERE name = 'trg_partidas_stock_origen_inmutable'").fetchone()[0]
        conexion.execute("DROP TRIGGER trg_partidas_stock_origen_inmutable")
        conexion.execute("UPDATE partidas_stock SET fecha_vencimiento = ? WHERE id = ?", (fecha, partida_id))
        conexion.execute(trigger)
        conexion.commit()
    finally:
        conexion.close()


@pytest.fixture
def e(base_datos_temporal):
    owner, co = usuario_logueado("OWNER", "duenio")
    cajero, ca = usuario_logueado("CASHIER", "cajera")
    prov = servicio_proveedores.crear_proveedor("Lácteos")
    yogur = servicio_stock.registrar_producto("1", "Yogur", 100, 300)
    servicio_compras.registrar_compra(prov.id, owner.id, [ItemCompra(yogur.id, 10, 120, (
        PartidaIngreso(4, _dia(30)), PartidaIngreso(6, _dia(5)),
    ))])
    vencida, vigente = sorted(servicio_partidas.listar_partidas(yogur.id), key=lambda p: p.fecha_vencimiento)
    _vencer_partida(base_datos_temporal, vencida.id, _dia(-1))  # las 6 que vencían en 5 días ya vencieron
    yield type("E", (), {"ruta": base_datos_temporal, "owner": owner, "co": co, "cajero": cajero, "ca": ca,
                          "prov": prov, "p": yogur, "vencida": vencida, "vigente": vigente})
    assert servicio_partidas.productos_no_conciliados() == []


class TestReglas:
    def test_estados(self):
        assert estado_de_vencimiento(_dia(-1), HOY.isoformat(), 10) == "VENCIDO"
        assert estado_de_vencimiento(_dia(0), HOY.isoformat(), 10) == "PROXIMO_A_VENCER"
        assert estado_de_vencimiento(_dia(10), HOY.isoformat(), 10) == "PROXIMO_A_VENCER"
        assert estado_de_vencimiento(_dia(11), HOY.isoformat(), 10) is None
        assert estado_de_vencimiento(None, HOY.isoformat(), 10) is None

    def test_resumen_por_producto(self):
        hoy = HOY.isoformat()
        partidas = [Partida(1, 1, "COMPRA", 5, 5, _dia(-2), None, hoy), Partida(2, 1, "COMPRA", 3, 3, _dia(4), None, hoy),
                    Partida(3, 1, "COMPRA", 2, 2, _dia(4), None, hoy), Partida(4, 1, "HEREDADA", 7, 7, None, None, None)]
        resumen = resumir_vencimientos(partidas, hoy, 10)
        assert (resumen.proximo_vencimiento, resumen.cantidad_proxima, resumen.cantidad_vencida,
                resumen.cantidad_sin_vencimiento, resumen.estado) == (_dia(4), 5, 5, 7, "VENCIDO")

    @pytest.mark.parametrize("texto", ["-1", "366", "x", ""])
    def test_dias_invalidos(self, texto):
        with pytest.raises(DatosInvalidosError):
            validar_dias_alerta(texto)


def test_dias_de_alerta_por_defecto_y_configurables_auditados(e):
    assert servicio_configuracion.obtener_dias_alerta_vencimiento() == 10
    assert servicio_configuracion.guardar_dias_alerta_vencimiento("3", e.owner.id) == 3
    assert servicio_configuracion.obtener_dias_alerta_vencimiento() == 3
    resumenes = [f[0] for f in consultar(e.ruta, "SELECT resumen FROM auditoria WHERE accion = 'CONFIGURACION_VENCIMIENTOS_CAMBIADA'")]
    assert resumenes == ["Alerta de vencimiento: 10 -> 3 días"]
    respuesta = solicitud("POST", "/configuracion/vencimientos", cookies=e.ca, formulario={"dias_alerta": "1"})
    assert respuesta.status == 403 and servicio_configuracion.obtener_dias_alerta_vencimiento() == 3


def test_filtros_del_listado_por_vencimiento(e):
    servicio_stock.registrar_producto("2", "Galletas", 50, 90, stock_actual=5)
    nombres = lambda filtro: [p.nombre for p in servicio_stock.listar_para_administracion(vencimiento=filtro)]  # noqa: E731
    assert nombres("vencido") == ["Yogur"]
    assert nombres("sin_fecha") == ["Galletas"]
    assert nombres("proximo") == []  # lo vigente vence en 30 días, fuera de los 10
    servicio_configuracion.guardar_dias_alerta_vencimiento("30", e.owner.id)
    servicio_stock.ajustar_stock(e.p.id, -6, "VENCIMIENTO", e.owner.id, partida_id=e.vencida.id)
    assert nombres("proximo") == ["Yogur"] and nombres("vencido") == []
    html = solicitud("GET", "/productos?vencimiento=proximo", cookies=e.co).texto
    assert ">Yogur</p>" in html and ">Galletas</p>" not in html and 'data-vencimiento="PROXIMO_A_VENCER"' in html


def test_baja_por_vencimiento_desde_la_partida_con_su_costo_real(e):
    respuesta = solicitud("POST", f"/productos/{e.p.id}/vencimientos/{e.vencida.id}/baja", cookies=e.co,
                          formulario={"cantidad": "6", "clave_idempotencia": "k-baja"})
    assert "tipo=success" in (respuesta.header("location") or "")
    ajuste = consultar(e.ruta, "SELECT id, motivo, delta, costo_unitario_centavos FROM ajustes_stock WHERE motivo = 'VENCIMIENTO'")
    assert [tuple(f)[1:] for f in ajuste] == [("VENCIMIENTO", -6, 120)]
    assert [tuple(f) for f in consultar(e.ruta, "SELECT partida_id, delta, criterio FROM movimientos_partida WHERE ajuste_id = ?",
                                        (ajuste[0][0],))] == [(e.vencida.id, -6, "ELEGIDA")]
    # reenvío: no se aplica dos veces
    solicitud("POST", f"/productos/{e.p.id}/vencimientos/{e.vencida.id}/baja", cookies=e.co,
              formulario={"cantidad": "6", "clave_idempotencia": "k-baja"})
    assert servicio_stock.obtener_por_id(e.p.id).stock_actual == 4


def test_baja_por_vencimiento_con_varias_partidas_exige_elegir(e):
    with pytest.raises(DatosInvalidosError, match="elegí cuál venció"):
        servicio_stock.ajustar_stock(e.p.id, -1, "VENCIMIENTO", e.owner.id)


def test_baja_por_vencimiento_de_existencia_sin_costo_no_inventa_costo(e):
    from db.conexion import obtener_conexion
    from db.repositorios import productos as repositorio_productos
    from domain.producto import Producto
    from tests.utilidades_clientes import heredar_existencia_en_conexion

    with obtener_conexion() as conexion:
        viejo = repositorio_productos.crear_producto_en_conexion(conexion, Producto("9", "Viejo", 77, 90, stock_actual=3))
        heredar_existencia_en_conexion(conexion, viejo.id, 3)
    ajuste = servicio_stock.ajustar_stock(viejo.id, -1, "VENCIMIENTO", e.owner.id)  # única partida: se resuelve sola
    assert ajuste.costo_unitario_centavos is None  # nunca el costo actual (77)


def test_otra_perdida_eligiendo_partida_usa_su_costo(e):
    ajuste = servicio_stock.ajustar_stock(e.p.id, -1, "ROTURA", e.owner.id, partida_id=e.vigente.id)
    assert ajuste.costo_unitario_centavos == 120
    with pytest.raises(DatosInvalidosError, match="no corresponde"):
        otro = servicio_stock.registrar_producto("3", "Otro", 1, 2, stock_actual=1)
        servicio_stock.ajustar_stock(otro.id, -1, "ROTURA", e.owner.id, partida_id=e.vigente.id)


class TestVentaDeVencidos:
    def test_lo_vigente_alcanza_no_pide_nada(self, e):
        venta = servicio_ventas.registrar_venta([ItemVenta(e.p.id, 4)], "EFECTIVO", usuario_id=e.cajero.id)
        assert [c.partida_id for c in servicio_partidas.consumos_de_venta(venta.id)] == [e.vigente.id]

    def test_sin_autorizacion_no_se_vende_ni_se_toca_nada(self, e):
        with pytest.raises(VentaRequiereAutorizacionVencidosError, match="VENCIDA: Yogur \\(2 u.\\)"):
            servicio_ventas.registrar_venta([ItemVenta(e.p.id, 6)], "EFECTIVO", usuario_id=e.cajero.id)
        assert consultar(e.ruta, "SELECT COUNT(*) FROM ventas")[0][0] == 0
        assert servicio_stock.obtener_por_id(e.p.id).stock_actual == 10

    def test_un_cajero_no_puede_autorizarse_a_si_mismo(self, e):
        with pytest.raises(PermisoDenegadoError):
            servicio_ventas.registrar_venta([ItemVenta(e.p.id, 6)], "EFECTIVO", usuario_id=e.cajero.id,
                                            autorizado_por_id=e.cajero.id)

    def test_con_autorizacion_owner_queda_auditada(self, e):
        venta = servicio_ventas.registrar_venta([ItemVenta(e.p.id, 6)], "EFECTIVO", usuario_id=e.cajero.id,
                                                autorizado_por_id=e.owner.id)
        auditoria = consultar(e.ruta, "SELECT usuario_id, entidad_id, resumen FROM auditoria WHERE accion = 'VENTA_VENCIDOS_AUTORIZADA'")
        assert [(f[0], f[1]) for f in auditoria] == [(e.owner.id, venta.id)]
        assert "Yogur (2 u.)" in auditoria[0][2]

    def _api(self, e, cookies, **extra):
        cuerpo = {"items": [{"producto_id": e.p.id, "cantidad": 6}], "tipo_pago": "EFECTIVO", "clave_idempotencia": "k-api", **extra}
        return solicitud("POST", "/api/ventas", cookies=cookies, json_body=cuerpo)

    def test_api_devuelve_codigo_para_que_el_pos_pida_autorizacion(self, e):
        respuesta = self._api(e, e.ca)
        assert respuesta.status == 422 and respuesta.json()["codigo"] == "REQUIERE_AUTORIZACION_VENCIDOS"

    def test_api_cajero_con_credenciales_de_owner(self, e):
        assert self._api(e, e.ca, autorizar_vencidos=True).status == 422  # el flag solo no alcanza a un cajero
        malas = self._api(e, e.ca, autorizacion_usuario="duenio", autorizacion_password="mal")
        assert malas.status == 422 and consultar(e.ruta, "SELECT COUNT(*) FROM ventas")[0][0] == 0
        de_cajero = self._api(e, e.ca, autorizacion_usuario="cajera", autorizacion_password="clave-correcta-123")
        assert de_cajero.status == 403
        buena = self._api(e, e.ca, autorizacion_usuario="duenio", autorizacion_password="clave-correcta-123")
        assert buena.status == 200
        assert consultar(e.ruta, "SELECT usuario_id FROM ventas")[0][0] == e.cajero.id

    def test_api_owner_confirma(self, e):
        assert self._api(e, e.co, autorizar_vencidos=True).status == 200

    def test_pos_tiene_el_dialogo(self, e):
        html = solicitud("GET", "/ventas", cookies=e.ca).texto
        assert 'id="dialogo-vencidos"' in html and 'data-es-owner="false"' in html and 'id="vencidos-password"' in html


def test_pagina_de_vencimientos_del_producto(e):
    html = solicitud("GET", f"/productos/{e.p.id}/vencimientos", cookies=e.co).texto
    assert html.index(f'data-partida-id="{e.vigente.id}"') < html.index(f'data-partida-id="{e.vencida.id}"')  # FEFO
    assert 'data-estado="VENCIDO"' in html and "costo $1,20" in html
    assert solicitud("GET", f"/productos/{e.p.id}/vencimientos", cookies=e.ca).status == 403
