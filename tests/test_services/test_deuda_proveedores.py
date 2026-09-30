"""V1.10-D: servicio del reporte de deuda a proveedores. El saldo del libro es la verdad agregada; los
buckets (vencida/próxima/vigente/sin vencimiento) son una estimación FIFO por antigüedad y nunca se
presentan como válidos si el libro es imposible."""

import logging
from datetime import date, timedelta

import pytest

from domain.compra import ItemCompra
from excepciones import DatosInvalidosError
from services import servicio_compras, servicio_deuda_proveedores, servicio_pagos_proveedor, servicio_proveedores, servicio_stock
from tests.utilidades_clientes import consultar, escribir

HOY = date.fromisoformat(date.today().isoformat())


@pytest.fixture
def e(base_datos_temporal):
    from db.repositorios import usuarios as repositorio_usuarios
    from domain.usuario import Usuario

    owner = repositorio_usuarios.crear_usuario(
        Usuario(nombre_usuario="duenio", nombre_completo="Dueño", password_hash="hash-de-prueba", rol="OWNER")
    )
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 300, stock_actual=10)
    return type("E", (), {
        "ruta": base_datos_temporal, "owner": owner, "p": producto,
        "a": servicio_proveedores.crear_proveedor("Alfa SA"), "b": servicio_proveedores.crear_proveedor("Beta SRL"),
    })


def _comprar(e, proveedor, monto, dias=None, condicion="CREDITO"):
    """Compra por `monto` centavos. `dias` = vencimiento relativo a hoy (negativo: ya vencida, se retrocede
    la fecha de la compra para respetar el CHECK `fecha_vencimiento >= date(fecha)`)."""
    vencimiento = None
    if dias is not None and dias >= 0:
        vencimiento = (HOY + timedelta(days=dias)).isoformat()
    compra = servicio_compras.registrar_compra(
        proveedor.id, e.owner.id, [ItemCompra(e.p.id, 1, monto)],
        condicion_pago=condicion, fecha_vencimiento=vencimiento,
    )
    if dias is not None and dias < 0:
        escribir(e.ruta, "UPDATE compras SET fecha = ? WHERE id = ?", ((HOY - timedelta(days=60)).isoformat() + " 10:00:00", compra.id))
        escribir(e.ruta, "UPDATE compras SET fecha_vencimiento = ? WHERE id = ?", ((HOY + timedelta(days=dias)).isoformat(), compra.id))
    return compra


def _pagar(e, proveedor, monto):
    return servicio_pagos_proveedor.registrar_pago(proveedor.id, monto, "TRANSFERENCIA", e.owner.id)


def _reporte(**kw):
    return servicio_deuda_proveedores.generar_reporte_deuda_proveedores(hoy=HOY, **kw)


def _fila(reporte, proveedor):
    return next(f for f in reporte.filas if f.proveedor_id == proveedor.id)


def _buckets(fila):
    return (fila.vencida_centavos, fila.proxima_centavos, fila.vigente_centavos, fila.sin_vencimiento_centavos)


def _forzar_pago_imposible(e, proveedor, monto):
    escribir(
        e.ruta,
        "INSERT INTO movimientos_proveedor (proveedor_id, tipo, monto_centavos, medio_pago, usuario_id)"
        " VALUES (?, 'PAGO', ?, 'TRANSFERENCIA', ?)",
        (proveedor.id, monto, e.owner.id),
    )


class TestProveedorSimple:
    def test_una_compra_por_bucket(self, e):
        _comprar(e, e.a, 100, dias=-5)
        _comprar(e, e.a, 200, dias=3)
        _comprar(e, e.a, 300, dias=30)
        _comprar(e, e.a, 400)

        fila = _fila(_reporte(), e.a)

        assert fila.consistente
        assert fila.saldo_total_centavos == 1000
        assert _buckets(fila) == (100, 200, 300, 400)

    def test_suma_de_buckets_es_el_saldo(self, e):
        _comprar(e, e.a, 100, dias=-5)
        _comprar(e, e.a, 250)
        _pagar(e, e.a, 130)

        fila = _fila(_reporte(), e.a)

        assert sum(_buckets(fila)) == fila.saldo_total_centavos == 220

    def test_compra_contado_no_aporta_deuda(self, e):
        _comprar(e, e.a, 500, condicion="CONTADO")
        _comprar(e, e.a, 100)

        assert _fila(_reporte(), e.a).saldo_total_centavos == 100


class TestPagos:
    def test_pago_parcial_baja_la_mas_antigua(self, e):
        _comprar(e, e.a, 100, dias=-5)
        _comprar(e, e.a, 200, dias=3)
        _pagar(e, e.a, 40)

        assert _buckets(_fila(_reporte(), e.a)) == (60, 200, 0, 0)

    def test_pago_cruza_compras(self, e):
        _comprar(e, e.a, 100, dias=-5)
        _comprar(e, e.a, 200, dias=3)
        _pagar(e, e.a, 150)

        assert _buckets(_fila(_reporte(), e.a)) == (0, 150, 0, 0)

    def test_pago_total_saca_al_proveedor_del_reporte(self, e):
        _comprar(e, e.a, 100, dias=-5)
        _pagar(e, e.a, 100)

        assert _reporte().filas == ()

    def test_causalidad_un_pago_no_cubre_compras_posteriores(self, e):
        # El pago ocurre cuando solo existía la compra sin vencimiento: no puede descontar la vencida posterior.
        _comprar(e, e.a, 100)
        _pagar(e, e.a, 100)
        _comprar(e, e.a, 100, dias=-5)

        assert _buckets(_fila(_reporte(), e.a)) == (100, 0, 0, 0)


class TestAnulaciones:
    def test_compra_anulada_no_aporta_deuda(self, e):
        _comprar(e, e.a, 100, dias=-5)
        anulada = _comprar(e, e.a, 300, dias=3)
        servicio_compras.anular_compra(anulada.id, "ERROR_CARGA", None, e.owner.id)

        fila = _fila(_reporte(), e.a)

        assert fila.saldo_total_centavos == 100
        assert _buckets(fila) == (100, 0, 0, 0)

    def test_ultima_compra_ignora_anuladas(self, e):
        primera = _comprar(e, e.a, 100)
        segunda = _comprar(e, e.a, 300)
        servicio_compras.anular_compra(segunda.id, "ERROR_CARGA", None, e.owner.id)
        escribir(e.ruta, "UPDATE compras SET fecha = '2020-01-01 10:00:00' WHERE id = ?", (primera.id,))

        assert _fila(_reporte(), e.a).ultima_compra == "2020-01-01"


class TestUltimoPago:
    def test_ultimo_pago_es_la_fecha_del_pago_mas_reciente(self, e):
        _comprar(e, e.a, 500)
        _pagar(e, e.a, 100)
        ultimo = _pagar(e, e.a, 100)
        escribir(e.ruta, "DROP TRIGGER trg_movimientos_proveedor_inmutable")
        escribir(e.ruta, "UPDATE movimientos_proveedor SET fecha = '2021-03-04 09:00:00' WHERE id = ?", (ultimo.id,))

        assert _fila(_reporte(), e.a).ultimo_pago == "2021-03-04"

    def test_sin_pagos_es_none(self, e):
        _comprar(e, e.a, 500)

        assert _fila(_reporte(), e.a).ultimo_pago is None

    def test_ultima_compra_es_la_fecha_de_la_compra_activa(self, e):
        _comprar(e, e.a, 500)

        assert _fila(_reporte(), e.a).ultima_compra == date.today().isoformat()


class TestMultiplesProveedores:
    def test_cada_proveedor_con_su_propio_replay(self, e):
        _comprar(e, e.a, 100, dias=-5)
        _comprar(e, e.b, 700)
        _pagar(e, e.a, 30)

        reporte = _reporte()

        assert _buckets(_fila(reporte, e.a)) == (70, 0, 0, 0)
        assert _buckets(_fila(reporte, e.b)) == (0, 0, 0, 700)
        assert [f.nombre for f in reporte.filas] == ["Alfa SA", "Beta SRL"]

    def test_proveedor_sin_deuda_no_aparece_por_defecto(self, e):
        _comprar(e, e.a, 100)
        _comprar(e, e.b, 100)
        _pagar(e, e.b, 100)

        assert [f.proveedor_id for f in _reporte().filas] == [e.a.id]

    def test_proveedor_inactivo_con_deuda_aparece_marcado(self, e):
        _comprar(e, e.a, 100)
        escribir(e.ruta, "UPDATE proveedores SET activo = 0 WHERE id = ?", (e.a.id,))

        assert _fila(_reporte(), e.a).activo is False

    def test_sin_movimientos_el_reporte_esta_vacio(self, e):
        reporte = _reporte()

        assert reporte.filas == ()
        assert reporte.total_saldo_centavos == 0


class TestTotales:
    def test_totales_suman_las_filas(self, e):
        _comprar(e, e.a, 100, dias=-5)
        _comprar(e, e.a, 50)
        _comprar(e, e.b, 200, dias=3)

        reporte = _reporte()

        assert reporte.total_saldo_centavos == 350
        assert reporte.total_vencida_centavos == 100
        assert reporte.total_proxima_centavos == 200
        assert reporte.total_vigente_centavos == 0
        assert reporte.total_sin_vencimiento_centavos == 50
        assert (
            reporte.total_vencida_centavos + reporte.total_proxima_centavos + reporte.total_vigente_centavos
            + reporte.total_sin_vencimiento_centavos == reporte.total_saldo_centavos
        )


class TestFiltros:
    @pytest.fixture(autouse=True)
    def _datos(self, e):
        _comprar(e, e.a, 100, dias=-5)   # A: vencida
        _comprar(e, e.b, 200, dias=3)    # B: próxima
        _comprar(e, e.b, 50)             # B: sin vencimiento

    def test_por_proveedor(self, e):
        assert [f.proveedor_id for f in _reporte(proveedor_id=e.b.id).filas] == [e.b.id]

    def test_situacion_vencida(self, e):
        assert [f.proveedor_id for f in _reporte(situacion="vencida").filas] == [e.a.id]

    def test_situacion_proxima(self, e):
        assert [f.proveedor_id for f in _reporte(situacion="proxima").filas] == [e.b.id]

    def test_situacion_sin_vencimiento(self, e):
        assert [f.proveedor_id for f in _reporte(situacion="sin_vencimiento").filas] == [e.b.id]

    def test_combinado_proveedor_y_situacion(self, e):
        assert [f.proveedor_id for f in _reporte(proveedor_id=e.b.id, situacion="proxima").filas] == [e.b.id]
        assert _reporte(proveedor_id=e.a.id, situacion="proxima").filas == ()

    def test_filtro_sin_resultados(self, e):
        reporte = _reporte(situacion="vencida", proveedor_id=e.b.id)

        assert reporte.filas == ()
        assert reporte.total_saldo_centavos == 0

    def test_totales_solo_de_lo_filtrado(self, e):
        assert _reporte(proveedor_id=e.b.id).total_saldo_centavos == 250

    def test_situacion_invalida(self, e):
        with pytest.raises(DatosInvalidosError):
            _reporte(situacion="cualquiera")

    def test_situacion_vacia_equivale_a_todas(self, e):
        assert len(_reporte(situacion="").filas) == 2


class TestInconsistencia:
    def _armar_libro_imposible(self, e):
        _comprar(e, e.a, 100, dias=-5)
        _forzar_pago_imposible(e, e.a, 900)  # el pago supera el saldo de ese punto causal

    def test_la_fila_queda_marcada_y_sin_buckets(self, e):
        _comprar(e, e.a, 100, dias=-5)
        _comprar(e, e.a, 50)
        _forzar_pago_imposible(e, e.a, 200)  # supera lo adeudado

        reporte = _reporte()
        fila = _fila(reporte, e.a)

        assert not fila.consistente
        assert _buckets(fila) == (None, None, None, None)

    def test_saldo_agregado_se_muestra_solo_si_es_confiable(self, e):
        _comprar(e, e.a, 100)
        _forzar_pago_imposible(e, e.a, 150)     # imposible en ese punto, pero el saldo final es >= 0
        _comprar(e, e.a, 100)
        _comprar(e, e.b, 100)
        _forzar_pago_imposible(e, e.b, 5000)    # saldo negativo: no confiable

        reporte = _reporte()

        assert not _fila(reporte, e.a).consistente
        assert _fila(reporte, e.a).saldo_total_centavos == 50
        assert not _fila(reporte, e.b).consistente
        assert _fila(reporte, e.b).saldo_total_centavos is None

    def test_las_inconsistentes_no_entran_a_los_totales_fifo(self, e):
        _comprar(e, e.a, 100, dias=-5)
        _comprar(e, e.b, 200, dias=3)
        _forzar_pago_imposible(e, e.a, 900)  # A pasa a saldo negativo -> inconsistente

        reporte = _reporte()

        assert reporte.cantidad_inconsistentes == 1
        assert reporte.total_saldo_centavos == 200
        assert reporte.total_proxima_centavos == 200
        assert reporte.total_vencida_centavos == 0

    def test_no_modifica_el_libro(self, e):
        self._armar_libro_imposible(e)
        antes = [tuple(f) for f in consultar(e.ruta, "SELECT * FROM movimientos_proveedor ORDER BY id")]

        _reporte()

        assert [tuple(f) for f in consultar(e.ruta, "SELECT * FROM movimientos_proveedor ORDER BY id")] == antes

    def test_registra_un_warning(self, e, caplog):
        self._armar_libro_imposible(e)

        with caplog.at_level(logging.WARNING):
            _reporte()

        assert any("inconsistente" in r.message.lower() and str(e.a.id) in r.message for r in caplog.records)

    def test_sigue_listada_con_cualquier_filtro_de_situacion(self, e):
        self._armar_libro_imposible(e)

        assert [f.proveedor_id for f in _reporte(situacion="proxima").filas] == [e.a.id]

    def test_una_inconsistente_no_afecta_al_resto(self, e):
        self._armar_libro_imposible(e)
        _comprar(e, e.b, 70, dias=3)

        assert _buckets(_fila(_reporte(), e.b)) == (0, 70, 0, 0)
