"""Hallazgos de la auditoría post-implementación de V1.4: atomicidad de cada operación de proveedores con su
auditoría, compra con falla posterior al vínculo, baja lógica de productos con líneas de inventario y
validación de stock independiente de la de versión."""

import pytest

from db.repositorios import auditoria as repositorio_auditoria
from domain.compra import ItemCompra
from excepciones import InventarioDesactualizadoError
from services import servicio_compras, servicio_inventario, servicio_proveedores, servicio_stock
from tests.utilidades_clientes import conectar, consultar, contar, crear_owner, crear_producto

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
            "prov_a": servicio_proveedores.crear_proveedor("Proveedor A", usuario_id=owner.id),
            "prov_b": servicio_proveedores.crear_proveedor("Proveedor B", usuario_id=owner.id),
            "p1": crear_producto("7790000000001", stock=10),
        },
    )


def _fallar_auditoria(monkeypatch, accion_objetivo):
    original = repositorio_auditoria.registrar_en_conexion

    def fallar(conexion, usuario_id, accion, *resto):
        if accion == accion_objetivo:
            raise RuntimeError("falla simulada")
        return original(conexion, usuario_id, accion, *resto)

    monkeypatch.setattr(repositorio_auditoria, "registrar_en_conexion", fallar)


def _estado(e):
    return (
        [tuple(f) for f in consultar(e.ruta, "SELECT id, nombre, activo FROM proveedores ORDER BY id")],
        [tuple(f) for f in consultar(e.ruta, "SELECT producto_id, proveedor_id, es_principal FROM producto_proveedor")],
    )


# --- proveedores: cada operación y su auditoría son una sola transacción -----------------------------------------


@pytest.mark.parametrize(
    ("accion", "operacion"),
    [
        ("PROVEEDOR_CREADO", lambda e: servicio_proveedores.crear_proveedor("Nuevo", usuario_id=e.owner.id)),
        (
            "PROVEEDOR_EDITADO",
            lambda e: servicio_proveedores.actualizar_proveedor(e.prov_a.id, "Renombrado", usuario_id=e.owner.id),
        ),
        ("PROVEEDOR_BAJA", lambda e: servicio_proveedores.eliminar_proveedor(e.prov_a.id, usuario_id=e.owner.id)),
        (
            "PRODUCTO_PROVEEDOR_VINCULADO",
            lambda e: servicio_proveedores.vincular_producto(e.prov_a.id, e.p1.id, e.owner.id),
        ),
    ],
)
def test_si_la_auditoria_falla_la_operacion_se_revierte(e, monkeypatch, accion, operacion):
    antes = _estado(e)
    _fallar_auditoria(monkeypatch, accion)

    with pytest.raises(RuntimeError):
        operacion(e)

    assert _estado(e) == antes


@pytest.mark.parametrize("accion", ["PROVEEDOR_REACTIVADO", "PRODUCTO_PROVEEDOR_QUITADO", "PRODUCTO_PROVEEDOR_PRINCIPAL"])
def test_si_la_auditoria_falla_reactivar_quitar_y_cambiar_principal_se_revierten(e, monkeypatch, accion):
    servicio_proveedores.vincular_producto(e.prov_a.id, e.p1.id, e.owner.id)
    servicio_proveedores.vincular_producto(e.prov_b.id, e.p1.id, e.owner.id)
    servicio_proveedores.eliminar_proveedor(e.prov_a.id)  # baja lógica: tiene vínculo
    antes = _estado(e)
    _fallar_auditoria(monkeypatch, accion)
    operaciones = {
        "PROVEEDOR_REACTIVADO": lambda: servicio_proveedores.reactivar_proveedor(e.prov_a.id, usuario_id=e.owner.id),
        "PRODUCTO_PROVEEDOR_QUITADO": lambda: servicio_proveedores.quitar_vinculo(e.prov_b.id, e.p1.id, e.owner.id),
        "PRODUCTO_PROVEEDOR_PRINCIPAL": lambda: servicio_proveedores.establecer_principal(
            e.prov_b.id, e.p1.id, e.owner.id
        ),
    }

    with pytest.raises(RuntimeError):
        operaciones[accion]()

    assert _estado(e) == antes


# --- compra ------------------------------------------------------------------------------------------------------------


def test_una_falla_despues_de_crear_el_vinculo_revierte_compra_vinculo_stock_y_costo(e, monkeypatch):
    _fallar_auditoria(monkeypatch, "COMPRA_REGISTRADA")  # la auditoría ocurre después del vínculo y del stock
    antes = servicio_stock.obtener_por_id(e.p1.id)

    with pytest.raises(RuntimeError):
        servicio_compras.registrar_compra(e.prov_a.id, e.owner.id, [ItemCompra(e.p1.id, 2, 777)])

    assert contar(e.ruta, "producto_proveedor") == 0 and contar(e.ruta, "compras") == 0
    despues = servicio_stock.obtener_por_id(e.p1.id)
    assert (despues.stock_actual, despues.precio_costo_centavos) == (antes.stock_actual, antes.precio_costo_centavos)
    assert contar(e.ruta, "historial_precios") == 0


def test_secuencia_primer_segundo_y_segundo_con_principal_existente(e):
    def comprar(proveedor):
        servicio_compras.registrar_compra(proveedor.id, e.owner.id, [ItemCompra(e.p1.id, 1, 100)])

    def vinculos():
        return {
            f["proveedor_id"]: bool(f["es_principal"]) for f in consultar(e.ruta, "SELECT * FROM producto_proveedor")
        }

    comprar(e.prov_a)  # 1) primer proveedor: se crea y queda principal
    assert vinculos() == {e.prov_a.id: True}
    comprar(e.prov_b)  # 2) segundo proveedor con principal existente: se crea, NO reemplaza
    assert vinculos() == {e.prov_a.id: True, e.prov_b.id: False}
    comprar(e.prov_b)  # 3) otra compra al segundo: nada cambia ni se duplica
    assert vinculos() == {e.prov_a.id: True, e.prov_b.id: False}


# --- inventario ----------------------------------------------------------------------------------------------------------


def test_un_cambio_de_stock_sin_cambio_de_version_tambien_se_rechaza(e):
    """Defensa en profundidad: aunque el trigger de versión no estuviera, un stock distinto al del conteo
    rechaza la confirmación (la validación de stock es independiente de la de versión)."""
    inventario = servicio_inventario.crear_inventario(e.owner.id, [e.p1.id])
    servicio_inventario.registrar_conteo(inventario.id, e.p1.id, 8, e.owner.id)
    con = conectar(e.ruta)
    try:
        con.execute("DROP TRIGGER trg_productos_version_stock")
        con.execute("UPDATE productos SET stock_actual = 9 WHERE id = ?", (e.p1.id,))
        con.commit()
    finally:
        con.close()

    with pytest.raises(InventarioDesactualizadoError):
        servicio_inventario.confirmar_inventario(inventario.id, e.owner.id)
    assert servicio_stock.obtener_por_id(e.p1.id).stock_actual == 9 and contar(e.ruta, "ajustes_stock") == 0


def test_un_producto_con_lineas_de_inventario_pasa_a_baja_logica_y_lo_informa(e):
    """Sin ventas/compras/ajustes, la única razón para conservarlo es el inventario: debe desactivarse (no
    borrarse) y decir por qué, en vez de informar 'eliminado' y borrar su imagen."""
    inventario = servicio_inventario.crear_inventario(e.owner.id, [e.p1.id])

    baja_logica = servicio_stock.eliminar_producto(e.p1.id, usuario_id=e.owner.id)

    assert baja_logica is True
    assert servicio_stock.motivos_de_conservacion(e.p1.id) == ["inventarios"]
    assert contar(e.ruta, "productos", f"id = {e.p1.id} AND activo = 0") == 1
    servicio_inventario.registrar_conteo(inventario.id, e.p1.id, 9, e.owner.id)  # sigue funcionando inactivo
    assert servicio_inventario.confirmar_inventario(inventario.id, e.owner.id).ajustes_generados == 1
