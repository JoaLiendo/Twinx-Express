"""RC4: una compra al contado dice explícitamente cómo se pagó. Con efectivo de la caja genera un egreso
vinculado, atómico e idempotente; fuera de la caja no altera el efectivo esperado. Al anular una compra
pagada con la caja, el OWNER indica si el efectivo vuelve (nunca se asume ni se borra el egreso)."""

import sqlite3

import pytest

import db.conexion as modulo_conexion
from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import compras as repositorio_compras
from db.repositorios import usuarios as repositorio_usuarios
from domain.compra import ItemCompra
from domain.usuario import Usuario
from excepciones import (
    CajaCerradaError,
    ClaveIdempotenciaReutilizadaError,
    CompraYaAnuladaError,
    DatosInvalidosError,
    ErrorBaseDatos,
)
from services import servicio_caja, servicio_compras, servicio_proveedores, servicio_stock


@pytest.fixture
def escenario(caja_abierta):
    usuario = repositorio_usuarios.crear_usuario(
        Usuario(nombre_usuario="ana", nombre_completo="Ana", password_hash="hash-de-prueba", rol="OWNER")
    )
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=5)
    return {"usuario": usuario.id, "proveedor": proveedor.id, "producto": producto.id}


def _comprar(escenario, origen_pago, cantidad=10, costo=120, **extra):
    return servicio_compras.registrar_compra(
        proveedor_id=escenario["proveedor"],
        usuario_id=escenario["usuario"],
        items=[ItemCompra(escenario["producto"], cantidad, costo)],
        origen_pago=origen_pago,
        **extra,
    )


def _efectivo_esperado():
    return servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos


def _movimientos_caja():
    return [(m.tipo, m.monto_centavos, m.origen) for m in servicio_caja.listar_movimientos_de_sesion_actual()]


def _stock(escenario):
    return servicio_stock.obtener_por_id(escenario["producto"]).stock_actual


# --- Registro ----------------------------------------------------------------------------------------


def test_con_efectivo_de_la_caja_crea_un_egreso_vinculado_y_baja_el_esperado(escenario):
    antes = _efectivo_esperado()

    compra = _comprar(escenario, "CAJA_ACTUAL")

    assert compra.origen_pago == "CAJA_ACTUAL" and compra.caja_movimiento_id is not None
    assert _efectivo_esperado() == antes - 1200
    egreso = [m for m in servicio_caja.listar_movimientos_de_sesion_actual() if m.id == compra.caja_movimiento_id][0]
    assert (egreso.tipo, egreso.monto_centavos, egreso.origen) == ("EGRESO", 1200, "PAGO_PROVEEDOR")
    assert "Distribuidora SA" in egreso.descripcion


def test_fuera_de_la_caja_no_toca_la_caja_y_queda_persistido_y_auditado(escenario):
    antes, movimientos = _efectivo_esperado(), _movimientos_caja()

    compra = _comprar(escenario, "FUERA_DE_CAJA")

    assert _efectivo_esperado() == antes and _movimientos_caja() == movimientos
    assert repositorio_compras.obtener_por_id(compra.id).origen_pago == "FUERA_DE_CAJA"
    auditoria = repositorio_auditoria.listar(accion="COMPRA_REGISTRADA")
    assert "pagada fuera de la caja" in auditoria[0].resumen


def test_sin_indicar_como_se_pago_se_rechaza(escenario):
    with pytest.raises(DatosInvalidosError, match="Indicá cómo se pagó"):
        _comprar(escenario, "")
    assert _stock(escenario) == 5


def test_origen_invalido_o_en_compra_a_credito_se_rechaza(escenario):
    with pytest.raises(DatosInvalidosError):
        _comprar(escenario, "BOLSILLO")
    for origen in ("CAJA_ACTUAL", "FUERA_DE_CAJA"):
        with pytest.raises(DatosInvalidosError, match="crédito"):
            _comprar(escenario, origen, condicion_pago="CREDITO")
    assert _stock(escenario) == 5
    assert _comprar(escenario, None, condicion_pago="CREDITO").origen_pago is None


def test_contado_con_origen_none_se_rechaza_en_el_servicio_sin_inferir_ninguno(escenario):
    """Ningún caller interno puede registrar una compra al contado sin decir cómo se pagó: la API no
    infiere 'FUERA_DE_CAJA' ni ningún otro origen."""
    movimientos = _movimientos_caja()
    with pytest.raises(DatosInvalidosError, match="Indicá cómo se pagó"):
        _comprar(escenario, None)
    assert _stock(escenario) == 5 and _movimientos_caja() == movimientos
    assert repositorio_compras.listar_todas() == []


def test_contado_con_origen_explicito_se_registra(escenario):
    assert _comprar(escenario, "FUERA_DE_CAJA").origen_pago == "FUERA_DE_CAJA"
    assert _comprar(escenario, "CAJA_ACTUAL").origen_pago == "CAJA_ACTUAL"


@pytest.mark.sin_caja_abierta
def test_con_la_caja_cerrada_no_se_puede_pagar_con_su_efectivo_y_no_queda_nada(escenario):
    with pytest.raises(CajaCerradaError):
        _comprar(escenario, "CAJA_ACTUAL")
    assert _stock(escenario) == 5 and repositorio_compras.listar_todas() == []


def test_un_fallo_despues_del_egreso_revierte_compra_y_egreso(escenario, monkeypatch):
    movimientos = _movimientos_caja()

    def fallar(*args, **kwargs):
        raise sqlite3.OperationalError("falla simulada al insertar la compra")

    monkeypatch.setattr(repositorio_compras, "registrar_compra_con_detalle", fallar)
    with pytest.raises(ErrorBaseDatos):
        _comprar(escenario, "CAJA_ACTUAL")

    assert _movimientos_caja() == movimientos and _stock(escenario) == 5


def test_doble_envio_no_duplica_el_egreso_aunque_la_caja_ya_se_haya_cerrado(escenario):
    primera = _comprar(escenario, "CAJA_ACTUAL", clave_idempotencia="clave-1")
    egresos = [m for m in _movimientos_caja() if m[0] == "EGRESO"]

    assert _comprar(escenario, "CAJA_ACTUAL", clave_idempotencia="clave-1").id == primera.id
    assert [m for m in _movimientos_caja() if m[0] == "EGRESO"] == egresos
    servicio_caja.cerrar_caja(0)
    assert _comprar(escenario, "CAJA_ACTUAL", clave_idempotencia="clave-1").id == primera.id


def test_la_misma_clave_con_otro_origen_se_rechaza(escenario):
    _comprar(escenario, "CAJA_ACTUAL", clave_idempotencia="clave-1")
    with pytest.raises(ClaveIdempotenciaReutilizadaError):
        _comprar(escenario, "FUERA_DE_CAJA", clave_idempotencia="clave-1")


def test_compra_sin_importe_no_puede_salir_de_la_caja(escenario):
    with pytest.raises(DatosInvalidosError, match="sin importe"):
        _comprar(escenario, "CAJA_ACTUAL", costo=0)


# --- Anulación ---------------------------------------------------------------------------------------


def _anular(escenario, compra, reintegro):
    return servicio_compras.anular_compra(compra.id, "ERROR_CARGA", None, escenario["usuario"], reintegro_a_caja=reintegro)


def test_anular_una_compra_pagada_con_la_caja_exige_decir_si_vuelve_el_efectivo(escenario):
    compra = _comprar(escenario, "CAJA_ACTUAL")
    with pytest.raises(DatosInvalidosError, match="vuelve a la caja"):
        _anular(escenario, compra, None)
    assert repositorio_compras.obtener_por_id(compra.id).estado == "ACTIVA" and _stock(escenario) == 15


def test_anular_con_reintegro_devuelve_el_efectivo_con_un_ingreso_vinculado(escenario):
    antes = _efectivo_esperado()
    compra = _comprar(escenario, "CAJA_ACTUAL")

    anulada = _anular(escenario, compra, True)

    assert anulada.estado == "ANULADA" and anulada.caja_movimiento_reintegro_id is not None
    assert _efectivo_esperado() == antes
    tipos = _movimientos_caja()
    assert ("EGRESO", 1200, "PAGO_PROVEEDOR") in tipos and ("INGRESO", 1200, "MANUAL") in tipos
    resumen = repositorio_auditoria.listar(accion="COMPRA_ANULADA")[0].resumen
    assert "reintegro a caja" in resumen
    with pytest.raises(CompraYaAnuladaError):
        _anular(escenario, compra, True)
    assert [m for m in _movimientos_caja() if m[0] == "INGRESO"] == [("INGRESO", 1200, "MANUAL")]


def test_anular_sin_reintegro_deja_la_caja_como_esta_y_lo_audita(escenario):
    compra = _comprar(escenario, "CAJA_ACTUAL")
    despues_de_comprar = _efectivo_esperado()

    anulada = _anular(escenario, compra, False)

    assert anulada.caja_movimiento_reintegro_id is None and _efectivo_esperado() == despues_de_comprar
    assert "sin reintegro a caja" in repositorio_auditoria.listar(accion="COMPRA_ANULADA")[0].resumen


def test_reintegro_con_la_caja_cerrada_se_rechaza_sin_anular(escenario):
    compra = _comprar(escenario, "CAJA_ACTUAL")
    servicio_caja.cerrar_caja(0)

    with pytest.raises(CajaCerradaError):
        _anular(escenario, compra, True)
    assert repositorio_compras.obtener_por_id(compra.id).estado == "ACTIVA" and _stock(escenario) == 15
    # Sin reintegro sí se puede anular aunque la sesión del egreso ya esté cerrada: el egreso no se toca.
    assert _anular(escenario, compra, False).estado == "ANULADA"


def test_una_compra_fuera_de_caja_o_historica_no_acepta_reintegro(escenario):
    compra = _comprar(escenario, "FUERA_DE_CAJA")
    with pytest.raises(DatosInvalidosError, match="no se pagó con efectivo de la caja"):
        _anular(escenario, compra, True)
    assert _anular(escenario, compra, None).estado == "ANULADA"


# --- Defensa en la base ------------------------------------------------------------------------------


def _insertar_compra_cruda(escenario, origen, caja_movimiento_id=None, condicion="CONTADO"):
    with obtener_conexion() as conexion:
        conexion.execute(
            "INSERT INTO compras (proveedor_id, usuario_id, total_centavos, condicion_pago, origen_pago, caja_movimiento_id)"
            " VALUES (?, ?, 100, ?, ?, ?)",
            (escenario["proveedor"], escenario["usuario"], condicion, origen, caja_movimiento_id),
        )


def test_la_base_rechaza_origenes_incoherentes(escenario):
    for origen, movimiento, condicion in (
        ("CAJA_ACTUAL", None, "CONTADO"),
        ("FUERA_DE_CAJA", None, "CREDITO"),
    ):
        with pytest.raises(ErrorBaseDatos):
            _insertar_compra_cruda(escenario, origen, movimiento, condicion)


def test_la_base_impide_cambiar_el_origen_o_el_egreso_de_una_compra(escenario):
    compra = _comprar(escenario, "CAJA_ACTUAL")
    conexion = sqlite3.connect(modulo_conexion.RUTA_BASE_DATOS)
    try:
        for sentencia in (
            "UPDATE compras SET origen_pago = 'FUERA_DE_CAJA' WHERE id = ?",
            "UPDATE compras SET caja_movimiento_id = NULL WHERE id = ?",
            "UPDATE compras SET caja_movimiento_reintegro_id = caja_movimiento_id WHERE id = ?",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                conexion.execute(sentencia, (compra.id,))
    finally:
        conexion.close()
