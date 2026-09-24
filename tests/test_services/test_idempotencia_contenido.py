"""Auditoría post-V1.1: una clave de idempotencia solo devuelve el resultado
original si el pedido es el mismo. Reenviar la clave con otros datos (p. ej.
"atrás" en el navegador, cambiar el monto y enviar) se rechaza en vez de
dar por registrado algo que no se registró. También: el reintento de un
movimiento de caja ya registrado funciona aunque la caja se haya cerrado."""

import pytest

from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from domain.compra import ItemCompra
from domain.usuario import Usuario
from excepciones import ClaveIdempotenciaReutilizadaError
from services import servicio_caja, servicio_compras, servicio_proveedores, servicio_stock


def _owner():
    return repositorio_usuarios.crear_usuario(
        Usuario(nombre_usuario="ana", nombre_completo="Ana", password_hash="hash-de-prueba", rol="OWNER")
    )


def _contar(tabla: str) -> int:
    with obtener_conexion() as conexion:
        return conexion.execute(f"SELECT COUNT(*) AS n FROM {tabla}").fetchone()["n"]


class TestCajaContenidoDistinto:
    def test_egreso_con_otro_monto_y_la_misma_clave_se_rechaza(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        servicio_caja.registrar_egreso(2_000, "pago", clave_idempotencia="k")

        with pytest.raises(ClaveIdempotenciaReutilizadaError):
            servicio_caja.registrar_egreso(3_000, "pago", clave_idempotencia="k")

        assert servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos == 98_000

    def test_ingreso_con_otra_descripcion_y_la_misma_clave_se_rechaza(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        servicio_caja.registrar_ingreso(500, "cambio", clave_idempotencia="k")

        with pytest.raises(ClaveIdempotenciaReutilizadaError):
            servicio_caja.registrar_ingreso(500, "otra cosa", clave_idempotencia="k")

    def test_la_misma_clave_no_sirve_para_ingreso_y_egreso_a_la_vez(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        servicio_caja.registrar_ingreso(500, "x", clave_idempotencia="k")

        with pytest.raises(ClaveIdempotenciaReutilizadaError):
            servicio_caja.registrar_egreso(500, "x", clave_idempotencia="k")

    def test_el_reintento_de_un_movimiento_registrado_devuelve_el_original_aunque_se_cerro_la_caja(
        self, base_datos_temporal
    ):
        servicio_caja.abrir_caja(100_000)
        original = servicio_caja.registrar_egreso(2_000, "pago", clave_idempotencia="k")
        servicio_caja.cerrar_caja(98_000)

        reintento = servicio_caja.registrar_egreso(2_000, "pago", clave_idempotencia="k")

        assert reintento.id == original.id

    def test_un_egreso_nuevo_con_la_caja_cerrada_sigue_rechazado(self, base_datos_temporal):
        from excepciones import CajaError

        with pytest.raises(CajaError):
            servicio_caja.registrar_egreso(2_000, "pago", clave_idempotencia="nueva")


class TestAjusteContenidoDistinto:
    def test_ajuste_con_otra_cantidad_y_la_misma_clave_se_rechaza_sin_tocar_stock(self, base_datos_temporal):
        owner = _owner()
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        servicio_stock.ajustar_stock(producto.id, -2, "MERMA", owner.id, clave_idempotencia="k")

        with pytest.raises(ClaveIdempotenciaReutilizadaError):
            servicio_stock.ajustar_stock(producto.id, -5, "MERMA", owner.id, clave_idempotencia="k")

        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 8
        assert _contar("ajustes_stock") == 1

    def test_ajuste_con_otro_motivo_y_la_misma_clave_se_rechaza(self, base_datos_temporal):
        owner = _owner()
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        servicio_stock.ajustar_stock(producto.id, -2, "MERMA", owner.id, clave_idempotencia="k")

        with pytest.raises(ClaveIdempotenciaReutilizadaError):
            servicio_stock.ajustar_stock(producto.id, -2, "ROTURA", owner.id, clave_idempotencia="k")


class TestCompraContenidoDistinto:
    def _preparar(self):
        proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=5)
        return proveedor, _owner(), producto

    def test_compra_con_otra_cantidad_y_la_misma_clave_se_rechaza(self, base_datos_temporal):
        proveedor, owner, producto = self._preparar()
        servicio_compras.registrar_compra(
            proveedor.id, owner.id, [ItemCompra(producto.id, 10, 120)], clave_idempotencia="k"
        )

        with pytest.raises(ClaveIdempotenciaReutilizadaError):
            servicio_compras.registrar_compra(
                proveedor.id, owner.id, [ItemCompra(producto.id, 99, 120)], clave_idempotencia="k"
            )

        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 15
        assert _contar("compras") == 1

    def test_compra_con_otro_costo_y_la_misma_clave_se_rechaza(self, base_datos_temporal):
        proveedor, owner, producto = self._preparar()
        servicio_compras.registrar_compra(
            proveedor.id, owner.id, [ItemCompra(producto.id, 10, 120)], clave_idempotencia="k"
        )

        with pytest.raises(ClaveIdempotenciaReutilizadaError):
            servicio_compras.registrar_compra(
                proveedor.id, owner.id, [ItemCompra(producto.id, 10, 130)], clave_idempotencia="k"
            )

    def test_el_reintento_identico_con_items_en_otro_orden_es_el_mismo_pedido(self, base_datos_temporal):
        proveedor, owner, producto_1 = self._preparar()
        producto_2 = servicio_stock.registrar_producto("7790000000002", "Gaseosa", 300, 500, stock_actual=1)
        items = [ItemCompra(producto_1.id, 10, 120), ItemCompra(producto_2.id, 4, 350)]
        original = servicio_compras.registrar_compra(proveedor.id, owner.id, items, clave_idempotencia="k")

        reintento = servicio_compras.registrar_compra(
            proveedor.id, owner.id, list(reversed(items)), clave_idempotencia="k"
        )

        assert reintento.id == original.id
        assert _contar("compras") == 1
