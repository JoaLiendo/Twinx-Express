"""Inventario físico (V1.4): creación, conteo a ciegas, diferencias, confirmación atómica e
idempotente, rechazo ante movimientos posteriores al conteo (incluido el caso ABA), cancelación,
permisos y auditoría. Todo contra una base temporal real."""

import pytest

from db.repositorios import auditoria as repositorio_auditoria
from domain.compra import ItemCompra
from domain.inventario import LineaConteo
from domain.venta import ItemVenta
from excepciones import (
    ClaveIdempotenciaReutilizadaError,
    DatosInvalidosError,
    InventarioAbiertoExistenteError,
    InventarioDesactualizadoError,
    InventarioNoAbiertoError,
    InventarioNoEncontradoError,
    InventarioSinConteosError,
    PermisoDenegadoError,
    ProductoFueraDeInventarioError,
    ProductoNoEncontradoError,
)
from services import servicio_compras, servicio_inventario, servicio_proveedores, servicio_stock, servicio_ventas
from tests.utilidades_clientes import consultar, contar, crear_owner, crear_producto, crear_usuario

pytestmark = pytest.mark.usefixtures("caja_abierta")


@pytest.fixture
def e(base_datos_temporal):
    owner = crear_owner()
    return type(
        "Escenario",
        (),
        {
            "ruta": base_datos_temporal,
            "owner": owner,
            "cajera": crear_usuario("cajera", "CASHIER"),
            "a": crear_producto("7790000000001", stock=10),
            "b": crear_producto("7790000000002", stock=20),
            "c": crear_producto("7790000000003", stock=5),
        },
    )


def _stock(producto) -> int:
    return servicio_stock.obtener_por_id(producto.id).stock_actual


def _version(e, producto) -> int:
    return consultar(e.ruta, "SELECT version_stock FROM productos WHERE id = ?", (producto.id,))[0][0]


def _abrir(e, *productos):
    ids = [p.id for p in productos] or None
    return servicio_inventario.crear_inventario(e.owner.id, ids)


def _contar(e, inventario, producto, cantidad, usuario=None):
    servicio_inventario.registrar_conteo(inventario.id, producto.id, cantidad, (usuario or e.owner).id)


def _vender(e, producto, cantidad=1):
    return servicio_ventas.registrar_venta([ItemVenta(producto.id, cantidad)], "EFECTIVO", usuario_id=e.cajera.id)


def _comprar(e, producto, cantidad=1, costo=100):
    proveedor = servicio_proveedores.crear_proveedor(f"Prov {producto.id} {cantidad}")
    return servicio_compras.registrar_compra(proveedor.id, e.owner.id, [ItemCompra(producto.id, cantidad, costo)])


def _acciones(prefijo):
    """Acciones auditadas que empiezan con `prefijo`, de la más antigua a la más reciente."""
    return [x.accion for x in reversed(repositorio_auditoria.listar()) if x.accion.startswith(prefijo)]


def _ajustes_recuento(e):
    return consultar(e.ruta, "SELECT producto_id, delta, inventario_id FROM ajustes_stock WHERE motivo = 'RECUENTO'")


# --- creación ------------------------------------------------------------------------------------------


def test_crea_un_inventario_con_todos_los_productos_activos(e):
    servicio_stock.eliminar_producto(e.c.id)  # sin registros: se borra
    inactivo = crear_producto("7790000000009", stock=3)
    servicio_compras.registrar_compra(
        servicio_proveedores.crear_proveedor("P").id, e.owner.id, [ItemCompra(inactivo.id, 1, 1)]
    )
    servicio_stock.eliminar_producto(inactivo.id)  # baja lógica

    inventario = _abrir(e)

    lineas = servicio_inventario.listar_lineas_para_conteo(inventario.id, e.owner.id)
    assert sorted(l.producto_id for l in lineas) == sorted([e.a.id, e.b.id])
    assert inventario.abierto and inventario.usuario_id == e.owner.id
    assert _acciones("INVENTARIO") == ["INVENTARIO_CREADO"]


def test_seleccion_manual_incluye_un_producto_inactivo_con_stock(e):
    servicio_compras.registrar_compra(
        servicio_proveedores.crear_proveedor("P").id, e.owner.id, [ItemCompra(e.c.id, 1, 1)]
    )
    servicio_stock.eliminar_producto(e.c.id)  # baja lógica, conserva su stock (6)

    inventario = _abrir(e, e.a, e.c)

    assert sorted(l.producto_id for l in servicio_inventario.listar_lineas_para_conteo(inventario.id, e.owner.id)) == sorted(
        [e.a.id, e.c.id]
    )


def test_un_producto_inactivo_sin_stock_o_inexistente_no_puede_incluirse(e):
    servicio_compras.registrar_compra(
        servicio_proveedores.crear_proveedor("P").id, e.owner.id, [ItemCompra(e.c.id, 1, 1)]
    )
    servicio_ventas.registrar_venta([ItemVenta(e.c.id, 6)], "EFECTIVO", usuario_id=e.cajera.id)
    servicio_stock.eliminar_producto(e.c.id)  # inactivo y sin stock

    with pytest.raises(DatosInvalidosError):
        _abrir(e, e.c)
    with pytest.raises(ProductoNoEncontradoError):
        servicio_inventario.crear_inventario(e.owner.id, [9999])
    assert contar(e.ruta, "inventarios") == 0


def test_sin_productos_no_se_crea_el_inventario(base_datos_temporal):
    owner = crear_owner()

    with pytest.raises(DatosInvalidosError):
        servicio_inventario.crear_inventario(owner.id)


def test_un_solo_inventario_abierto_a_la_vez(e):
    _abrir(e)

    with pytest.raises(InventarioAbiertoExistenteError):
        _abrir(e, e.a)
    assert contar(e.ruta, "inventarios") == 1
    assert contar(e.ruta, "inventario_lineas") == 3


def test_tras_cerrar_uno_se_puede_abrir_otro(e):
    primero = _abrir(e)
    servicio_inventario.cancelar_inventario(primero.id, e.owner.id)

    segundo = _abrir(e)

    assert segundo.id != primero.id


# --- conteo a ciegas ------------------------------------------------------------------------------------


def test_el_conteo_registra_esperado_version_cantidad_costo_usuario_y_fecha(e):
    inventario = _abrir(e, e.a)
    _contar(e, inventario, e.a, 8, usuario=e.cajera)

    fila = consultar(e.ruta, "SELECT * FROM inventario_lineas")[0]

    assert (fila["stock_esperado"], fila["cantidad_contada"], fila["costo_unitario_centavos"]) == (10, 8, 10)
    assert fila["version_esperada"] == _version(e, e.a)
    assert fila["usuario_conteo_id"] == e.cajera.id and fila["fecha_conteo"]


def test_la_vista_de_conteo_nunca_expone_el_esperado_ni_la_diferencia(e):
    inventario = _abrir(e, e.a)
    _contar(e, inventario, e.a, 8)

    (linea,) = servicio_inventario.listar_lineas_para_conteo(inventario.id, e.cajera.id)

    assert isinstance(linea, LineaConteo)
    campos = set(linea.__dataclass_fields__)
    assert not {"stock_esperado", "diferencia", "version_esperada"} & campos
    assert linea.cantidad_contada == 8 and linea.usuario_conteo_nombre == "Duenio"
    assert not hasattr(linea, "diferencia") and not hasattr(linea, "stock_esperado")


def test_registrar_conteo_no_devuelve_esperado_ni_diferencia(e):
    inventario = _abrir(e, e.a)

    assert servicio_inventario.registrar_conteo(inventario.id, e.a.id, 3, e.cajera.id) is None


def test_recontar_reemplaza_el_conteo_y_toma_un_esperado_nuevo(e):
    inventario = _abrir(e, e.a)
    _contar(e, inventario, e.a, 8)
    _vender(e, e.a, 2)

    _contar(e, inventario, e.a, 8)  # recuento después de la venta

    fila = consultar(e.ruta, "SELECT stock_esperado, version_esperada FROM inventario_lineas")[0]
    assert (fila[0], fila[1]) == (8, _version(e, e.a))
    resultado = servicio_inventario.confirmar_inventario(inventario.id, e.owner.id)
    assert resultado.ajustes_generados == 0 and resultado.lineas_sin_diferencia == 1
    assert _stock(e.a) == 8


@pytest.mark.parametrize("cantidad", [-1, 1.5, "3", None, True])
def test_cantidades_contadas_invalidas_se_rechazan(e, cantidad):
    inventario = _abrir(e, e.a)

    with pytest.raises(DatosInvalidosError):
        servicio_inventario.registrar_conteo(inventario.id, e.a.id, cantidad, e.owner.id)
    assert consultar(e.ruta, "SELECT cantidad_contada FROM inventario_lineas")[0][0] is None


def test_contar_un_producto_ajeno_o_en_un_inventario_cerrado_falla(e):
    inventario = _abrir(e, e.a)

    with pytest.raises(ProductoFueraDeInventarioError):
        servicio_inventario.registrar_conteo(inventario.id, e.b.id, 1, e.owner.id)
    with pytest.raises(ProductoFueraDeInventarioError):
        servicio_inventario.registrar_conteo(inventario.id, 9999, 1, e.owner.id)
    with pytest.raises(InventarioNoEncontradoError):
        servicio_inventario.registrar_conteo(9999, e.a.id, 1, e.owner.id)
    servicio_inventario.cancelar_inventario(inventario.id, e.owner.id)
    with pytest.raises(InventarioNoAbiertoError):
        servicio_inventario.registrar_conteo(inventario.id, e.a.id, 1, e.owner.id)


def test_contar_no_modifica_el_stock_ni_su_version(e):
    inventario = _abrir(e)
    version_antes = _version(e, e.a)

    _contar(e, inventario, e.a, 1)

    assert _stock(e.a) == 10 and _version(e, e.a) == version_antes
    assert contar(e.ruta, "ajustes_stock") == 0


# --- confirmación: diferencias -----------------------------------------------------------------------------


def test_diferencia_negativa_positiva_y_cero(e):
    inventario = _abrir(e)
    _contar(e, inventario, e.a, 8)  # -2
    _contar(e, inventario, e.b, 25)  # +5
    _contar(e, inventario, e.c, 5)  # 0

    resultado = servicio_inventario.confirmar_inventario(inventario.id, e.owner.id)

    assert (resultado.ajustes_generados, resultado.lineas_sin_diferencia, resultado.lineas_sin_contar) == (2, 1, 0)
    assert (_stock(e.a), _stock(e.b), _stock(e.c)) == (8, 25, 5)
    assert sorted(tuple(f) for f in _ajustes_recuento(e)) == sorted(
        [(e.a.id, -2, inventario.id), (e.b.id, 5, inventario.id)]
    )
    assert resultado.inventario.estado == "CONFIRMADO" and resultado.inventario.fecha_cierre
    assert resultado.inventario.usuario_cierre_id == e.owner.id


def test_el_ajuste_queda_explicado_por_su_origen_y_con_auditoria(e):
    inventario = _abrir(e, e.a)
    _contar(e, inventario, e.a, 7)

    servicio_inventario.confirmar_inventario(inventario.id, e.owner.id)

    ajuste = consultar(e.ruta, "SELECT * FROM ajustes_stock")[0]
    assert (ajuste["motivo"], ajuste["stock_anterior"], ajuste["stock_resultante"]) == ("RECUENTO", 10, 7)
    assert ajuste["observaciones"] == f"Inventario #{inventario.id}" and ajuste["usuario_id"] == e.owner.id
    linea = consultar(e.ruta, "SELECT ajuste_id FROM inventario_lineas")[0]
    assert linea["ajuste_id"] == ajuste["id"]
    assert _acciones("INVENTARIO") == ["INVENTARIO_CREADO", "INVENTARIO_CONFIRMADO"]
    assert _acciones("AJUSTE_STOCK") == ["AJUSTE_STOCK"]


def test_diferencia_cero_no_genera_ajuste_ni_toca_stock_ni_version(e):
    inventario = _abrir(e, e.a)
    _contar(e, inventario, e.a, 10)
    version = _version(e, e.a)

    resultado = servicio_inventario.confirmar_inventario(inventario.id, e.owner.id)

    assert resultado.ajustes_generados == 0 and contar(e.ruta, "ajustes_stock") == 0
    assert _stock(e.a) == 10 and _version(e, e.a) == version


def test_las_lineas_sin_contar_no_se_tocan_ni_se_interpretan_como_cero(e):
    inventario = _abrir(e)
    _contar(e, inventario, e.a, 9)

    resultado = servicio_inventario.confirmar_inventario(inventario.id, e.owner.id)

    assert resultado.lineas_sin_contar == 2 and resultado.ajustes_generados == 1
    assert (_stock(e.b), _stock(e.c)) == (20, 5)


def test_no_se_confirma_un_inventario_sin_ninguna_linea_contada(e):
    inventario = _abrir(e)

    with pytest.raises(InventarioSinConteosError):
        servicio_inventario.confirmar_inventario(inventario.id, e.owner.id)
    assert servicio_inventario.obtener_detalle(inventario.id, e.owner.id).inventario.abierto


def test_producto_inactivo_con_stock_se_cuenta_y_ajusta(e):
    servicio_compras.registrar_compra(
        servicio_proveedores.crear_proveedor("P").id, e.owner.id, [ItemCompra(e.c.id, 1, 1)]
    )
    servicio_stock.eliminar_producto(e.c.id)  # inactivo con stock 6
    inventario = _abrir(e, e.c)
    _contar(e, inventario, e.c, 4)

    servicio_inventario.confirmar_inventario(inventario.id, e.owner.id)

    fila = consultar(e.ruta, "SELECT stock_actual, activo FROM productos WHERE id = ?", (e.c.id,))[0]
    assert (fila["stock_actual"], fila["activo"]) == (4, 0)


def test_el_ajuste_manual_sigue_exigiendo_un_producto_activo(e):
    servicio_compras.registrar_compra(
        servicio_proveedores.crear_proveedor("P").id, e.owner.id, [ItemCompra(e.c.id, 1, 1)]
    )
    servicio_stock.eliminar_producto(e.c.id)

    with pytest.raises(ProductoNoEncontradoError):
        servicio_stock.ajustar_stock(e.c.id, -1, "MERMA", e.owner.id)


# --- REGLA CRÍTICA: movimientos posteriores al conteo ----------------------------------------------------------


def _assert_rechazo_sin_efectos(e, inventario, stock_esperado_por_producto):
    with pytest.raises(InventarioDesactualizadoError) as error:
        servicio_inventario.confirmar_inventario(inventario.id, e.owner.id)
    assert contar(e.ruta, "ajustes_stock", "motivo = 'RECUENTO'") == 0
    assert servicio_inventario.obtener_detalle(inventario.id, e.owner.id).inventario.estado == "ABIERTO"
    assert _acciones("INVENTARIO_CONFIRMADO") == []
    for producto, stock in stock_esperado_por_producto:
        assert _stock(producto) == stock
    return error.value


def test_venta_posterior_al_conteo_rechaza_y_no_aplica_menos_dos(e):
    """El caso de la regla: esperado 10, contado 8, venta de 2 => stock actual 8. NO se genera ajuste -2."""
    inventario = _abrir(e, e.a)
    _contar(e, inventario, e.a, 8)
    _vender(e, e.a, 2)

    error = _assert_rechazo_sin_efectos(e, inventario, [(e.a, 8)])

    assert error.productos == ["Producto 7790000000001"]
    assert "volvé a contar" in str(error)


def test_compra_posterior_al_conteo_rechaza(e):
    inventario = _abrir(e, e.a)
    _contar(e, inventario, e.a, 10)  # incluso con diferencia cero
    _comprar(e, e.a, 3)

    _assert_rechazo_sin_efectos(e, inventario, [(e.a, 13)])


def test_ajuste_manual_posterior_al_conteo_rechaza(e):
    inventario = _abrir(e, e.a)
    _contar(e, inventario, e.a, 9)
    servicio_stock.ajustar_stock(e.a.id, -1, "MERMA", e.owner.id)

    _assert_rechazo_sin_efectos(e, inventario, [(e.a, 9)])


def test_anulacion_posterior_al_conteo_rechaza(e):
    venta = _vender(e, e.a, 2)  # stock 8
    inventario = _abrir(e, e.a)
    _contar(e, inventario, e.a, 8)
    servicio_ventas.anular_venta(venta.id, "ERROR_CARGA", None, e.owner.id)  # stock 10

    _assert_rechazo_sin_efectos(e, inventario, [(e.a, 10)])


def test_caso_aba_venta_y_compra_con_el_mismo_stock_numerico_rechaza(e):
    inventario = _abrir(e, e.a)
    _contar(e, inventario, e.a, 10)
    _vender(e, e.a, 2)
    _comprar(e, e.a, 2)
    assert _stock(e.a) == 10  # el mismo número que al contar

    _assert_rechazo_sin_efectos(e, inventario, [(e.a, 10)])


def test_la_version_cambia_aunque_el_stock_numerico_sea_igual(e):
    antes = _version(e, e.a)

    _vender(e, e.a, 2)
    _comprar(e, e.a, 2)

    assert _stock(e.a) == 10 and _version(e, e.a) == antes + 2


def test_todas_las_lineas_contadas_validan_version_no_solo_las_de_diferencia(e):
    inventario = _abrir(e)
    _contar(e, inventario, e.a, 8)  # con diferencia
    _contar(e, inventario, e.b, 20)  # sin diferencia
    _vender(e, e.b, 1)
    _comprar(e, e.b, 1)  # b vuelve a 20 pero con otra versión

    error = _assert_rechazo_sin_efectos(e, inventario, [(e.a, 10), (e.b, 20)])

    assert len(error.productos) == 1


def test_un_solo_producto_desactualizado_rechaza_todo_sin_ajustes_parciales(e):
    inventario = _abrir(e)
    _contar(e, inventario, e.a, 8)
    _contar(e, inventario, e.b, 25)
    _vender(e, e.b, 1)

    _assert_rechazo_sin_efectos(e, inventario, [(e.a, 10), (e.b, 19)])


def test_tras_el_rechazo_recontar_permite_confirmar(e):
    inventario = _abrir(e, e.a)
    _contar(e, inventario, e.a, 8)
    _vender(e, e.a, 2)
    with pytest.raises(InventarioDesactualizadoError):
        servicio_inventario.confirmar_inventario(inventario.id, e.owner.id)

    _contar(e, inventario, e.a, 8)
    resultado = servicio_inventario.confirmar_inventario(inventario.id, e.owner.id)

    assert resultado.ajustes_generados == 0 and _stock(e.a) == 8


def test_el_detalle_marca_las_lineas_desactualizadas(e):
    inventario = _abrir(e)
    _contar(e, inventario, e.a, 8)
    _contar(e, inventario, e.b, 20)
    _vender(e, e.a, 1)

    lineas = {l.producto_id: l for l in servicio_inventario.obtener_detalle(inventario.id, e.owner.id).lineas}

    assert lineas[e.a.id].desactualizada is True
    assert lineas[e.b.id].desactualizada is False and lineas[e.c.id].desactualizada is False


def test_movimientos_de_productos_no_contados_no_bloquean_la_confirmacion(e):
    inventario = _abrir(e, e.a, e.b)
    _contar(e, inventario, e.a, 9)
    _vender(e, e.b, 3)  # b está en el inventario pero sin contar

    resultado = servicio_inventario.confirmar_inventario(inventario.id, e.owner.id)

    assert resultado.ajustes_generados == 1 and _stock(e.b) == 17


# --- atomicidad -----------------------------------------------------------------------------------------------


def test_falla_en_un_ajuste_intermedio_revierte_todo(e, monkeypatch):
    inventario = _abrir(e)
    _contar(e, inventario, e.a, 8)
    _contar(e, inventario, e.b, 25)
    _contar(e, inventario, e.c, 4)
    original = servicio_stock.repositorio_ajustes_stock.registrar_ajuste_en_conexion
    llamadas = []

    def fallar_en_el_tercero(conexion, ajuste, clave_idempotencia=None):
        llamadas.append(ajuste.producto_id)
        if len(llamadas) == 3:
            raise RuntimeError("falla simulada")
        return original(conexion, ajuste, clave_idempotencia=clave_idempotencia)

    with monkeypatch.context() as parche:
        parche.setattr(servicio_stock.repositorio_ajustes_stock, "registrar_ajuste_en_conexion", fallar_en_el_tercero)
        with pytest.raises(RuntimeError):
            servicio_inventario.confirmar_inventario(inventario.id, e.owner.id)

    assert (_stock(e.a), _stock(e.b), _stock(e.c)) == (10, 20, 5)
    assert contar(e.ruta, "ajustes_stock") == 0
    assert contar(e.ruta, "inventario_lineas", "ajuste_id IS NOT NULL") == 0
    assert _acciones("INVENTARIO_CONFIRMADO") == [] and _acciones("AJUSTE_STOCK") == []
    assert servicio_inventario.obtener_detalle(inventario.id, e.owner.id).inventario.abierto
    # y el inventario se puede confirmar después, sin residuos
    resultado = servicio_inventario.confirmar_inventario(inventario.id, e.owner.id)
    assert resultado.ajustes_generados == 3


def test_falla_en_la_auditoria_final_revierte_todo(e, monkeypatch):
    inventario = _abrir(e, e.a)
    _contar(e, inventario, e.a, 8)
    original = repositorio_auditoria.registrar_en_conexion

    def fallar_al_confirmar(conexion, usuario_id, accion, *resto):
        if accion == "INVENTARIO_CONFIRMADO":
            raise RuntimeError("falla simulada")
        return original(conexion, usuario_id, accion, *resto)

    with monkeypatch.context() as parche:
        parche.setattr(repositorio_auditoria, "registrar_en_conexion", fallar_al_confirmar)
        with pytest.raises(RuntimeError):
            servicio_inventario.confirmar_inventario(inventario.id, e.owner.id)

    assert _stock(e.a) == 10 and contar(e.ruta, "ajustes_stock") == 0
    assert servicio_inventario.obtener_detalle(inventario.id, e.owner.id).inventario.abierto


# --- idempotencia ------------------------------------------------------------------------------------------------


def test_doble_confirmacion_no_duplica_ajustes(e):
    inventario = _abrir(e, e.a)
    _contar(e, inventario, e.a, 8)
    servicio_inventario.confirmar_inventario(inventario.id, e.owner.id)

    with pytest.raises(InventarioNoAbiertoError):
        servicio_inventario.confirmar_inventario(inventario.id, e.owner.id)

    assert contar(e.ruta, "ajustes_stock") == 1 and _stock(e.a) == 8
    assert _acciones("INVENTARIO_CONFIRMADO") == ["INVENTARIO_CONFIRMADO"]


def test_reenvio_con_la_misma_clave_devuelve_el_mismo_resultado_sin_reaplicar(e):
    inventario = _abrir(e, e.a)
    _contar(e, inventario, e.a, 8)
    primero = servicio_inventario.confirmar_inventario(inventario.id, e.owner.id, clave_idempotencia="k-1")

    segundo = servicio_inventario.confirmar_inventario(inventario.id, e.owner.id, clave_idempotencia="k-1")

    assert segundo.ajustes_generados == primero.ajustes_generados == 1
    assert contar(e.ruta, "ajustes_stock") == 1 and _stock(e.a) == 8


def test_una_clave_ya_usada_en_otro_inventario_se_rechaza(e):
    primero = _abrir(e, e.a)
    _contar(e, primero, e.a, 8)
    servicio_inventario.confirmar_inventario(primero.id, e.owner.id, clave_idempotencia="k-1")
    segundo = _abrir(e, e.b)
    _contar(e, segundo, e.b, 1)

    with pytest.raises(ClaveIdempotenciaReutilizadaError):
        servicio_inventario.confirmar_inventario(segundo.id, e.owner.id, clave_idempotencia="k-1")
    assert _stock(e.b) == 20


# --- cancelación y estados ------------------------------------------------------------------------------------------


def test_cancelar_no_toca_stock_y_no_se_puede_confirmar_ni_reabrir(e):
    inventario = _abrir(e, e.a)
    _contar(e, inventario, e.a, 1)

    cancelado = servicio_inventario.cancelar_inventario(inventario.id, e.owner.id)

    assert cancelado.estado == "CANCELADO" and cancelado.usuario_cierre_id == e.owner.id
    assert _stock(e.a) == 10 and contar(e.ruta, "ajustes_stock") == 0
    with pytest.raises(InventarioNoAbiertoError):
        servicio_inventario.confirmar_inventario(inventario.id, e.owner.id)
    with pytest.raises(InventarioNoAbiertoError):
        servicio_inventario.cancelar_inventario(inventario.id, e.owner.id)
    assert _acciones("INVENTARIO") == ["INVENTARIO_CREADO", "INVENTARIO_CANCELADO"]


def test_inventario_inexistente(e):
    for operacion in (
        lambda: servicio_inventario.confirmar_inventario(9999, e.owner.id),
        lambda: servicio_inventario.cancelar_inventario(9999, e.owner.id),
        lambda: servicio_inventario.obtener_detalle(9999, e.owner.id),
        lambda: servicio_inventario.listar_lineas_para_conteo(9999, e.owner.id),
    ):
        with pytest.raises(InventarioNoEncontradoError):
            operacion()


# --- permisos --------------------------------------------------------------------------------------------------------------


def test_cashier_cuenta_y_consulta_el_inventario_abierto_pero_no_lo_gestiona(e):
    inventario = _abrir(e, e.a)

    assert servicio_inventario.obtener_inventario_abierto(e.cajera.id).id == inventario.id
    _contar(e, inventario, e.a, 7, usuario=e.cajera)
    assert servicio_inventario.listar_lineas_para_conteo(inventario.id, e.cajera.id)[0].cantidad_contada == 7

    for operacion in (
        lambda: servicio_inventario.crear_inventario(e.cajera.id),
        lambda: servicio_inventario.confirmar_inventario(inventario.id, e.cajera.id),
        lambda: servicio_inventario.cancelar_inventario(inventario.id, e.cajera.id),
        lambda: servicio_inventario.obtener_detalle(inventario.id, e.cajera.id),
        lambda: servicio_inventario.listar_inventarios(e.cajera.id),
    ):
        with pytest.raises(PermisoDenegadoError):
            operacion()
    assert servicio_inventario.obtener_detalle(inventario.id, e.owner.id).inventario.abierto
    assert _stock(e.a) == 10


def test_un_usuario_inexistente_o_inactivo_no_puede_operar(e):
    inventario = _abrir(e, e.a)
    inactivo = crear_usuario("baja", "CASHIER", activo=False)

    for usuario_id in (9999, inactivo.id):
        with pytest.raises(PermisoDenegadoError):
            servicio_inventario.registrar_conteo(inventario.id, e.a.id, 1, usuario_id)
        with pytest.raises(PermisoDenegadoError):
            servicio_inventario.obtener_inventario_abierto(usuario_id)


def test_owner_consulta_historial_y_detalle_con_esperado_contado_diferencia_y_costo(e):
    inventario = _abrir(e, e.a)
    _contar(e, inventario, e.a, 7, usuario=e.cajera)
    servicio_inventario.confirmar_inventario(inventario.id, e.owner.id)

    (resumen,) = servicio_inventario.listar_inventarios(e.owner.id)
    (linea,) = servicio_inventario.obtener_detalle(inventario.id, e.owner.id).lineas

    assert (resumen.cantidad_lineas, resumen.cantidad_contadas, resumen.cantidad_ajustes) == (1, 1, 1)
    assert resumen.usuario_nombre_completo == "Duenio"
    assert (linea.stock_esperado, linea.cantidad_contada, linea.diferencia) == (10, 7, -3)
    assert (linea.costo_unitario_centavos, linea.diferencia_valorizada_centavos) == (10, -30)
    assert linea.usuario_conteo_nombre == "Cajera" and linea.ajuste_id is not None


# --- invariantes globales -----------------------------------------------------------------------------------------


def _violaciones_de_invariantes(ruta) -> list[str]:
    """Invariantes del inventario que el esquema no puede exigir todos juntos al insertar."""
    consultas = {
        "más de un inventario abierto": "SELECT COUNT(*) FROM inventarios WHERE estado = 'ABIERTO' HAVING COUNT(*) > 1",
        "ajuste de inventario que no coincide con su línea": """
            SELECT a.id FROM ajustes_stock a
            LEFT JOIN inventario_lineas l ON l.inventario_id = a.inventario_id AND l.producto_id = a.producto_id
            WHERE a.inventario_id IS NOT NULL
              AND (l.id IS NULL OR l.ajuste_id IS NOT a.id OR a.motivo <> 'RECUENTO'
                   OR a.delta <> l.cantidad_contada - l.stock_esperado OR a.stock_anterior <> l.stock_esperado
                   OR a.stock_resultante <> l.cantidad_contada)""",
        "línea cuyo ajuste no pertenece a su inventario": """
            SELECT l.id FROM inventario_lineas l JOIN ajustes_stock a ON a.id = l.ajuste_id
            WHERE a.inventario_id IS NOT l.inventario_id""",
        "línea con ajuste en un inventario que no está confirmado": """
            SELECT l.id FROM inventario_lineas l JOIN inventarios i ON i.id = l.inventario_id
            WHERE l.ajuste_id IS NOT NULL AND i.estado <> 'CONFIRMADO'""",
        "confirmado con una diferencia sin ajuste": """
            SELECT l.id FROM inventario_lineas l JOIN inventarios i ON i.id = l.inventario_id
            WHERE i.estado = 'CONFIRMADO' AND l.cantidad_contada IS NOT NULL
              AND l.cantidad_contada <> l.stock_esperado AND l.ajuste_id IS NULL""",
        "cancelado con ajustes": """
            SELECT a.id FROM ajustes_stock a JOIN inventarios i ON i.id = a.inventario_id WHERE i.estado = 'CANCELADO'""",
        "cerrado sin usuario o fecha de cierre": """
            SELECT id FROM inventarios WHERE estado <> 'ABIERTO' AND (fecha_cierre IS NULL OR usuario_cierre_id IS NULL)""",
    }
    return [descripcion for descripcion, sql in consultas.items() if consultar(ruta, sql)]


def test_invariantes_del_inventario_tras_un_escenario_variado(e):
    primero = _abrir(e)
    _contar(e, primero, e.a, 7)
    _contar(e, primero, e.b, 20)
    _vender(e, e.c)
    servicio_inventario.confirmar_inventario(primero.id, e.owner.id)
    assert _violaciones_de_invariantes(e.ruta) == []

    segundo = _abrir(e, e.a, e.c)
    _contar(e, segundo, e.a, 1)
    _vender(e, e.a)
    with pytest.raises(InventarioDesactualizadoError):
        servicio_inventario.confirmar_inventario(segundo.id, e.owner.id)
    assert _violaciones_de_invariantes(e.ruta) == []
    servicio_inventario.cancelar_inventario(segundo.id, e.owner.id)

    tercero = _abrir(e, e.a)
    _contar(e, tercero, e.a, 3)
    servicio_inventario.confirmar_inventario(tercero.id, e.owner.id)

    assert _violaciones_de_invariantes(e.ruta) == []
    assert _stock(e.a) == 3


def test_el_verificador_de_invariantes_detecta_una_violacion_real(e):
    from tests.utilidades_clientes import conectar

    inventario = _abrir(e, e.a)
    _contar(e, inventario, e.a, 8)
    servicio_inventario.confirmar_inventario(inventario.id, e.owner.id)
    con = conectar(e.ruta)
    try:  # se rompe a mano, saltando el trigger, solo para probar que el verificador no es vacuo
        con.execute("DROP TRIGGER trg_ajustes_stock_inventario_inmutable")
        con.execute("UPDATE ajustes_stock SET inventario_id = NULL")
        con.commit()
    finally:
        con.close()

    assert _violaciones_de_invariantes(e.ruta) == ["línea cuyo ajuste no pertenece a su inventario"]
