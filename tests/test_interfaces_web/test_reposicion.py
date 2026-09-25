"""V1.2 Fase 6: reposición de stock (primera versión, solo consulta).

Regla definida: entra si `stock_minimo > 0 AND stock_actual < stock_minimo`
(el stock 0 entra; el stock igual al mínimo NO); cantidad sugerida =
`stock_minimo - stock_actual`; sin factor configurable; no crea compras ni
modifica stock."""

from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth, servicio_stock

from ._asgi_cliente import solicitud


def _cookies(nombre="duenio", rol="OWNER"):
    repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre,
            nombre_completo=nombre,
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol=rol,
        )
    )
    return {NOMBRE_COOKIE_SESION: servicio_auth.iniciar_sesion(nombre, "clave-correcta-123").token}


def _p(codigo, nombre, stock, minimo, costo=100):
    return servicio_stock.registrar_producto(codigo, nombre, costo, costo * 2, stock_actual=stock, stock_minimo=minimo)


class TestServicio:
    def test_entran_solo_los_que_estan_estrictamente_por_debajo_del_minimo_incluido_stock_cero(self, base_datos_temporal):
        _p("1", "Sin stock", 0, 5)
        _p("2", "Por debajo", 2, 5)
        _p("3", "Justo en el mínimo", 5, 5)
        _p("4", "Con stock de sobra", 20, 5)

        nombres = [s.nombre for s in servicio_stock.listar_reposicion()]

        assert nombres == ["Sin stock", "Por debajo"]  # más urgentes primero; igual al mínimo NO entra

    def test_stock_cero_entra_si_tiene_minimo(self, base_datos_temporal):
        _p("1", "Agotado", 0, 1)

        assert [s.nombre for s in servicio_stock.listar_reposicion()] == ["Agotado"]

    def test_stock_igual_al_minimo_no_entra(self, base_datos_temporal):
        _p("1", "Justo", 3, 3)

        assert servicio_stock.listar_reposicion() == []

    def test_minimo_cero_no_entra_ni_con_stock_cero(self, base_datos_temporal):
        _p("1", "Sin mínimo y sin stock", 0, 0)

        assert servicio_stock.listar_reposicion() == []

    def test_no_entran_los_inactivos(self, base_datos_temporal):
        inactivo = _p("1", "Inactivo", 0, 5)
        with obtener_conexion() as conexion:
            conexion.execute("UPDATE productos SET activo = 0 WHERE id = ?", (inactivo.id,))

        assert servicio_stock.listar_reposicion() == []

    def test_la_alerta_del_dashboard_no_cambia_y_sigue_excluyendo_el_stock_cero(self, base_datos_temporal):
        _p("1", "Sin stock", 0, 5)
        _p("2", "Por debajo", 2, 5)
        _p("3", "Justo", 5, 5)

        assert [p.nombre for p in servicio_stock.listar_stock_critico()] == ["Justo", "Por debajo"]

    def test_cantidad_sugerida_es_exactamente_minimo_menos_stock(self, base_datos_temporal):
        _p("1", "A", 2, 10)
        _p("2", "B", 0, 4)
        _p("3", "C", 9, 10)

        sugeridas = {s.nombre: s.cantidad_sugerida for s in servicio_stock.listar_reposicion()}

        assert sugeridas == {"A": 8, "B": 4, "C": 1}

    def test_la_cantidad_sugerida_nunca_es_cero_ni_negativa(self, base_datos_temporal):
        for i, (stock, minimo) in enumerate([(0, 1), (1, 2), (0, 50), (49, 50)]):
            _p(str(i), f"P{i}", stock, minimo)

        assert all(s.cantidad_sugerida >= 1 for s in servicio_stock.listar_reposicion())

    def test_no_hay_factor_ni_objetivo_configurable(self, base_datos_temporal):
        import inspect

        assert not inspect.signature(servicio_stock.listar_reposicion).parameters
        assert not hasattr(servicio_stock, "FACTOR_REPOSICION_MAXIMO")

    def test_el_costo_estimado_usa_el_costo_vigente(self, base_datos_temporal):
        _p("1", "A", 0, 4, costo=250)

        (s,) = servicio_stock.listar_reposicion()

        assert (s.cantidad_sugerida, s.costo_unitario_centavos, s.costo_estimado_centavos) == (4, 250, 1000)

    def test_es_solo_lectura(self, base_datos_temporal):
        producto = _p("1", "A", 0, 4)

        servicio_stock.listar_reposicion()

        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 0
        with obtener_conexion() as conexion:
            assert conexion.execute("SELECT COUNT(*) FROM compras").fetchone()[0] == 0
            assert conexion.execute("SELECT COUNT(*) FROM ajustes_stock").fetchone()[0] == 0


class TestPantalla:
    def test_solo_el_owner_accede(self, base_datos_temporal):
        cajera = _cookies("cajera", "CASHIER")

        assert solicitud("GET", "/reposicion", cookies=cajera).status == 403
        assert solicitud("GET", "/reposicion").status == 303

    def test_muestra_stock_minimo_cantidad_costo_y_total(self, base_datos_temporal):
        cookies = _cookies()
        _p("1", "Gaseosa", 1, 6, costo=500)
        _p("2", "Sin stock", 0, 2, costo=100)

        html = solicitud("GET", "/reposicion", cookies=cookies).texto

        assert "Gaseosa" in html and "Sin stock" in html
        assert 'value="5"' in html and 'value="2"' in html  # cantidades sugeridas editables
        assert "25,00" in html  # 5 × $5,00 de Gaseosa
        assert "27,00" in html  # total estimado: 25 + 2
        assert "2</strong> producto(s) para reponer, <strong>1</strong> sin stock" in html

    def test_el_producto_justo_en_el_minimo_no_aparece(self, base_datos_temporal):
        cookies = _cookies()
        _p("1", "Justo en el mínimo", 5, 5)

        html = solicitud("GET", "/reposicion", cookies=cookies).texto

        assert "No hay productos para reponer" in html and "Justo en el mínimo" not in html

    def test_no_ofrece_ningun_control_de_objetivo_ni_factor(self, base_datos_temporal):
        cookies = _cookies()
        _p("1", "A", 0, 4)

        html = solicitud("GET", "/reposicion", cookies=cookies).texto

        assert 'name="hasta"' not in html and "× stock mínimo" not in html

    def test_un_parametro_hasta_ajeno_no_rompe_la_pantalla_ni_cambia_la_regla(self, base_datos_temporal):
        cookies = _cookies()
        _p("1", "A", 2, 10)

        for consulta in ("?hasta=abc", "?hasta=99", "?hasta=2"):
            respuesta = solicitud("GET", "/reposicion" + consulta, cookies=cookies)
            assert respuesta.status == 200  # nunca un 422 JSON crudo
            assert 'value="8"' in respuesta.texto  # siempre mínimo - stock

    def test_el_menu_del_owner_incluye_reposicion_y_el_del_cashier_no(self, base_datos_temporal):
        owner, cajera = _cookies("duenio", "OWNER"), _cookies("cajera", "CASHIER")

        assert 'href="/reposicion"' in solicitud("GET", "/", cookies=owner).texto
        assert 'href="/reposicion"' not in solicitud("GET", "/", cookies=cajera).texto

    def test_no_crea_compras_ni_ordenes_solo_enlaza_al_formulario_existente(self, base_datos_temporal):
        cookies = _cookies()
        _p("1", "A", 0, 4)

        html = solicitud("GET", "/reposicion", cookies=cookies).texto

        assert 'href="/compras/nueva"' in html
        assert 'action="/compras' not in html and 'method="post"' not in html

    def test_visitar_la_pantalla_no_escribe_nada(self, base_datos_temporal):
        cookies = _cookies()
        producto = _p("1", "A", 0, 4)
        with obtener_conexion() as conexion:
            antes = conexion.execute("SELECT COUNT(*) FROM auditoria").fetchone()[0]

        solicitud("GET", "/reposicion", cookies=cookies)

        with obtener_conexion() as conexion:
            assert conexion.execute("SELECT COUNT(*) FROM auditoria").fetchone()[0] == antes
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 0
