"""V1.7-B: anulación segura de compras -- stock todo-o-nada, restauración de costo solo si se demuestra,
compras históricas, doble anulación, auditoría, reportes y reposición."""

import pytest

from db.conexion import obtener_conexion
from db.repositorios import compras as repositorio_compras
from db.repositorios import producto_proveedor as repositorio_producto_proveedor
from domain.compra import (
    CAUSA_COMPRA_HISTORICA,
    CAUSA_COMPRA_POSTERIOR,
    CAUSA_COSTO_ACTUAL_DIVERGENTE,
    CAUSA_EVENTO_COSTO_POSTERIOR,
    CAUSA_SIN_CAMBIO_COSTO,
    DecisionCosto,
    ItemCompra,
    LineaParaAnular,
    decidir_costo_de_linea,
)
from excepciones import (
    CompraNoEncontradaError,
    CompraYaAnuladaError,
    DatosInvalidosError,
    InventarioDesactualizadoError,
    StockInsuficienteError,
)
from services import servicio_compras, servicio_inventario, servicio_proveedores, servicio_reportes, servicio_stock
from services.servicio_compras import _resumen_de_anulacion
from tests.utilidades_clientes import consultar, escribir


@pytest.fixture
def e(base_datos_temporal):
    from db.repositorios import usuarios as repositorio_usuarios
    from domain.usuario import Usuario

    owner = repositorio_usuarios.crear_usuario(
        Usuario(nombre_usuario="duenio", nombre_completo="Dueño", password_hash="hash-de-prueba", rol="OWNER")
    )
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 300, stock_actual=10)
    otro = servicio_stock.registrar_producto("7790000000002", "Gaseosa", 400, 700, stock_actual=5)
    return type("E", (), {"ruta": base_datos_temporal, "owner": owner, "prov": proveedor, "p": producto, "q": otro})


def _comprar(e, *lineas):
    """`lineas`: (producto, cantidad, costo). Devuelve la compra."""
    return servicio_compras.registrar_compra(e.prov.id, e.owner.id, [ItemCompra(p.id, c, k) for p, c, k in lineas])


def _anular(e, compra, motivo="ERROR_CARGA", observaciones=None):
    return servicio_compras.anular_compra(compra.id, motivo, observaciones, e.owner.id)


def _stock(e, producto):
    return consultar(e.ruta, "SELECT stock_actual FROM productos WHERE id = ?", (producto.id,))[0][0]


def _costo(e, producto):
    return consultar(e.ruta, "SELECT precio_costo_centavos FROM productos WHERE id = ?", (producto.id,))[0][0]


def _eventos(e, producto=None):
    donde, args = ("WHERE producto_id = ?", (producto.id,)) if producto else ("", ())
    return [
        tuple(f)
        for f in consultar(
            e.ruta,
            "SELECT id, campo, precio_anterior_centavos, precio_nuevo_centavos, origen FROM historial_precios "
            f"{donde} ORDER BY id",
            args,
        )
    ]


def _auditoria(e, accion):
    return [f["resumen"] for f in consultar(e.ruta, "SELECT resumen FROM auditoria WHERE accion = ?", (accion,))]


def _foto(e):
    """Estado completo relevante: si una operación falla, debe quedar idéntico."""
    tablas = {
        "productos": "SELECT id, stock_actual, precio_costo_centavos, version_stock FROM productos ORDER BY id",
        "compras": "SELECT id, estado, motivo_anulacion, fecha_anulacion FROM compras ORDER BY id",
        "historial": "SELECT * FROM historial_precios ORDER BY id",
        "auditoria": "SELECT id, accion FROM auditoria ORDER BY id",
    }
    return {nombre: [tuple(f) for f in consultar(e.ruta, sql)] for nombre, sql in tablas.items()}


class TestRegistroConTrazabilidad:
    def test_la_linea_guarda_el_evento_de_costo_si_el_costo_cambio(self, e):
        compra = _comprar(e, (e.p, 5, 150))

        evento = _eventos(e, e.p)[-1]
        fila = consultar(e.ruta, "SELECT historial_precio_id FROM detalle_compra WHERE compra_id = ?", (compra.id,))[0]
        assert fila[0] == evento[0] and evento[1:] == ("COSTO", 100, 150, "COMPRA")
        assert consultar(e.ruta, "SELECT costo_trazable FROM compras WHERE id = ?", (compra.id,))[0][0] == 1

    def test_la_linea_queda_sin_evento_si_el_costo_no_cambio(self, e):
        compra = _comprar(e, (e.p, 5, 100))  # mismo costo vigente

        fila = consultar(e.ruta, "SELECT historial_precio_id FROM detalle_compra WHERE compra_id = ?", (compra.id,))[0]
        assert fila[0] is None and _eventos(e, e.p) == []
        assert consultar(e.ruta, "SELECT costo_trazable FROM compras WHERE id = ?", (compra.id,))[0][0] == 1


class TestAnulacionFeliz:
    def test_revierte_stock_restaura_costo_registra_evento_y_audita(self, e):
        compra = _comprar(e, (e.p, 5, 150), (e.q, 2, 400))

        anulada = _anular(e, compra, "DEVOLUCION_A_PROVEEDOR")

        assert anulada.estado == "ANULADA"
        assert (_stock(e, e.p), _stock(e, e.q)) == (10, 5)
        assert (_costo(e, e.p), _costo(e, e.q)) == (100, 400)
        assert _eventos(e, e.p)[-1][1:] == ("COSTO", 150, 100, "ANULACION_COMPRA")
        fila = consultar(
            e.ruta,
            "SELECT estado, motivo_anulacion, anulada_por_usuario_id, fecha_anulacion FROM compras WHERE id = ?",
            (compra.id,),
        )[0]
        assert tuple(fila)[:3] == ("ANULADA", "DEVOLUCION_A_PROVEEDOR", e.owner.id) and fila[3]
        resumen = _auditoria(e, "COMPRA_ANULADA")
        assert len(resumen) == 1 and f"p{e.p.id}:-5 RESTAURADO" in resumen[0]
        assert f"p{e.q.id}:-2 CONSERVADO:{CAUSA_SIN_CAMBIO_COSTO}" in resumen[0]
        assert "DEVOLUCION_A_PROVEEDOR" in resumen[0]

    def test_la_compra_anulada_sigue_existiendo_con_su_detalle(self, e):
        compra = _comprar(e, (e.p, 5, 150))
        _anular(e, compra)

        assert servicio_compras.obtener_resumen_por_id(compra.id).estado == "ANULADA"
        assert len(servicio_compras.listar_detalle_con_producto(compra.id)) == 1

    def test_producto_inactivo_se_revierte_igual(self, e):
        compra = _comprar(e, (e.p, 5, 150))
        escribir(e.ruta, "UPDATE productos SET activo = 0 WHERE id = ?", (e.p.id,))

        _anular(e, compra)

        assert _stock(e, e.p) == 10 and _costo(e, e.p) == 100

    def test_no_elimina_el_vinculo_producto_proveedor(self, e):
        compra = _comprar(e, (e.p, 5, 150))
        _anular(e, compra)

        assert consultar(e.ruta, "SELECT COUNT(*) FROM producto_proveedor WHERE producto_id = ?", (e.p.id,))[0][0] == 1


class TestStockInsuficiente:
    def test_una_linea_sin_stock_aborta_todo_sin_cambiar_nada(self, e):
        compra = _comprar(e, (e.p, 5, 150), (e.q, 2, 500))
        escribir(e.ruta, "UPDATE productos SET stock_actual = 1 WHERE id = ?", (e.q.id,))  # se vendió: 1 < 2
        antes = _foto(e)

        with pytest.raises(StockInsuficienteError):
            _anular(e, compra)

        assert _foto(e) == antes  # ni stock (incluida la línea que sí alcanzaba), ni costo, ni estado, ni auditoría

    def test_alcanza_justo(self, e):
        compra = _comprar(e, (e.p, 5, 150))
        escribir(e.ruta, "UPDATE productos SET stock_actual = 5 WHERE id = ?", (e.p.id,))

        _anular(e, compra)

        assert _stock(e, e.p) == 0


class TestCompraHistorica:
    def _compra_historica(self, e, producto, cantidad, costo):
        """Compra registrada sin trazabilidad (como las de V1.6): el costo se cambió sin vincular evento."""
        with obtener_conexion() as con:
            compra = repositorio_compras.registrar_compra_con_detalle(
                con, e.prov.id, e.owner.id, None, cantidad * costo, [(ItemCompra(producto.id, cantidad, costo), cantidad * costo)]
            )
            con.execute("UPDATE productos SET stock_actual = stock_actual + ? WHERE id = ?", (cantidad, producto.id))
            from db.repositorios import productos as repositorio_productos

            repositorio_productos.actualizar_costo_con_evento_en_conexion(con, producto.id, costo, e.owner.id, "COMPRA")
        return compra

    def test_revierte_stock_pero_nunca_restaura_costo(self, e):
        compra = self._compra_historica(e, e.p, 5, 150)
        eventos_antes = _eventos(e, e.p)

        _anular(e, compra)

        assert _stock(e, e.p) == 10
        assert _costo(e, e.p) == 150  # costo conservado: no se infiere el evento por fecha, valor ni origen
        assert _eventos(e, e.p) == eventos_antes  # ni siquiera se registra un evento de restauración
        assert f"CONSERVADO:{CAUSA_COMPRA_HISTORICA}" in _auditoria(e, "COMPRA_ANULADA")[0]

    def test_una_compra_historica_queda_sin_marca_de_trazabilidad(self, e):
        compra = self._compra_historica(e, e.p, 5, 150)

        assert consultar(e.ruta, "SELECT costo_trazable FROM compras WHERE id = ?", (compra.id,))[0][0] == 0


class TestCondicionesDeRestauracion:
    def test_compra_posterior_con_distinto_costo_no_restaura(self, e):
        primera = _comprar(e, (e.p, 5, 150))
        _comprar(e, (e.p, 3, 200))

        _anular(e, primera)

        assert _costo(e, e.p) == 200 and _stock(e, e.p) == 13  # 10 + 5 + 3 - 5
        assert f"CONSERVADO:{CAUSA_COMPRA_POSTERIOR}" in _auditoria(e, "COMPRA_ANULADA")[0]

    def test_compra_posterior_con_el_mismo_costo_impide_restaurar_aunque_no_creo_evento(self, e):
        primera = _comprar(e, (e.p, 5, 150))
        segunda = _comprar(e, (e.p, 3, 150))  # mismo costo: no genera evento
        assert consultar(e.ruta, "SELECT historial_precio_id FROM detalle_compra WHERE compra_id = ?", (segunda.id,))[0][0] is None

        _anular(e, primera)

        assert _costo(e, e.p) == 150  # restaurar a 100 ignoraría que la segunda compra pagó 150
        assert f"CONSERVADO:{CAUSA_COMPRA_POSTERIOR}" in _auditoria(e, "COMPRA_ANULADA")[0]
        assert [ev[4] for ev in _eventos(e, e.p)] == ["COMPRA"]  # no hubo evento de restauración

    def test_la_cronologia_es_la_de_la_cabecera_no_la_del_detalle(self, e):
        primera = _comprar(e, (e.p, 5, 150))
        segunda = _comprar(e, (e.p, 3, 150))
        # Un detalle con id menor no cambia el orden: la segunda compra sigue siendo posterior por su cabecera.
        escribir(e.ruta, "UPDATE detalle_compra SET id = id + 1000 WHERE compra_id = ?", (primera.id,))

        _anular(e, primera)

        assert _costo(e, e.p) == 150
        assert segunda.id > primera.id

    def test_una_compra_posterior_anulada_no_impide_restaurar(self, e):
        primera = _comprar(e, (e.p, 5, 150))
        segunda = _comprar(e, (e.p, 3, 150))
        _anular(e, segunda)  # sin evento propio: el costo sigue en 150

        _anular(e, primera)

        assert _costo(e, e.p) == 100 and _stock(e, e.p) == 10

    def test_cambio_manual_posterior_no_restaura(self, e):
        compra = _comprar(e, (e.p, 5, 150))
        servicio_stock.actualizar_producto(e.p.id, "7790000000001", "Alfajor", 200, 300, 0, usuario_id=e.owner.id)

        _anular(e, compra)

        assert _costo(e, e.p) == 200
        assert f"CONSERVADO:{CAUSA_EVENTO_COSTO_POSTERIOR}" in _auditoria(e, "COMPRA_ANULADA")[0]

    def test_un_cambio_manual_que_vuelve_al_mismo_valor_tampoco_restaura(self, e):
        """150 -> 200 -> 150 a mano: el costo vigente coincide con el de la compra (C4), pero alguien lo fijó
        después (C2): restaurar a 100 pisaría una decisión posterior."""
        compra = _comprar(e, (e.p, 5, 150))
        for costo in (200, 150):
            servicio_stock.actualizar_producto(e.p.id, "7790000000001", "Alfajor", costo, 300, 0, usuario_id=e.owner.id)

        _anular(e, compra)

        assert _costo(e, e.p) == 150
        assert f"CONSERVADO:{CAUSA_EVENTO_COSTO_POSTERIOR}" in _auditoria(e, "COMPRA_ANULADA")[0]

    def test_mismo_timestamp_se_decide_por_id_del_evento(self, e):
        compra = _comprar(e, (e.p, 5, 150))
        servicio_stock.actualizar_producto(e.p.id, "7790000000001", "Alfajor", 200, 300, 0, usuario_id=e.owner.id)
        escribir(e.ruta, "UPDATE historial_precios SET fecha = '2026-01-01 00:00:00'")  # empate total de fechas

        _anular(e, compra)

        assert _costo(e, e.p) == 200

    def test_costo_vigente_divergente_no_restaura(self, e):
        compra = _comprar(e, (e.p, 5, 150))
        escribir(e.ruta, "UPDATE productos SET precio_costo_centavos = 175 WHERE id = ?", (e.p.id,))  # sin evento

        _anular(e, compra)

        assert _costo(e, e.p) == 175
        assert f"CONSERVADO:{CAUSA_COSTO_ACTUAL_DIVERGENTE}" in _auditoria(e, "COMPRA_ANULADA")[0]


class TestDecisionPura:
    LINEA = LineaParaAnular(1, 5, 10, 100, 150)

    def _decidir(self, **cambios):
        datos = dict(
            costo_trazable=True, linea=self.LINEA, ultimo_evento_costo_id=10,
            hay_compra_activa_posterior=False, costo_vigente_centavos=150,
        )
        return decidir_costo_de_linea(**{**datos, **cambios})

    def test_restaura_solo_con_todas_las_condiciones(self):
        assert self._decidir() == DecisionCosto(1, 100, None)

    @pytest.mark.parametrize(
        "cambios, causa",
        [
            ({"costo_trazable": False}, CAUSA_COMPRA_HISTORICA),
            ({"linea": LineaParaAnular(1, 5, None, None, None)}, CAUSA_SIN_CAMBIO_COSTO),
            ({"hay_compra_activa_posterior": True}, CAUSA_COMPRA_POSTERIOR),
            ({"ultimo_evento_costo_id": 11}, CAUSA_EVENTO_COSTO_POSTERIOR),
            ({"costo_vigente_centavos": 151}, CAUSA_COSTO_ACTUAL_DIVERGENTE),
        ],
    )
    def test_cada_condicion_incumplida_conserva_con_su_causa(self, cambios, causa):
        assert self._decidir(**cambios) == DecisionCosto(1, None, causa)


class TestValidacionYDobleAnulacion:
    def test_compra_inexistente(self, e):
        with pytest.raises(CompraNoEncontradaError):
            servicio_compras.anular_compra(999, "ERROR_CARGA", None, e.owner.id)

    @pytest.mark.parametrize("motivo, observaciones", [("NO_EXISTE", None), ("OTRO", None), ("OTRO", "   ")])
    def test_motivo_invalido_u_otro_sin_observaciones_no_cambia_nada(self, e, motivo, observaciones):
        compra = _comprar(e, (e.p, 5, 150))
        antes = _foto(e)

        with pytest.raises(DatosInvalidosError):
            servicio_compras.anular_compra(compra.id, motivo, observaciones, e.owner.id)

        assert _foto(e) == antes

    def test_otro_con_observacion_se_acepta(self, e):
        compra = _comprar(e, (e.p, 5, 150))

        assert _anular(e, compra, "OTRO", "  Se cargó al proveedor equivocado  ").estado == "ANULADA"
        assert consultar(e.ruta, "SELECT observaciones_anulacion FROM compras WHERE id = ?", (compra.id,))[0][0] == (
            "Se cargó al proveedor equivocado"
        )

    def test_segunda_anulacion_es_error_controlado_sin_segundo_efecto(self, e):
        compra = _comprar(e, (e.p, 5, 150))
        _anular(e, compra)
        despues_de_la_primera = _foto(e)

        with pytest.raises(CompraYaAnuladaError):
            _anular(e, compra)

        assert _foto(e) == despues_de_la_primera
        assert _stock(e, e.p) == 10 and len(_auditoria(e, "COMPRA_ANULADA")) == 1

    def test_la_barrera_final_es_el_update_condicionado_por_estado(self, e, monkeypatch):
        """Simula una carrera: la lectura previa aún ve la compra ACTIVA cuando otra transacción ya la anuló."""
        compra = _comprar(e, (e.p, 5, 150))
        _anular(e, compra)
        despues = _foto(e)
        original = repositorio_compras.obtener_por_id_en_conexion

        def lectura_vieja(conexion, compra_id):
            obtenida = original(conexion, compra_id)
            obtenida.estado = "ACTIVA"
            return obtenida

        monkeypatch.setattr(repositorio_compras, "obtener_por_id_en_conexion", lectura_vieja)

        with pytest.raises(CompraYaAnuladaError):
            _anular(e, compra)

        assert _foto(e) == despues  # la reversión de stock del segundo intento se deshizo entera


class TestAuditoriaCompacta:
    def test_el_resumen_nunca_excede_el_limite_de_auditoria(self):
        decisiones = [DecisionCosto(i, None, CAUSA_EVENTO_COSTO_POSTERIOR) for i in range(1, 60)]

        resumen = _resumen_de_anulacion(7, "ERROR_CARGA", {i: 3 for i in range(1, 60)}, decisiones)

        assert len(resumen) <= 300 and "Compra #7 anulada" in resumen and "59 productos" in resumen


class TestInventarioAbierto:
    def test_anular_cambia_la_version_de_stock_y_el_conteo_previo_queda_desactualizado(self, e):
        compra = _comprar(e, (e.p, 5, 150))
        inventario = servicio_inventario.crear_inventario(e.owner.id, [e.p.id])
        servicio_inventario.registrar_conteo(inventario.id, e.p.id, 15, e.owner.id)

        _anular(e, compra)

        with pytest.raises(InventarioDesactualizadoError):
            servicio_inventario.confirmar_inventario(inventario.id, e.owner.id)
        assert _stock(e, e.p) == 10  # el inventario no aplicó nada


class TestReportesYReposicion:
    def test_el_reporte_por_proveedor_solo_suma_compras_activas(self, e):
        _comprar(e, (e.p, 5, 100))  # 500
        anulada = _comprar(e, (e.q, 2, 400))  # 800
        _anular(e, anulada)

        reporte = servicio_reportes.generar_reporte_compras("2000-01-01", "2100-01-01")

        assert (reporte.total_compras, reporte.total_unidades, reporte.total_centavos) == (1, 5, 500)

    def test_la_reposicion_no_toma_el_costo_de_una_compra_anulada(self, e):
        _comprar(e, (e.p, 5, 120))
        anulada = _comprar(e, (e.p, 5, 300))
        principales = repositorio_producto_proveedor.listar_principales_con_costo()
        assert principales[e.p.id].ultimo_costo_centavos == 300

        _anular(e, anulada)

        assert repositorio_producto_proveedor.listar_principales_con_costo()[e.p.id].ultimo_costo_centavos == 120
