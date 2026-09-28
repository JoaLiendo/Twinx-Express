"""Pruebas de integración de services.servicio_pagos_proveedor (V1.9-C) contra una base de datos
SQLite real (temporal y aislada, ver tests/conftest.py)."""

import threading

import pytest

from db.conexion import obtener_conexion
from db.repositorios import movimientos_proveedor as repositorio_movimientos_proveedor
from domain.compra import ItemCompra
from excepciones import (
    CajaCerradaError,
    ClaveIdempotenciaReutilizadaError,
    CompraConPagosPosterioresError,
    PagoProveedorInvalidoError,
    PermisoDenegadoError,
    ProveedorNoEncontradoError,
)
from services import servicio_caja, servicio_compras, servicio_pagos_proveedor, servicio_proveedores, servicio_stock


def _crear_usuario(rol="OWNER", nombre_usuario="duenio"):
    from db.repositorios import usuarios as repositorio_usuarios
    from domain.usuario import Usuario

    return repositorio_usuarios.crear_usuario(
        Usuario(nombre_usuario=nombre_usuario, nombre_completo="Dueño", password_hash="hash", rol=rol)
    )


def _proveedor_con_deuda(monto_centavos=1200):
    """`monto_centavos` debe ser múltiplo de 100 (costo unitario fijo) para que la deuda generada
    sea exactamente ese valor."""
    assert monto_centavos % 100 == 0
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    usuario = _crear_usuario()
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)
    servicio_compras.registrar_compra(
        proveedor.id, usuario.id, [ItemCompra(producto.id, monto_centavos // 100, 100)], condicion_pago="CREDITO"
    )
    return proveedor, usuario


def _saldo(proveedor_id):
    return repositorio_movimientos_proveedor.obtener_saldo(proveedor_id)


class TestPagoEfectivoFeliz:
    def test_pago_efectivo_reduce_el_saldo_y_crea_egreso_de_caja(self, base_datos_temporal):
        proveedor, usuario = _proveedor_con_deuda(1200)
        servicio_caja.abrir_caja(0, usuario_id=usuario.id)

        pago = servicio_pagos_proveedor.registrar_pago(proveedor.id, 500, "EFECTIVO", usuario.id)

        assert pago.tipo == "PAGO"
        assert pago.monto_centavos == 500
        assert pago.medio_pago == "EFECTIVO"
        assert pago.caja_movimiento_id is not None
        assert _saldo(proveedor.id) == 700

        with obtener_conexion() as conexion:
            egreso = conexion.execute(
                "SELECT tipo, monto_centavos, origen FROM caja_movimientos WHERE id = ?", (pago.caja_movimiento_id,)
            ).fetchone()
        assert (egreso["tipo"], egreso["monto_centavos"], egreso["origen"]) == ("EGRESO", 500, "PAGO_PROVEEDOR")

    def test_pago_efectivo_sin_caja_abierta_falla(self, base_datos_temporal):
        proveedor, usuario = _proveedor_con_deuda(1200)

        with pytest.raises(CajaCerradaError):
            servicio_pagos_proveedor.registrar_pago(proveedor.id, 500, "EFECTIVO", usuario.id)

        assert _saldo(proveedor.id) == 1200
        assert repositorio_movimientos_proveedor.obtener_saldo(proveedor.id) == 1200

    def test_pago_por_el_saldo_exacto_lo_deja_en_cero(self, base_datos_temporal):
        proveedor, usuario = _proveedor_con_deuda(1200)
        servicio_caja.abrir_caja(0, usuario_id=usuario.id)

        servicio_pagos_proveedor.registrar_pago(proveedor.id, 1200, "EFECTIVO", usuario.id)

        assert _saldo(proveedor.id) == 0


class TestPagoTransferenciaFeliz:
    def test_transferencia_funciona_sin_caja_abierta(self, base_datos_temporal):
        proveedor, usuario = _proveedor_con_deuda(1200)

        pago = servicio_pagos_proveedor.registrar_pago(proveedor.id, 500, "TRANSFERENCIA", usuario.id)

        assert pago.medio_pago == "TRANSFERENCIA"
        assert pago.caja_movimiento_id is None
        assert _saldo(proveedor.id) == 700

    def test_transferencia_no_crea_ningun_movimiento_de_caja(self, base_datos_temporal):
        proveedor, usuario = _proveedor_con_deuda(1200)

        servicio_pagos_proveedor.registrar_pago(proveedor.id, 500, "TRANSFERENCIA", usuario.id)

        with obtener_conexion() as conexion:
            total = conexion.execute("SELECT COUNT(*) FROM caja_movimientos").fetchone()[0]
        assert total == 0

    def test_m4_transferencia_nunca_llama_a_registrar_movimiento_de_caja(self, base_datos_temporal, monkeypatch):
        """M4: si un futuro cambio hiciera que una transferencia intentara tocar caja, esto debe
        fallar de inmediato en vez de crear el movimiento."""
        from db.repositorios import caja as repositorio_caja

        proveedor, usuario = _proveedor_con_deuda(1200)

        def no_deberia_llamarse(*args, **kwargs):
            raise AssertionError("una transferencia no debe crear ningún movimiento de caja")

        monkeypatch.setattr(repositorio_caja, "registrar_movimiento_en_conexion", no_deberia_llamarse)

        pago = servicio_pagos_proveedor.registrar_pago(proveedor.id, 500, "TRANSFERENCIA", usuario.id)
        assert pago.caja_movimiento_id is None


class TestValidaciones:
    def test_medio_invalido_falla(self, base_datos_temporal):
        proveedor, usuario = _proveedor_con_deuda(1200)

        with pytest.raises(PagoProveedorInvalidoError):
            servicio_pagos_proveedor.registrar_pago(proveedor.id, 500, "CHEQUE", usuario.id)
        assert _saldo(proveedor.id) == 1200

    def test_monto_cero_falla(self, base_datos_temporal):
        proveedor, usuario = _proveedor_con_deuda(1200)

        with pytest.raises(PagoProveedorInvalidoError):
            servicio_pagos_proveedor.registrar_pago(proveedor.id, 0, "TRANSFERENCIA", usuario.id)

    def test_monto_negativo_falla(self, base_datos_temporal):
        proveedor, usuario = _proveedor_con_deuda(1200)

        with pytest.raises(PagoProveedorInvalidoError):
            servicio_pagos_proveedor.registrar_pago(proveedor.id, -100, "TRANSFERENCIA", usuario.id)

    def test_m1_sobrepago_falla(self, base_datos_temporal):
        proveedor, usuario = _proveedor_con_deuda(1200)

        with pytest.raises(PagoProveedorInvalidoError):
            servicio_pagos_proveedor.registrar_pago(proveedor.id, 1201, "TRANSFERENCIA", usuario.id)
        assert _saldo(proveedor.id) == 1200  # sin efecto: nunca queda un saldo negativo

    def test_saldo_cero_no_permite_ningun_pago(self, base_datos_temporal):
        proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
        usuario = _crear_usuario()

        with pytest.raises(PagoProveedorInvalidoError):
            servicio_pagos_proveedor.registrar_pago(proveedor.id, 1, "TRANSFERENCIA", usuario.id)

    def test_proveedor_inexistente_falla(self, base_datos_temporal):
        usuario = _crear_usuario()

        with pytest.raises(ProveedorNoEncontradoError):
            servicio_pagos_proveedor.registrar_pago(9999, 100, "TRANSFERENCIA", usuario.id)

    def test_proveedor_inactivo_con_deuda_puede_pagarse(self, base_datos_temporal):
        """Mismo criterio que domain.cliente: un proveedor inactivo con deuda puede pagarla; solo
        se le prohíben las compras nuevas (eso ya lo garantiza registrar_compra)."""
        proveedor, usuario = _proveedor_con_deuda(1200)
        servicio_proveedores.eliminar_proveedor(proveedor.id)

        pago = servicio_pagos_proveedor.registrar_pago(proveedor.id, 1200, "TRANSFERENCIA", usuario.id)

        assert pago.monto_centavos == 1200
        assert _saldo(proveedor.id) == 0

    def test_cashier_no_puede_registrar_pagos(self, base_datos_temporal):
        proveedor, _owner = _proveedor_con_deuda(1200)
        cashier = _crear_usuario(rol="CASHIER", nombre_usuario="carlos")

        with pytest.raises(PermisoDenegadoError):
            servicio_pagos_proveedor.registrar_pago(proveedor.id, 500, "TRANSFERENCIA", cashier.id)
        assert _saldo(proveedor.id) == 1200


class TestGuardiaDeEsquema:
    def test_m2_pago_efectivo_sin_egreso_real_es_rechazado_por_la_base(self, base_datos_temporal):
        """M2: aunque el servicio siempre crea el EGRESO antes del PAGO, la base es la última
        defensa -- un PAGO en efectivo sin `caja_movimiento_id` (sin egreso real) viola el CHECK
        de la tabla, sin depender de que el código del servicio nunca tenga ese bug."""
        proveedor, usuario = _proveedor_con_deuda(1200)

        with obtener_conexion() as conexion, pytest.raises(Exception):
            conexion.execute(
                "INSERT INTO movimientos_proveedor (proveedor_id, tipo, monto_centavos, medio_pago)"
                " VALUES (?, 'PAGO', 500, 'EFECTIVO')",
                (proveedor.id,),
            )
        assert _saldo(proveedor.id) == 1200


class TestAtomicidad:
    def test_falla_el_pago_revierte_el_egreso_de_caja(self, base_datos_temporal, monkeypatch):
        """Nunca debe existir un EGRESO PAGO_PROVEEDOR sin su PAGO: si la inserción del PAGO falla
        (simulado acá), toda la transacción se revierte, incluido el egreso ya creado."""
        from excepciones import ErrorBaseDatos

        proveedor, usuario = _proveedor_con_deuda(1200)
        servicio_caja.abrir_caja(0, usuario_id=usuario.id)

        def falla_el_pago(*args, **kwargs):
            raise ErrorBaseDatos("fallo simulado en el PAGO")

        monkeypatch.setattr(repositorio_movimientos_proveedor, "registrar_pago_en_conexion", falla_el_pago)

        with pytest.raises(ErrorBaseDatos):
            servicio_pagos_proveedor.registrar_pago(proveedor.id, 500, "EFECTIVO", usuario.id)

        assert _saldo(proveedor.id) == 1200
        with obtener_conexion() as conexion:
            assert conexion.execute("SELECT COUNT(*) FROM caja_movimientos WHERE origen = 'PAGO_PROVEEDOR'").fetchone()[0] == 0

    def test_m2_falla_el_egreso_no_crea_ningun_pago(self, base_datos_temporal, monkeypatch):
        from db.repositorios import caja as repositorio_caja
        from excepciones import ErrorBaseDatos

        proveedor, usuario = _proveedor_con_deuda(1200)
        servicio_caja.abrir_caja(0, usuario_id=usuario.id)

        def falla_el_egreso(*args, **kwargs):
            raise ErrorBaseDatos("fallo simulado en el egreso")

        monkeypatch.setattr(repositorio_caja, "registrar_movimiento_en_conexion", falla_el_egreso)

        with pytest.raises(ErrorBaseDatos):
            servicio_pagos_proveedor.registrar_pago(proveedor.id, 500, "EFECTIVO", usuario.id)

        assert _saldo(proveedor.id) == 1200
        with obtener_conexion() as conexion:
            assert conexion.execute("SELECT COUNT(*) FROM movimientos_proveedor WHERE tipo = 'PAGO'").fetchone()[0] == 0


class TestIdempotencia:
    def test_reintento_identico_devuelve_el_mismo_pago_sin_duplicar(self, base_datos_temporal):
        proveedor, usuario = _proveedor_con_deuda(1200)
        servicio_caja.abrir_caja(0, usuario_id=usuario.id)

        pago_1 = servicio_pagos_proveedor.registrar_pago(
            proveedor.id, 500, "EFECTIVO", usuario.id, clave_idempotencia="clave-1"
        )
        pago_2 = servicio_pagos_proveedor.registrar_pago(
            proveedor.id, 500, "EFECTIVO", usuario.id, clave_idempotencia="clave-1"
        )

        assert pago_1.id == pago_2.id
        assert _saldo(proveedor.id) == 700
        with obtener_conexion() as conexion:
            assert conexion.execute("SELECT COUNT(*) FROM movimientos_proveedor WHERE tipo = 'PAGO'").fetchone()[0] == 1
            assert conexion.execute("SELECT COUNT(*) FROM caja_movimientos WHERE origen = 'PAGO_PROVEEDOR'").fetchone()[0] == 1

    def test_m5_reintento_con_otro_contenido_se_rechaza_sin_duplicar(self, base_datos_temporal):
        proveedor, usuario = _proveedor_con_deuda(1200)
        servicio_caja.abrir_caja(0, usuario_id=usuario.id)

        servicio_pagos_proveedor.registrar_pago(
            proveedor.id, 500, "EFECTIVO", usuario.id, clave_idempotencia="clave-1"
        )

        with pytest.raises(ClaveIdempotenciaReutilizadaError):
            servicio_pagos_proveedor.registrar_pago(
                proveedor.id, 999, "EFECTIVO", usuario.id, clave_idempotencia="clave-1"
            )

        assert _saldo(proveedor.id) == 700  # solo el primer pago tuvo efecto
        with obtener_conexion() as conexion:
            assert conexion.execute("SELECT COUNT(*) FROM movimientos_proveedor WHERE tipo = 'PAGO'").fetchone()[0] == 1

    def test_reintento_sobrevive_a_que_la_caja_se_haya_cerrado(self, base_datos_temporal):
        """Mismo criterio que registrar_cobro: un reintento de un pago ya registrado devuelve el
        original aunque la caja se haya cerrado entretanto."""
        proveedor, usuario = _proveedor_con_deuda(1200)
        servicio_caja.abrir_caja(0, usuario_id=usuario.id)
        pago_1 = servicio_pagos_proveedor.registrar_pago(
            proveedor.id, 500, "EFECTIVO", usuario.id, clave_idempotencia="clave-1"
        )
        servicio_caja.cerrar_caja(0, usuario_id=usuario.id)

        pago_2 = servicio_pagos_proveedor.registrar_pago(
            proveedor.id, 500, "EFECTIVO", usuario.id, clave_idempotencia="clave-1"
        )

        assert pago_1.id == pago_2.id


class TestConcurrencia:
    def test_dos_pagos_simultaneos_nunca_dejan_saldo_negativo(self, base_datos_temporal):
        """Dos pagos reales y concurrentes (dos hilos, dos conexiones SQLite reales) que juntos
        superarían el saldo: `BEGIN IMMEDIATE` los serializa, así que como mucho uno de los dos
        consigue el monto completo y el otro se rechaza por sobrepago, nunca los dos."""
        proveedor, usuario = _proveedor_con_deuda(1000)  # saldo 1000, cada pago pide 700: juntos exceden

        barrera = threading.Barrier(2)
        resultados = {}

        def pagar(nombre):
            barrera.wait()
            try:
                pago = servicio_pagos_proveedor.registrar_pago(proveedor.id, 700, "TRANSFERENCIA", usuario.id)
                resultados[nombre] = ("ok", pago.id)
            except PagoProveedorInvalidoError:
                resultados[nombre] = ("rechazado", None)

        hilo_a = threading.Thread(target=pagar, args=("A",))
        hilo_b = threading.Thread(target=pagar, args=("B",))
        hilo_a.start()
        hilo_b.start()
        hilo_a.join(timeout=10)
        hilo_b.join(timeout=10)

        assert not hilo_a.is_alive() and not hilo_b.is_alive()
        estados = sorted(r[0] for r in resultados.values())
        assert estados == ["ok", "rechazado"]  # exactamente uno de los dos se aceptó
        assert _saldo(proveedor.id) == 300  # 1000 - 700, nunca negativo


class TestAuditoria:
    def test_auditoria_registra_proveedor_monto_y_medio(self, base_datos_temporal):
        proveedor, usuario = _proveedor_con_deuda(1200)

        servicio_pagos_proveedor.registrar_pago(proveedor.id, 500, "TRANSFERENCIA", usuario.id)

        with obtener_conexion() as conexion:
            fila = conexion.execute(
                "SELECT accion, entidad, entidad_id, resumen FROM auditoria WHERE accion = 'PAGO_PROVEEDOR_REGISTRADO'"
            ).fetchone()
        assert fila["entidad"] == "PROVEEDOR" and fila["entidad_id"] == proveedor.id
        assert "TRANSFERENCIA" in fila["resumen"] and proveedor.nombre in fila["resumen"]


class TestCajaYArqueo:
    def test_pago_efectivo_reduce_el_efectivo_estimado(self, base_datos_temporal):
        proveedor, usuario = _proveedor_con_deuda(1200)
        servicio_caja.abrir_caja(1000, usuario_id=usuario.id)
        estimado_antes = servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos

        servicio_pagos_proveedor.registrar_pago(proveedor.id, 500, "EFECTIVO", usuario.id)

        estimado_despues = servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos
        assert estimado_despues == estimado_antes - 500

    def test_pago_por_transferencia_no_altera_el_arqueo(self, base_datos_temporal):
        proveedor, usuario = _proveedor_con_deuda(1200)
        servicio_caja.abrir_caja(1000, usuario_id=usuario.id)
        estimado_antes = servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos

        servicio_pagos_proveedor.registrar_pago(proveedor.id, 500, "TRANSFERENCIA", usuario.id)

        estimado_despues = servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos
        assert estimado_despues == estimado_antes


class TestInteraccionConAnulacion:
    def test_un_pago_real_del_servicio_bloquea_la_anulacion_posterior(self, base_datos_temporal):
        """No reimplementa la regla de V1.9-B: solo confirma que un PAGO creado por el servicio
        real (no simulado con SQL directo) también dispara CompraConPagosPosterioresError."""
        proveedor, usuario = _proveedor_con_deuda(1200)
        compra = servicio_compras.listar_todas()[0]

        servicio_pagos_proveedor.registrar_pago(proveedor.id, 500, "TRANSFERENCIA", usuario.id)

        with pytest.raises(CompraConPagosPosterioresError):
            servicio_compras.anular_compra(compra.id, "ERROR_CARGA", None, usuario.id)