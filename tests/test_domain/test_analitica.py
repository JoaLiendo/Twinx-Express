"""Cálculos puros de la analítica (V1.17): agrupación por período, margen con tres bases de costo,
precios, deuda por antigüedad, dependencia de proveedores y reposición por rotación."""

from datetime import date

import pytest

from domain import analitica as a


def _linea(fecha="2026-03-02", producto_id=1, nombre="Alfajor", categoria_id=None, categoria=None, **kw):
    base = dict(
        unidades=2,
        facturado_centavos=400,
        unidades_con_costo_ref=2,
        costo_ref_centavos=200,
        facturado_con_costo_ref_centavos=400,
        unidades_costo_real=2,
        costo_real_centavos=220,
        unidades_comparables=2,
        costo_ref_comparable_centavos=200,
        costo_real_comparable_centavos=220,
    )
    base.update(kw)
    return a.LineaVentaDia(fecha, producto_id, nombre, categoria_id, categoria, **base)


class TestPeriodos:
    def test_claves_por_dia_semana_y_mes(self):
        assert a.clave_periodo("2026-03-02 10:00:00", "dia") == "2026-03-02"
        assert a.clave_periodo("2026-03-02", "mes") == "2026-03"
        assert a.clave_periodo("2026-01-01", "semana") == "2026-W01"
        assert a.clave_periodo("2027-01-01", "semana") == "2026-W53"

    def test_granularidad_desconocida(self):
        with pytest.raises(ValueError):
            a.clave_periodo("2026-03-02", "trimestre")

    def test_periodo_anterior_misma_longitud(self):
        # Marzo tiene 31 días: el período anterior son los 31 días que terminan el 28 de febrero.
        assert a.periodo_anterior("2026-03-01", "2026-03-31") == ("2026-01-29", "2026-02-28")
        assert a.periodo_anterior("2026-03-10", "2026-03-10") == ("2026-03-09", "2026-03-09")

    def test_mismo_periodo_anio_anterior_con_bisiesto(self):
        assert a.mismo_periodo_anio_anterior("2024-02-29", "2024-03-05") == ("2023-02-28", "2023-03-05")

    def test_fechas_extremas_son_un_error_de_datos_no_un_500(self):
        from excepciones import DatosInvalidosError

        with pytest.raises(DatosInvalidosError):
            a.periodo_anterior("0001-01-01", "0001-01-05")
        with pytest.raises(DatosInvalidosError):
            a.mismo_periodo_anio_anterior("0001-01-01", "0001-01-05")

    def test_agrupar_ventas_por_semana(self):
        filas = [
            a.VentasDia("2026-03-02", 2, 5, 1000),
            a.VentasDia("2026-03-03", 1, 1, 300),
            a.VentasDia("2026-03-09", 4, 8, 2000),
        ]
        semanas = a.agrupar_ventas(filas, "semana")
        assert [(s.clave, s.cantidad_ventas, s.unidades, s.total_centavos) for s in semanas] == [
            ("2026-W10", 3, 6, 1300),
            ("2026-W11", 4, 8, 2000),
        ]
        assert semanas[0].ticket_promedio_centavos == 433


class TestMargen:
    def test_total_distingue_referencia_real_y_desconocido(self):
        lineas = [
            _linea(),
            # Venta anterior a Reportes V2: sin costo de referencia ni partidas.
            _linea(
                producto_id=2, nombre="Viejo", unidades=3, facturado_centavos=900,
                unidades_con_costo_ref=0, costo_ref_centavos=0, facturado_con_costo_ref_centavos=0,
                unidades_costo_real=0, costo_real_centavos=0,
                unidades_comparables=0, costo_ref_comparable_centavos=0, costo_real_comparable_centavos=0,
            ),
        ]
        [total] = a.agrupar_margen(lineas, "mes")
        assert total.unidades == 5
        assert total.margen_ref_centavos == 200
        assert total.margen_ref_porcentual == 50
        assert total.unidades_sin_costo_ref == 3
        assert total.unidades_sin_costo_real == 3
        assert total.diferencia_real_vs_ref_centavos == 20
        assert total.diferencia_real_vs_ref_porcentual == 10

    def test_sin_costo_conocido_el_margen_es_none_no_cero(self):
        linea = _linea(
            unidades_con_costo_ref=0, costo_ref_centavos=0, facturado_con_costo_ref_centavos=0,
            unidades_comparables=0, costo_ref_comparable_centavos=0, costo_real_comparable_centavos=0,
        )
        [grupo] = a.agrupar_margen([linea], "producto")
        assert grupo.margen_ref_centavos is None
        assert grupo.margen_ref_porcentual is None
        assert grupo.diferencia_real_vs_ref_centavos is None

    def test_agrupa_por_categoria_con_sin_categoria_y_ordena_por_facturacion(self):
        lineas = [
            _linea(categoria_id=1, categoria="Golosinas", facturado_centavos=400),
            _linea(producto_id=2, nombre="Gaseosa", categoria_id=None, facturado_centavos=1000),
            _linea(producto_id=3, nombre="Chicle", categoria_id=1, categoria="Golosinas", facturado_centavos=100),
        ]
        grupos = a.agrupar_margen(lineas, "categoria")
        assert [(g.etiqueta, g.facturado_centavos) for g in grupos] == [("Sin categoría", 1000), ("Golosinas", 500)]

    def test_los_periodos_quedan_en_orden_cronologico(self):
        grupos = a.agrupar_margen([_linea(fecha="2026-03-09"), _linea(fecha="2026-03-02")], "dia")
        assert [g.clave for g in grupos] == ["2026-03-02", "2026-03-09"]


class TestEstacionalidad:
    def test_insuficiente_con_menos_de_doce_meses(self):
        estacionalidad = a.calcular_estacionalidad([("2026-01", 3, 300), ("2026-02", 1, 100)])
        assert not estacionalidad.suficiente
        assert [(m.mes, m.anios, m.promedio_centavos) for m in estacionalidad.por_mes] == [(1, 1, 300), (2, 1, 100)]

    def test_promedia_el_mismo_mes_entre_anios(self):
        meses = [(f"{anio}-{mes:02d}", 1, 100 * anio_i) for anio_i, anio in enumerate((2024, 2025), start=1) for mes in range(1, 13)]
        estacionalidad = a.calcular_estacionalidad(meses)
        assert estacionalidad.suficiente
        enero = estacionalidad.por_mes[0]
        assert (enero.mes, enero.anios, enero.promedio_centavos) == (1, 2, 150)

    def test_excluye_el_mes_en_curso_porque_esta_incompleto(self):
        meses = [("2025-11", 10, 1_000_000), ("2026-11", 2, 200_000)]
        [noviembre] = a.calcular_estacionalidad(meses, mes_en_curso="2026-11").por_mes
        assert (noviembre.anios, noviembre.promedio_centavos) == (1, 1_000_000)
        # Sin mes en curso declarado (tests o historia cerrada) se promedia todo.
        [noviembre] = a.calcular_estacionalidad(meses).por_mes
        assert (noviembre.anios, noviembre.promedio_centavos) == (2, 600_000)


class TestPrecios:
    def _evolucion(self, campo, inicial, final, producto_id=1, precio_venta=200):
        return a.EvolucionPrecioProducto(producto_id, "Alfajor", None, None, campo, 1, inicial, final, final, precio_venta)

    def test_variacion_y_relevancia(self):
        evolucion = self._evolucion("VENTA", 200, 250)
        assert evolucion.variacion_centavos == 50
        assert evolucion.variacion_porcentual == 25
        assert evolucion.es_relevante(10) and not evolucion.es_relevante(30)
        assert self._evolucion("VENTA", 0, 250).variacion_porcentual is None

    def test_costo_que_sube_sin_cambio_de_precio(self):
        evoluciones = [
            self._evolucion("COSTO", 100, 130, producto_id=1),
            self._evolucion("COSTO", 100, 130, producto_id=2),
            self._evolucion("VENTA", 200, 260, producto_id=2),
            self._evolucion("COSTO", 100, 90, producto_id=3),
        ]
        [erosionado] = a.costos_subieron_sin_precio(evoluciones)
        assert erosionado.producto_id == 1
        assert erosionado.variacion_costo_porcentual == 30
        # El precio informado es el de venta vigente, no el costo actual del producto.
        assert (erosionado.costo_final_centavos, erosionado.precio_venta_centavos) == (130, 200)


class TestDeudaClientes:
    def test_reparte_por_antiguedad_con_los_umbrales_del_credito(self):
        hoy = date(2026, 3, 31)
        obligaciones = [
            a.ObligacionPendiente(1, "Ana", True, 1000, "2026-03-20"),  # 11 días: NORMAL
            a.ObligacionPendiente(1, "Ana", True, 500, "2026-01-01"),  # 89 días: CRITICA
            a.ObligacionPendiente(2, "Beto", False, 300, "2026-02-10"),  # 49 días: DEMORADO
        ]
        analisis = a.analizar_deuda_clientes(obligaciones, {1: 1}, {1}, hoy)
        ana, beto = analisis.clientes
        assert (ana.saldo_centavos, ana.estado, ana.dias_antiguedad, ana.strikes, ana.credito_cerrado) == (
            1500, "CRITICA", 89, 1, True
        )
        assert ana.por_estado == {"NORMAL": 1000, "CRITICA": 500}
        assert (beto.estado, beto.activo, beto.strikes, beto.credito_cerrado) == ("DEMORADO", False, 0, False)
        assert analisis.total_centavos == 1800
        assert analisis.total_por_estado("CRITICA") == 500
        assert [c.cliente_id for c in analisis.clientes_en("DEMORADO")] == [2]

    def test_evolucion_de_saldo_acumula_el_libro(self):
        evolucion = a.evolucion_de_saldo([("2026-01", 1000, 200), ("2026-02", 0, 500)])
        assert [(m.mes, m.saldo_centavos) for m in evolucion] == [("2026-01", 800), ("2026-02", 300)]


class TestProveedores:
    def test_dependencia_por_categoria(self):
        compras = [
            a.CompraCategoriaProveedor(1, 10, "Bebidas", 900),
            a.CompraCategoriaProveedor(2, 10, "Bebidas", 100),
            a.CompraCategoriaProveedor(2, None, None, 50),
        ]
        dependencias = a.calcular_dependencias(compras)
        [bebidas_1] = dependencias[1]
        assert (bebidas_1.categoria_nombre, bebidas_1.porcentaje, bebidas_1.alta) == ("Bebidas", 90, True)
        assert [(d.categoria_nombre, d.porcentaje, d.alta) for d in dependencias[2]] == [
            ("Sin categoría", 100, True),
            ("Bebidas", 10, False),
        ]


class TestReposicion:
    def test_sugerencia_por_rotacion_redondea_hacia_arriba(self):
        # 10 u. en 30 días → 14 días necesitan 4,67 → 5; con 2 en stock faltan 3.
        assert a.sugerencia_por_rotacion(2, 10, 30, 14) == 3
        assert a.sugerencia_por_rotacion(50, 10, 30, 14) == 0
        assert a.sugerencia_por_rotacion(2, 0, 30, 14) is None

    def test_dias_de_cobertura(self):
        assert a.dias_de_cobertura(10, 30, 30) == 10
        assert a.dias_de_cobertura(10, 0, 30) is None
