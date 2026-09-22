"""Pruebas de integración de services.servicio_ventas, con foco en que
`registrar_venta` sea realmente atómica: descuento de stock + alta de
venta y detalle, todo o nada (ver tests/conftest.py para la base de
datos temporal)."""

import sqlite3
import threading

import pytest

from db.repositorios import usuarios as repositorio_usuarios
from db.repositorios import ventas as repositorio_ventas
from domain.usuario import Usuario
from domain.venta import ItemVenta
from excepciones import (
    ClaveIdempotenciaReutilizadaError,
    DatosInvalidosError,
    ProductoNoEncontradoError,
    StockInsuficienteError,
)
from services import servicio_stock, servicio_ventas


def _contar_filas(ruta_base_datos, tabla: str) -> int:
    conexion = sqlite3.connect(str(ruta_base_datos))
    try:
        return conexion.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
    finally:
        conexion.close()


def _leer_ventas_y_costos(ruta_base_datos, venta_id: int):
    """`(usuario_id de la venta, [costo_unitario_centavos de cada línea, en orden de inserción])`."""
    conexion = sqlite3.connect(str(ruta_base_datos))
    try:
        usuario_id = conexion.execute(
            "SELECT usuario_id FROM ventas WHERE id = ?", (venta_id,)
        ).fetchone()[0]
        costos = [
            fila[0]
            for fila in conexion.execute(
                "SELECT costo_unitario_centavos FROM detalle_venta WHERE venta_id = ? ORDER BY id",
                (venta_id,),
            ).fetchall()
        ]
        return usuario_id, costos
    finally:
        conexion.close()


def _crear_usuario(nombre_usuario="cajera1", rol="CASHIER"):
    return repositorio_usuarios.crear_usuario(
        Usuario(nombre_usuario=nombre_usuario, nombre_completo="Test", password_hash="hash", rol=rol)
    )


def test_venta_exitosa_descuenta_stock_y_calcula_total_exacto(base_datos_temporal):
    producto = servicio_stock.registrar_producto(
        "7790000000001", "Alfajor", 100, 200, stock_actual=10, stock_minimo=2
    )

    venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 3)], "EFECTIVO")

    assert venta.total_centavos == 600  # 200 centavos x 3, aritmética entera exacta
    assert venta.tipo_pago == "EFECTIVO"

    producto_actualizado = servicio_stock.buscar_por_codigo_barras("7790000000001")
    assert producto_actualizado.stock_actual == 7


def test_venta_con_el_mismo_producto_en_varios_items_suma_las_cantidades(base_datos_temporal):
    producto = servicio_stock.registrar_producto(
        "7790000000001", "Alfajor", 100, 200, stock_actual=10, stock_minimo=2
    )

    venta = servicio_ventas.registrar_venta(
        [ItemVenta(producto.id, 2), ItemVenta(producto.id, 1)], "TARJETA"
    )

    assert venta.total_centavos == 600
    producto_actualizado = servicio_stock.buscar_por_codigo_barras("7790000000001")
    assert producto_actualizado.stock_actual == 7


def test_venta_con_stock_insuficiente_no_modifica_nada(base_datos_temporal):
    """Prueba crítica de rollback: nada se persiste si el stock no alcanza."""
    producto = servicio_stock.registrar_producto(
        "7790000000001", "Alfajor", 100, 200, stock_actual=2, stock_minimo=1
    )

    with pytest.raises(StockInsuficienteError):
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 5)], "EFECTIVO")

    # El stock del producto no cambió...
    producto_sin_cambios = servicio_stock.buscar_por_codigo_barras("7790000000001")
    assert producto_sin_cambios.stock_actual == 2

    # ...y no quedó ni la venta ni ningún detalle a medio persistir.
    assert _contar_filas(base_datos_temporal, "ventas") == 0
    assert _contar_filas(base_datos_temporal, "detalle_venta") == 0


def test_venta_con_un_item_sin_stock_no_descuenta_ningun_producto(base_datos_temporal):
    """La validación de stock corre para todos los ítems antes de tocar la
    base de datos: si cualquiera falla, ningún producto pierde stock,
    ni siquiera los que sí tenían stock suficiente."""
    producto_con_stock = servicio_stock.registrar_producto(
        "7790000000001", "Con stock", 100, 200, stock_actual=10, stock_minimo=1
    )
    producto_sin_stock = servicio_stock.registrar_producto(
        "7790000000002", "Sin stock", 100, 200, stock_actual=1, stock_minimo=1
    )

    with pytest.raises(StockInsuficienteError):
        servicio_ventas.registrar_venta(
            [ItemVenta(producto_con_stock.id, 5), ItemVenta(producto_sin_stock.id, 99)],
            "EFECTIVO",
        )

    assert servicio_stock.buscar_por_codigo_barras("7790000000001").stock_actual == 10
    assert servicio_stock.buscar_por_codigo_barras("7790000000002").stock_actual == 1
    assert _contar_filas(base_datos_temporal, "ventas") == 0


def test_venta_con_producto_inexistente_falla_y_no_persiste_nada(base_datos_temporal):
    with pytest.raises(ProductoNoEncontradoError):
        servicio_ventas.registrar_venta([ItemVenta(9999, 1)], "EFECTIVO")

    assert _contar_filas(base_datos_temporal, "ventas") == 0


def test_venta_sin_items_falla(base_datos_temporal):
    with pytest.raises(DatosInvalidosError):
        servicio_ventas.registrar_venta([], "EFECTIVO")


def test_venta_con_tipo_de_pago_invalido_no_toca_la_base(base_datos_temporal):
    producto = servicio_stock.registrar_producto(
        "7790000000001", "Alfajor", 100, 200, stock_actual=10, stock_minimo=1
    )

    with pytest.raises(DatosInvalidosError):
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "CRIPTO")

    assert servicio_stock.buscar_por_codigo_barras("7790000000001").stock_actual == 10
    assert _contar_filas(base_datos_temporal, "ventas") == 0


def test_dos_ventas_concurrentes_del_ultimo_stock_no_generan_lost_update(base_datos_temporal):
    """Reproduce con dos hilos y dos conexiones SQLite reales (nunca con
    monkeypatch) el escenario de dos POS vendiendo el último ítem del
    mismo producto casi al mismo tiempo (ver auditoría de concurrencia,
    Stage D: `services.servicio_auth` ya documenta que un mismo kiosco
    puede operarse desde más de una pestaña/POS a la vez).

    Antes de la corrección, el `SELECT` de stock corría sin ningún lock
    que lo protegiera (sqlite3 solo abre una transacción implícita
    recién antes de la primera escritura): las dos ventas podían leer
    el mismo stock y confirmarse las dos -- un "lost update" silencioso,
    sin ningún error, verificado empíricamente antes de esta corrección.

    Con `obtener_conexion(inmediata=True)`, las dos transacciones quedan
    serializadas: la primera vende y descuenta el único ítem; la
    segunda, ya detrás de la primera, vuelve a leer el stock (0) y es
    rechazada limpiamente por `StockInsuficienteError` -- nunca un error
    crudo de SQLite, nunca una segunda venta indebida.
    """
    producto = servicio_stock.registrar_producto(
        "7790000000001", "Alfajor", 100, 200, stock_actual=1, stock_minimo=0
    )

    barrera = threading.Barrier(2)
    resultados = {}

    def vender(nombre):
        barrera.wait()
        try:
            venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
            resultados[nombre] = ("OK", venta.id)
        except StockInsuficienteError:
            resultados[nombre] = ("RECHAZADA", None)

    hilo_a = threading.Thread(target=vender, args=("A",))
    hilo_b = threading.Thread(target=vender, args=("B",))
    hilo_a.start()
    hilo_b.start()
    hilo_a.join(timeout=10)
    hilo_b.join(timeout=10)

    assert not hilo_a.is_alive()
    assert not hilo_b.is_alive()

    resueltas = list(resultados.values())
    exitosas = [r for r in resueltas if r[0] == "OK"]
    rechazadas = [r for r in resueltas if r[0] == "RECHAZADA"]

    # Exactamente una vendió el único ítem disponible; la otra fue
    # rechazada con el error de negocio esperado -- nunca ambas
    # exitosas (lost update) ni ambas rechazadas.
    assert len(exitosas) == 1
    assert len(rechazadas) == 1

    # Stock final 0: nunca -1 (dos descuentos aplicados) ni 1 (ningún
    # descuento aplicado).
    producto_final = servicio_stock.buscar_por_codigo_barras("7790000000001")
    assert producto_final.stock_actual == 0

    # Ninguna venta quedó parcialmente registrada: exactamente una
    # venta persistida, con exactamente una línea de detalle.
    assert _contar_filas(base_datos_temporal, "ventas") == 1
    assert _contar_filas(base_datos_temporal, "detalle_venta") == 1

    # No queda ningún lock permanente: la base sigue operable después
    # de la contención.
    otro_producto = servicio_stock.registrar_producto("7790000000002", "Otro", 50, 100, stock_actual=5)
    assert otro_producto.id is not None


class TestIdempotencia:
    """Fase 5A: `registrar_venta(..., clave_idempotencia=...)` nunca debe
    duplicar una venta ante un reintento (doble envío, retry de red)."""

    def test_venta_con_clave_persiste_la_clave_y_su_hash(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)

        venta = servicio_ventas.registrar_venta(
            [ItemVenta(producto.id, 2)], "EFECTIVO", clave_idempotencia="clave-abc"
        )

        resultado = repositorio_ventas.obtener_por_clave_idempotencia("clave-abc")
        assert resultado is not None
        venta_guardada, _ = resultado
        assert venta_guardada.id == venta.id

    def test_reintento_con_mismo_key_y_mismo_contenido_devuelve_la_misma_venta(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)

        primera = servicio_ventas.registrar_venta(
            [ItemVenta(producto.id, 2)], "EFECTIVO", clave_idempotencia="clave-retry"
        )
        segunda = servicio_ventas.registrar_venta(
            [ItemVenta(producto.id, 2)], "EFECTIVO", clave_idempotencia="clave-retry"
        )

        assert segunda.id == primera.id
        assert _contar_filas(base_datos_temporal, "ventas") == 1
        assert _contar_filas(base_datos_temporal, "detalle_venta") == 1
        # el stock se descontó una sola vez (10 - 2 = 8, no 6)
        assert servicio_stock.buscar_por_codigo_barras("7790000000001").stock_actual == 8

    def test_reintento_con_items_en_otro_orden_sigue_siendo_el_mismo_contenido(self, base_datos_temporal):
        p1 = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        p2 = servicio_stock.registrar_producto("7790000000002", "Gaseosa", 100, 300, stock_actual=10)

        primera = servicio_ventas.registrar_venta(
            [ItemVenta(p1.id, 2), ItemVenta(p2.id, 1)], "EFECTIVO", clave_idempotencia="clave-orden"
        )
        segunda = servicio_ventas.registrar_venta(
            [ItemVenta(p2.id, 1), ItemVenta(p1.id, 2)], "EFECTIVO", clave_idempotencia="clave-orden"
        )

        assert segunda.id == primera.id
        assert _contar_filas(base_datos_temporal, "ventas") == 1

    def test_mismo_key_con_cantidad_distinta_falla_controlado(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 2)], "EFECTIVO", clave_idempotencia="clave-x")

        with pytest.raises(ClaveIdempotenciaReutilizadaError):
            servicio_ventas.registrar_venta([ItemVenta(producto.id, 3)], "EFECTIVO", clave_idempotencia="clave-x")

        assert _contar_filas(base_datos_temporal, "ventas") == 1
        assert servicio_stock.buscar_por_codigo_barras("7790000000001").stock_actual == 8  # solo la primera

    def test_mismo_key_con_tipo_de_pago_distinto_falla_controlado(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 2)], "EFECTIVO", clave_idempotencia="clave-y")

        with pytest.raises(ClaveIdempotenciaReutilizadaError):
            servicio_ventas.registrar_venta([ItemVenta(producto.id, 2)], "TARJETA", clave_idempotencia="clave-y")

        assert _contar_filas(base_datos_temporal, "ventas") == 1

    def test_venta_fallida_por_stock_no_reserva_la_clave(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=1)

        with pytest.raises(StockInsuficienteError):
            servicio_ventas.registrar_venta([ItemVenta(producto.id, 5)], "EFECTIVO", clave_idempotencia="clave-z")

        assert _contar_filas(base_datos_temporal, "ventas") == 0

        # Reintento con la misma clave y datos corregidos: debe poder registrarse.
        venta = servicio_ventas.registrar_venta(
            [ItemVenta(producto.id, 1)], "EFECTIVO", clave_idempotencia="clave-z"
        )
        assert venta.id is not None
        assert _contar_filas(base_datos_temporal, "ventas") == 1

    def test_sin_clave_sigue_funcionando_como_antes(self, base_datos_temporal):
        """Compatibilidad con el CLI y con cualquier llamado interno que
        no necesite protección de idempotencia."""
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)

        venta_1 = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        venta_2 = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")

        assert venta_1.id != venta_2.id
        assert _contar_filas(base_datos_temporal, "ventas") == 2

    def test_carrera_perdida_recupera_la_venta_del_ganador_sin_duplicar(self, base_datos_temporal, monkeypatch):
        """Simula que el chequeo previo de idempotencia no ve todavía la
        fila de otra transacción que ya comprometió (carrera real de
        escritura): fuerza a `registrar_venta` a intentar el INSERT de
        todas formas, que debe chocar contra el UNIQUE real de la base
        y recuperar la venta ganadora -- sin duplicar la venta ni
        descontar el stock dos veces."""
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)

        venta_ganadora = servicio_ventas.registrar_venta(
            [ItemVenta(producto.id, 2)], "EFECTIVO", clave_idempotencia="clave-carrera"
        )

        # Solo la PRIMERA llamada (el chequeo previo, dentro de la
        # transacción que va a intentar el INSERT) debe simular "todavía
        # no veo la fila del ganador". La lectura de RECUPERACIÓN tras el
        # conflicto también pasa por esta misma función (la usa
        # `obtener_por_clave_idempotencia`) y esa sí tiene que funcionar
        # de verdad -- si se mockeara siempre, nunca se podría recuperar
        # nada, que fue exactamente el bug que este comentario documenta
        # haber encontrado al escribir este test.
        original = repositorio_ventas.obtener_por_clave_idempotencia_en_conexion
        llamadas = {"n": 0}

        def chequeo_que_falla_solo_la_primera_vez(conexion, clave):
            llamadas["n"] += 1
            if llamadas["n"] == 1:
                return None
            return original(conexion, clave)

        monkeypatch.setattr(
            repositorio_ventas, "obtener_por_clave_idempotencia_en_conexion", chequeo_que_falla_solo_la_primera_vez
        )

        venta_recuperada = servicio_ventas.registrar_venta(
            [ItemVenta(producto.id, 2)], "EFECTIVO", clave_idempotencia="clave-carrera"
        )

        assert venta_recuperada.id == venta_ganadora.id
        assert _contar_filas(base_datos_temporal, "ventas") == 1
        assert servicio_stock.buscar_por_codigo_barras("7790000000001").stock_actual == 8  # una sola vez

    def test_carrera_perdida_con_contenido_distinto_falla_controlado(self, base_datos_temporal, monkeypatch):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)

        servicio_ventas.registrar_venta(
            [ItemVenta(producto.id, 2)], "EFECTIVO", clave_idempotencia="clave-carrera-2"
        )

        original = repositorio_ventas.obtener_por_clave_idempotencia_en_conexion
        llamadas = {"n": 0}

        def chequeo_que_falla_solo_la_primera_vez(conexion, clave):
            llamadas["n"] += 1
            if llamadas["n"] == 1:
                return None
            return original(conexion, clave)

        monkeypatch.setattr(
            repositorio_ventas, "obtener_por_clave_idempotencia_en_conexion", chequeo_que_falla_solo_la_primera_vez
        )

        with pytest.raises(ClaveIdempotenciaReutilizadaError):
            servicio_ventas.registrar_venta(
                [ItemVenta(producto.id, 3)], "EFECTIVO", clave_idempotencia="clave-carrera-2"
            )

        assert _contar_filas(base_datos_temporal, "ventas") == 1
        assert servicio_stock.buscar_por_codigo_barras("7790000000001").stock_actual == 8  # solo la ganadora


class TestHistoricoDeCostoYUsuario:
    """Migración 009: `registrar_venta` captura `usuario_id` y el costo
    vigente de cada producto en la misma transacción que ya resuelve
    `precio_venta_centavos` -- sin ninguna lectura adicional."""

    def test_venta_guarda_el_costo_vigente_del_producto(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto(
            "7790000000001", "Alfajor", 100, 200, stock_actual=10
        )

        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 2)], "EFECTIVO")

        _, costos = _leer_ventas_y_costos(base_datos_temporal, venta.id)
        assert costos == [100]

    def test_cambiar_el_costo_del_producto_y_vender_de_nuevo_conserva_el_costo_de_cada_venta(
        self, base_datos_temporal
    ):
        producto = servicio_stock.registrar_producto(
            "7790000000001", "Alfajor", 100, 200, stock_actual=10
        )

        venta_1 = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")

        servicio_stock.actualizar_producto(
            producto.id, producto.codigo_barras, producto.nombre, 150, producto.precio_venta_centavos, 0
        )
        venta_2 = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")

        _, costos_venta_1 = _leer_ventas_y_costos(base_datos_temporal, venta_1.id)
        _, costos_venta_2 = _leer_ventas_y_costos(base_datos_temporal, venta_2.id)
        assert costos_venta_1 == [100]  # conserva el costo vigente al momento de ESA venta
        assert costos_venta_2 == [150]  # la venta posterior usa el costo ya actualizado

    def test_venta_con_multiples_productos_conserva_el_costo_correcto_por_linea(self, base_datos_temporal):
        p1 = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        p2 = servicio_stock.registrar_producto("7790000000002", "Gaseosa", 150, 300, stock_actual=10)

        venta = servicio_ventas.registrar_venta(
            [ItemVenta(p1.id, 1), ItemVenta(p2.id, 1)], "EFECTIVO"
        )

        _, costos = _leer_ventas_y_costos(base_datos_temporal, venta.id)
        assert sorted(costos) == [100, 150]

    def test_venta_guarda_usuario_id_cuando_se_provee(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        usuario = _crear_usuario()

        venta = servicio_ventas.registrar_venta(
            [ItemVenta(producto.id, 1)], "EFECTIVO", usuario_id=usuario.id
        )

        usuario_id_guardado, _ = _leer_ventas_y_costos(base_datos_temporal, venta.id)
        assert usuario_id_guardado == usuario.id

    def test_dos_usuarios_distintos_generan_ventas_asociadas_correctamente(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        owner = _crear_usuario(nombre_usuario="duenio", rol="OWNER")
        cajera = _crear_usuario(nombre_usuario="cajera1", rol="CASHIER")

        venta_owner = servicio_ventas.registrar_venta(
            [ItemVenta(producto.id, 1)], "EFECTIVO", usuario_id=owner.id
        )
        venta_cajera = servicio_ventas.registrar_venta(
            [ItemVenta(producto.id, 1)], "EFECTIVO", usuario_id=cajera.id
        )

        usuario_de_venta_owner, _ = _leer_ventas_y_costos(base_datos_temporal, venta_owner.id)
        usuario_de_venta_cajera, _ = _leer_ventas_y_costos(base_datos_temporal, venta_cajera.id)
        assert usuario_de_venta_owner == owner.id
        assert usuario_de_venta_cajera == cajera.id

    def test_venta_sin_usuario_id_como_hace_el_cli_queda_con_usuario_null(self, base_datos_temporal):
        """Compatibilidad con el CLI: no autentica a nadie, así que sigue
        llamando a `registrar_venta` sin `usuario_id` -- nunca se inventa
        un usuario para esa venta."""
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)

        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")

        usuario_id_guardado, _ = _leer_ventas_y_costos(base_datos_temporal, venta.id)
        assert usuario_id_guardado is None
