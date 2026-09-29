"""V1.10-C: alta y edición auditada del vencimiento de las compras a crédito.

El vencimiento es un dato informativo: nada de esto puede tocar el saldo del proveedor, el libro
(`movimientos_proveedor`), el stock ni el costo."""

import threading
from datetime import date, timedelta

import pytest

from db.repositorios import compras as repositorio_compras
from domain.compra import ItemCompra
from excepciones import (
    ClaveIdempotenciaReutilizadaError,
    CompraNoEncontradaError,
    DatosInvalidosError,
    ErrorAplicacion,
    VencimientoNoEditableError,
)
from services import servicio_compras, servicio_pagos_proveedor, servicio_proveedores, servicio_stock
from tests.utilidades_clientes import conectar, consultar

ACCION = "VENCIMIENTO_COMPRA_MODIFICADO"


@pytest.fixture
def e(base_datos_temporal):
    from db.repositorios import usuarios as repositorio_usuarios
    from domain.usuario import Usuario

    owner = repositorio_usuarios.crear_usuario(
        Usuario(nombre_usuario="duenio", nombre_completo="Dueño", password_hash="hash-de-prueba", rol="OWNER")
    )
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 300, stock_actual=10)
    return type("E", (), {"ruta": base_datos_temporal, "owner": owner, "prov": proveedor, "p": producto})


def _hoy(e) -> date:
    return date.fromisoformat(consultar(e.ruta, "SELECT date('now', 'localtime')")[0][0])


def _comprar(e, condicion="CREDITO", vencimiento=None, clave=None, cantidad=5, costo=150):
    return servicio_compras.registrar_compra(
        e.prov.id, e.owner.id, [ItemCompra(e.p.id, cantidad, costo)],
        clave_idempotencia=clave, condicion_pago=condicion, fecha_vencimiento=vencimiento,
    )


def _vencimiento(e, compra_id):
    return consultar(e.ruta, "SELECT fecha_vencimiento FROM compras WHERE id = ?", (compra_id,))[0][0]


def _libro(e):
    return [tuple(f) for f in consultar(e.ruta, "SELECT * FROM movimientos_proveedor ORDER BY id")]


def _saldo(e):
    return consultar(
        e.ruta,
        "SELECT COALESCE(SUM(CASE tipo WHEN 'CARGO_COMPRA' THEN monto_centavos ELSE -monto_centavos END), 0) "
        "FROM movimientos_proveedor WHERE proveedor_id = ?",
        (e.prov.id,),
    )[0][0]


def _producto(e):
    return tuple(consultar(e.ruta, "SELECT stock_actual, precio_costo_centavos FROM productos WHERE id = ?", (e.p.id,))[0])


def _auditoria(e, accion=ACCION):
    return [f["resumen"] for f in consultar(e.ruta, "SELECT resumen FROM auditoria WHERE accion = ? ORDER BY id", (accion,))]


def _estado_completo(e):
    return _libro(e), _saldo(e), _producto(e), consultar(e.ruta, "SELECT COUNT(*) FROM auditoria")[0][0]


# --- Alta -------------------------------------------------------------------------------------------


def test_contado_sin_vencimiento_queda_null(e):
    compra = _comprar(e, "CONTADO")

    assert compra.fecha_vencimiento is None and _vencimiento(e, compra.id) is None


def test_contado_con_vencimiento_se_rechaza_sin_efectos(e):
    antes = _estado_completo(e)

    with pytest.raises(DatosInvalidosError, match="contado"):
        _comprar(e, "CONTADO", vencimiento=(_hoy(e) + timedelta(days=5)).isoformat())

    assert _estado_completo(e) == antes
    assert consultar(e.ruta, "SELECT COUNT(*) FROM compras")[0][0] == 0


def test_credito_sin_vencimiento_queda_null(e):
    compra = _comprar(e)

    assert compra.fecha_vencimiento is None and _vencimiento(e, compra.id) is None


@pytest.mark.parametrize("dias", [0, 1, 30, 400, 3650])
def test_credito_con_vencimiento_igual_o_posterior_se_persiste(e, dias):
    fecha = (_hoy(e) + timedelta(days=dias)).isoformat()

    compra = _comprar(e, vencimiento=fecha)

    assert compra.fecha_vencimiento == fecha and _vencimiento(e, compra.id) == fecha
    assert consultar(e.ruta, "SELECT SUM(monto_centavos) FROM movimientos_proveedor")[0][0] == 750  # cargo intacto


def test_credito_con_vencimiento_anterior_se_rechaza_sin_efectos(e):
    antes = _estado_completo(e)

    with pytest.raises(DatosInvalidosError, match="anterior"):
        _comprar(e, vencimiento=(_hoy(e) - timedelta(days=1)).isoformat())

    assert _estado_completo(e) == antes
    assert consultar(e.ruta, "SELECT COUNT(*) FROM compras")[0][0] == 0


@pytest.mark.parametrize("texto", ["2099-02-30", "hoy", "01/01/2099", "", "2099-1-1"])
def test_credito_con_vencimiento_invalido_se_rechaza_con_error_controlado(e, texto):
    with pytest.raises(DatosInvalidosError):
        _comprar(e, vencimiento=texto)

    assert consultar(e.ruta, "SELECT COUNT(*) FROM compras")[0][0] == 0


def test_la_validacion_usa_la_fecha_que_queda_persistida(e, monkeypatch):
    """El dominio compara contra la misma fecha que se guarda (no contra otro reloj): si la fecha de la
    compra difiere del 'hoy' de Python (ej. el día cambia a mitad de la operación), sigue coincidiendo
    con el CHECK del esquema."""
    monkeypatch.setattr(
        repositorio_compras, "obtener_fecha_hora_actual_en_conexion", lambda conexion: "2099-06-15 23:59:59"
    )

    with pytest.raises(DatosInvalidosError, match="anterior"):
        _comprar(e, vencimiento="2099-06-14")
    compra = _comprar(e, vencimiento="2099-06-15")

    assert compra.fecha == "2099-06-15 23:59:59" and compra.fecha_vencimiento == "2099-06-15"
    assert consultar(e.ruta, "SELECT fecha, fecha_vencimiento FROM compras")[0][:] == ("2099-06-15 23:59:59", "2099-06-15")


def test_el_alta_a_credito_con_vencimiento_lo_muestra_en_la_auditoria_y_no_agrega_otra_fila(e):
    fecha = (_hoy(e) + timedelta(days=7)).isoformat()

    compra = _comprar(e, vencimiento=fecha)

    resumenes = _auditoria(e, "COMPRA_REGISTRADA")
    assert len(resumenes) == 1 and f"vence {fecha}" in resumenes[0]
    assert _auditoria(e) == [] and compra.id


def test_el_alta_sin_vencimiento_mantiene_el_resumen_anterior(e):
    _comprar(e)
    _comprar(e, "CONTADO")

    resumenes = _auditoria(e, "COMPRA_REGISTRADA")
    assert all("vence" not in r for r in resumenes)
    assert resumenes[0].endswith("(crédito)")


# --- Idempotencia -----------------------------------------------------------------------------------


def test_reintento_identico_devuelve_la_misma_compra_sin_duplicar_nada(e):
    fecha = (_hoy(e) + timedelta(days=10)).isoformat()
    primera = _comprar(e, vencimiento=fecha, clave="k-1")
    antes = _estado_completo(e)

    segunda = _comprar(e, vencimiento=fecha, clave="k-1")

    assert segunda.id == primera.id and segunda.fecha_vencimiento == fecha
    assert _estado_completo(e) == antes
    assert consultar(e.ruta, "SELECT COUNT(*) FROM compras")[0][0] == 1


def test_misma_clave_con_otro_vencimiento_es_error_de_idempotencia_sin_efectos(e):
    fecha = (_hoy(e) + timedelta(days=10)).isoformat()
    otra = (_hoy(e) + timedelta(days=20)).isoformat()
    _comprar(e, vencimiento=fecha, clave="k-1")
    antes = _estado_completo(e)

    with pytest.raises(ClaveIdempotenciaReutilizadaError):
        _comprar(e, vencimiento=otra, clave="k-1")
    with pytest.raises(ClaveIdempotenciaReutilizadaError):
        _comprar(e, vencimiento=None, clave="k-1")

    assert _estado_completo(e) == antes
    assert consultar(e.ruta, "SELECT fecha_vencimiento FROM compras")[0][0] == fecha


def test_compra_anterior_sin_vencimiento_se_reintenta_igual(e):
    primera = _comprar(e, clave="k-1")

    assert _comprar(e, clave="k-1").id == primera.id


# --- Edición ----------------------------------------------------------------------------------------


def _editar(e, compra, fecha):
    return servicio_compras.actualizar_vencimiento_compra(compra.id, fecha, e.owner.id)


def test_asignar_vencimiento_a_una_compra_sin_vencimiento(e):
    compra = _comprar(e)
    fecha = (_hoy(e) + timedelta(days=15)).isoformat()

    assert _editar(e, compra, fecha) is True

    assert _vencimiento(e, compra.id) == fecha


def test_cambiar_un_vencimiento_por_otro(e):
    compra = _comprar(e, vencimiento=(_hoy(e) + timedelta(days=5)).isoformat())
    nueva = (_hoy(e) + timedelta(days=500)).isoformat()

    assert _editar(e, compra, nueva) is True

    assert _vencimiento(e, compra.id) == nueva


def test_quitar_el_vencimiento(e):
    compra = _comprar(e, vencimiento=(_hoy(e) + timedelta(days=5)).isoformat())

    assert _editar(e, compra, None) is True

    assert _vencimiento(e, compra.id) is None


def test_editar_no_toca_saldo_libro_stock_ni_costo(e):
    compra = _comprar(e, vencimiento=(_hoy(e) + timedelta(days=5)).isoformat())
    servicio_pagos_proveedor.registrar_pago(e.prov.id, 200, "TRANSFERENCIA", e.owner.id)
    libro, saldo, producto = _libro(e), _saldo(e), _producto(e)

    _editar(e, compra, (_hoy(e) + timedelta(days=90)).isoformat())
    _editar(e, compra, None)

    assert (_libro(e), _saldo(e), _producto(e)) == (libro, saldo, producto)


def test_mismo_valor_es_no_op_sin_update_ni_auditoria(e):
    fecha = (_hoy(e) + timedelta(days=5)).isoformat()
    compra = _comprar(e, vencimiento=fecha)
    sin_vencimiento = _comprar(e)
    antes = _estado_completo(e)

    assert _editar(e, compra, fecha) is False
    assert _editar(e, sin_vencimiento, None) is False

    assert _estado_completo(e) == antes and _auditoria(e) == []


def test_compra_contado_no_se_puede_editar(e):
    compra = _comprar(e, "CONTADO")
    antes = _estado_completo(e)

    with pytest.raises(VencimientoNoEditableError, match="contado"):
        _editar(e, compra, (_hoy(e) + timedelta(days=5)).isoformat())
    with pytest.raises(VencimientoNoEditableError):
        _editar(e, compra, None)

    assert _estado_completo(e) == antes and _vencimiento(e, compra.id) is None


def test_compra_anulada_no_se_puede_editar(e):
    fecha = (_hoy(e) + timedelta(days=5)).isoformat()
    compra = _comprar(e, vencimiento=fecha)
    servicio_compras.anular_compra(compra.id, "ERROR_CARGA", None, e.owner.id)
    antes = _estado_completo(e)

    with pytest.raises(VencimientoNoEditableError, match="anulada"):
        _editar(e, compra, (_hoy(e) + timedelta(days=9)).isoformat())

    assert _estado_completo(e) == antes and _vencimiento(e, compra.id) == fecha


def test_compra_inexistente(e):
    with pytest.raises(CompraNoEncontradaError):
        servicio_compras.actualizar_vencimiento_compra(999, "2099-01-01", e.owner.id)


def test_fecha_anterior_a_la_compra_se_rechaza(e):
    fecha = (_hoy(e) + timedelta(days=5)).isoformat()
    compra = _comprar(e, vencimiento=fecha)
    antes = _estado_completo(e)

    with pytest.raises(DatosInvalidosError, match="anterior"):
        _editar(e, compra, (_hoy(e) - timedelta(days=1)).isoformat())

    assert _estado_completo(e) == antes and _vencimiento(e, compra.id) == fecha


@pytest.mark.parametrize("texto", ["2099-02-30", "mañana", "", "2099-1-1"])
def test_fecha_invalida_se_rechaza_con_error_controlado(e, texto):
    compra = _comprar(e)
    antes = _estado_completo(e)

    with pytest.raises(DatosInvalidosError):
        _editar(e, compra, texto)

    assert _estado_completo(e) == antes and _vencimiento(e, compra.id) is None


# --- Auditoría de la edición -------------------------------------------------------------------------


def test_edicion_real_genera_una_sola_fila_con_el_resumen_anterior_y_nuevo(e):
    anterior = (_hoy(e) + timedelta(days=5)).isoformat()
    nueva = (_hoy(e) + timedelta(days=9)).isoformat()
    compra = _comprar(e, vencimiento=anterior)

    _editar(e, compra, nueva)

    assert _auditoria(e) == [f"Compra #{compra.id}: vencimiento {anterior} → {nueva}"]
    fila = consultar(e.ruta, "SELECT usuario_id, entidad, entidad_id FROM auditoria WHERE accion = ?", (ACCION,))[0]
    assert tuple(fila) == (e.owner.id, "COMPRA", compra.id)


def test_el_null_se_representa_como_sin_vencimiento(e):
    compra = _comprar(e)
    nueva = (_hoy(e) + timedelta(days=9)).isoformat()

    _editar(e, compra, nueva)
    _editar(e, compra, None)

    assert _auditoria(e) == [
        f"Compra #{compra.id}: vencimiento sin vencimiento → {nueva}",
        f"Compra #{compra.id}: vencimiento {nueva} → sin vencimiento",
    ]


def test_un_fallo_no_audita(e):
    compra = _comprar(e)
    contado = _comprar(e, "CONTADO")

    for intento in (
        lambda: _editar(e, compra, "2000-01-01"),
        lambda: _editar(e, compra, "no-es-fecha"),
        lambda: _editar(e, contado, "2099-01-01"),
    ):
        with pytest.raises(ErrorAplicacion):
            intento()

    assert _auditoria(e) == []


# --- Concurrencia -------------------------------------------------------------------------------------


def test_una_anulacion_confirmada_antes_impide_la_edicion(e):
    """La edición corre en `BEGIN IMMEDIATE` y relee el estado dentro de la transacción: si otra
    conexión anula la compra mientras la edición espera el lock, no queda editada."""
    fecha = (_hoy(e) + timedelta(days=5)).isoformat()
    compra = _comprar(e, vencimiento=fecha)
    antes = _libro(e), _saldo(e), _producto(e)
    resultado: dict = {}

    otra = conectar(e.ruta)
    otra.execute("BEGIN IMMEDIATE")
    otra.execute("UPDATE compras SET estado = 'ANULADA' WHERE id = ?", (compra.id,))

    def editar():
        try:
            resultado["ok"] = _editar(e, compra, (_hoy(e) + timedelta(days=30)).isoformat())
        except VencimientoNoEditableError as error:
            resultado["error"] = error

    hilo = threading.Thread(target=editar)
    hilo.start()
    hilo.join(timeout=0.5)
    assert hilo.is_alive()  # espera el lock: todavía no leyó nada
    otra.commit()
    otra.close()
    hilo.join(timeout=15)

    assert "error" in resultado and "ok" not in resultado
    assert _vencimiento(e, compra.id) == fecha
    assert (_libro(e), _saldo(e), _producto(e)) == antes
    assert _auditoria(e) == []
