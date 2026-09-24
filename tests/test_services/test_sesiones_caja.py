"""Sesiones de caja explícitas (V1.3, migración 019) desde la capa de servicios:
apertura, cierre, venta, venta con caja cerrada, anulación y concurrencia.

Todos parten de una base nueva (sin historia): las sesiones las crea la
aplicación (`origen = 'NORMAL'`). La migración sobre datos V1.2 está en
`tests/test_db/test_migracion_019.py`.
"""

import threading

import pytest

from db.conexion import obtener_conexion
from db.repositorios import caja as repositorio_caja
from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from domain.venta import ItemVenta
from excepciones import CajaCerradaError, CajaError, StockInsuficienteError, VentaDeCajaCerradaError
from services import servicio_caja, servicio_stock, servicio_ventas

pytestmark = pytest.mark.usefixtures("base_datos_temporal")


def _producto():
    return servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 1000, stock_actual=500)


def _usuario(nombre="duenio", rol="OWNER"):
    return repositorio_usuarios.crear_usuario(
        Usuario(nombre_usuario=nombre, nombre_completo="Test", password_hash="hash", rol=rol)
    )


def _sesiones():
    with obtener_conexion() as conexion:
        return conexion.execute("SELECT * FROM sesiones_caja ORDER BY id").fetchall()


def _cantidad(tabla, condicion="1=1"):
    with obtener_conexion() as conexion:
        return conexion.execute(f"SELECT COUNT(*) AS n FROM {tabla} WHERE {condicion}").fetchone()["n"]


# --- Apertura -----------------------------------------------------------------------


def test_abrir_caja_crea_sesion_normal_abierta_y_movimiento_asociado():
    usuario = _usuario()

    movimiento = servicio_caja.abrir_caja(2500, "fondo", usuario_id=usuario.id)

    (sesion,) = _sesiones()
    assert (sesion["estado"], sesion["origen"]) == ("ABIERTA", "NORMAL")
    assert sesion["fondo_centavos"] == 2500 and sesion["usuario_apertura_id"] == usuario.id
    assert sesion["fecha_cierre"] is None
    with obtener_conexion() as conexion:
        fila = conexion.execute(
            "SELECT sesion_caja_id, fecha FROM caja_movimientos WHERE id = ?", (movimiento.id,)
        ).fetchone()
    assert fila["sesion_caja_id"] == sesion["id"]
    assert fila["fecha"] == sesion["fecha_apertura"]
    assert servicio_caja.consultar_estado() is True


def test_abrir_caja_con_otra_abierta_falla_sin_crear_nada():
    servicio_caja.abrir_caja(100)

    with pytest.raises(CajaError, match="ya está abierta"):
        servicio_caja.abrir_caja(200)

    assert len(_sesiones()) == 1
    assert _cantidad("caja_movimientos") == 1


def test_apertura_registra_su_auditoria_en_la_misma_transaccion():
    usuario = _usuario()

    servicio_caja.abrir_caja(100, usuario_id=usuario.id)

    assert _cantidad("auditoria", "accion = 'CAJA_APERTURA'") == 1


def test_una_apertura_que_falla_no_deja_sesion_huerfana(monkeypatch):
    """Si algo falla después de crear la sesión (acá, la auditoría) se revierte todo:
    ni sesión ni movimiento."""
    usuario = _usuario()

    def _falla(*args, **kwargs):
        raise RuntimeError("falla simulada")

    monkeypatch.setattr(repositorio_caja.repositorio_auditoria, "registrar_en_conexion", _falla)
    with pytest.raises(RuntimeError):
        servicio_caja.abrir_caja(100, usuario_id=usuario.id)

    assert _sesiones() == []
    assert _cantidad("caja_movimientos") == 0
    assert servicio_caja.consultar_estado() is False


# --- Cierre -------------------------------------------------------------------------


def test_cerrar_caja_calcula_por_sesion_guarda_contado_y_diferencia_y_cierra():
    usuario = _usuario()
    producto = _producto()
    servicio_caja.abrir_caja(1000, usuario_id=usuario.id)
    servicio_ventas.registrar_venta([ItemVenta(producto.id, 2)], "EFECTIVO")  # +2000
    servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "TARJETA")  # no mueve efectivo
    servicio_caja.registrar_ingreso(300, "cambio", usuario_id=usuario.id)
    servicio_caja.registrar_egreso(100, "proveedor", usuario_id=usuario.id)

    cierre = servicio_caja.cerrar_caja(3100, usuario_id=usuario.id)  # esperado 1000+2000+300-100 = 3200

    assert cierre.diferencia_centavos == -100
    (sesion,) = _sesiones()
    assert (sesion["estado"], sesion["origen"]) == ("CERRADA", "NORMAL")
    assert sesion["contado_centavos"] == 3100 and sesion["diferencia_centavos"] == -100
    assert sesion["fecha_cierre"] is not None and sesion["usuario_cierre_id"] == usuario.id
    assert servicio_caja.consultar_estado() is False
    assert _cantidad("caja_movimientos", f"sesion_caja_id = {sesion['id']}") == 4  # apertura, ingreso, egreso, cierre
    assert _cantidad("auditoria", "accion = 'CAJA_CIERRE'") == 1


def test_cerrar_sin_caja_abierta_falla():
    with pytest.raises(CajaError, match="No hay una caja abierta para cerrar"):
        servicio_caja.cerrar_caja(0)


def test_despues_del_cierre_no_se_registran_movimientos_ni_ventas_en_esa_sesion():
    producto = _producto()
    servicio_caja.abrir_caja(0)
    servicio_caja.cerrar_caja(0)

    with pytest.raises(CajaError):
        servicio_caja.registrar_ingreso(10, "x")
    with pytest.raises(CajaCerradaError):
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")


def test_dos_sesiones_seguidas_no_mezclan_ventas_ni_arqueo():
    producto = _producto()
    servicio_caja.abrir_caja(0)
    servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
    servicio_caja.cerrar_caja(1000)
    servicio_caja.abrir_caja(500)
    servicio_ventas.registrar_venta([ItemVenta(producto.id, 3)], "EFECTIVO")

    arqueo = servicio_caja.calcular_arqueo_de_sesion()

    assert arqueo.sesion_abierta is True
    assert arqueo.cantidad_ventas == 1 and arqueo.total_vendido_centavos == 3000
    assert arqueo.efectivo_estimado_centavos == 3500
    primera, segunda = _sesiones()
    assert _cantidad("ventas", f"sesion_caja_id = {primera['id']}") == 1
    assert _cantidad("ventas", f"sesion_caja_id = {segunda['id']}") == 1


def test_el_arqueo_de_la_ultima_sesion_cerrada_sigue_disponible():
    producto = _producto()
    servicio_caja.abrir_caja(0)
    servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
    servicio_caja.cerrar_caja(1000)

    arqueo = servicio_caja.calcular_arqueo_de_sesion()

    assert arqueo.sesion_abierta is False and arqueo.efectivo_estimado_centavos == 1000


# --- Ventas -------------------------------------------------------------------------


def test_venta_nueva_queda_asociada_a_la_sesion_abierta():
    producto = _producto()
    servicio_caja.abrir_caja(0)

    venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")

    (sesion,) = _sesiones()
    assert _cantidad("ventas", f"id = {venta.id} AND sesion_caja_id = {sesion['id']}") == 1


def test_venta_con_caja_cerrada_se_bloquea_y_no_toca_stock_ni_crea_filas():
    producto = _producto()

    with pytest.raises(CajaCerradaError):
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 5)], "EFECTIVO")

    assert _cantidad("ventas") == 0
    assert servicio_stock.obtener_por_id(producto.id).stock_actual == 500


def test_la_clave_de_idempotencia_no_se_consume_si_la_venta_falla_por_caja_cerrada():
    producto = _producto()
    items = [ItemVenta(producto.id, 1)]

    with pytest.raises(CajaCerradaError):
        servicio_ventas.registrar_venta(items, "EFECTIVO", clave_idempotencia="clave-x")
    assert _cantidad("ventas", "clave_idempotencia = 'clave-x'") == 0

    servicio_caja.abrir_caja(0)
    venta = servicio_ventas.registrar_venta(items, "EFECTIVO", clave_idempotencia="clave-x")
    reintento = servicio_ventas.registrar_venta(items, "EFECTIVO", clave_idempotencia="clave-x")

    assert reintento.id == venta.id and _cantidad("ventas") == 1


def test_una_venta_que_falla_por_stock_no_queda_con_sesion_ni_descuenta():
    producto = servicio_stock.registrar_producto("7790000000002", "Escaso", 1, 10, stock_actual=1)
    servicio_caja.abrir_caja(0)

    with pytest.raises(StockInsuficienteError):
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 5)], "EFECTIVO")

    assert _cantidad("ventas") == 0


# --- Anulación ----------------------------------------------------------------------


def test_se_puede_anular_una_venta_de_la_sesion_abierta():
    usuario = _usuario()
    producto = _producto()
    servicio_caja.abrir_caja(0)
    venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 2)], "EFECTIVO")

    servicio_ventas.anular_venta(venta.id, "ERROR_CARGA", None, usuario.id)

    assert servicio_stock.obtener_por_id(producto.id).stock_actual == 500
    assert _cantidad("ventas", f"id = {venta.id} AND estado = 'ANULADA'") == 1


def test_no_se_puede_anular_una_venta_de_una_sesion_cerrada_aunque_haya_otra_abierta():
    usuario = _usuario()
    producto = _producto()
    servicio_caja.abrir_caja(0)
    venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 2)], "EFECTIVO")
    servicio_caja.cerrar_caja(2000)
    servicio_caja.abrir_caja(0)

    with pytest.raises(VentaDeCajaCerradaError):
        servicio_ventas.anular_venta(venta.id, "ERROR_CARGA", None, usuario.id)

    assert _cantidad("ventas", f"id = {venta.id} AND estado = 'ACTIVA'") == 1
    assert servicio_stock.obtener_por_id(producto.id).stock_actual == 498


def test_no_se_puede_anular_sin_ninguna_caja_abierta():
    usuario = _usuario()
    producto = _producto()
    servicio_caja.abrir_caja(0)
    venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
    servicio_caja.cerrar_caja(1000)

    with pytest.raises(VentaDeCajaCerradaError):
        servicio_ventas.anular_venta(venta.id, "ERROR_CARGA", None, usuario.id)


# --- Concurrencia -------------------------------------------------------------------


def _correr_en_hilos(tareas):
    """Ejecuta cada callable en su propio hilo, todos arrancando a la vez.
    Devuelve una lista con el resultado o la excepción de cada uno."""
    resultados = [None] * len(tareas)
    largada = threading.Barrier(len(tareas))

    def ejecutar(indice, tarea):
        largada.wait()
        try:
            resultados[indice] = tarea()
        except Exception as error:  # noqa: BLE001 -- el test inspecciona cada resultado
            resultados[indice] = error

    hilos = [threading.Thread(target=ejecutar, args=(i, t)) for i, t in enumerate(tareas)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=60)
    return resultados


def test_aperturas_concurrentes_solo_una_gana_y_hay_una_unica_sesion_abierta():
    resultados = _correr_en_hilos([lambda: servicio_caja.abrir_caja(100)] * 6)

    exitos = [r for r in resultados if not isinstance(r, Exception)]
    fallos = [r for r in resultados if isinstance(r, Exception)]
    assert len(exitos) == 1
    assert all(isinstance(f, CajaError) for f in fallos), fallos
    assert len([s for s in _sesiones() if s["estado"] == "ABIERTA"]) == 1
    assert _cantidad("caja_movimientos", "tipo = 'APERTURA'") == 1


def test_ventas_concurrentes_con_un_cierre_quedan_todas_en_la_sesion_o_se_rechazan_limpio():
    """Cada venta confirmada pertenece a la sesión y suma al esperado que
    calculó el cierre; las que llegan después del cierre se rechazan sin
    dejar filas. El esperado no puede perder ni duplicar ninguna."""
    producto = _producto()
    servicio_caja.abrir_caja(0)
    tareas = [lambda: servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")] * 8
    tareas.append(lambda: servicio_caja.cerrar_caja(0))

    resultados = _correr_en_hilos(tareas)

    inesperados = [
        r for r in resultados if isinstance(r, Exception) and not isinstance(r, (CajaCerradaError, CajaError))
    ]
    assert inesperados == [], inesperados
    (sesion,) = _sesiones()
    assert sesion["estado"] == "CERRADA"
    ventas_en_sesion = _cantidad("ventas", f"sesion_caja_id = {sesion['id']}")
    assert ventas_en_sesion == _cantidad("ventas")  # ninguna venta quedó fuera de la única sesión
    assert ventas_en_sesion == len([r for r in resultados[:-1] if not isinstance(r, Exception)])
    # esperado calculado en el cierre = fondo + ventas efectivo de la sesión (contado 0 => diferencia = -esperado)
    assert -sesion["diferencia_centavos"] == 1000 * ventas_en_sesion


def test_cierre_y_apertura_concurrentes_nunca_dejan_dos_sesiones_abiertas():
    servicio_caja.abrir_caja(0)

    resultados = _correr_en_hilos(
        [lambda: servicio_caja.cerrar_caja(0), lambda: servicio_caja.abrir_caja(50), lambda: servicio_caja.abrir_caja(60)]
    )

    assert len([s for s in _sesiones() if s["estado"] == "ABIERTA"]) <= 1
    assert not [r for r in resultados if isinstance(r, Exception) and not isinstance(r, CajaError)], resultados
    with obtener_conexion() as conexion:
        assert conexion.execute("PRAGMA foreign_key_check").fetchall() == []
