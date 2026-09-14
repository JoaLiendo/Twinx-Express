"""Pruebas de los invariantes de negocio del dominio (domain/).

No dependen de base de datos: validan que las entidades se
autovaliden al construirse, en memoria.
"""

import pytest

from domain.caja import MovimientoCaja
from domain.producto import Producto
from domain.venta import ItemVenta, calcular_hash_contenido
from excepciones import DatosInvalidosError


class TestProducto:
    def _crear(self, **overrides):
        datos = dict(
            codigo_barras="7790000000001",
            nombre="Alfajor",
            precio_costo_centavos=100,
            precio_venta_centavos=200,
            stock_actual=10,
            stock_minimo=2,
        )
        datos.update(overrides)
        return Producto(**datos)

    def test_producto_valido_se_crea_sin_error(self):
        producto = self._crear()
        assert producto.precio_venta_centavos == 200

    def test_rechaza_codigo_de_barras_vacio(self):
        with pytest.raises(DatosInvalidosError):
            self._crear(codigo_barras="   ")

    def test_rechaza_nombre_vacio(self):
        with pytest.raises(DatosInvalidosError):
            self._crear(nombre="")

    def test_rechaza_precio_costo_negativo(self):
        with pytest.raises(DatosInvalidosError):
            self._crear(precio_costo_centavos=-1)

    def test_rechaza_precio_venta_negativo(self):
        with pytest.raises(DatosInvalidosError):
            self._crear(precio_venta_centavos=-1)

    def test_rechaza_stock_actual_negativo(self):
        with pytest.raises(DatosInvalidosError):
            self._crear(stock_actual=-1)

    def test_rechaza_stock_minimo_negativo(self):
        with pytest.raises(DatosInvalidosError):
            self._crear(stock_minimo=-1)

    def test_tiene_stock_critico_cuando_actual_es_menor_o_igual_al_minimo(self):
        assert self._crear(stock_actual=2, stock_minimo=2).tiene_stock_critico
        assert self._crear(stock_actual=1, stock_minimo=2).tiene_stock_critico
        assert not self._crear(stock_actual=3, stock_minimo=2).tiene_stock_critico

    def test_actualizar_stock_rechaza_valor_negativo(self):
        producto = self._crear()
        with pytest.raises(DatosInvalidosError):
            producto.actualizar_stock(-1)

    def test_actualizar_stock_cambia_el_valor(self):
        producto = self._crear(stock_actual=10)
        producto.actualizar_stock(5)
        assert producto.stock_actual == 5

    def test_categoria_id_es_none_por_defecto(self):
        assert self._crear().categoria_id is None

    def test_acepta_categoria_id_explicito(self):
        assert self._crear(categoria_id=5).categoria_id == 5

    def test_unidad_medida_es_unidad_por_defecto(self):
        assert self._crear().unidad_medida == "UNIDAD"

    @pytest.mark.parametrize("unidad", ["UNIDAD", "KG", "G", "LITRO", "ML"])
    def test_acepta_todas_las_unidades_validas(self, unidad):
        assert self._crear(unidad_medida=unidad).unidad_medida == unidad

    def test_rechaza_unidad_de_medida_invalida(self):
        with pytest.raises(DatosInvalidosError):
            self._crear(unidad_medida="TONELADA")

    def test_imagen_archivo_es_none_por_defecto(self):
        assert self._crear().imagen_archivo is None

    def test_acepta_imagen_archivo_explicito(self):
        assert self._crear(imagen_archivo="5_abc123.jpg").imagen_archivo == "5_abc123.jpg"


class TestItemVenta:
    def test_item_valido_se_crea_sin_error(self):
        item = ItemVenta(producto_id=1, cantidad=3)
        assert item.cantidad == 3

    @pytest.mark.parametrize("cantidad_invalida", [0, -1, -100])
    def test_rechaza_cantidad_no_positiva(self, cantidad_invalida):
        with pytest.raises(DatosInvalidosError):
            ItemVenta(producto_id=1, cantidad=cantidad_invalida)


class TestCalcularHashContenido:
    """Fase 5A: hash canónico usado para detectar si una clave de
    idempotencia se reutiliza con el mismo contenido o con uno distinto."""

    def test_es_determinista(self):
        items = [ItemVenta(1, 2), ItemVenta(3, 1)]
        assert calcular_hash_contenido(items, "EFECTIVO") == calcular_hash_contenido(items, "EFECTIVO")

    def test_mismo_contenido_en_distinto_orden_da_el_mismo_hash(self):
        hash_1 = calcular_hash_contenido([ItemVenta(1, 2), ItemVenta(3, 1)], "EFECTIVO")
        hash_2 = calcular_hash_contenido([ItemVenta(3, 1), ItemVenta(1, 2)], "EFECTIVO")
        assert hash_1 == hash_2

    def test_cantidad_distinta_da_hash_distinto(self):
        hash_1 = calcular_hash_contenido([ItemVenta(1, 2)], "EFECTIVO")
        hash_2 = calcular_hash_contenido([ItemVenta(1, 3)], "EFECTIVO")
        assert hash_1 != hash_2

    def test_producto_distinto_da_hash_distinto(self):
        hash_1 = calcular_hash_contenido([ItemVenta(1, 2)], "EFECTIVO")
        hash_2 = calcular_hash_contenido([ItemVenta(2, 2)], "EFECTIVO")
        assert hash_1 != hash_2

    def test_tipo_de_pago_distinto_da_hash_distinto(self):
        hash_1 = calcular_hash_contenido([ItemVenta(1, 2)], "EFECTIVO")
        hash_2 = calcular_hash_contenido([ItemVenta(1, 2)], "TARJETA")
        assert hash_1 != hash_2

    def test_items_repetidos_del_mismo_producto_se_agregan_antes_de_hashear(self):
        """Dos líneas del mismo producto que sumadas dan la misma cantidad
        total que una sola línea deben producir el mismo hash -- el hash
        representa el carrito ya agregado por producto, igual que lo agrega
        `servicio_ventas.registrar_venta`."""
        hash_dividido = calcular_hash_contenido([ItemVenta(1, 2), ItemVenta(1, 1)], "EFECTIVO")
        hash_unico = calcular_hash_contenido([ItemVenta(1, 3)], "EFECTIVO")
        assert hash_dividido == hash_unico

    def test_devuelve_un_hash_hexadecimal_de_64_caracteres(self):
        resultado = calcular_hash_contenido([ItemVenta(1, 1)], "EFECTIVO")
        assert len(resultado) == 64
        assert all(c in "0123456789abcdef" for c in resultado)


class TestMovimientoCaja:
    @pytest.mark.parametrize("tipo", ["APERTURA", "CIERRE", "INGRESO", "EGRESO"])
    def test_acepta_todos_los_tipos_validos(self, tipo):
        descripcion = "justificación del movimiento" if tipo in ("INGRESO", "EGRESO") else None
        movimiento = MovimientoCaja(tipo=tipo, monto_centavos=1000, descripcion=descripcion)
        assert movimiento.tipo == tipo

    def test_rechaza_tipo_invalido(self):
        with pytest.raises(DatosInvalidosError):
            MovimientoCaja(tipo="TRANSFERENCIA", monto_centavos=1000)

    def test_rechaza_monto_negativo(self):
        with pytest.raises(DatosInvalidosError):
            MovimientoCaja(tipo="APERTURA", monto_centavos=-1)

    @pytest.mark.parametrize("tipo", ["INGRESO", "EGRESO"])
    def test_ingreso_y_egreso_requieren_descripcion(self, tipo):
        with pytest.raises(DatosInvalidosError):
            MovimientoCaja(tipo=tipo, monto_centavos=1000, descripcion=None)

    @pytest.mark.parametrize("tipo", ["INGRESO", "EGRESO"])
    def test_ingreso_y_egreso_rechazan_descripcion_solo_espacios(self, tipo):
        with pytest.raises(DatosInvalidosError):
            MovimientoCaja(tipo=tipo, monto_centavos=1000, descripcion="   ")

    @pytest.mark.parametrize("tipo", ["APERTURA", "CIERRE"])
    def test_apertura_y_cierre_no_requieren_descripcion(self, tipo):
        movimiento = MovimientoCaja(tipo=tipo, monto_centavos=1000, descripcion=None)
        assert movimiento.descripcion is None
