"""Pruebas de integración de services.servicio_ventas, con foco en que
`registrar_venta` sea realmente atómica: descuento de stock + alta de
venta y detalle, todo o nada (ver tests/conftest.py para la base de
datos temporal)."""

import sqlite3
import threading
from datetime import date, timedelta

import pytest

from db.conexion import obtener_conexion
from db.repositorios import productos as repositorio_productos
from db.repositorios import usuarios as repositorio_usuarios
from db.repositorios import ventas as repositorio_ventas
from domain.usuario import Usuario
from domain.venta import ItemVenta
from excepciones import (
    CajaCerradaError,
    ClaveIdempotenciaReutilizadaError,
    DatosInvalidosError,
    PrecioVentaNoConfiguradoError,
    ProductoNoEncontradoError,
    StockInsuficienteError,
    VentaDeCajaCerradaError,
    VentaNoEncontradaError,
    VentaYaAnuladaError,
)
from services import servicio_caja, servicio_stock, servicio_ventas

pytestmark = pytest.mark.usefixtures("caja_abierta")


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


class TestListarHistorial:
    """Historial de Ventas: rango por defecto de 30 días (calendario,
    incluyendo hoy) cuando no se especifica ninguno completo."""

    def test_sin_filtros_usa_el_rango_por_defecto_de_30_dias(self, base_datos_temporal):
        fecha_desde, fecha_hasta, ventas = servicio_ventas.listar_historial()

        assert fecha_hasta == date.today().isoformat()
        assert fecha_desde == (date.today() - timedelta(days=29)).isoformat()
        assert ventas == []

    def test_con_filtros_explicitos_los_respeta(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")

        fecha_desde, fecha_hasta, ventas = servicio_ventas.listar_historial(
            fecha_desde="2020-01-01", fecha_hasta="2020-01-31"
        )

        assert fecha_desde == "2020-01-01"
        assert fecha_hasta == "2020-01-31"
        assert ventas == []  # la venta de hoy queda fuera del rango pedido

    def test_rango_a_medio_especificar_usa_el_rango_por_defecto_completo(self, base_datos_temporal):
        """Pasar solo fecha_desde (sin fecha_hasta) es ambiguo: se trata
        igual que no pasar ningún límite, no se adivina el que falta
        (mismo criterio que `servicio_reportes.generar_reporte_ventas`)."""
        fecha_desde, fecha_hasta, _ = servicio_ventas.listar_historial(fecha_desde="2020-01-01")

        assert fecha_desde == (date.today() - timedelta(days=29)).isoformat()
        assert fecha_hasta == date.today().isoformat()

    def test_delega_al_repositorio_con_los_filtros_resueltos(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "TARJETA")

        _, _, ventas = servicio_ventas.listar_historial(tipo_pago="TARJETA")

        assert len(ventas) == 1
        assert ventas[0].id == venta.id

    @pytest.mark.sin_caja_abierta
    def test_delega_el_filtro_de_estado_al_repositorio(self, base_datos_temporal):
        """Visibilidad de Anulaciones: `estado` se enhebra directo hacia
        `repositorio_ventas.listar_resumen`, sin lógica propia."""
        servicio_caja.abrir_caja(100_000)
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta_activa = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        venta_anulada = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        owner = _owner()
        servicio_ventas.anular_venta(venta_anulada.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        _, _, activas = servicio_ventas.listar_historial(estado="ACTIVA")
        _, _, anuladas = servicio_ventas.listar_historial(estado="ANULADA")
        _, _, todas = servicio_ventas.listar_historial()

        assert [v.id for v in activas] == [venta_activa.id]
        assert [v.id for v in anuladas] == [venta_anulada.id]
        assert {v.id for v in todas} == {venta_activa.id, venta_anulada.id}


class TestObtenerResumenPorId:
    def test_delega_directamente_al_repositorio(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")

        resultado = servicio_ventas.obtener_resumen_por_id(venta.id)

        assert resultado is not None
        assert resultado.id == venta.id

    def test_inexistente_devuelve_none(self, base_datos_temporal):
        assert servicio_ventas.obtener_resumen_por_id(9999) is None


def _owner():
    return _crear_usuario(nombre_usuario="duenio", rol="OWNER")


class TestAnularVenta:
    """Anulación de ventas (ver auditoría de diseño): restaura stock y
    marca `ANULADA` en una única transacción, solo para ventas de la
    sesión de caja actualmente abierta -- nunca genera movimientos de
    caja nuevos."""

    @pytest.mark.sin_caja_abierta
    def test_caso_feliz_restaura_stock_y_marca_anulada(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 3)], "EFECTIVO")
        owner = _owner()

        anulada = servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        assert anulada.estado == "ANULADA"
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 10

    @pytest.mark.sin_caja_abierta
    def test_multiples_lineas_restaura_cada_producto(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        p1 = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        p2 = servicio_stock.registrar_producto("7790000000002", "Gaseosa", 100, 300, stock_actual=5)
        venta = servicio_ventas.registrar_venta([ItemVenta(p1.id, 2), ItemVenta(p2.id, 1)], "EFECTIVO")
        owner = _owner()

        servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        assert servicio_stock.obtener_por_id(p1.id).stock_actual == 10
        assert servicio_stock.obtener_por_id(p2.id).stock_actual == 5

    @pytest.mark.sin_caja_abierta
    def test_producto_repetido_en_la_venta_restaura_la_cantidad_total(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = servicio_ventas.registrar_venta(
            [ItemVenta(producto.id, 2), ItemVenta(producto.id, 1)], "EFECTIVO"
        )
        owner = _owner()

        servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 10

    @pytest.mark.sin_caja_abierta
    def test_producto_inactivo_despues_de_la_venta_restaura_stock_igual(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 3)], "EFECTIVO")
        servicio_stock.eliminar_producto(producto.id)  # baja lógica: tiene una venta asociada
        owner = _owner()

        servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        with obtener_conexion() as conexion:
            fila = conexion.execute(
                "SELECT stock_actual, activo FROM productos WHERE id = ?", (producto.id,)
            ).fetchone()
        assert fila["stock_actual"] == 10
        assert fila["activo"] == 0  # sigue inactivo: anular no reactiva el catálogo

    @pytest.mark.sin_caja_abierta
    def test_no_persiste_ninguna_venta_ni_ajuste_si_producto_no_existe(self, base_datos_temporal, monkeypatch):
        """No debería poder pasar en el flujo real (`ON DELETE RESTRICT`),
        pero si el producto de una línea desapareciera, la anulación
        entera se aborta -- ninguna otra línea queda restaurada a medias."""
        servicio_caja.abrir_caja(100_000)
        p1 = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        p2 = servicio_stock.registrar_producto("7790000000002", "Gaseosa", 100, 300, stock_actual=5)
        venta = servicio_ventas.registrar_venta([ItemVenta(p1.id, 1), ItemVenta(p2.id, 1)], "EFECTIVO")
        owner = _owner()

        original = repositorio_productos.obtener_por_id_en_conexion_incluyendo_inactivos

        def falla_para_p2(conexion, producto_id):
            if producto_id == p2.id:
                return None
            return original(conexion, producto_id)

        monkeypatch.setattr(
            repositorio_productos, "obtener_por_id_en_conexion_incluyendo_inactivos", falla_para_p2
        )

        with pytest.raises(ProductoNoEncontradoError):
            servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        # ninguna línea quedó restaurada, la venta sigue ACTIVA.
        assert servicio_stock.obtener_por_id(p1.id).stock_actual == 9
        with obtener_conexion() as conexion:
            estado = conexion.execute("SELECT estado FROM ventas WHERE id = ?", (venta.id,)).fetchone()["estado"]
        assert estado == "ACTIVA"

    @pytest.mark.sin_caja_abierta
    def test_venta_inexistente_falla(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        owner = _owner()

        with pytest.raises(VentaNoEncontradaError):
            servicio_ventas.anular_venta(9999, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

    @pytest.mark.sin_caja_abierta
    def test_venta_ya_anulada_falla(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        owner = _owner()
        servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        with pytest.raises(VentaYaAnuladaError):
            servicio_ventas.anular_venta(venta.id, motivo="OTRO", observaciones="otro intento", usuario_id=owner.id)

        # el stock no se restauró una segunda vez.
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 10

    @pytest.mark.sin_caja_abierta
    def test_motivo_invalido_no_toca_nada(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        owner = _owner()

        with pytest.raises(DatosInvalidosError):
            servicio_ventas.anular_venta(venta.id, motivo="PORQUE_SI", observaciones=None, usuario_id=owner.id)

        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 9

    @pytest.mark.sin_caja_abierta
    def test_otro_sin_observaciones_no_toca_nada(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        owner = _owner()

        with pytest.raises(DatosInvalidosError):
            servicio_ventas.anular_venta(venta.id, motivo="OTRO", observaciones=None, usuario_id=owner.id)

        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 9

    @pytest.mark.sin_caja_abierta
    def test_sin_caja_abierta_falla(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        servicio_caja.cerrar_caja(100_200)
        owner = _owner()

        with pytest.raises(VentaDeCajaCerradaError):
            servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 9

    @pytest.mark.sin_caja_abierta
    def test_venta_de_sesion_de_caja_ya_cerrada_falla(self, base_datos_temporal):
        """Caso obligatorio de la auditoría de diseño: 10:00 venta / 18:00
        cierre / 20:00 apertura -- la venta pertenece a la sesión ya
        cerrada, se rechaza sin tocar el cierre histórico.

        `venta.fecha` se retrasa a mano (mismo recurso que ya usa el
        resto de la suite, ej. `TestListarEnRango`) para que el caso sea
        determinístico: el gap real entre `abrir_caja`/`cerrar_caja`/
        `abrir_caja` en un test automatizado puede caer dentro del mismo
        segundo (resolución de `datetime('now')` en SQLite), y el caso
        que se quiere probar depende de una diferencia real de horas.
        """
        servicio_caja.abrir_caja(100_000)
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        with obtener_conexion() as conexion:
            conexion.execute("UPDATE ventas SET fecha = ? WHERE id = ?", ("2020-01-01 10:00:00", venta.id))
        servicio_caja.cerrar_caja(100_100)  # cierre de esa sesión (18:00)
        servicio_caja.abrir_caja(50_000)  # nueva sesión, más tarde (20:00)
        owner = _owner()

        with pytest.raises(VentaDeCajaCerradaError):
            servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 9
        with obtener_conexion() as conexion:
            estado = conexion.execute("SELECT estado FROM ventas WHERE id = ?", (venta.id,)).fetchone()["estado"]
        assert estado == "ACTIVA"
        # el cierre histórico no se tocó.
        with obtener_conexion() as conexion:
            cierres = conexion.execute(
                "SELECT COUNT(*) AS n FROM caja_movimientos WHERE tipo = 'CIERRE'"
            ).fetchone()["n"]
        assert cierres == 1

    @pytest.mark.sin_caja_abierta
    def test_venta_dentro_de_la_sesion_vigente_con_movimientos_manuales_de_por_medio(self, base_datos_temporal):
        """Ingresos/egresos manuales entre la apertura y la venta no
        cambian cuál es la sesión vigente (ver
        `db.repositorios.caja.obtener_sesion_abierta_en_conexion`)."""
        servicio_caja.abrir_caja(100_000)
        servicio_caja.registrar_ingreso(5_000, "cambio extra")
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        owner = _owner()

        anulada = servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        assert anulada.estado == "ANULADA"
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 10

    @pytest.mark.sin_caja_abierta
    def test_persiste_motivo_observaciones_usuario_y_fecha(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        owner = _owner()

        servicio_ventas.anular_venta(
            venta.id, motivo="OTRO", observaciones="se equivocó de producto", usuario_id=owner.id
        )

        resumen = servicio_ventas.obtener_resumen_por_id(venta.id)
        assert resumen.motivo_anulacion == "OTRO"
        assert resumen.observaciones_anulacion == "se equivocó de producto"
        assert resumen.anulado_por_nombre == "Test"
        assert resumen.fecha_anulacion is not None

    @pytest.mark.sin_caja_abierta
    def test_no_altera_precio_costo_ni_vendedor_original(self, base_datos_temporal):
        """El detalle histórico (precio, costo, vendedor) queda intacto:
        anular solo agrega estado + auditoría a la cabecera."""
        servicio_caja.abrir_caja(100_000)
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        vendedor = _crear_usuario(nombre_usuario="cajera1", rol="CASHIER")
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO", usuario_id=vendedor.id)
        owner = _owner()

        servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        detalle = servicio_ventas.obtener_venta_con_detalle(venta.id)
        resumen = servicio_ventas.obtener_resumen_por_id(venta.id)
        assert detalle.lineas[0].precio_unitario_centavos == 200
        assert resumen.vendedor_nombre == "Test"  # el vendedor original, no el que anuló
        _, costos = _leer_ventas_y_costos(base_datos_temporal, venta.id)
        assert costos == [100]

    @pytest.mark.sin_caja_abierta
    def test_no_inserta_ninguna_fila_en_caja_movimientos(self, base_datos_temporal):
        """No hay reembolso financiero modelado: anular nunca genera un
        movimiento de caja, ni INGRESO, EGRESO, ni ningún otro tipo."""
        servicio_caja.abrir_caja(100_000)  # 1 fila (APERTURA)
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        owner = _owner()

        servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        assert _contar_filas(base_datos_temporal, "caja_movimientos") == 1  # solo la APERTURA original

    @pytest.mark.sin_caja_abierta
    def test_venta_efectivo_anulada_no_suma_al_efectivo_estimado(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 2)], "EFECTIVO")  # 400 centavos
        owner = _owner()

        arqueo_antes = servicio_caja.calcular_arqueo_de_sesion()
        assert arqueo_antes.total_efectivo_ventas_centavos == 400

        servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        arqueo_despues = servicio_caja.calcular_arqueo_de_sesion()
        assert arqueo_despues.total_efectivo_ventas_centavos == 0
        assert arqueo_despues.efectivo_estimado_centavos == 100_000  # solo la apertura
        assert arqueo_despues.cantidad_ventas == 0

    @pytest.mark.sin_caja_abierta
    def test_fallo_al_persistir_la_anulacion_no_deja_stock_restaurado(self, base_datos_temporal, monkeypatch):
        """Atomicidad: si la transición final de estado falla (simulada
        acá después de que el stock ya se restauró en memoria de la
        transacción), toda la transacción se revierte -- ni el stock
        queda restaurado ni la venta queda anulada."""
        servicio_caja.abrir_caja(100_000)
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 3)], "EFECTIVO")
        owner = _owner()

        def falla(*_args, **_kwargs):
            raise sqlite3.IntegrityError("fallo simulado")

        monkeypatch.setattr(repositorio_ventas, "anular_venta_en_conexion", falla)

        with pytest.raises(Exception):
            servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 7  # sin restaurar
        with obtener_conexion() as conexion:
            estado = conexion.execute("SELECT estado FROM ventas WHERE id = ?", (venta.id,)).fetchone()["estado"]
        assert estado == "ACTIVA"

    @pytest.mark.sin_caja_abierta
    def test_dos_anulaciones_concurrentes_de_la_misma_venta_una_sola_gana(self, base_datos_temporal):
        """Mismo patrón que `TestAjustarStock.test_dos_ajustes_concurrentes_no_generan_lost_update`:
        dos hilos reales, dos conexiones SQLite reales."""
        servicio_caja.abrir_caja(100_000)
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 3)], "EFECTIVO")
        owner = _owner()

        barrera = threading.Barrier(2)
        resultados = {}

        def anular(nombre):
            barrera.wait()
            try:
                servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)
                resultados[nombre] = "OK"
            except VentaYaAnuladaError:
                resultados[nombre] = "YA_ANULADA"

        hilo_a = threading.Thread(target=anular, args=("A",))
        hilo_b = threading.Thread(target=anular, args=("B",))
        hilo_a.start()
        hilo_b.start()
        hilo_a.join(timeout=10)
        hilo_b.join(timeout=10)

        assert not hilo_a.is_alive()
        assert not hilo_b.is_alive()

        resueltos = list(resultados.values())
        assert resueltos.count("OK") == 1
        assert resueltos.count("YA_ANULADA") == 1

        # el stock se restauró una sola vez: 10, nunca 13 (restaurado dos
        # veces) ni 7 (nunca restaurado).
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 10

    @pytest.mark.sin_caja_abierta
    def test_anulacion_concurrente_con_ajuste_de_stock_del_mismo_producto_no_genera_lost_update(
        self, base_datos_temporal
    ):
        servicio_caja.abrir_caja(100_000)
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 3)], "EFECTIVO")  # stock queda en 7
        owner = _owner()

        barrera = threading.Barrier(2)
        errores = []

        def anular():
            barrera.wait()
            try:
                servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)
            except Exception as error:  # noqa: BLE001 -- se inspecciona más abajo
                errores.append(("anular", error))

        def ajustar():
            barrera.wait()
            try:
                servicio_stock.ajustar_stock(producto.id, delta=-2, motivo="MERMA", usuario_id=owner.id)
            except Exception as error:  # noqa: BLE001
                errores.append(("ajustar", error))

        hilo_a = threading.Thread(target=anular)
        hilo_b = threading.Thread(target=ajustar)
        hilo_a.start()
        hilo_b.start()
        hilo_a.join(timeout=10)
        hilo_b.join(timeout=10)

        assert not hilo_a.is_alive()
        assert not hilo_b.is_alive()
        assert errores == []

        # Stock final determinista sin importar el orden real de
        # ejecución: 7 (post-venta) + 3 (restaurado por la anulación) -
        # 2 (merma) = 8. Lo que NO puede pasar es un lost update (ej. 10
        # o 5), que sería la señal de que las dos transacciones pisaron
        # el valor de la otra.
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 8


class TestListarHistorialIncluyeAnuladas:
    @pytest.mark.sin_caja_abierta
    def test_venta_anulada_sigue_apareciendo_en_el_historial(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        owner = _owner()
        servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        _, _, ventas = servicio_ventas.listar_historial(fecha_desde="2000-01-01", fecha_hasta="2099-12-31")

        assert len(ventas) == 1
        assert ventas[0].id == venta.id
        assert ventas[0].estado == "ANULADA"

    @pytest.mark.sin_caja_abierta
    def test_venta_anulada_sigue_accesible_por_detalle(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        owner = _owner()
        servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        resumen = servicio_ventas.obtener_resumen_por_id(venta.id)
        detalle = servicio_ventas.obtener_venta_con_detalle(venta.id)

        assert resumen is not None
        assert detalle is not None
        assert len(detalle.lineas) == 1


# ---------------------------------------------------------------------------
# Guardia contra precio de venta 0
# ---------------------------------------------------------------------------


def test_venta_con_precio_mayor_a_cero_funciona_normalmente(base_datos_temporal):
    producto = servicio_stock.registrar_producto("TX-P-001", "Con precio", 100, 250, stock_actual=5)

    venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 2)], "EFECTIVO")

    assert venta.total_centavos == 500
    assert servicio_stock.buscar_por_codigo_barras("TX-P-001").stock_actual == 3


def test_venta_con_precio_cero_se_rechaza_con_un_mensaje_claro(base_datos_temporal):
    producto = servicio_stock.registrar_producto("TX-P-002", "Sin precio", 0, 0, stock_actual=5)

    with pytest.raises(PrecioVentaNoConfiguradoError) as error:
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")

    assert "Sin precio" in str(error.value)
    assert "precio" in str(error.value).lower()


@pytest.mark.sin_caja_abierta
def test_venta_con_precio_cero_no_descuenta_stock_ni_registra_venta_ni_movimiento_de_caja(base_datos_temporal):
    servicio_caja.abrir_caja(100_000)
    movimientos_antes = _contar_filas(base_datos_temporal, "caja_movimientos")
    producto = servicio_stock.registrar_producto("TX-P-003", "Sin precio", 0, 0, stock_actual=5)

    with pytest.raises(PrecioVentaNoConfiguradoError):
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 2)], "EFECTIVO")

    assert servicio_stock.buscar_por_codigo_barras("TX-P-003").stock_actual == 5
    assert _contar_filas(base_datos_temporal, "ventas") == 0
    assert _contar_filas(base_datos_temporal, "detalle_venta") == 0
    assert _contar_filas(base_datos_temporal, "caja_movimientos") == movimientos_antes


def test_una_linea_con_precio_cero_rechaza_la_venta_completa(base_datos_temporal):
    con_precio = servicio_stock.registrar_producto("TX-P-004", "Con precio", 100, 250, stock_actual=5)
    sin_precio = servicio_stock.registrar_producto("TX-P-005", "Sin precio", 0, 0, stock_actual=5)

    with pytest.raises(PrecioVentaNoConfiguradoError):
        servicio_ventas.registrar_venta([ItemVenta(con_precio.id, 1), ItemVenta(sin_precio.id, 1)], "EFECTIVO")

    assert servicio_stock.buscar_por_codigo_barras("TX-P-004").stock_actual == 5
    assert servicio_stock.buscar_por_codigo_barras("TX-P-005").stock_actual == 5
    assert _contar_filas(base_datos_temporal, "ventas") == 0


def test_el_precio_cero_se_informa_antes_que_la_falta_de_stock(base_datos_temporal):
    """Un producto recién sembrado (precio 0 y stock 0): lo accionable es el precio."""
    producto = servicio_stock.registrar_producto("TX-P-006", "Recién sembrado", 0, 0)

    with pytest.raises(PrecioVentaNoConfiguradoError):
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")


class TestVentaSinCajaAbierta:
    """C2 (V1.1): toda venta pertenece a una caja abierta. Sin caja abierta
    se rechaza antes de tocar stock, ventas o caja, y no consume la clave
    de idempotencia."""

    @pytest.mark.sin_caja_abierta
    def test_sin_caja_abierta_se_rechaza_y_no_persiste_nada(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)

        with pytest.raises(CajaCerradaError):
            servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO", clave_idempotencia="k1")

        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 10
        assert _contar_filas(base_datos_temporal, "ventas") == 0
        assert _contar_filas(base_datos_temporal, "detalle_venta") == 0
        assert _contar_filas(base_datos_temporal, "caja_movimientos") == 0

    @pytest.mark.sin_caja_abierta
    def test_la_clave_rechazada_por_caja_cerrada_sirve_al_abrir_la_caja(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        with pytest.raises(CajaCerradaError):
            servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO", clave_idempotencia="k1")

        servicio_caja.abrir_caja(100_000)
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO", clave_idempotencia="k1")

        assert venta.id is not None
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 9

    @pytest.mark.sin_caja_abierta
    def test_despues_de_cerrar_la_caja_no_se_puede_vender(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        servicio_caja.abrir_caja(100_000)
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        servicio_caja.cerrar_caja(100_200)

        with pytest.raises(CajaCerradaError):
            servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")

        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 9

    @pytest.mark.sin_caja_abierta
    def test_reintento_de_venta_ya_registrada_devuelve_la_misma_aunque_se_cerro_la_caja(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        servicio_caja.abrir_caja(100_000)
        original = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO", clave_idempotencia="k1")
        servicio_caja.cerrar_caja(100_200)

        reintento = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO", clave_idempotencia="k1")

        assert reintento.id == original.id
        assert _contar_filas(base_datos_temporal, "ventas") == 1
