"""C3 (V1.1): un doble clic, doble submit o reintento con la misma clave de
idempotencia no duplica dinero, stock, compras ni ajustes -- ingreso y
egreso de caja, ajuste manual de stock y compra."""

import threading

import pytest

from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from domain.compra import ItemCompra
from domain.usuario import Usuario
from excepciones import ProductoNoEncontradoError
from services import servicio_caja, servicio_compras, servicio_proveedores, servicio_stock


def _contar(tabla: str) -> int:
    with obtener_conexion() as conexion:
        return conexion.execute(f"SELECT COUNT(*) AS n FROM {tabla}").fetchone()["n"]


def _crear_owner():
    return repositorio_usuarios.crear_usuario(
        Usuario(nombre_usuario="ana", nombre_completo="Ana", password_hash="hash-de-prueba", rol="OWNER")
    )


def _movimientos_manuales(tipo: str) -> int:
    with obtener_conexion() as conexion:
        return conexion.execute(
            "SELECT COUNT(*) AS n FROM caja_movimientos WHERE tipo = ?", (tipo,)
        ).fetchone()["n"]


class TestIngresoYEgresoDeCaja:
    def test_doble_ingreso_con_la_misma_clave_registra_uno_solo(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)

        primero = servicio_caja.registrar_ingreso(5_000, "cambio", clave_idempotencia="ing-1")
        segundo = servicio_caja.registrar_ingreso(5_000, "cambio", clave_idempotencia="ing-1")

        assert segundo.id == primero.id
        assert _movimientos_manuales("INGRESO") == 1
        assert servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos == 105_000

    def test_doble_egreso_con_la_misma_clave_registra_uno_solo(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)

        primero = servicio_caja.registrar_egreso(2_000, "pago", clave_idempotencia="egr-1")
        segundo = servicio_caja.registrar_egreso(2_000, "pago", clave_idempotencia="egr-1")

        assert segundo.id == primero.id
        assert _movimientos_manuales("EGRESO") == 1
        assert servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos == 98_000

    def test_claves_distintas_son_operaciones_distintas(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)

        servicio_caja.registrar_egreso(2_000, "pago", clave_idempotencia="egr-1")
        servicio_caja.registrar_egreso(2_000, "pago", clave_idempotencia="egr-2")

        assert _movimientos_manuales("EGRESO") == 2

    def test_sin_clave_no_hay_proteccion_y_sigue_funcionando_como_antes(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)

        servicio_caja.registrar_egreso(2_000, "pago")
        servicio_caja.registrar_egreso(2_000, "pago")

        assert _movimientos_manuales("EGRESO") == 2

    def test_dos_egresos_simultaneos_con_la_misma_clave_registran_uno_solo(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        barrera = threading.Barrier(2)
        resultados = []

        def egresar():
            barrera.wait()
            resultados.append(servicio_caja.registrar_egreso(2_000, "pago", clave_idempotencia="egr-1"))

        hilos = [threading.Thread(target=egresar) for _ in range(2)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join()

        assert len(resultados) == 2
        assert resultados[0].id == resultados[1].id
        assert _movimientos_manuales("EGRESO") == 1


class TestAjusteManualDeStock:
    def test_doble_ajuste_con_la_misma_clave_aplica_el_delta_una_sola_vez(self, base_datos_temporal):
        owner = _crear_owner()
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)

        primero = servicio_stock.ajustar_stock(
            producto.id, delta=-3, motivo="MERMA", usuario_id=owner.id, clave_idempotencia="aj-1"
        )
        segundo = servicio_stock.ajustar_stock(
            producto.id, delta=-3, motivo="MERMA", usuario_id=owner.id, clave_idempotencia="aj-1"
        )

        assert segundo.id == primero.id
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 7
        assert _contar("ajustes_stock") == 1

    def test_el_reintento_de_un_ajuste_ya_aplicado_no_falla_por_stock_insuficiente(self, base_datos_temporal):
        owner = _crear_owner()
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=3)
        servicio_stock.ajustar_stock(
            producto.id, delta=-3, motivo="MERMA", usuario_id=owner.id, clave_idempotencia="aj-1"
        )

        reintento = servicio_stock.ajustar_stock(
            producto.id, delta=-3, motivo="MERMA", usuario_id=owner.id, clave_idempotencia="aj-1"
        )

        assert reintento.delta == -3
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 0

    def test_dos_ajustes_simultaneos_con_la_misma_clave_aplican_uno_solo(self, base_datos_temporal):
        owner = _crear_owner()
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        barrera = threading.Barrier(2)

        def ajustar():
            barrera.wait()
            servicio_stock.ajustar_stock(
                producto.id, delta=-4, motivo="MERMA", usuario_id=owner.id, clave_idempotencia="aj-1"
            )

        hilos = [threading.Thread(target=ajustar) for _ in range(2)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join()

        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 6
        assert _contar("ajustes_stock") == 1

    def test_claves_distintas_aplican_cada_ajuste(self, base_datos_temporal):
        owner = _crear_owner()
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)

        servicio_stock.ajustar_stock(producto.id, -1, "MERMA", owner.id, clave_idempotencia="aj-1")
        servicio_stock.ajustar_stock(producto.id, -1, "MERMA", owner.id, clave_idempotencia="aj-2")

        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 8
        assert _contar("ajustes_stock") == 2


class TestCompra:
    def _preparar(self):
        proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
        owner = _crear_owner()
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=5)
        return proveedor, owner, producto

    def test_doble_compra_con_la_misma_clave_registra_una_sola(self, base_datos_temporal):
        proveedor, owner, producto = self._preparar()
        items = [ItemCompra(producto.id, 10, 120)]

        primera = servicio_compras.registrar_compra(proveedor.id, owner.id, items, clave_idempotencia="cmp-1")
        segunda = servicio_compras.registrar_compra(proveedor.id, owner.id, items, clave_idempotencia="cmp-1")

        assert segunda.id == primera.id
        assert _contar("compras") == 1
        assert _contar("detalle_compra") == 1
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 15  # 5 + 10, no 25

    def test_dos_compras_simultaneas_con_la_misma_clave_registran_una_sola(self, base_datos_temporal):
        proveedor, owner, producto = self._preparar()
        barrera = threading.Barrier(2)
        items = [ItemCompra(producto.id, 10, 120)]

        def comprar():
            barrera.wait()
            servicio_compras.registrar_compra(proveedor.id, owner.id, items, clave_idempotencia="cmp-1")

        hilos = [threading.Thread(target=comprar) for _ in range(2)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join()

        assert _contar("compras") == 1
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 15

    def test_claves_distintas_registran_compras_distintas(self, base_datos_temporal):
        proveedor, owner, producto = self._preparar()
        items = [ItemCompra(producto.id, 10, 120)]

        servicio_compras.registrar_compra(proveedor.id, owner.id, items, clave_idempotencia="cmp-1")
        servicio_compras.registrar_compra(proveedor.id, owner.id, items, clave_idempotencia="cmp-2")

        assert _contar("compras") == 2
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 25

    def test_una_compra_rechazada_no_consume_la_clave(self, base_datos_temporal):
        proveedor, owner, producto = self._preparar()

        with pytest.raises(ProductoNoEncontradoError):
            servicio_compras.registrar_compra(
                proveedor.id, owner.id, [ItemCompra(99999, 1, 100)], clave_idempotencia="cmp-1"
            )
        compra = servicio_compras.registrar_compra(
            proveedor.id, owner.id, [ItemCompra(producto.id, 10, 120)], clave_idempotencia="cmp-1"
        )

        assert compra.id is not None
        assert _contar("compras") == 1
