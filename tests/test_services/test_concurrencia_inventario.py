"""Concurrencia del inventario físico (V1.4): hilos, cada uno con su propia conexión, compiten con la
confirmación. `BEGIN IMMEDIATE` los serializa: la confirmación jamás sobrescribe una venta o compra
que ocurrió después del conteo, y jamás duplica ajustes."""

import threading

import pytest

from domain.compra import ItemCompra
from domain.venta import ItemVenta
from excepciones import InventarioDesactualizadoError, InventarioNoAbiertoError
from services import servicio_compras, servicio_inventario, servicio_proveedores, servicio_stock, servicio_ventas
from tests.utilidades_clientes import consultar, contar, crear_owner, crear_producto, crear_usuario

pytestmark = pytest.mark.usefixtures("caja_abierta")


@pytest.fixture
def e(base_datos_temporal):
    owner = crear_owner()
    producto = crear_producto("7790000000001", stock=10)
    inventario = servicio_inventario.crear_inventario(owner.id, [producto.id])
    servicio_inventario.registrar_conteo(inventario.id, producto.id, 8, owner.id)  # esperado 10, contado 8
    return type(
        "Escenario",
        (),
        {
            "ruta": base_datos_temporal,
            "owner": owner,
            "cajera": crear_usuario("cajera", "CASHIER"),
            "producto": producto,
            "inventario": inventario,
        },
    )


def _correr_en_paralelo(*funciones):
    """Ejecuta cada función en su hilo, todas liberadas a la vez; devuelve `[(resultado, error)]` en orden."""
    largada = threading.Barrier(len(funciones))
    resultados: list = [None] * len(funciones)

    def trabajar(indice, funcion):
        largada.wait()
        try:
            resultados[indice] = (funcion(), None)
        except Exception as error:  # noqa: BLE001 - se devuelve para verificarlo en el hilo principal
            resultados[indice] = (None, error)

    hilos = [threading.Thread(target=trabajar, args=(i, f)) for i, f in enumerate(funciones)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()
    return resultados


def _stock(e) -> int:
    return servicio_stock.obtener_por_id(e.producto.id).stock_actual


def test_venta_y_confirmacion_simultaneas_nunca_sobrescriben_la_venta(e):
    """Sea cual sea el orden, o la confirmación va primero (y la venta descuenta después) o la venta va
    primero y la confirmación se rechaza. Nunca debe quedar el stock en un valor que ignore la venta."""
    (venta, error_venta), (confirmacion, error_confirmacion) = _correr_en_paralelo(
        lambda: servicio_ventas.registrar_venta(
            [ItemVenta(e.producto.id, 2)], "EFECTIVO", usuario_id=e.cajera.id
        ),
        lambda: servicio_inventario.confirmar_inventario(e.inventario.id, e.owner.id),
    )

    assert error_venta is None
    if error_confirmacion is None:
        # Confirmó primero: stock 10 -> 8 por el recuento, y después la venta -2 => 6 (ambos efectos).
        assert _stock(e) == 6
        assert contar(e.ruta, "ajustes_stock", "motivo = 'RECUENTO'") == 1
    else:
        # La venta fue primero: se rechaza la confirmación y el stock refleja solo la venta.
        assert isinstance(error_confirmacion, InventarioDesactualizadoError)
        assert _stock(e) == 8
        assert contar(e.ruta, "ajustes_stock") == 0
        assert servicio_inventario.obtener_detalle(e.inventario.id, e.owner.id).inventario.abierto


def test_compra_y_confirmacion_simultaneas_nunca_sobrescriben_la_compra(e):
    proveedor = servicio_proveedores.crear_proveedor("P")

    (_, error_compra), (_, error_confirmacion) = _correr_en_paralelo(
        lambda: servicio_compras.registrar_compra(proveedor.id, e.owner.id, [ItemCompra(e.producto.id, 5, 10)]),
        lambda: servicio_inventario.confirmar_inventario(e.inventario.id, e.owner.id),
    )

    assert error_compra is None
    if error_confirmacion is None:
        assert _stock(e) == 13  # 10 -> 8 (recuento) -> 13 (compra)
    else:
        assert isinstance(error_confirmacion, InventarioDesactualizadoError)
        assert _stock(e) == 15 and contar(e.ruta, "ajustes_stock") == 0


def test_dos_confirmaciones_concurrentes_generan_un_solo_ajuste(e):
    resultados = _correr_en_paralelo(
        *[lambda: servicio_inventario.confirmar_inventario(e.inventario.id, e.owner.id) for _ in range(4)]
    )

    exitos = [r for r, error in resultados if error is None]
    errores = [error for _, error in resultados if error is not None]
    assert len(exitos) == 1 and all(isinstance(error, InventarioNoAbiertoError) for error in errores)
    assert contar(e.ruta, "ajustes_stock", "motivo = 'RECUENTO'") == 1 and _stock(e) == 8
    assert contar(e.ruta, "auditoria", "accion = 'INVENTARIO_CONFIRMADO'") == 1


def test_reenvios_concurrentes_con_la_misma_clave_devuelven_el_mismo_resultado(e):
    resultados = _correr_en_paralelo(
        *[
            lambda: servicio_inventario.confirmar_inventario(e.inventario.id, e.owner.id, clave_idempotencia="k")
            for _ in range(4)
        ]
    )

    assert all(error is None for _, error in resultados)
    assert {r.ajustes_generados for r, _ in resultados} == {1}
    assert contar(e.ruta, "ajustes_stock") == 1 and _stock(e) == 8


def test_cancelar_y_confirmar_a_la_vez_deja_un_unico_estado_final(e):
    (_, error_confirmar), (_, error_cancelar) = _correr_en_paralelo(
        lambda: servicio_inventario.confirmar_inventario(e.inventario.id, e.owner.id),
        lambda: servicio_inventario.cancelar_inventario(e.inventario.id, e.owner.id),
    )

    assert (error_confirmar is None) != (error_cancelar is None)
    estado = servicio_inventario.obtener_detalle(e.inventario.id, e.owner.id).inventario.estado
    if estado == "CONFIRMADO":
        assert _stock(e) == 8 and contar(e.ruta, "ajustes_stock") == 1
    else:
        assert estado == "CANCELADO" and _stock(e) == 10 and contar(e.ruta, "ajustes_stock") == 0


def test_conteo_y_confirmacion_simultaneos_no_aplican_un_conteo_a_medias(e):
    """Un reconteo concurrente con la confirmación: o entra antes (y se confirma con el nuevo esperado)
    o después (y se rechaza por inventario cerrado); nunca queda una línea a medias."""
    resultados = _correr_en_paralelo(
        lambda: servicio_inventario.registrar_conteo(e.inventario.id, e.producto.id, 7, e.cajera.id),
        lambda: servicio_inventario.confirmar_inventario(e.inventario.id, e.owner.id),
    )

    (_, error_conteo), (_, error_confirmacion) = resultados
    assert error_confirmacion is None
    fila = consultar(e.ruta, "SELECT stock_esperado, cantidad_contada, ajuste_id FROM inventario_lineas")[0]
    if error_conteo is None:
        assert (fila["cantidad_contada"], _stock(e)) == (7, 7)
    else:
        assert isinstance(error_conteo, InventarioNoAbiertoError)
        assert (fila["cantidad_contada"], _stock(e)) == (8, 8)
    assert contar(e.ruta, "ajustes_stock", "motivo = 'RECUENTO'") == 1


def test_muchas_ventas_concurrentes_con_la_confirmacion_dejan_el_stock_consistente(e):
    ventas = [
        lambda: servicio_ventas.registrar_venta([ItemVenta(e.producto.id, 1)], "EFECTIVO", usuario_id=e.cajera.id)
        for _ in range(5)
    ]

    resultados = _correr_en_paralelo(*ventas, lambda: servicio_inventario.confirmar_inventario(e.inventario.id, e.owner.id))

    *resultados_ventas, (_, error_confirmacion) = resultados
    assert all(error is None for _, error in resultados_ventas)
    if error_confirmacion is None:
        assert _stock(e) == 3  # 10 -> 8 por el recuento -> 3 por las cinco ventas posteriores
    else:
        assert isinstance(error_confirmacion, InventarioDesactualizadoError)
        assert _stock(e) == 10 - 5 and contar(e.ruta, "ajustes_stock") == 0
