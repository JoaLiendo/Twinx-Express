"""V1.13-C: reclamos e incidentes con proveedores (estados auditables, relaciones reales, sin borrar)."""

import threading

import pytest

from domain.compra import ItemCompra
from excepciones import (
    AjusteNoEncontradoError,
    AjusteYaReclamadoError,
    ClaveIdempotenciaReutilizadaError,
    CompraNoEncontradaError,
    DatosInvalidosError,
    PermisoDenegadoError,
    ProductoNoEncontradoError,
    ProveedorNoEncontradoError,
    ReclamoDesactualizadoError,
    TransicionReclamoInvalidaError,
)
from services import (
    servicio_compras,
    servicio_evaluaciones_proveedor,
    servicio_partidas,
    servicio_proveedores,
    servicio_reclamos_proveedor,
    servicio_stock,
)
from tests.utilidades_clientes import consultar, crear_owner, crear_producto, crear_usuario, escribir

crear = servicio_reclamos_proveedor.crear_reclamo
cambiar = servicio_reclamos_proveedor.cambiar_estado


@pytest.fixture
def e(base_datos_temporal):
    owner = crear_owner()
    a = servicio_proveedores.crear_proveedor("Fábrica A")
    b = servicio_proveedores.crear_proveedor("Fábrica B")
    producto = crear_producto("7790000000001", stock=0)
    return type("E", (), {"ruta": base_datos_temporal, "owner": owner, "a": a, "b": b, "producto": producto})


def _comprar(e, proveedor, cantidad=10, producto=None):
    producto = producto or e.producto
    compra = servicio_compras.registrar_compra(proveedor.id, e.owner.id, [ItemCompra(producto.id, cantidad, 100)])
    return compra


def _partida_de(e, compra_id):
    [detalle] = consultar(e.ruta, "SELECT id FROM detalle_compra WHERE compra_id = ?", (compra_id,))
    return next(p for p in servicio_partidas.listar_partidas(e.producto.id) if p.detalle_compra_id == detalle["id"])


def _falla(e, cantidad=2, partida_id=None, producto=None):
    producto = producto or e.producto
    return servicio_stock.ajustar_stock(producto.id, -cantidad, "FALLA_FABRICA", e.owner.id, partida_id=partida_id)


# --- creación y relaciones --------------------------------------------------------------------------


def test_crear_reclamo_minimo_queda_pendiente_con_usuario_y_fecha(e):
    reclamo = crear(e.a.id, e.owner.id, "ENTREGA", "  llegó tarde  ")

    assert (reclamo.estado, reclamo.tipo, reclamo.descripcion) == ("PENDIENTE", "ENTREGA", "llegó tarde")
    assert reclamo.usuario_id == e.owner.id and reclamo.fecha and reclamo.cambios == []
    assert reclamo.producto_id is None and reclamo.compra_id is None and reclamo.ajuste_id is None


@pytest.mark.parametrize(
    "tipo, descripcion, cantidad", [("INVENTADO", "x", None), ("OTRO", " ", None), ("OTRO", "x", 0), ("OTRO", "x", True)]
)
def test_datos_invalidos(e, tipo, descripcion, cantidad):
    with pytest.raises(DatosInvalidosError):
        crear(e.a.id, e.owner.id, tipo, descripcion, cantidad=cantidad)


def test_proveedor_producto_compra_o_ajuste_inexistentes(e):
    with pytest.raises(ProveedorNoEncontradoError):
        crear(9999, e.owner.id, "OTRO", "x")
    with pytest.raises(ProductoNoEncontradoError):
        crear(e.a.id, e.owner.id, "CALIDAD", "x", producto_id=9999)
    with pytest.raises(CompraNoEncontradaError):
        crear(e.a.id, e.owner.id, "ENTREGA", "x", compra_id=9999)
    with pytest.raises(AjusteNoEncontradoError):
        crear(e.a.id, e.owner.id, "FALLA_FABRICA", "x", ajuste_id=9999)


def test_compra_y_producto_resuelven_la_linea_de_compra(e):
    compra = _comprar(e, e.a)
    reclamo = crear(e.a.id, e.owner.id, "CALIDAD", "venció antes", producto_id=e.producto.id, compra_id=compra.id, cantidad=3)

    [detalle] = consultar(e.ruta, "SELECT id FROM detalle_compra WHERE compra_id = ?", (compra.id,))
    assert (reclamo.compra_id, reclamo.detalle_compra_id, reclamo.cantidad) == (compra.id, detalle["id"], 3)


def test_compra_de_otro_proveedor_o_producto_ajeno_a_la_compra_se_rechazan(e):
    compra_b = _comprar(e, e.b)
    otro = crear_producto("7790000000002", stock=0)
    with pytest.raises(DatosInvalidosError, match="no es de este proveedor"):
        crear(e.a.id, e.owner.id, "ENTREGA", "x", compra_id=compra_b.id)
    with pytest.raises(DatosInvalidosError, match="no está en la compra"):
        crear(e.b.id, e.owner.id, "CALIDAD", "x", producto_id=otro.id, compra_id=compra_b.id)


def test_reclamo_por_una_compra_anulada_o_a_un_proveedor_dado_de_baja(e):
    compra = _comprar(e, e.a)
    servicio_compras.anular_compra(compra.id, "ERROR_CARGA", None, e.owner.id)
    servicio_proveedores.eliminar_proveedor(e.a.id)

    reclamo = crear(e.a.id, e.owner.id, "FACTURACION", "factura duplicada", compra_id=compra.id)
    assert reclamo.compra_id == compra.id


# --- desde un ajuste FALLA_FABRICA ------------------------------------------------------------------


def test_ajuste_de_una_unica_partida_de_compra_identifica_proveedor_compra_y_linea(e):
    compra = _comprar(e, e.a)
    ajuste = _falla(e, 2)

    reclamable = servicio_reclamos_proveedor.ajuste_reclamable(ajuste.id)
    assert reclamable.origen.proveedor_id == e.a.id and reclamable.origen.compra_id == compra.id
    reclamo = crear(e.a.id, e.owner.id, "FALLA_FABRICA", "vinieron rotos", ajuste_id=ajuste.id, cantidad=2)
    assert (reclamo.producto_id, reclamo.compra_id, reclamo.ajuste_id) == (e.producto.id, compra.id, ajuste.id)
    assert reclamo.detalle_compra_id == _partida_de(e, compra.id).detalle_compra_id


def test_partida_elegida_entre_varios_proveedores_identifica_al_de_esa_partida(e):
    _comprar(e, e.a)
    compra_b = _comprar(e, e.b)
    ajuste = _falla(e, 1, partida_id=_partida_de(e, compra_b.id).id)

    assert servicio_reclamos_proveedor.ajuste_reclamable(ajuste.id).origen.proveedor_id == e.b.id
    with pytest.raises(DatosInvalidosError, match="provino de Fábrica B"):
        crear(e.a.id, e.owner.id, "FALLA_FABRICA", "x", ajuste_id=ajuste.id)


def test_asignacion_fefo_entre_varias_partidas_no_identifica_a_nadie_aunque_sean_del_mismo_proveedor(e):
    _comprar(e, e.a)
    _comprar(e, e.a)
    ajuste = _falla(e, 1)

    assert servicio_reclamos_proveedor.ajuste_reclamable(ajuste.id).origen is None
    reclamo = crear(e.b.id, e.owner.id, "FALLA_FABRICA", "el OWNER sabe que era de B", ajuste_id=ajuste.id)
    assert reclamo.proveedor_id == e.b.id and reclamo.compra_id is None and reclamo.detalle_compra_id is None


def test_ajuste_de_existencia_sin_compra_no_identifica_proveedor(e):
    producto = crear_producto("7790000000009", stock=5)  # STOCK_INICIAL, no viene de ninguna compra
    ajuste = _falla(e, 1, producto=producto)

    assert servicio_reclamos_proveedor.ajuste_reclamable(ajuste.id).origen is None
    assert crear(e.a.id, e.owner.id, "FALLA_FABRICA", "x", ajuste_id=ajuste.id).producto_id == producto.id


def test_ajuste_que_no_es_falla_de_fabrica_o_revertido_no_origina_reclamo(e):
    _comprar(e, e.a)
    rotura = servicio_stock.ajustar_stock(e.producto.id, -1, "ROTURA", e.owner.id)
    falla = _falla(e, 1)
    servicio_stock.revertir_ajuste(falla.id, "cargado por error", e.owner.id)

    with pytest.raises(DatosInvalidosError, match="falla de fábrica"):
        crear(e.a.id, e.owner.id, "FALLA_FABRICA", "x", ajuste_id=rotura.id)
    with pytest.raises(DatosInvalidosError, match="revertido"):
        crear(e.a.id, e.owner.id, "FALLA_FABRICA", "x", ajuste_id=falla.id)


def test_ajuste_con_tipo_producto_o_cantidad_incoherentes(e):
    _comprar(e, e.a)
    ajuste = _falla(e, 2)
    otro = crear_producto("7790000000002", stock=0)
    with pytest.raises(DatosInvalidosError, match="Falla de fábrica"):
        crear(e.a.id, e.owner.id, "CALIDAD", "x", ajuste_id=ajuste.id)
    with pytest.raises(DatosInvalidosError, match="producto no coincide"):
        crear(e.a.id, e.owner.id, "FALLA_FABRICA", "x", ajuste_id=ajuste.id, producto_id=otro.id)
    with pytest.raises(DatosInvalidosError, match="no puede superar"):
        crear(e.a.id, e.owner.id, "FALLA_FABRICA", "x", ajuste_id=ajuste.id, cantidad=3)


def test_un_ajuste_origina_a_lo_sumo_un_reclamo(e):
    _comprar(e, e.a)
    ajuste = _falla(e, 1)
    primero = crear(e.a.id, e.owner.id, "FALLA_FABRICA", "x", ajuste_id=ajuste.id)

    with pytest.raises(AjusteYaReclamadoError):
        crear(e.a.id, e.owner.id, "FALLA_FABRICA", "otra vez", ajuste_id=ajuste.id)
    assert servicio_reclamos_proveedor.ajuste_reclamable(ajuste.id).reclamo_existente_id == primero.id
    assert servicio_reclamos_proveedor.reclamos_de_ajustes([ajuste.id, 999]) == {ajuste.id: primero.id}


# --- estados ------------------------------------------------------------------------------------------


def test_ciclo_de_estados_con_historial_usuario_fecha_y_auditoria(e):
    reclamo = crear(e.a.id, e.owner.id, "CALIDAD", "x")
    reclamo = cambiar(reclamo.id, e.owner.id, "RECONOCIDO", "lo aceptó", "PENDIENTE")
    reclamo = cambiar(reclamo.id, e.owner.id, "RESUELTO", "mandó nota de crédito", "RECONOCIDO")
    reclamo = cambiar(reclamo.id, e.owner.id, "PENDIENTE", "la nota nunca llegó", "RESUELTO")

    assert reclamo.estado == "PENDIENTE" and reclamo.abierto
    assert [(c.estado_anterior, c.estado_nuevo, c.usuario_id) for c in reclamo.cambios] == [
        ("PENDIENTE", "RECONOCIDO", e.owner.id), ("RECONOCIDO", "RESUELTO", e.owner.id), ("RESUELTO", "PENDIENTE", e.owner.id)
    ]
    assert all(c.fecha for c in reclamo.cambios)
    acciones = [f["accion"] for f in consultar(e.ruta, "SELECT accion FROM auditoria WHERE entidad = 'RECLAMO_PROVEEDOR' ORDER BY id")]
    assert acciones == ["RECLAMO_PROVEEDOR_CREADO"] + ["RECLAMO_PROVEEDOR_ESTADO"] * 3


@pytest.mark.parametrize(
    "camino, invalido",
    [([], "PENDIENTE"), (["RECHAZADO"], "RESUELTO"), (["RECONOCIDO"], "PENDIENTE"), (["RESUELTO"], "RECONOCIDO"), ([], "ABIERTO")],
)
def test_transiciones_invalidas_incluido_repetir_el_estado(e, camino, invalido):
    reclamo = crear(e.a.id, e.owner.id, "OTRO", "x")
    for estado in camino:
        reclamo = cambiar(reclamo.id, e.owner.id, estado, "ok", reclamo.estado)
    with pytest.raises(TransicionReclamoInvalidaError):
        cambiar(reclamo.id, e.owner.id, invalido, "ok", reclamo.estado)


def test_cambio_sobre_un_estado_ya_superado_se_rechaza(e):
    reclamo = crear(e.a.id, e.owner.id, "OTRO", "x")
    cambiar(reclamo.id, e.owner.id, "RECONOCIDO", "pestaña 1", "PENDIENTE")

    with pytest.raises(ReclamoDesactualizadoError):
        cambiar(reclamo.id, e.owner.id, "RECHAZADO", "pestaña 2", "PENDIENTE")


def test_observacion_obligatoria_en_cada_cambio(e):
    reclamo = crear(e.a.id, e.owner.id, "OTRO", "x")
    with pytest.raises(DatosInvalidosError, match="obligatoria"):
        cambiar(reclamo.id, e.owner.id, "RECONOCIDO", "  ", "PENDIENTE")


def test_doble_envio_del_cambio_registra_uno_solo(e):
    reclamo = crear(e.a.id, e.owner.id, "OTRO", "x")
    cambiar(reclamo.id, e.owner.id, "RECONOCIDO", "ok", "PENDIENTE", clave_idempotencia="c1")
    repetido = cambiar(reclamo.id, e.owner.id, "RECONOCIDO", "ok", "PENDIENTE", clave_idempotencia="c1")

    assert len(repetido.cambios) == 1
    with pytest.raises(ClaveIdempotenciaReutilizadaError):
        cambiar(reclamo.id, e.owner.id, "RESUELTO", "otro", "RECONOCIDO", clave_idempotencia="c1")


def test_doble_envio_del_alta_registra_un_solo_reclamo(e):
    primero = crear(e.a.id, e.owner.id, "OTRO", "x", clave_idempotencia="r1")
    assert crear(e.a.id, e.owner.id, "OTRO", " x ", clave_idempotencia="r1").id == primero.id
    with pytest.raises(ClaveIdempotenciaReutilizadaError):
        crear(e.b.id, e.owner.id, "OTRO", "x", clave_idempotencia="r1")
    assert consultar(e.ruta, "SELECT COUNT(*) FROM reclamos_proveedor")[0][0] == 1


def test_cambios_concurrentes_desde_el_mismo_estado_aplican_uno_solo(e):
    reclamo = crear(e.a.id, e.owner.id, "OTRO", "x")
    resultados = []

    def intentar(estado):
        try:
            cambiar(reclamo.id, e.owner.id, estado, f"hilo {estado}", "PENDIENTE")
            resultados.append("ok")
        except ReclamoDesactualizadoError:
            resultados.append("desactualizado")

    hilos = [threading.Thread(target=intentar, args=(estado,)) for estado in ("RECONOCIDO", "RECHAZADO", "RESUELTO")]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    assert sorted(resultados) == ["desactualizado", "desactualizado", "ok"]
    assert len(servicio_reclamos_proveedor.obtener_por_id(reclamo.id).cambios) == 1


# --- permisos, historia y separación de dimensiones -------------------------------------------------


def test_cashier_u_owner_inactivo_no_crean_ni_cambian(e):
    reclamo = crear(e.a.id, e.owner.id, "OTRO", "x")
    for usuario in (crear_usuario("cajera", "CASHIER"), crear_usuario("viejo", "OWNER", activo=False)):
        with pytest.raises(PermisoDenegadoError):
            crear(e.a.id, usuario.id, "OTRO", "x")
        with pytest.raises(PermisoDenegadoError):
            cambiar(reclamo.id, usuario.id, "RECONOCIDO", "x", "PENDIENTE")


def test_la_base_impide_borrar_editar_o_saltear_el_historial(e):
    reclamo = crear(e.a.id, e.owner.id, "OTRO", "x")
    cambiar(reclamo.id, e.owner.id, "RECONOCIDO", "ok", "PENDIENTE")
    for sql in (
        "DELETE FROM reclamos_proveedor",
        "DELETE FROM reclamos_proveedor_cambios",
        "UPDATE reclamos_proveedor SET descripcion = 'otra'",
        "UPDATE reclamos_proveedor SET estado = 'RESUELTO'",
        "UPDATE reclamos_proveedor_cambios SET observacion = 'otra'",
    ):
        with pytest.raises(Exception, match="no se puede|solo cambia"):
            escribir(e.ruta, sql)
    with pytest.raises(Exception, match="ya no está en el estado"):
        escribir(
            e.ruta,
            "INSERT INTO reclamos_proveedor_cambios (reclamo_id, estado_anterior, estado_nuevo, observacion, usuario_id)"
            " VALUES (?, 'PENDIENTE', 'RESUELTO', 'x', ?)",
            (reclamo.id, e.owner.id),
        )
    with pytest.raises(Exception, match="CHECK"):
        escribir(
            e.ruta,
            "INSERT INTO reclamos_proveedor_cambios (reclamo_id, estado_anterior, estado_nuevo, observacion, usuario_id)"
            " VALUES (?, 'RECONOCIDO', 'PENDIENTE', 'x', ?)",
            (reclamo.id, e.owner.id),
        )


def test_la_base_rechaza_relaciones_incoherentes_escritas_por_fuera(e):
    compra_b = _comprar(e, e.b)
    with pytest.raises(Exception, match="Reclamo inválido"):
        escribir(
            e.ruta,
            "INSERT INTO reclamos_proveedor (proveedor_id, tipo, descripcion, compra_id, usuario_id) VALUES (?, 'OTRO', 'x', ?, ?)",
            (e.a.id, compra_b.id, e.owner.id),
        )
    with pytest.raises(Exception, match="Reclamo inválido"):
        escribir(
            e.ruta,
            "INSERT INTO reclamos_proveedor (proveedor_id, tipo, estado, descripcion, usuario_id) VALUES (?, 'OTRO', 'RESUELTO', 'x', ?)",
            (e.a.id, e.owner.id),
        )


def test_un_reclamo_impide_la_baja_fisica_del_proveedor_y_no_toca_su_calificacion_ni_su_deuda(e):
    servicio_evaluaciones_proveedor.registrar_evaluacion(e.b.id, e.owner.id, 5, "excelente")
    reclamo = crear(e.b.id, e.owner.id, "CALIDAD", "x")
    cambiar(reclamo.id, e.owner.id, "RECHAZADO", "el proveedor no lo acepta", "PENDIENTE")

    assert servicio_evaluaciones_proveedor.calificaciones_actuales() == {e.b.id: 5}
    assert servicio_proveedores.obtener_ficha(e.b.id).saldo_centavos == 0
    assert servicio_proveedores.eliminar_proveedor(e.b.id) is True
    assert consultar(e.ruta, "SELECT COUNT(*) FROM reclamos_proveedor")[0][0] == 1


def test_ficha_y_listado_muestran_abiertos_y_total(e):
    abierto = crear(e.a.id, e.owner.id, "OTRO", "abierto")
    cerrado = crear(e.a.id, e.owner.id, "OTRO", "cerrado")
    cambiar(cerrado.id, e.owner.id, "RESUELTO", "listo", "PENDIENTE")

    ficha = servicio_proveedores.obtener_ficha(e.a.id)
    assert [r.id for r in ficha.reclamos] == [cerrado.id, abierto.id]
    assert [r.id for r in ficha.reclamos_abiertos] == [abierto.id]
    assert {r.proveedor.nombre: r.reclamos_abiertos for r in servicio_proveedores.listar_resumen()} == {
        "Fábrica A": 1, "Fábrica B": 0
    }
