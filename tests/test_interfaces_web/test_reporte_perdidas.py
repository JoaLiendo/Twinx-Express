"""V1.11-D: reporte de pérdidas de stock (`/reportes/perdidas` y su CSV).

Escenario único con todos los casos que el reporte debe distinguir: los cuatro motivos vigentes (con costo
histórico), dos legacy de pérdida sin costo histórico (MERMA, PERDIDA), un legacy que sumó stock y un OTRO
(ninguno de los dos es pérdida), el STOCK_INICIAL del alta, un RECUENTO negativo y una REVERSA.
"""

import contextlib
import csv
import io

import pytest

from db.conexion import obtener_conexion
from db.repositorios import ajustes_stock as repositorio_ajustes
from db.repositorios import productos as repositorio_productos
from domain.ajuste_stock import AjusteStock
from excepciones import DatosInvalidosError
from services import servicio_reportes, servicio_stock
from tests.utilidades_clientes import consultar, escribir, usuario_logueado

from ._asgi_cliente import solicitud

COSTO = 100  # centavos, costo vigente al momento de los ajustes nuevos


def _legacy(producto_id, usuario_id, motivo, delta, stock_anterior, observaciones=None):
    """Fila histórica (anterior a V1.11-A): sin snapshot de costo, insertada tal cual por el repositorio."""
    with obtener_conexion() as conexion:
        return repositorio_ajustes.registrar_ajuste_en_conexion(
            conexion,
            AjusteStock(
                producto_id=producto_id, usuario_id=usuario_id, motivo=motivo, delta=delta,
                stock_anterior=stock_anterior, stock_resultante=stock_anterior + delta, observaciones=observaciones,
            ),
        )


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies = usuario_logueado("OWNER", "duenio")
    _, cajera = usuario_logueado("CASHIER", "cajera")
    p = servicio_stock.registrar_producto("7790000000001", "Alfajor", COSTO, 300, stock_actual=100, usuario_id=owner.id)
    q = servicio_stock.registrar_producto("7790000000002", "Gaseosa", 400, 700, stock_actual=50, usuario_id=owner.id)

    rotura = servicio_stock.ajustar_stock(p.id, -2, "ROTURA", owner.id)
    servicio_stock.ajustar_stock(p.id, -3, "VENCIMIENTO", owner.id)
    servicio_stock.ajustar_stock(p.id, -1, "ROBO", owner.id, observaciones="=HYPERLINK(\"http://x\")")
    servicio_stock.ajustar_stock(q.id, -4, "FALLA_FABRICA", owner.id)  # costo 400
    servicio_stock.revertir_ajuste(rotura.id, "se cargó en el producto equivocado", owner.id)
    _legacy(p.id, owner.id, "MERMA", -5, 92)
    _legacy(p.id, owner.id, "PERDIDA", -1, 87)
    _legacy(p.id, owner.id, "MERMA", 2, 86)  # legacy que sumó: no es pérdida
    _legacy(p.id, owner.id, "OTRO", -1, 88, observaciones="no dice qué pasó")  # no es pérdida
    with obtener_conexion(inmediata=True) as conexion:  # RECUENTO negativo: inventario, no pérdida
        producto = repositorio_productos.obtener_por_id_en_conexion(conexion, p.id)
        servicio_stock.aplicar_ajuste_en_conexion(conexion, producto, -1, "RECUENTO", owner.id)
    return type("E", (), {"ruta": base_datos_temporal, "owner": owner, "co": cookies, "cajera": cajera,
                          "p": p, "q": q, "rotura": rotura})


def _por_motivo(reporte):
    return {fila.motivo: fila for fila in reporte.filas}


class TestQueCuentaComoPerdida:
    def test_cada_motivo_de_perdida_aparece_y_ningun_otro(self, e):
        reporte = servicio_reportes.generar_reporte_perdidas()

        assert sorted(f.motivo for f in reporte.filas) == [
            "FALLA_FABRICA", "MERMA", "PERDIDA", "ROBO", "ROTURA", "VENCIMIENTO",
        ]

    def test_cantidad_es_positiva_y_es_la_resta_de_stock(self, e):
        filas = _por_motivo(servicio_reportes.generar_reporte_perdidas())

        assert (filas["VENCIMIENTO"].cantidad, filas["MERMA"].cantidad, filas["FALLA_FABRICA"].cantidad) == (3, 5, 4)

    def test_la_reversa_neutraliza_su_original_y_no_aparece_ella_misma(self, e):
        reporte = servicio_reportes.generar_reporte_perdidas()
        rotura = _por_motivo(reporte)["ROTURA"]

        assert rotura.revertida and rotura.reversa_usuario_nombre_completo is not None
        assert reporte.total.filas_revertidas == 1
        assert all(f.ajuste_id != e.rotura.id or f.revertida for f in reporte.filas)


class TestSumas:
    def test_totales_solo_de_pérdidas_vigentes_y_con_costo_conocido(self, e):
        total = servicio_reportes.generar_reporte_perdidas().total

        # vigentes: VENCIMIENTO 3 + ROBO 1 (a 100) + FALLA_FABRICA 4 (a 400) + MERMA 5 + PERDIDA 1 (sin costo)
        assert total.filas_vigentes == 5
        assert total.cantidad == 14
        assert total.valor_conocido_centavos == 3 * 100 + 1 * 100 + 4 * 400
        assert (total.filas_costo_desconocido, total.cantidad_costo_desconocido) == (2, 6)

    def test_un_costo_historico_nulo_nunca_se_valoriza_ni_con_el_costo_actual(self, e):
        merma = _por_motivo(servicio_reportes.generar_reporte_perdidas())["MERMA"]

        assert merma.costo_unitario_centavos is None and merma.perdida_centavos is None
        assert servicio_stock.obtener_por_id(e.p.id).precio_costo_centavos == COSTO  # existía, y no se usó

    def test_la_valoracion_usa_el_costo_del_momento_del_ajuste(self, e):
        servicio_stock.actualizar_producto(e.p.id, e.p.codigo_barras, e.p.nombre, 999, 300, 0)

        vencimiento = _por_motivo(servicio_reportes.generar_reporte_perdidas())["VENCIMIENTO"]

        assert vencimiento.costo_unitario_centavos == COSTO and vencimiento.perdida_centavos == 3 * COSTO

    def test_resumen_por_motivo(self, e):
        por_motivo = {r.motivo: r for r in servicio_reportes.generar_reporte_perdidas().por_motivo}

        assert (por_motivo["ROTURA"].cantidad, por_motivo["ROTURA"].filas_revertidas) == (0, 1)
        assert por_motivo["FALLA_FABRICA"].valor_conocido_centavos == 1600
        assert (por_motivo["MERMA"].cantidad, por_motivo["MERMA"].filas_costo_desconocido) == (5, 1)
        assert por_motivo["MERMA"].valor_conocido_centavos == 0


class TestFiltros:
    def test_por_motivo(self, e):
        reporte = servicio_reportes.generar_reporte_perdidas(motivo="VENCIMIENTO")

        assert [f.motivo for f in reporte.filas] == ["VENCIMIENTO"] and reporte.total.cantidad == 3

    def test_por_producto(self, e):
        reporte = servicio_reportes.generar_reporte_perdidas(producto_id=e.q.id)

        assert [f.motivo for f in reporte.filas] == ["FALLA_FABRICA"]

    def test_por_fechas(self, e):
        escribir(e.ruta, "UPDATE ajustes_stock SET fecha = '2026-01-05 10:00:00' WHERE motivo = 'ROBO'")

        enero = servicio_reportes.generar_reporte_perdidas("2026-01-01", "2026-01-31")
        sin_enero = servicio_reportes.generar_reporte_perdidas()

        assert [f.motivo for f in enero.filas] == ["ROBO"]
        assert "ROBO" not in {f.motivo for f in sin_enero.filas}

    @pytest.mark.parametrize("motivo", ["RECUENTO", "STOCK_INICIAL", "REVERSA", "OTRO", "BASURA"])
    def test_un_motivo_que_no_es_perdida_es_rechazado(self, e, motivo):
        with pytest.raises(DatosInvalidosError):
            servicio_reportes.generar_reporte_perdidas(motivo=motivo)

    def test_las_opciones_de_producto_son_las_que_tienen_perdidas(self, e):
        servicio_stock.registrar_producto("7790000000003", "Sin pérdidas", 1, 2, stock_actual=1)

        assert servicio_reportes.listar_productos_con_perdidas() == [(e.p.id, "Alfajor"), (e.q.id, "Gaseosa")]


class TestPantalla:
    def test_solo_owner(self, e):
        assert solicitud("GET", "/reportes/perdidas", cookies=e.cajera).status == 403
        assert solicitud("GET", "/reportes/perdidas").status == 303
        assert solicitud("GET", "/reportes/perdidas/exportar", cookies=e.cajera).status == 403

    def test_muestra_filas_estado_y_costo_desconocido(self, e):
        html = solicitud("GET", "/reportes/perdidas", cookies=e.co).texto

        assert html.count('data-estado="REVERTIDA"') == 1 and html.count('data-estado="VIGENTE"') == 5
        assert "Desconocido" in html and "Falla de fábrica" in html and "Merma (histórico)" in html
        assert 'href="/reportes/perdidas"' in html  # navegación entre reportes

    def test_filtro_invalido_es_error_controlado(self, e):
        respuesta = solicitud("GET", "/reportes/perdidas?motivo=RECUENTO", cookies=e.co)

        assert respuesta.status == 303 and "tipo=error" in respuesta.header("location")

    def test_es_solo_lectura(self, e):
        tablas = ("productos", "ajustes_stock", "auditoria")
        antes = [consultar(e.ruta, f"SELECT COUNT(*) FROM {t}")[0][0] for t in tablas]

        solicitud("GET", "/reportes/perdidas", cookies=e.co)
        solicitud("GET", "/reportes/perdidas/exportar", cookies=e.co)

        assert antes == [consultar(e.ruta, f"SELECT COUNT(*) FROM {t}")[0][0] for t in tablas]


class TestCsv:
    def _filas(self, e, consulta=""):
        respuesta = solicitud("GET", f"/reportes/perdidas/exportar{consulta}", cookies=e.co)
        assert respuesta.status == 200
        assert respuesta.cuerpo.startswith(b"\xef\xbb\xbf")  # BOM
        return list(csv.DictReader(io.StringIO(respuesta.cuerpo.decode("utf-8-sig"), newline="")))

    def test_mismo_dataset_que_la_pantalla(self, e):
        filas = self._filas(e)

        assert len(filas) == len(servicio_reportes.generar_reporte_perdidas().filas) == 6
        merma = next(f for f in filas if f["motivo"] == "MERMA")
        assert (merma["costo_unitario"], merma["perdida_a_costo"], merma["costo_historico"]) == ("", "", "DESCONOCIDO")
        rotura = next(f for f in filas if f["motivo"] == "ROTURA")
        assert rotura["estado"] == "REVERTIDA" and rotura["revertido_por"]

    def test_respeta_los_filtros(self, e):
        filas = self._filas(e, f"?motivo=FALLA_FABRICA&producto_id={e.q.id}")

        assert [(f["motivo"], f["cantidad"], f["perdida_a_costo"]) for f in filas] == [("FALLA_FABRICA", "4", "16.00")]

    def test_neutraliza_formulas(self, e):
        robo = next(f for f in self._filas(e) if f["motivo"] == "ROBO")

        assert robo["observaciones"].startswith("'=")


class TestEscala:
    def test_cantidad_constante_de_consultas_sin_importar_cuantas_filas(self, e, monkeypatch):
        original = repositorio_ajustes.obtener_conexion
        sentencias: list[str] = []

        @contextlib.contextmanager
        def con_traza(**kwargs):
            with original(**kwargs) as conexion:
                conexion.set_trace_callback(sentencias.append)
                yield conexion

        monkeypatch.setattr(repositorio_ajustes, "obtener_conexion", con_traza)
        servicio_reportes.generar_reporte_perdidas()
        con_pocas = len(sentencias)
        for _ in range(20):
            servicio_stock.ajustar_stock(e.p.id, -1, "ROTURA", e.owner.id)
        sentencias.clear()

        reporte = servicio_reportes.generar_reporte_perdidas()

        assert len(reporte.filas) == 26 and len(sentencias) == con_pocas
