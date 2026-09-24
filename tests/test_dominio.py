"""Pruebas de los invariantes de negocio del dominio (domain/).

No dependen de base de datos: validan que las entidades se
autovaliden al construirse, en memoria.
"""

import pytest

from domain.ajuste_stock import MOTIVOS_AJUSTE_VALIDOS, AjusteStock
from domain.caja import MovimientoCaja, clasificar_diferencia
from domain.producto import Producto
from domain.venta import MOTIVOS_ANULACION_VALIDOS, ItemVenta, calcular_hash_contenido, validar_motivo_anulacion
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

    def test_stock_cero_nunca_es_critico(self):
        assert not self._crear(stock_actual=0, stock_minimo=0).tiene_stock_critico
        assert not self._crear(stock_actual=0, stock_minimo=5).tiene_stock_critico

    def test_tiene_stock_critico_para_los_casos_de_borde_del_minimo(self):
        assert self._crear(stock_actual=3, stock_minimo=5).tiene_stock_critico
        assert self._crear(stock_actual=5, stock_minimo=5).tiene_stock_critico
        assert not self._crear(stock_actual=10, stock_minimo=5).tiene_stock_critico

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

    @pytest.mark.parametrize("diferencia", [500, -500, 0])
    def test_cierre_acepta_diferencia_positiva_negativa_o_cero(self, diferencia):
        movimiento = MovimientoCaja(tipo="CIERRE", monto_centavos=1000, diferencia_centavos=diferencia)
        assert movimiento.diferencia_centavos == diferencia

    def test_cierre_sin_diferencia_sigue_siendo_valido(self):
        """Compatibilidad con cierres históricos/tests existentes: el
        campo es opcional, por defecto `None`."""
        movimiento = MovimientoCaja(tipo="CIERRE", monto_centavos=1000)
        assert movimiento.diferencia_centavos is None

    @pytest.mark.parametrize("tipo", ["APERTURA", "INGRESO", "EGRESO"])
    def test_diferencia_en_movimiento_que_no_es_cierre_es_rechazada(self, tipo):
        descripcion = "justificación del movimiento" if tipo in ("INGRESO", "EGRESO") else None
        with pytest.raises(DatosInvalidosError):
            MovimientoCaja(tipo=tipo, monto_centavos=1000, descripcion=descripcion, diferencia_centavos=100)


class TestClasificarDiferencia:
    def test_positiva_es_sobrante(self):
        assert clasificar_diferencia(500) == "SOBRANTE"

    def test_negativa_es_faltante(self):
        assert clasificar_diferencia(-500) == "FALTANTE"

    def test_cero_es_cuadrada(self):
        assert clasificar_diferencia(0) == "CUADRADA"


class TestAjusteStock:
    def _ajuste(self, **overrides):
        base = dict(
            producto_id=1,
            usuario_id=1,
            motivo="MERMA",
            delta=-5,
            stock_anterior=10,
            stock_resultante=5,
        )
        base.update(overrides)
        return AjusteStock(**base)

    @pytest.mark.parametrize("motivo", sorted(MOTIVOS_AJUSTE_VALIDOS))
    def test_acepta_todos_los_motivos_validos(self, motivo):
        kwargs = {"observaciones": "justificación"} if motivo == "OTRO" else {}
        ajuste = self._ajuste(motivo=motivo, **kwargs)
        assert ajuste.motivo == motivo

    def test_rechaza_motivo_invalido(self):
        with pytest.raises(DatosInvalidosError):
            self._ajuste(motivo="DESCUENTO_PROMOCIONAL")

    def test_acepta_delta_positivo(self):
        ajuste = self._ajuste(motivo="RECUENTO", delta=3, stock_anterior=10, stock_resultante=13)
        assert ajuste.delta == 3

    def test_acepta_delta_negativo(self):
        ajuste = self._ajuste(delta=-3, stock_anterior=10, stock_resultante=7)
        assert ajuste.delta == -3

    def test_rechaza_delta_cero(self):
        with pytest.raises(DatosInvalidosError):
            self._ajuste(delta=0, stock_anterior=10, stock_resultante=10)

    def test_rechaza_inconsistencia_entre_stock_anterior_delta_y_resultante(self):
        with pytest.raises(DatosInvalidosError):
            self._ajuste(stock_anterior=10, delta=-5, stock_resultante=999)

    def test_rechaza_stock_resultante_negativo(self):
        with pytest.raises(DatosInvalidosError):
            self._ajuste(stock_anterior=3, delta=-5, stock_resultante=-2)

    def test_permite_dejar_stock_resultante_en_cero(self):
        """Distinto de `delta == 0`: acá el RESULTADO es 0, un caso
        legítimo (se perdió/vendió/ajustó todo el stock restante)."""
        ajuste = self._ajuste(stock_anterior=5, delta=-5, stock_resultante=0)
        assert ajuste.stock_resultante == 0

    def test_otro_sin_observaciones_es_rechazado(self):
        with pytest.raises(DatosInvalidosError):
            self._ajuste(motivo="OTRO", observaciones=None)

    def test_otro_con_observaciones_solo_espacios_es_rechazado(self):
        with pytest.raises(DatosInvalidosError):
            self._ajuste(motivo="OTRO", observaciones="   ")


class TestValidarMotivoAnulacion:
    """Anulación de ventas: mismo criterio que `TestAjusteStock` de
    arriba (motivo cerrado, `OTRO` exige observación) -- acá como
    función libre, no como `__post_init__` de un dataclass, porque
    anular no crea una entidad nueva (ver `domain.venta.Venta.estado`)."""

    @pytest.mark.parametrize("motivo", sorted(MOTIVOS_ANULACION_VALIDOS))
    def test_acepta_todos_los_motivos_validos(self, motivo):
        observaciones = "justificación" if motivo == "OTRO" else None
        validar_motivo_anulacion(motivo, observaciones)  # no debe lanzar

    def test_rechaza_motivo_invalido(self):
        with pytest.raises(DatosInvalidosError):
            validar_motivo_anulacion("PORQUE_SI", None)

    def test_otro_sin_observaciones_es_rechazado(self):
        with pytest.raises(DatosInvalidosError):
            validar_motivo_anulacion("OTRO", None)

    def test_otro_con_observaciones_solo_espacios_es_rechazado(self):
        with pytest.raises(DatosInvalidosError):
            validar_motivo_anulacion("OTRO", "   ")

    def test_otro_con_observaciones_es_aceptado(self):
        validar_motivo_anulacion("OTRO", "se equivocó de producto")  # no debe lanzar

    def test_motivo_distinto_de_otro_no_exige_observaciones(self):
        validar_motivo_anulacion("ERROR_CARGA", None)  # no debe lanzar
