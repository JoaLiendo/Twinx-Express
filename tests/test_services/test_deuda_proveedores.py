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

    def test_sin_situacion_la_inconsistente_aparece(self, e):
        self._armar_libro_imposible(e)

        reporte = _reporte()

        assert [f.proveedor_id for f in reporte.filas] == [e.a.id]
        assert reporte.excluidos_por_inconsistencia == 0

    def test_proveedor_especifico_inconsistente_aparece(self, e):
        self._armar_libro_imposible(e)
        _comprar(e, e.b, 70, dias=3)

        assert [f.proveedor_id for f in _reporte(proveedor_id=e.a.id).filas] == [e.a.id]

    @pytest.mark.parametrize("situacion", ["vencida", "proxima", "sin_vencimiento"])
    def test_inconsistente_no_coincide_con_un_filtro_de_situacion(self, e, situacion):
        self._armar_libro_imposible(e)

        reporte = _reporte(situacion=situacion)

        assert reporte.filas == ()
        assert reporte.excluidos_por_inconsistencia == 1

    def test_el_filtro_de_situacion_informa_excluidas_y_conserva_a_las_consistentes(self, e):
        self._armar_libro_imposible(e)
        _comprar(e, e.b, 70, dias=-3)

        reporte = _reporte(situacion="vencida")

        assert [f.proveedor_id for f in reporte.filas] == [e.b.id]
        assert reporte.excluidos_por_inconsistencia == 1

    def test_proveedor_y_situacion_sobre_inconsistente_lo_excluye_e_informa(self, e):
        self._armar_libro_imposible(e)

        reporte = _reporte(proveedor_id=e.a.id, situacion="vencida")

        assert reporte.filas == ()
        assert reporte.excluidos_por_inconsistencia == 1

    def test_una_inconsistente_no_afecta_al_resto(self, e):
        self._armar_libro_imposible(e)
        _comprar(e, e.b, 70, dias=3)

        assert _buckets(_fila(_reporte(), e.b)) == (0, 70, 0, 0)


def _cuenta(e, proveedor=None):
    return servicio_deuda_proveedores.generar_cuenta_proveedor((proveedor or e.a).id, hoy=HOY)


def _cuatro(cuenta):
    return (cuenta.vencida_centavos, cuenta.proxima_centavos, cuenta.vigente_centavos, cuenta.sin_vencimiento_centavos)


class TestCuentaProveedor:
    def test_proveedor_sin_movimientos(self, e):
        cuenta = _cuenta(e)

        assert cuenta.consistente and cuenta.saldo_centavos == 0
        assert cuenta.compras_abiertas == () and cuenta.proximo_vencimiento is None
        assert _cuatro(cuenta) == (0, 0, 0, 0)

    def test_proveedor_sin_deuda_tras_pagar_todo(self, e):
        _comprar(e, e.a, 100, dias=3)
        _pagar(e, e.a, 100)

        cuenta = _cuenta(e)

        assert cuenta.saldo_centavos == 0 and cuenta.compras_abiertas == () and cuenta.proximo_vencimiento is None

    def test_una_compra_por_estado_y_orden_por_antiguedad(self, e):
        c1 = _comprar(e, e.a, 100, dias=-5)
        c2 = _comprar(e, e.a, 200, dias=3)
        c3 = _comprar(e, e.a, 300, dias=30)
        c4 = _comprar(e, e.a, 400)

        cuenta = _cuenta(e)

        assert [(c.compra_id, c.pendiente_estimado_centavos, c.estado_vencimiento) for c in cuenta.compras_abiertas] == [
            (c1.id, 100, "VENCIDA"), (c2.id, 200, "PROXIMA_A_VENCER"), (c3.id, 300, "PENDIENTE"), (c4.id, 400, "SIN_VENCIMIENTO"),
        ]
        assert _cuatro(cuenta) == (100, 200, 300, 400)

    def test_la_compra_abierta_trae_fecha_total_y_vencimiento(self, e):
        _comprar(e, e.a, 250, dias=3)

        abierta = _cuenta(e).compras_abiertas[0]

        assert abierta.fecha == HOY.isoformat()
        assert abierta.total_centavos == 250
        assert abierta.fecha_vencimiento == HOY + timedelta(days=3)

    def test_pago_parcial_deja_pendiente_estimado_menor(self, e):
        _comprar(e, e.a, 100, dias=-5)
        _comprar(e, e.a, 200, dias=3)
        _pagar(e, e.a, 40)

        assert [c.pendiente_estimado_centavos for c in _cuenta(e).compras_abiertas] == [60, 200]

    def test_pago_que_cruza_compras_cierra_la_primera(self, e):
        c1 = _comprar(e, e.a, 100, dias=-5)
        c2 = _comprar(e, e.a, 200, dias=3)
        _pagar(e, e.a, 150)

        cuenta = _cuenta(e)

        assert [(c.compra_id, c.pendiente_estimado_centavos) for c in cuenta.compras_abiertas] == [(c2.id, 150)]
        assert cuenta.pendiente_de(c1.id) == 0 and cuenta.pendiente_de(c2.id) == 150

    def test_compra_anulada_no_esta_abierta_ni_tiene_pendiente(self, e):
        activa = _comprar(e, e.a, 100, dias=-5)
        anulada = _comprar(e, e.a, 300, dias=3)
        servicio_compras.anular_compra(anulada.id, "ERROR_CARGA", None, e.owner.id)

        cuenta = _cuenta(e)

        assert [c.compra_id for c in cuenta.compras_abiertas] == [activa.id]
        assert cuenta.pendiente_de(anulada.id) == 0

    def test_compra_contado_no_tiene_pendiente_estimado(self, e):
        contado = _comprar(e, e.a, 500, condicion="CONTADO")
        _comprar(e, e.a, 100)

        cuenta = _cuenta(e)

        assert cuenta.pendiente_de(contado.id) is None
        assert len(cuenta.compras_abiertas) == 1

    def test_la_suma_de_abiertas_es_el_saldo(self, e):
        _comprar(e, e.a, 100, dias=-5)
        _comprar(e, e.a, 250)
        _comprar(e, e.a, 90, dias=40)
        _pagar(e, e.a, 130)

        cuenta = _cuenta(e)

        assert sum(c.pendiente_estimado_centavos for c in cuenta.compras_abiertas) == cuenta.saldo_centavos == 310

    def test_coincide_con_la_fila_del_reporte(self, e):
        _comprar(e, e.a, 100, dias=-5)
        _comprar(e, e.a, 250, dias=2)
        _pagar(e, e.a, 30)

        cuenta, fila = _cuenta(e), _fila(_reporte(), e.a)

        assert (cuenta.saldo_centavos, *_cuatro(cuenta)) == (fila.saldo_total_centavos, *_buckets(fila))


class TestProximoVencimiento:
    def test_es_la_fecha_mas_cercana_desde_hoy_e_ignora_las_vencidas(self, e):
        _comprar(e, e.a, 100, dias=-5)
        _comprar(e, e.a, 300, dias=30)
        _comprar(e, e.a, 200, dias=3)
        _comprar(e, e.a, 400)

        proximo = _cuenta(e).proximo_vencimiento

        assert (proximo.fecha, proximo.importe_centavos) == (HOY + timedelta(days=3), 200)

    def test_vence_hoy_cuenta_como_proximo(self, e):
        _comprar(e, e.a, 100, dias=0)

        assert _cuenta(e).proximo_vencimiento.fecha == HOY

    def test_suma_las_compras_con_la_misma_fecha(self, e):
        _comprar(e, e.a, 100, dias=5)
        _comprar(e, e.a, 50, dias=5)

        assert _cuenta(e).proximo_vencimiento.importe_centavos == 150

    def test_solo_vencidas_y_sin_vencimiento_no_hay_proximo(self, e):
        _comprar(e, e.a, 100, dias=-5)
        _comprar(e, e.a, 400)

        assert _cuenta(e).proximo_vencimiento is None

    def test_una_compra_pagada_no_es_el_proximo(self, e):
        _comprar(e, e.a, 100, dias=3)
        _comprar(e, e.a, 300, dias=30)
        _pagar(e, e.a, 100)

        proximo = _cuenta(e).proximo_vencimiento

        assert (proximo.fecha, proximo.importe_centavos) == (HOY + timedelta(days=30), 300)

    def test_el_importe_es_el_pendiente_estimado_no_el_total(self, e):
        _comprar(e, e.a, 200, dias=3)
        _pagar(e, e.a, 50)

        assert _cuenta(e).proximo_vencimiento.importe_centavos == 150


class TestCuentaInconsistente:
    def test_sin_cifras_fifo_y_con_saldo_si_es_confiable(self, e):
        _comprar(e, e.a, 100, dias=-5)
        _forzar_pago_imposible(e, e.a, 150)
        _comprar(e, e.a, 100)

        cuenta = _cuenta(e)

        assert not cuenta.consistente
        assert cuenta.saldo_centavos == 50 and cuenta.saldo_confiable
        assert _cuatro(cuenta) == (None,) * 4
        assert cuenta.compras_abiertas == () and cuenta.proximo_vencimiento is None
        assert cuenta.pendiente_de(1) is None

    def test_saldo_negativo_no_es_confiable(self, e):
        _comprar(e, e.a, 100)
        _forzar_pago_imposible(e, e.a, 5000)

        cuenta = _cuenta(e)

        assert not cuenta.consistente and not cuenta.saldo_confiable

    def test_registra_warning_y_no_modifica_el_libro(self, e, caplog):
        _comprar(e, e.a, 100)
        _forzar_pago_imposible(e, e.a, 5000)
        antes = [tuple(f) for f in consultar(e.ruta, "SELECT * FROM movimientos_proveedor ORDER BY id")]

        with caplog.at_level(logging.WARNING):
            _cuenta(e)

        assert any("inconsistente" in r.message.lower() for r in caplog.records)
        assert [tuple(f) for f in consultar(e.ruta, "SELECT * FROM movimientos_proveedor ORDER BY id")] == antes
