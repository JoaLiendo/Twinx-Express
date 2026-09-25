"""V1.6-C: reportes operativos de solo lectura -- caja por sesión, compras por proveedor, deuda y
cobranzas de cuenta corriente. Todo se agrega en SQL; ningún reporte modifica datos."""

import re
from datetime import date, timedelta

import pytest

from db.repositorios import caja as repositorio_caja
from db.repositorios import clientes as repositorio_clientes
from db.repositorios import compras as repositorio_compras
from db.repositorios import ventas as repositorio_ventas
from domain.compra import ItemCompra
from domain.venta import ItemVenta
from services import (
    servicio_caja,
    servicio_clientes,
    servicio_compras,
    servicio_cuenta_corriente,
    servicio_proveedores,
    servicio_reportes,
    servicio_stock,
    servicio_ventas,
)
from tests.utilidades_clientes import consultar, escribir, forzar_cliente_inactivo, usuario_logueado

from ._asgi_cliente import solicitud

pytestmark = pytest.mark.sin_caja_abierta  # cada test abre y cierra sus propias sesiones

CC = "CUENTA_CORRIENTE"


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies = usuario_logueado("OWNER", "duenio")
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=1000)
    ana = servicio_clientes.crear_cliente("Ana", owner.id)
    beto = servicio_clientes.crear_cliente("Beto", owner.id)
    return type(
        "E", (), {"ruta": base_datos_temporal, "owner": owner, "co": cookies, "p": producto, "ana": ana, "beto": beto}
    )


def _vender(e, tipo_pago, unidades=1, cliente=None):
    return servicio_ventas.registrar_venta(
        [ItemVenta(e.p.id, unidades)], tipo_pago, usuario_id=e.owner.id, cliente_id=cliente.id if cliente else None
    )


def _dos_sesiones(e):
    """Sesión 1 (cerrada): fondo 1000, ingreso 500, egreso 200, venta EFECTIVO 400 y TARJETA 200, cierre con
    un faltante de 100. Sesión 2 (abierta): fondo 2000, ingreso 50, venta CC 600 (Ana), cobro de 250 y EFECTIVO 200."""
    servicio_caja.abrir_caja(1000, "s1", e.owner.id)
    servicio_caja.registrar_ingreso(500, "ingreso s1", e.owner.id)
    servicio_caja.registrar_egreso(200, "egreso s1", e.owner.id)
    _vender(e, "EFECTIVO", 2)
    _vender(e, "TARJETA", 1)
    cierre = servicio_caja.cerrar_caja(1600 - 100, "cierre s1", e.owner.id)  # esperado: 1000+500-200+400 = 1700
    servicio_caja.abrir_caja(2000, "s2", e.owner.id)
    servicio_caja.registrar_ingreso(50, "ingreso s2", e.owner.id)
    _vender(e, CC, 3, e.ana)
    servicio_cuenta_corriente.registrar_cobro(e.ana.id, 250, e.owner.id)
    _vender(e, "EFECTIVO", 1)
    return cierre


class TestCajaPorSesion:
    def test_cada_sesion_muestra_solo_lo_suyo(self, e):
        _dos_sesiones(e)

        sesiones = {s.sesion_id: s for s in servicio_reportes.generar_reporte_caja().sesiones}
        s1, s2 = sesiones[1], sesiones[2]

        assert (s1.estado, s2.estado) == ("CERRADA", "ABIERTA")
        assert (s1.apertura_centavos, s1.ingresos_centavos, s1.cobranzas_centavos, s1.egresos_centavos) == (1000, 500, 0, 200)
        assert (s2.apertura_centavos, s2.ingresos_centavos, s2.cobranzas_centavos, s2.egresos_centavos) == (2000, 50, 250, 0)
        assert {v.tipo_pago: (v.cantidad_ventas, v.total_centavos) for v in s1.ventas_por_medio} == {
            "EFECTIVO": (1, 400),
            "TARJETA": (1, 200),
        }
        assert {v.tipo_pago: v.total_centavos for v in s2.ventas_por_medio} == {CC: 600, "EFECTIVO": 200}
        assert (s1.total_ventas_centavos, s2.total_ventas_centavos) == (600, 800)

    def test_efectivo_esperado_declarado_y_diferencia_coinciden_con_el_cierre_y_el_arqueo(self, e):
        cierre = _dos_sesiones(e)

        sesiones = {s.sesion_id: s for s in servicio_reportes.generar_reporte_caja().sesiones}
        s1, s2 = sesiones[1], sesiones[2]

        assert (s1.efectivo_esperado_centavos, s1.contado_centavos, s1.diferencia_centavos) == (1700, 1500, -200)
        assert s1.diferencia_centavos == cierre.diferencia_centavos
        assert s1.contado_centavos - s1.diferencia_centavos == s1.efectivo_esperado_centavos
        assert (s2.contado_centavos, s2.diferencia_centavos) == (None, None)
        assert s2.efectivo_esperado_centavos == servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos

    def test_la_pagina_muestra_las_sesiones_con_sus_montos_y_estados(self, e):
        _dos_sesiones(e)

        html = solicitud("GET", "/reportes/caja", cookies=e.co).texto

        assert html.index('data-sesion="2"') < html.index('data-sesion="1"')  # la más reciente primero
        bloque_1 = html[html.index('data-sesion="1"') :]
        assert re.search(r'data-campo="esperado"[^>]*>\$17,00<', bloque_1)
        assert re.search(r'data-campo="declarado"[^>]*>\$15,00<', bloque_1)
        assert "-$2,00" in bloque_1 and "Cerrada" in bloque_1 and "Abierta" in html

    def test_el_periodo_filtra_por_fecha_de_apertura(self, e):
        # La apertura de una sesión es inmutable (trigger): se filtra contra la fecha real de hoy.
        _dos_sesiones(e)
        hoy = date.today()
        ayer, manana = hoy - timedelta(days=1), hoy + timedelta(days=1)

        de_hoy = servicio_reportes.generar_reporte_caja(hoy.isoformat(), hoy.isoformat())
        solo_ayer = servicio_reportes.generar_reporte_caja(ayer.isoformat(), ayer.isoformat())
        desde_manana = servicio_reportes.generar_reporte_caja(manana.isoformat(), (manana + timedelta(days=5)).isoformat())

        assert sorted(s.sesion_id for s in de_hoy.sesiones) == [1, 2]
        assert solo_ayer.sesiones == [] and desde_manana.sesiones == []

    def test_no_modifica_ninguna_caja(self, e):
        _dos_sesiones(e)
        antes = (
            consultar(e.ruta, "SELECT COUNT(*) FROM caja_movimientos")[0][0],
            [tuple(f) for f in consultar(e.ruta, "SELECT * FROM sesiones_caja ORDER BY id")],
        )

        solicitud("GET", "/reportes/caja", cookies=e.co)

        assert antes == (
            consultar(e.ruta, "SELECT COUNT(*) FROM caja_movimientos")[0][0],
            [tuple(f) for f in consultar(e.ruta, "SELECT * FROM sesiones_caja ORDER BY id")],
        )


class TestComprasPorProveedor:
    @pytest.fixture
    def compras(self, e):
        a = servicio_proveedores.crear_proveedor("Norte")
        b = servicio_proveedores.crear_proveedor("Sur")
        servicio_proveedores.crear_proveedor("Sin compras")
        c1 = servicio_compras.registrar_compra(a.id, e.owner.id, [ItemCompra(e.p.id, 3, 100)])
        c2 = servicio_compras.registrar_compra(a.id, e.owner.id, [ItemCompra(e.p.id, 2, 150)])
        c3 = servicio_compras.registrar_compra(b.id, e.owner.id, [ItemCompra(e.p.id, 10, 50)])
        for compra, fecha in ((c1, "2026-01-10 10:00:00"), (c2, "2026-02-10 10:00:00"), (c3, "2026-01-31 23:59:59")):
            escribir(e.ruta, "UPDATE compras SET fecha = ? WHERE id = ?", (fecha, compra.id))
        return type("C", (), {"a": a, "b": b})

    def test_agrupa_por_proveedor_con_compras_unidades_y_total(self, e, compras):
        reporte = servicio_reportes.generar_reporte_compras("2026-01-01", "2026-12-31")

        filas = {f.proveedor_nombre: (f.cantidad_compras, f.unidades, f.total_centavos) for f in reporte.filas}
        assert filas == {"Norte": (2, 5, 600), "Sur": (1, 10, 500)}  # 3x100+2x150 y 10x50; sin compras no aparece
        assert (reporte.total_compras, reporte.total_unidades, reporte.total_centavos) == (3, 15, 1100)
        assert [f.proveedor_nombre for f in reporte.filas] == ["Norte", "Sur"]  # mayor total primero

    def test_el_rango_es_por_dia_completo_e_incluye_el_ultimo_segundo_del_hasta(self, e, compras):
        enero = servicio_reportes.generar_reporte_compras("2026-01-01", "2026-01-31")

        assert {f.proveedor_nombre: f.cantidad_compras for f in enero.filas} == {"Norte": 1, "Sur": 1}
        assert servicio_reportes.generar_reporte_compras("2026-02-01", "2026-02-28").filas[0].total_centavos == 300
        assert servicio_reportes.generar_reporte_compras("2025-01-01", "2025-12-31").filas == []

    def test_un_proveedor_no_incluye_al_otro(self, e, compras):
        solo_b = servicio_reportes.generar_reporte_compras("2026-01-01", "2026-12-31", compras.b.id)

        assert [f.proveedor_nombre for f in solo_b.filas] == ["Sur"]
        assert (solo_b.total_compras, solo_b.total_unidades, solo_b.total_centavos) == (1, 10, 500)

    def test_la_pagina_muestra_el_resumen_y_conserva_el_proveedor_elegido(self, e, compras):
        html = solicitud(
            "GET", f"/reportes/compras?fecha_desde=2026-01-01&fecha_hasta=2026-12-31&proveedor_id={compras.a.id}", cookies=e.co
        ).texto

        assert 'data-proveedor="%d"' % compras.a.id in html and 'data-proveedor="%d"' % compras.b.id not in html
        assert re.search(r'id="total-compras-monto"[^>]*>\$6,00<', html)
        assert f'value="{compras.a.id}" selected' in html


class TestDeudaCuentaCorriente:
    def test_solo_saldos_positivos_con_estado_y_coincide_con_el_saldo_real(self, e):
        servicio_caja.abrir_caja(0, None, e.owner.id)
        _vender(e, CC, 2, e.ana)  # 400
        _vender(e, CC, 1, e.beto)  # 200
        _vender(e, CC, 1, e.beto)  # 400
        servicio_cuenta_corriente.registrar_cobro(e.beto.id, 400, e.owner.id)  # Beto queda en 0
        gama = servicio_clientes.crear_cliente("Gama sin movimientos", e.owner.id)
        forzar_cliente_inactivo(e.ruta, e.ana.id)

        reporte = servicio_reportes.generar_reporte_deuda()

        assert [(f.cliente_nombre, f.activo, f.saldo_centavos) for f in reporte.filas] == [("Ana", False, 400)]
        assert all(f.saldo_centavos == servicio_clientes.obtener_saldo(f.cliente_id) for f in reporte.filas)
        assert gama.id not in {f.cliente_id for f in reporte.filas}

    def test_coincide_con_la_deuda_total_y_un_cobro_la_reduce(self, e):
        servicio_caja.abrir_caja(0, None, e.owner.id)
        _vender(e, CC, 2, e.ana)
        _vender(e, CC, 1, e.beto)

        antes = servicio_reportes.generar_reporte_deuda()
        total_antes = servicio_clientes.obtener_deuda_total().total_centavos
        servicio_cuenta_corriente.registrar_cobro(e.ana.id, 150, e.owner.id)
        despues = servicio_reportes.generar_reporte_deuda()

        assert antes.total_centavos == total_antes == 600
        assert despues.total_centavos == servicio_clientes.obtener_deuda_total().total_centavos == 450
        assert {f.cliente_nombre: f.saldo_centavos for f in despues.filas} == {"Ana": 250, "Beto": 200}

    def test_la_pagina_lista_la_deuda(self, e):
        servicio_caja.abrir_caja(0, None, e.owner.id)
        _vender(e, CC, 2, e.ana)

        html = solicitud("GET", "/reportes/cuenta-corriente", cookies=e.co).texto

        assert 'data-cliente="%d"' % e.ana.id in html and re.search(r'id="total-deuda"[^>]*>\$4,00<', html)


class TestCobranzasCuentaCorriente:
    def test_solo_cobros_agrupados_por_cliente_sin_sumar_cargos(self, e):
        servicio_caja.abrir_caja(0, None, e.owner.id)
        _vender(e, CC, 5, e.ana)  # cargo de 1000
        _vender(e, CC, 1, e.beto)  # solo cargo: no debe aparecer
        servicio_cuenta_corriente.registrar_cobro(e.ana.id, 300, e.owner.id)
        servicio_cuenta_corriente.registrar_cobro(e.ana.id, 200, e.owner.id)

        reporte = servicio_reportes.generar_reporte_cobranzas()

        assert [(f.cliente_nombre, f.cantidad_cobros, f.total_centavos) for f in reporte.filas] == [("Ana", 2, 500)]
        assert (reporte.total_cobros, reporte.total_centavos) == (2, 500)

    def test_el_rango_filtra_por_fecha_del_cobro(self, e):
        # El libro de cuenta corriente es inmutable (trigger): se filtra contra la fecha real de hoy.
        servicio_caja.abrir_caja(0, None, e.owner.id)
        _vender(e, CC, 5, e.ana)
        servicio_cuenta_corriente.registrar_cobro(e.ana.id, 300, e.owner.id)
        servicio_cuenta_corriente.registrar_cobro(e.ana.id, 200, e.owner.id)
        hoy = date.today()
        ayer, manana = hoy - timedelta(days=1), hoy + timedelta(days=1)

        de_hoy = servicio_reportes.generar_reporte_cobranzas(hoy.isoformat(), hoy.isoformat())
        solo_ayer = servicio_reportes.generar_reporte_cobranzas(ayer.isoformat(), ayer.isoformat())
        desde_manana = servicio_reportes.generar_reporte_cobranzas(manana.isoformat(), (manana + timedelta(days=3)).isoformat())

        assert [(f.cantidad_cobros, f.total_centavos) for f in de_hoy.filas] == [(2, 500)]
        assert solo_ayer.filas == [] and desde_manana.filas == []

    def test_la_pagina_muestra_las_cobranzas_del_periodo(self, e):
        servicio_caja.abrir_caja(0, None, e.owner.id)
        _vender(e, CC, 5, e.ana)
        servicio_cuenta_corriente.registrar_cobro(e.ana.id, 300, e.owner.id)

        html = solicitud("GET", "/reportes/cuenta-corriente", cookies=e.co).texto

        assert re.search(r'id="total-cobranzas-monto"[^>]*>\$3,00<', html)


class TestEscalaYSoloLectura:
    def test_los_reportes_agregan_en_sql_sin_cargar_listados_completos(self, e, monkeypatch):
        _dos_sesiones(e)
        proveedor = servicio_proveedores.crear_proveedor("Norte")
        for _ in range(25):
            servicio_compras.registrar_compra(proveedor.id, e.owner.id, [ItemCompra(e.p.id, 2, 100)])

        def _prohibido(*_argumentos, **_kwargs):
            raise AssertionError("el reporte no debe cargar listados completos para sumar")

        for modulo, nombres in (
            (repositorio_caja, ("listar_movimientos", "listar_movimientos_de_sesion", "listar_movimientos_recientes")),
            (repositorio_ventas, ("listar_ventas_de_sesion", "listar_resumen", "listar_en_rango")),
            (repositorio_clientes, ("listar_con_saldo", "listar_movimientos_cuenta", "listar")),
            (repositorio_compras, ("listar_resumen", "listar_lineas_en_conexion")),
        ):
            for nombre in nombres:
                monkeypatch.setattr(modulo, nombre, _prohibido)

        assert len(servicio_reportes.generar_reporte_caja().sesiones) == 2
        compras = servicio_reportes.generar_reporte_compras()
        assert (compras.total_compras, compras.total_unidades, compras.total_centavos) == (25, 50, 5000)
        assert servicio_reportes.generar_reporte_deuda().total_centavos == 350  # 600 a cuenta - 250 cobrados
        assert servicio_reportes.generar_reporte_cobranzas().total_centavos == 250

    @pytest.mark.parametrize("ruta", ["/reportes/caja", "/reportes/compras", "/reportes/cuenta-corriente"])
    def test_visitar_un_reporte_no_escribe_nada(self, e, ruta):
        _dos_sesiones(e)
        tablas = ("caja_movimientos", "sesiones_caja", "ventas", "movimientos_cuenta", "compras", "auditoria")
        antes = [consultar(e.ruta, f"SELECT COUNT(*) FROM {t}")[0][0] for t in tablas]

        assert solicitud("GET", ruta, cookies=e.co).status == 200

        assert antes == [consultar(e.ruta, f"SELECT COUNT(*) FROM {t}")[0][0] for t in tablas]


class TestPermisosYEntradas:
    RUTAS = ("/reportes/caja", "/reportes/compras", "/reportes/cuenta-corriente")

    @pytest.mark.parametrize("ruta", RUTAS)
    def test_solo_el_owner_accede_por_url_directa(self, e, ruta):
        _, cajera = usuario_logueado("CASHIER", "cajera")

        assert solicitud("GET", ruta, cookies=cajera).status == 403
        assert solicitud("GET", ruta).status == 303

    @pytest.mark.parametrize("ruta", RUTAS)
    @pytest.mark.parametrize(
        "consulta",
        [
            "fecha_desde=abc&fecha_hasta=2026-01-01",
            "fecha_desde=2026-13-45&fecha_hasta=2026-01-01",
            "fecha_desde=2026-02-01&fecha_hasta=2026-01-01",
            "fecha_desde=2026-01-01&fecha_hasta=9999-12-31",
            "fecha_desde=" + "9" * 5000 + "&fecha_hasta=2026-01-01",
        ],
    )
    def test_fechas_invalidas_son_un_error_controlado(self, e, ruta, consulta):
        assert solicitud("GET", f"{ruta}?{consulta}", cookies=e.co).status == 303

    @pytest.mark.parametrize("proveedor", ["abc", "999999", "9" * 30, "9" * 5000])
    def test_proveedor_invalido_en_compras_es_controlado(self, e, proveedor):
        respuesta = solicitud("GET", f"/reportes/compras?proveedor_id={proveedor}", cookies=e.co)

        assert respuesta.status in (200, 303)  # un id inexistente da un reporte vacío; el resto un error claro
        assert respuesta.status == (303 if proveedor in ("abc", "9" * 30, "9" * 5000) else 200)

    @pytest.mark.parametrize("ruta", RUTAS)
    def test_campos_vacios_del_formulario_usan_el_periodo_por_defecto(self, e, ruta):
        assert solicitud("GET", f"{ruta}?fecha_desde=&fecha_hasta=", cookies=e.co).status == 200

    def test_la_navegacion_enlaza_los_cuatro_reportes(self, e):
        html = solicitud("GET", "/reportes", cookies=e.co).texto

        for destino in ("/reportes", "/reportes/caja", "/reportes/compras", "/reportes/cuenta-corriente"):
            assert f'href="{destino}"' in html
