"""Pedidos de clientes (migración 051): tomar sin tocar stock ni caja, listar, cancelar con motivo, abrir el
POS precargado y convertir en venta una sola vez (atómico con la venta, idempotente ante reintentos)."""

import sqlite3
from datetime import date, timedelta

import pytest

import db.conexion as modulo_conexion
from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from domain.pedido import ItemPedido
from domain.venta import ItemVenta
from excepciones import (
    ClaveIdempotenciaReutilizadaError,
    DatosInvalidosError,
    ErrorBaseDatos,
    PedidoNoEncontradoError,
    PedidoNoPendienteError,
    StockInsuficienteError,
)
from services import servicio_caja, servicio_clientes, servicio_pedidos, servicio_stock, servicio_ventas
from tests.test_interfaces_web._asgi_cliente import solicitud
from tests.utilidades_clientes import crear_usuario, usuario_logueado


def crear_cajera():
    return crear_usuario("cajera_pedidos", "CASHIER")


@pytest.fixture
def e(caja_abierta):
    owner, cookies = usuario_logueado("OWNER", "duenio")
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 250, stock_actual=10)
    otro = servicio_stock.registrar_producto("7790000000002", "Gaseosa", 300, 500, stock_actual=2)
    cliente = servicio_clientes.crear_cliente("Ana", owner.id, telefono="11-5555")
    return type("E", (), {"owner": owner, "cookies": cookies, "p": producto, "p2": otro, "cliente": cliente})


def _stock(producto_id):
    with obtener_conexion() as conexion:
        return conexion.execute("SELECT stock_actual FROM productos WHERE id = ?", (producto_id,)).fetchone()[0]


def _tomar(e, **extra):
    datos = {"usuario_id": e.owner.id, "items": [ItemPedido(e.p.id, 3), ItemPedido(e.p2.id, 1)], "cliente_id": e.cliente.id}
    datos.update(extra)
    return servicio_pedidos.registrar_pedido(**datos)


class TestTomar:
    def test_tomar_no_mueve_stock_ni_caja_y_congela_precios_como_referencia(self, e):
        esperado = servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos
        pedido = _tomar(e, notas="  pasa el viernes  ", fecha_entrega=(date.today() + timedelta(days=2)).isoformat())
        assert pedido.pendiente and pedido.cliente_nombre == "Ana" and pedido.notas == "pasa el viernes"
        assert pedido.cantidad_lineas == 2 and pedido.total_al_pedir_centavos == 3 * 250 + 500
        assert _stock(e.p.id) == 10 and _stock(e.p2.id) == 2
        assert servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos == esperado
        lineas = servicio_pedidos.listar_lineas(pedido.id)
        assert [(l.producto_nombre, l.cantidad, l.precio_unitario_centavos) for l in lineas] == [("Alfajor", 3, 250), ("Gaseosa", 1, 500)]
        assert "Pedido #1 para Ana" in repositorio_auditoria.listar(accion="PEDIDO_TOMADO")[0].resumen

    def test_sin_cliente_exige_contacto_y_acepta_pedido_anonimo(self, e):
        with pytest.raises(DatosInvalidosError, match="para quién"):
            _tomar(e, cliente_id=None)
        pedido = _tomar(e, cliente_id=None, contacto_nombre="Juan", contacto_telefono="11-1")
        assert pedido.para_quien == "Juan" and pedido.cliente_id is None

    @pytest.mark.parametrize(
        "extra, fragmento",
        [
            ({"items": []}, "al menos un producto"),
            ({"items": [ItemPedido(1, 1), ItemPedido(1, 2)]}, "más de una vez"),
            ({"fecha_entrega": "2000-01-01"}, "anterior a hoy"),
            ({"fecha_entrega": "2026-13-01"}, "no es válida"),
            ({"items": [ItemPedido(999, 1)]}, "No existe un producto"),
        ],
    )
    def test_datos_invalidos_no_escriben(self, e, extra, fragmento):
        with pytest.raises(Exception, match=fragmento):
            _tomar(e, **extra)
        with obtener_conexion() as conexion:
            assert conexion.execute("SELECT COUNT(*) FROM pedidos").fetchone()[0] == 0

    def test_cantidad_cero_se_rechaza_en_el_dominio(self):
        with pytest.raises(DatosInvalidosError):
            ItemPedido(1, 0)

    def test_reenvio_con_la_misma_clave_no_duplica(self, e):
        a = _tomar(e, clave_idempotencia="k1")
        b = _tomar(e, clave_idempotencia="k1")
        assert a.id == b.id
        with pytest.raises(ClaveIdempotenciaReutilizadaError):
            _tomar(e, clave_idempotencia="k1", items=[ItemPedido(e.p.id, 9)])

    def test_stock_insuficiente_no_impide_tomar_el_pedido(self, e):
        pedido = _tomar(e, items=[ItemPedido(e.p2.id, 50)])
        linea = servicio_pedidos.listar_lineas(pedido.id)[0]
        assert linea.sin_stock_suficiente


class TestCancelar:
    def test_cancelar_con_motivo_y_no_dos_veces(self, e):
        pedido = _tomar(e)
        with pytest.raises(DatosInvalidosError, match="motivo"):
            servicio_pedidos.cancelar_pedido(pedido.id, usuario_id=e.owner.id, motivo=" ")
        cancelado = servicio_pedidos.cancelar_pedido(pedido.id, usuario_id=e.owner.id, motivo="No lo retiró")
        assert cancelado.estado == "CANCELADO" and cancelado.cancelado_por_nombre == "Duenio"
        with pytest.raises(PedidoNoPendienteError):
            servicio_pedidos.cancelar_pedido(pedido.id, usuario_id=e.owner.id, motivo="otra vez")
        with pytest.raises(PedidoNoEncontradoError):
            servicio_pedidos.cancelar_pedido(999, usuario_id=e.owner.id, motivo="x")
        assert _stock(e.p.id) == 10

    def test_la_base_protege_los_estados_terminales_y_las_lineas(self, e):
        pedido = _tomar(e)
        servicio_pedidos.cancelar_pedido(pedido.id, usuario_id=e.owner.id, motivo="Error")
        with pytest.raises(ErrorBaseDatos, match="no se modifica"):
            with obtener_conexion() as conexion:
                conexion.execute("UPDATE pedidos SET estado = 'PENDIENTE', cancelado_en = NULL, cancelado_por_usuario_id = NULL, motivo_cancelacion = NULL WHERE id = ?", (pedido.id,))
        with pytest.raises(ErrorBaseDatos, match="no se borra"):
            with obtener_conexion() as conexion:
                conexion.execute("DELETE FROM pedidos WHERE id = ?", (pedido.id,))
        with pytest.raises(ErrorBaseDatos, match="no se modifican"):
            with obtener_conexion() as conexion:
                conexion.execute("UPDATE pedido_lineas SET cantidad = 99 WHERE pedido_id = ?", (pedido.id,))


class TestConversion:
    def test_la_venta_con_pedido_id_lo_deja_convertido_en_la_misma_transaccion(self, e):
        pedido = _tomar(e)
        venta = servicio_ventas.registrar_venta([ItemVenta(e.p.id, 3), ItemVenta(e.p2.id, 1)], "EFECTIVO", usuario_id=e.owner.id, cliente_id=e.cliente.id, pedido_id=pedido.id)
        convertido = servicio_pedidos.obtener_pedido(pedido.id)
        assert convertido.estado == "CONVERTIDO" and convertido.venta_id == venta.id and convertido.convertido_en
        assert _stock(e.p.id) == 7 and _stock(e.p2.id) == 1
        assert f"convertido en la venta #{venta.id}" in repositorio_auditoria.listar(accion="PEDIDO_CONVERTIDO")[0].resumen

    def test_nunca_dos_ventas_por_el_mismo_pedido(self, e):
        pedido = _tomar(e)
        servicio_ventas.registrar_venta([ItemVenta(e.p.id, 1)], "EFECTIVO", usuario_id=e.owner.id, pedido_id=pedido.id)
        with pytest.raises(PedidoNoPendienteError, match="no se puede vender dos veces"):
            servicio_ventas.registrar_venta([ItemVenta(e.p.id, 1)], "EFECTIVO", usuario_id=e.owner.id, pedido_id=pedido.id)
        assert _stock(e.p.id) == 9  # la segunda venta no existió
        with obtener_conexion() as conexion:
            assert conexion.execute("SELECT COUNT(*) FROM ventas").fetchone()[0] == 1

    def test_el_reintento_idempotente_de_la_venta_devuelve_la_misma_y_no_toca_el_pedido(self, e):
        pedido = _tomar(e)
        a = servicio_ventas.registrar_venta([ItemVenta(e.p.id, 1)], "EFECTIVO", clave_idempotencia="c1", usuario_id=e.owner.id, pedido_id=pedido.id)
        b = servicio_ventas.registrar_venta([ItemVenta(e.p.id, 1)], "EFECTIVO", clave_idempotencia="c1", usuario_id=e.owner.id, pedido_id=pedido.id)
        assert a.id == b.id and servicio_pedidos.obtener_pedido(pedido.id).venta_id == a.id

    def test_si_la_venta_falla_el_pedido_sigue_pendiente(self, e):
        pedido = _tomar(e)
        with pytest.raises(StockInsuficienteError):
            servicio_ventas.registrar_venta([ItemVenta(e.p2.id, 50)], "EFECTIVO", usuario_id=e.owner.id, pedido_id=pedido.id)
        assert servicio_pedidos.obtener_pedido(pedido.id).pendiente

    def test_un_pedido_cancelado_o_inexistente_no_se_vende(self, e):
        pedido = _tomar(e)
        servicio_pedidos.cancelar_pedido(pedido.id, usuario_id=e.owner.id, motivo="x")
        with pytest.raises(PedidoNoPendienteError):
            servicio_ventas.registrar_venta([ItemVenta(e.p.id, 1)], "EFECTIVO", usuario_id=e.owner.id, pedido_id=pedido.id)
        with pytest.raises(PedidoNoEncontradoError):
            servicio_ventas.registrar_venta([ItemVenta(e.p.id, 1)], "EFECTIVO", usuario_id=e.owner.id, pedido_id=999)
        assert _stock(e.p.id) == 10

    def test_precarga_del_pos_avisa_cambios_sin_modificar_nada(self, e):
        pedido = _tomar(e, items=[ItemPedido(e.p.id, 2), ItemPedido(e.p2.id, 5)])
        # Entre el pedido y la venta sube el precio del alfajor (250 → 300) y la gaseosa no alcanza.
        servicio_stock.actualizar_producto(e.p.id, e.p.codigo_barras, e.p.nombre, 100, 300, 0, usuario_id=e.owner.id)
        precarga = servicio_pedidos.preparar_precarga_pos(pedido.id)
        assert [i["producto_id"] for i in precarga.items_vendibles] == [e.p.id, e.p2.id]
        assert any("Alfajor: el precio cambió de $2,50 a $3,00" in a for a in precarga.avisos)
        assert any("Gaseosa: se pidieron 5 y hay 2" in a for a in precarga.avisos)
        assert servicio_pedidos.obtener_pedido(pedido.id).pendiente and _stock(e.p2.id) == 2
        # La venta cobra el precio vigente (no el congelado en el pedido) y valida el stock real.
        with pytest.raises(StockInsuficienteError):
            servicio_ventas.registrar_venta([ItemVenta(e.p.id, 2), ItemVenta(e.p2.id, 5)], "EFECTIVO", usuario_id=e.owner.id, pedido_id=pedido.id)
        venta = servicio_ventas.registrar_venta([ItemVenta(e.p.id, 2), ItemVenta(e.p2.id, 2)], "EFECTIVO", usuario_id=e.owner.id, pedido_id=pedido.id)
        assert venta.total_centavos == 2 * 300 + 2 * 500
        assert servicio_pedidos.obtener_pedido(pedido.id).venta_id == venta.id
        assert [l.precio_unitario_centavos for l in servicio_pedidos.listar_lineas(pedido.id)] == [250, 500]  # lo pedido no se reescribe

    def test_cashier_toma_cancela_y_vende_y_la_venta_queda_del_cliente(self, e):
        cajera = crear_cajera()
        pedido = servicio_pedidos.registrar_pedido(usuario_id=cajera.id, items=[ItemPedido(e.p.id, 1)], cliente_id=e.cliente.id)
        venta = servicio_ventas.registrar_venta([ItemVenta(e.p.id, 1)], "EFECTIVO", usuario_id=cajera.id, cliente_id=e.cliente.id, pedido_id=pedido.id)
        assert servicio_pedidos.obtener_pedido(pedido.id).estado == "CONVERTIDO"
        with obtener_conexion() as conexion:
            assert conexion.execute("SELECT cliente_id FROM ventas WHERE id = ?", (venta.id,)).fetchone()[0] == e.cliente.id
        otro = servicio_pedidos.registrar_pedido(usuario_id=cajera.id, items=[ItemPedido(e.p.id, 1)], contacto_nombre="Juan")
        assert servicio_pedidos.cancelar_pedido(otro.id, usuario_id=cajera.id, motivo="No vino").estado == "CANCELADO"


class TestWeb:
    def _crear_por_http(self, e, cookies, **extra):
        datos = {"cliente_id": str(e.cliente.id), "contacto_nombre": "", "contacto_telefono": "", "notas": "sin azúcar", "fecha_entrega": "", "producto_id": [str(e.p.id)], "cantidad": ["2"], "clave_idempotencia": "k-http"}
        datos.update(extra)
        return solicitud("POST", "/pedidos/nuevo", cookies=cookies, formulario=datos)

    def test_ambos_roles_toman_listan_ven_y_venden_pedidos(self, e):
        _, cajero = usuario_logueado("CASHIER", "cajera")
        assert 'name="producto_id"' in solicitud("GET", "/pedidos/nuevo", cookies=cajero).texto
        respuesta = self._crear_por_http(e, cajero)
        assert respuesta.status == 303 and respuesta.header("location").startswith("/pedidos/1?tipo=success")
        listado = solicitud("GET", "/pedidos", cookies=cajero).texto
        assert 'data-pedido="1"' in listado and "1 pedido(s) pendiente(s)" in listado and 'href="/pedidos"' in listado
        detalle = solicitud("GET", "/pedidos/1", cookies=cajero).texto
        assert "sin azúcar" in detalle and 'id="boton-vender-pedido"' in detalle and "El stock no está reservado" in detalle
        assert _stock(e.p.id) == 10

        pos = solicitud("GET", "/pedidos/1/vender", cookies=cajero).texto
        assert 'id="pedido-precarga"' in pos and "Vendiendo el pedido #1 de Ana" in pos and "pedido_id&#34;: 1" in pos
        venta = solicitud("POST", "/api/ventas", cookies=cajero, json_body={"items": [{"producto_id": e.p.id, "cantidad": 2}], "tipo_pago": "EFECTIVO", "clave_idempotencia": "v-1", "pedido_id": 1, "cliente_id": e.cliente.id})
        assert venta.status == 200
        detalle = solicitud("GET", "/pedidos/1", cookies=cajero).texto
        assert "Convertido en venta" in detalle and 'id="enlace-venta-pedido"' in detalle
        assert _stock(e.p.id) == 8
        # Vender de nuevo: redirección con error, nada cambia.
        assert solicitud("GET", "/pedidos/1/vender", cookies=cajero).header("location").startswith("/pedidos/1?tipo=error")
        segunda = solicitud("POST", "/api/ventas", cookies=cajero, json_body={"items": [{"producto_id": e.p.id, "cantidad": 1}], "tipo_pago": "EFECTIVO", "clave_idempotencia": "v-2", "pedido_id": 1})
        assert segunda.status >= 400 and _stock(e.p.id) == 8

    def test_cancelar_por_http_y_filtros(self, e):
        self._crear_por_http(e, e.cookies)
        self._crear_por_http(e, e.cookies, clave_idempotencia="k2", cliente_id="", contacto_nombre="Beto")
        assert 'name="motivo"' in solicitud("GET", "/pedidos/2/cancelar", cookies=e.cookies).texto
        assert solicitud("POST", "/pedidos/2/cancelar", cookies=e.cookies, formulario={"motivo": "No vino"}).status == 303
        pendientes = solicitud("GET", "/pedidos?estado=PENDIENTE", cookies=e.cookies).texto
        assert 'data-pedido="1"' in pendientes and 'data-pedido="2"' not in pendientes
        por_texto = solicitud("GET", "/pedidos?q=Beto", cookies=e.cookies).texto
        assert 'data-pedido="2"' in por_texto and 'data-pedido="1"' not in por_texto
        assert solicitud("GET", "/pedidos?estado=RARO", cookies=e.cookies).status < 500
        assert solicitud("GET", "/pedidos/999", cookies=e.cookies).header("location").startswith("/pedidos?tipo=error")
        assert solicitud("GET", "/pedidos/2/cancelar", cookies=e.cookies).header("location").startswith("/pedidos/2?tipo=error")

    def test_sin_sesion_redirige(self, e):
        assert solicitud("GET", "/pedidos").status == 303


def test_la_migracion_051_crea_las_tablas_vacias_sin_tocar_lo_demas(tmp_path, monkeypatch):
    ruta = tmp_path / "050.db"
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
    con = sqlite3.connect(ruta)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(modulo_conexion._TABLA_MIGRACIONES)
    for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
        if migracion.name < "051_pedidos_clientes.sql":
            con.executescript(migracion.read_text(encoding="utf-8"))
            con.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (migracion.name,))
    con.execute("INSERT INTO usuarios (nombre_usuario, nombre_completo, password_hash, rol) VALUES ('a', 'A', 'h', 'OWNER')")
    con.commit()
    tablas_antes = {f[0] for f in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    con.close()
    modulo_conexion.inicializar_base_datos()
    modulo_conexion.inicializar_base_datos()
    con = sqlite3.connect(ruta)
    try:
        tablas = {f[0] for f in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert tablas == tablas_antes | {"pedidos", "pedido_lineas"}
        assert con.execute("SELECT COUNT(*) FROM pedidos").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM schema_migraciones WHERE nombre_archivo = '051_pedidos_clientes.sql'").fetchone()[0] == 1
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        con.close()
