"""Pruebas de integración de services.servicio_caja: secuencia lógica de
una caja física (apertura, movimientos, cierre) contra una base de
datos SQLite temporal (ver tests/conftest.py)."""

import pytest

from db.conexion import obtener_conexion
from db.repositorios import caja as repositorio_caja
from db.repositorios import usuarios as repositorio_usuarios
from domain.caja import MovimientoCaja
from domain.usuario import Usuario
from domain.venta import ItemVenta
from excepciones import CajaError, DatosInvalidosError
from services import servicio_caja, servicio_stock, servicio_ventas


def _crear_usuario(nombre_usuario="cajera1", rol="CASHIER"):
    return repositorio_usuarios.crear_usuario(
        Usuario(nombre_usuario=nombre_usuario, nombre_completo="Test", password_hash="hash", rol=rol)
    )


def _usuario_id_del_ultimo_movimiento():
    with obtener_conexion() as conexion:
        return conexion.execute(
            "SELECT usuario_id FROM caja_movimientos ORDER BY id DESC LIMIT 1"
        ).fetchone()["usuario_id"]


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
    arqueo = servicio_caja.calcular_arqueo_de_sesion()

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

    arqueo = servicio_caja.calcular_arqueo_de_sesion()

    assert arqueo.cantidad_ventas == 2
    assert arqueo.total_vendido_centavos == 40000  # 20000 efectivo + 20000 tarjeta
    assert arqueo.total_efectivo_ventas_centavos == 20000
    # 100000 (apertura) + 5000 (ingreso) - 2000 (egreso) + 20000 (venta efectivo)
    assert arqueo.efectivo_estimado_centavos == 123000


class TestUsuarioDelMovimiento:
    """Migración 010: cada acción de caja acepta un `usuario_id` opcional
    y lo persiste tal cual -- el CLI y los llamados existentes sin
    usuario siguen funcionando igual que antes."""

    def test_abrir_caja_guarda_usuario_id(self, base_datos_temporal):
        usuario = _crear_usuario()

        servicio_caja.abrir_caja(100000, "Apertura", usuario_id=usuario.id)

        assert _usuario_id_del_ultimo_movimiento() == usuario.id

    def test_cerrar_caja_guarda_usuario_id(self, base_datos_temporal):
        usuario = _crear_usuario()
        servicio_caja.abrir_caja(100000)

        servicio_caja.cerrar_caja(100000, usuario_id=usuario.id)

        assert _usuario_id_del_ultimo_movimiento() == usuario.id

    def test_registrar_ingreso_guarda_usuario_id(self, base_datos_temporal):
        usuario = _crear_usuario()
        servicio_caja.abrir_caja(100000)

        servicio_caja.registrar_ingreso(5000, "cambio", usuario_id=usuario.id)

        assert _usuario_id_del_ultimo_movimiento() == usuario.id

    def test_registrar_egreso_guarda_usuario_id(self, base_datos_temporal):
        usuario = _crear_usuario()
        servicio_caja.abrir_caja(100000)

        servicio_caja.registrar_egreso(2000, "pago a proveedor", usuario_id=usuario.id)

        assert _usuario_id_del_ultimo_movimiento() == usuario.id

    def test_sin_usuario_id_como_hace_el_cli_queda_en_null(self, base_datos_temporal):
        """Compatibilidad con el CLI: sigue llamando a las 4 funciones sin
        `usuario_id` -- nunca se inventa un usuario para ese movimiento."""
        servicio_caja.abrir_caja(100000)

        assert _usuario_id_del_ultimo_movimiento() is None

    def test_owner_y_cashier_quedan_diferenciados(self, base_datos_temporal):
        owner = _crear_usuario(nombre_usuario="duenio", rol="OWNER")
        cajera = _crear_usuario(nombre_usuario="cajera1", rol="CASHIER")

        servicio_caja.abrir_caja(100000, usuario_id=owner.id)
        servicio_caja.registrar_ingreso(5000, "cambio", usuario_id=cajera.id)

        with obtener_conexion() as conexion:
            filas = conexion.execute("SELECT usuario_id FROM caja_movimientos ORDER BY id").fetchall()
        assert filas[0]["usuario_id"] == owner.id
        assert filas[1]["usuario_id"] == cajera.id

    def test_usuario_desactivado_conserva_su_movimiento_historico(self, base_datos_temporal):
        usuario = _crear_usuario()
        servicio_caja.abrir_caja(100000, usuario_id=usuario.id)

        repositorio_usuarios.actualizar_activo(usuario.id, False)

        assert _usuario_id_del_ultimo_movimiento() == usuario.id


class TestDiferenciaDeCierre:
    """Migración 011: `cerrar_caja` calcula
    `diferencia_centavos = monto_final_centavos - efectivo_estimado_centavos`
    (este último resuelto con `calcular_arqueo_de_sesion()`, sin
    duplicar esa lógica) y la persiste como parte del cierre."""

    def test_caja_exacta_diferencia_cero(self, base_datos_temporal):
        servicio_caja.abrir_caja(100000)

        cierre = servicio_caja.cerrar_caja(100000)

        assert cierre.diferencia_centavos == 0

    def test_sobrante_diferencia_positiva(self, base_datos_temporal):
        servicio_caja.abrir_caja(100000)

        cierre = servicio_caja.cerrar_caja(100500)

        assert cierre.diferencia_centavos == 500

    def test_faltante_diferencia_negativa(self, base_datos_temporal):
        servicio_caja.abrir_caja(100000)

        cierre = servicio_caja.cerrar_caja(99500)

        assert cierre.diferencia_centavos == -500

    def test_con_ventas_del_dia_la_diferencia_las_contempla(self, base_datos_temporal):
        servicio_caja.abrir_caja(100000)
        producto = servicio_stock.registrar_producto(
            "7790000000001", "Alfajor", 100, 20000, stock_actual=10, stock_minimo=1
        )
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        # esperado: 100000 (apertura) + 20000 (venta efectivo) = 120000

        cierre = servicio_caja.cerrar_caja(120000)

        assert cierre.diferencia_centavos == 0

    def test_solo_movimientos_manuales_sin_ventas(self, base_datos_temporal):
        servicio_caja.abrir_caja(100000)
        servicio_caja.registrar_ingreso(5000, "cambio")
        servicio_caja.registrar_egreso(2000, "pago")
        # esperado: 100000 + 5000 - 2000 = 103000

        cierre = servicio_caja.cerrar_caja(103000)

        assert cierre.diferencia_centavos == 0

    def test_la_diferencia_coincide_con_monto_final_menos_arqueo(self, base_datos_temporal):
        servicio_caja.abrir_caja(100000)
        servicio_caja.registrar_ingreso(3000, "cambio")

        esperado = servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos
        cierre = servicio_caja.cerrar_caja(esperado + 777)

        assert cierre.diferencia_centavos == 777

    def test_diferencia_queda_asociada_al_usuario_que_cierra(self, base_datos_temporal):
        usuario = _crear_usuario()
        servicio_caja.abrir_caja(100000)

        servicio_caja.cerrar_caja(100500, usuario_id=usuario.id)

        assert _usuario_id_del_ultimo_movimiento() == usuario.id

    def test_cierre_historico_sin_diferencia_se_lista_sin_error(self, base_datos_temporal):
        """Un cierre anterior a la migración 011 (simulado acá insertando
        directo un MovimientoCaja sin diferencia_centavos) debe poder
        seguir leyéndose con normalidad -- `listar_movimientos` no debe
        fallar ni inventar un valor."""
        repositorio_caja.registrar_movimiento(MovimientoCaja(tipo="CIERRE", monto_centavos=100000))

        movimientos = servicio_caja.listar_movimientos()

        assert len(movimientos) == 1
        assert movimientos[0].diferencia_centavos is None


def _fechar(tabla: str, registro_id: int, fecha: str) -> None:
    """Fija a mano la fecha de un movimiento/venta para simular horarios
    (el reloj real solo avanza segundos durante un test)."""
    assert tabla in ("caja_movimientos", "ventas")
    with obtener_conexion() as conexion:
        conexion.execute(f"UPDATE {tabla} SET fecha = ? WHERE id = ?", (fecha, registro_id))


def _producto_de_prueba():
    return servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 20000, stock_actual=50, stock_minimo=1)


def _vender_efectivo(producto, fecha: str):
    venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
    _fechar("ventas", venta.id, fecha)
    return venta


class TestArqueoPorSesion:
    """C1 (V1.1): el arqueo y la diferencia de cierre se calculan sobre la
    sesión de caja (APERTURA -> CIERRE), no sobre el día calendario."""

    def test_dos_sesiones_el_mismo_dia_no_se_mezclan(self, base_datos_temporal):
        producto = _producto_de_prueba()
        apertura1 = servicio_caja.abrir_caja(100000)
        _fechar("caja_movimientos", apertura1.id, "2026-01-10 08:00:00")
        _vender_efectivo(producto, "2026-01-10 09:00:00")
        cierre1 = servicio_caja.cerrar_caja(120000)
        _fechar("caja_movimientos", cierre1.id, "2026-01-10 12:00:00")

        apertura2 = servicio_caja.abrir_caja(50000)
        _fechar("caja_movimientos", apertura2.id, "2026-01-10 13:00:00")
        _vender_efectivo(producto, "2026-01-10 14:00:00")

        arqueo = servicio_caja.calcular_arqueo_de_sesion()

        assert arqueo.sesion_abierta is True
        assert arqueo.cantidad_ventas == 1
        assert arqueo.total_efectivo_ventas_centavos == 20000
        assert arqueo.efectivo_estimado_centavos == 70000  # 50000 apertura 2 + 20000 venta 2

    def test_diferencia_del_segundo_cierre_no_arrastra_la_primera_sesion(self, base_datos_temporal):
        producto = _producto_de_prueba()
        apertura1 = servicio_caja.abrir_caja(100000)
        _fechar("caja_movimientos", apertura1.id, "2026-01-10 08:00:00")
        _vender_efectivo(producto, "2026-01-10 09:00:00")
        cierre1 = servicio_caja.cerrar_caja(120000)
        _fechar("caja_movimientos", cierre1.id, "2026-01-10 12:00:00")
        apertura2 = servicio_caja.abrir_caja(50000)
        _fechar("caja_movimientos", apertura2.id, "2026-01-10 13:00:00")
        _vender_efectivo(producto, "2026-01-10 14:00:00")

        cierre2 = servicio_caja.cerrar_caja(70000)

        assert cierre1.diferencia_centavos == 0
        assert cierre2.diferencia_centavos == 0

    def test_sesion_que_cruza_medianoche_incluye_ventas_posteriores_a_las_00(self, base_datos_temporal):
        producto = _producto_de_prueba()
        apertura = servicio_caja.abrir_caja(100000)
        _fechar("caja_movimientos", apertura.id, "2026-01-10 22:00:00")
        _vender_efectivo(producto, "2026-01-10 23:30:00")
        _vender_efectivo(producto, "2026-01-11 01:15:00")

        arqueo = servicio_caja.calcular_arqueo_de_sesion()

        assert arqueo.cantidad_ventas == 2
        assert arqueo.total_efectivo_ventas_centavos == 40000
        assert arqueo.efectivo_estimado_centavos == 140000

    def test_cierre_despues_de_medianoche_calcula_bien_la_diferencia(self, base_datos_temporal):
        producto = _producto_de_prueba()
        apertura = servicio_caja.abrir_caja(100000)
        _fechar("caja_movimientos", apertura.id, "2026-01-10 22:00:00")
        _vender_efectivo(producto, "2026-01-10 23:30:00")
        _vender_efectivo(producto, "2026-01-11 01:15:00")

        cierre = servicio_caja.cerrar_caja(140000)

        assert cierre.diferencia_centavos == 0

    def test_movimientos_manuales_pertenecen_a_su_propia_sesion(self, base_datos_temporal):
        apertura1 = servicio_caja.abrir_caja(100000)
        ingreso1 = servicio_caja.registrar_ingreso(5000, "cambio sesión 1")
        egreso1 = servicio_caja.registrar_egreso(2000, "pago sesión 1")
        cierre1 = servicio_caja.cerrar_caja(103000)
        apertura2 = servicio_caja.abrir_caja(10000)
        servicio_caja.registrar_egreso(1000, "pago sesión 2")
        _fechar("caja_movimientos", apertura1.id, "2026-01-10 08:00:00")
        _fechar("caja_movimientos", ingreso1.id, "2026-01-10 09:00:00")
        _fechar("caja_movimientos", egreso1.id, "2026-01-10 10:00:00")
        _fechar("caja_movimientos", cierre1.id, "2026-01-10 12:00:00")
        _fechar("caja_movimientos", apertura2.id, "2026-01-10 13:00:00")

        arqueo = servicio_caja.calcular_arqueo_de_sesion()

        assert arqueo.efectivo_estimado_centavos == 9000  # 10000 - 1000, sin nada de la sesión 1

    def test_venta_anulada_no_cuenta_en_la_sesion(self, base_datos_temporal):
        producto = _producto_de_prueba()
        servicio_caja.abrir_caja(100000)
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        owner = _crear_usuario("dueno", "OWNER")
        servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        arqueo = servicio_caja.calcular_arqueo_de_sesion()

        assert arqueo.cantidad_ventas == 0
        assert arqueo.efectivo_estimado_centavos == 100000

    def test_con_la_caja_cerrada_el_arqueo_es_el_de_la_ultima_sesion(self, base_datos_temporal):
        producto = _producto_de_prueba()
        servicio_caja.abrir_caja(100000)
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        servicio_caja.cerrar_caja(120000)

        arqueo = servicio_caja.calcular_arqueo_de_sesion()

        assert arqueo.sesion_abierta is False
        assert arqueo.cantidad_ventas == 1
        assert arqueo.efectivo_estimado_centavos == 120000
