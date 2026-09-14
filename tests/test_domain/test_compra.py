"""Pruebas de los invariantes de negocio de domain.compra (Fase 4B).

No dependen de base de datos: validan que la entidad se autovalide al
construirse (mismo enfoque que tests/test_domain/test_categoria.py).
"""

import pytest

from domain.compra import Compra, DetalleCompra, ItemCompra
from excepciones import DatosInvalidosError


def test_item_compra_valido_se_crea_sin_error():
    item = ItemCompra(producto_id=1, cantidad=3, costo_unitario_centavos=150)
    assert item.producto_id == 1
    assert item.cantidad == 3
    assert item.costo_unitario_centavos == 150


def test_item_compra_rechaza_cantidad_cero():
    with pytest.raises(DatosInvalidosError):
        ItemCompra(producto_id=1, cantidad=0, costo_unitario_centavos=150)


def test_item_compra_rechaza_cantidad_negativa():
    with pytest.raises(DatosInvalidosError):
        ItemCompra(producto_id=1, cantidad=-1, costo_unitario_centavos=150)


def test_item_compra_rechaza_costo_negativo():
    with pytest.raises(DatosInvalidosError):
        ItemCompra(producto_id=1, cantidad=1, costo_unitario_centavos=-1)


def test_item_compra_admite_costo_cero():
    item = ItemCompra(producto_id=1, cantidad=1, costo_unitario_centavos=0)
    assert item.costo_unitario_centavos == 0


def test_compra_valida_se_crea_sin_error():
    compra = Compra(
        id=1,
        proveedor_id=2,
        usuario_id=3,
        fecha="2026-09-12 10:00:00",
        total_centavos=1500,
        observaciones="Entrega parcial",
    )
    assert compra.id == 1
    assert compra.proveedor_id == 2
    assert compra.usuario_id == 3
    assert compra.total_centavos == 1500
    assert compra.observaciones == "Entrega parcial"


def test_compra_admite_observaciones_none():
    compra = Compra(id=1, proveedor_id=2, usuario_id=3, fecha="2026-09-12", total_centavos=0)
    assert compra.observaciones is None


def test_detalle_compra_valido_se_crea_sin_error():
    detalle = DetalleCompra(
        id=1, compra_id=2, producto_id=3, cantidad=4, costo_unitario_centavos=150, subtotal_centavos=600
    )
    assert detalle.cantidad == 4
    assert detalle.subtotal_centavos == 600
