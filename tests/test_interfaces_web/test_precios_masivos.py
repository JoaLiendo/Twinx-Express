"""V1.2 Fase 2: actualización masiva de precios -- servicio (todo o nada,
historial con lote, idempotencia) y rutas (solo OWNER, vista previa, doble
envío)."""

import sqlite3
import threading

import pytest

import db.conexion as modulo_conexion
from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from domain.precios_masivos import CriterioActualizacion
from domain.usuario import Usuario
from domain.venta import ItemVenta
from excepciones import ClaveIdempotenciaReutilizadaError, DatosInvalidosError, ProductoNoEncontradoError
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth, servicio_caja, servicio_categorias, servicio_precios, servicio_stock, servicio_ventas

from ._asgi_cliente import solicitud

MAS_10 = CriterioActualizacion("PORCENTAJE", "AUMENTAR", 1000)


def _usuario(nombre="duenio", rol="OWNER"):
    return repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre,
            nombre_completo=f"Nombre {nombre}",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol=rol,
        )
    )


def _cookies(nombre="duenio", rol="OWNER"):
    _usuario(nombre, rol)
    return {NOMBRE_COOKIE_SESION: servicio_auth.iniciar_sesion(nombre, "clave-correcta-123").token}


def _producto(codigo, nombre, venta, categoria_id=None, stock=10):
    return servicio_stock.registrar_producto(codigo, nombre, 50, venta, stock_actual=stock, categoria_id=categoria_id)


def _precio(producto_id):
    return servicio_stock.obtener_por_id(producto_id).precio_venta_centavos


def _contar(tabla, donde="1=1"):
    with obtener_conexion() as conexion:
        return conexion.execute(f"SELECT COUNT(*) AS n FROM {tabla} WHERE {donde}").fetchone()["n"]


def _esperados(*productos):
    return {p.id: p.precio_venta_centavos for p in productos}


@pytest.fixture
def tres_productos(base_datos_temporal):
    usuario = _usuario()
    return usuario, _producto("1", "A", 10000), _producto("2", "B", 20000), _producto("3", "C", 30000)


class TestAplicar:
    def test_actualiza_cada_precio_y_registra_historial_y_lote(self, tres_productos):
        usuario, a, b, c = tres_productos

        lote = servicio_precios.aplicar_actualizacion(usuario.id, MAS_10, "TODOS", _esperados(a, b, c))

        assert [_precio(p.id) for p in (a, b, c)] == [11000, 22000, 33000]
        assert lote.cantidad_productos == 3
        assert lote.usuario_id == usuario.id
        assert lote.criterio == MAS_10 and lote.alcance == "TODOS"
        with obtener_conexion() as conexion:
            filas = conexion.execute("SELECT * FROM historial_precios ORDER BY producto_id").fetchall()
        assert [(f["campo"], f["origen"], f["lote_id"], f["usuario_id"]) for f in filas] == [
            ("VENTA", "MASIVA", lote.id, usuario.id)
        ] * 3
        assert [(f["precio_anterior_centavos"], f["precio_nuevo_centavos"]) for f in filas] == [
            (10000, 11000),
            (20000, 22000),
            (30000, 33000),
        ]

    def test_solo_actualiza_los_productos_elegidos(self, tres_productos):
        usuario, a, b, c = tres_productos

        servicio_precios.aplicar_actualizacion(usuario.id, MAS_10, "TODOS", _esperados(a, c))

        assert [_precio(p.id) for p in (a, b, c)] == [11000, 20000, 33000]

    def test_sin_productos_elegidos_se_rechaza(self, tres_productos):
        usuario, *_ = tres_productos

        with pytest.raises(DatosInvalidosError):
            servicio_precios.aplicar_actualizacion(usuario.id, MAS_10, "TODOS", {})

    def test_todo_o_nada_un_producto_no_aplicable_revierte_todos(self, tres_productos):
        usuario, a, b, c = tres_productos
        criterio = CriterioActualizacion("MONTO", "DISMINUIR", 15000)  # C=300 ok, A=100 -> <= 0

        with pytest.raises(DatosInvalidosError):
            servicio_precios.aplicar_actualizacion(usuario.id, criterio, "TODOS", _esperados(c, b, a))

        assert [_precio(p.id) for p in (a, b, c)] == [10000, 20000, 30000]
        assert _contar("lotes_precios") == 0 and _contar("historial_precios") == 0

    def test_todo_o_nada_un_producto_inexistente_revierte_todos(self, tres_productos):
        usuario, a, b, _ = tres_productos
        esperados = {**_esperados(a, b), 9999: 100}

        with pytest.raises(ProductoNoEncontradoError):
            servicio_precios.aplicar_actualizacion(usuario.id, MAS_10, "TODOS", esperados)

        assert [_precio(p.id) for p in (a, b)] == [10000, 20000]
        assert _contar("lotes_precios") == 0

    def test_un_precio_cambiado_desde_la_vista_previa_se_rechaza_sin_tocar_nada(self, tres_productos):
        usuario, a, b, _ = tres_productos
        esperados = _esperados(a, b)
        servicio_stock.actualizar_producto(b.id, b.codigo_barras, b.nombre, 50, 25000, 0)  # alguien lo cambió

        with pytest.raises(DatosInvalidosError, match="cambió desde la vista previa"):
            servicio_precios.aplicar_actualizacion(usuario.id, MAS_10, "TODOS", esperados)

        assert _precio(a.id) == 10000 and _precio(b.id) == 25000

    def test_un_producto_dado_de_baja_no_se_actualiza(self, tres_productos):
        usuario, a, b, _ = tres_productos
        servicio_stock.eliminar_producto(b.id)

        with pytest.raises(ProductoNoEncontradoError):
            servicio_precios.aplicar_actualizacion(usuario.id, MAS_10, "TODOS", _esperados(a, b))

        assert _precio(a.id) == 10000

    def test_un_producto_sin_cambio_no_es_aplicable(self, tres_productos):
        usuario, a, *_ = tres_productos
        criterio = CriterioActualizacion("MONTO", "AUMENTAR", 10, "ENTERO")  # 10010 -> 10000: sin cambio

        with pytest.raises(DatosInvalidosError):
            servicio_precios.aplicar_actualizacion(usuario.id, criterio, "TODOS", _esperados(a))

        assert _contar("historial_precios") == 0

    def test_las_ventas_anteriores_conservan_su_precio(self, tres_productos):
        usuario, a, *_ = tres_productos
        servicio_caja.abrir_caja(0)
        venta = servicio_ventas.registrar_venta([ItemVenta(a.id, 1)], "EFECTIVO")

        servicio_precios.aplicar_actualizacion(usuario.id, MAS_10, "TODOS", _esperados(a))

        with obtener_conexion() as conexion:
            precio = conexion.execute(
                "SELECT precio_unitario_centavos FROM detalle_venta WHERE venta_id = ?", (venta.id,)
            ).fetchone()[0]
        assert precio == 10000 and _precio(a.id) == 11000


class TestVistaPrevia:
    def test_no_escribe_nada_y_calcula_por_producto(self, tres_productos):
        propuestas = servicio_precios.proponer_actualizacion(MAS_10, "TODOS")

        assert [(p.nombre, p.precio_actual_centavos, p.precio_nuevo_centavos, p.estado) for p in propuestas] == [
            ("A", 10000, 11000, "OK"),
            ("B", 20000, 22000, "OK"),
            ("C", 30000, 33000, "OK"),
        ]
        assert _contar("lotes_precios") == 0 and _contar("historial_precios") == 0

    def test_excluye_productos_sin_precio_e_inactivos(self, tres_productos):
        _producto("4", "Sin precio", 0)
        inactivo = _producto("5", "Inactivo", 500)
        servicio_stock.eliminar_producto(inactivo.id)  # sin ventas: se borra; alcanza para que no figure

        nombres = [p.nombre for p in servicio_precios.proponer_actualizacion(MAS_10, "TODOS")]

        assert nombres == ["A", "B", "C"]

    def test_alcance_por_categoria_y_sin_categoria(self, base_datos_temporal):
        bebidas = servicio_categorias.crear_categoria("Bebidas")
        _producto("1", "Cola", 10000, categoria_id=bebidas.id)
        _producto("2", "Galleta", 20000)

        por_categoria = servicio_precios.proponer_actualizacion(MAS_10, f"CATEGORIA:{bebidas.id}")
        sin_categoria = servicio_precios.proponer_actualizacion(MAS_10, "SIN_CATEGORIA")

        assert [p.nombre for p in por_categoria] == ["Cola"]
        assert [p.nombre for p in sin_categoria] == ["Galleta"]

    def test_alcance_invalido(self, base_datos_temporal):
        with pytest.raises(DatosInvalidosError):
            servicio_precios.proponer_actualizacion(MAS_10, "CATEGORIA:abc")
        with pytest.raises(DatosInvalidosError):
            servicio_precios.proponer_actualizacion(MAS_10, "OTRO")

    def test_marca_como_invalido_lo_que_quedaria_en_cero(self, tres_productos):
        criterio = CriterioActualizacion("MONTO", "DISMINUIR", 10000)

        estados = {p.nombre: p.estado for p in servicio_precios.proponer_actualizacion(criterio, "TODOS")}

        assert estados == {"A": "INVALIDO", "B": "OK", "C": "OK"}


class TestIdempotencia:
    def test_reenviar_la_misma_clave_aplica_una_sola_vez(self, tres_productos):
        usuario, a, b, _ = tres_productos

        primero = servicio_precios.aplicar_actualizacion(usuario.id, MAS_10, "TODOS", _esperados(a, b), "k1")
        segundo = servicio_precios.aplicar_actualizacion(usuario.id, MAS_10, "TODOS", _esperados(a, b), "k1")

        assert segundo.id == primero.id
        assert _contar("lotes_precios") == 1 and _contar("historial_precios") == 2
        assert [_precio(a.id), _precio(b.id)] == [11000, 22000]  # +10 % una sola vez, no 12100

    def test_la_misma_clave_con_otro_criterio_o_seleccion_se_rechaza(self, tres_productos):
        usuario, a, b, _ = tres_productos
        servicio_precios.aplicar_actualizacion(usuario.id, MAS_10, "TODOS", _esperados(a), "k1")

        with pytest.raises(ClaveIdempotenciaReutilizadaError):
            servicio_precios.aplicar_actualizacion(
                usuario.id, CriterioActualizacion("PORCENTAJE", "AUMENTAR", 2000), "TODOS", _esperados(a), "k1"
            )
        with pytest.raises(ClaveIdempotenciaReutilizadaError):
            servicio_precios.aplicar_actualizacion(usuario.id, MAS_10, "TODOS", _esperados(a, b), "k1")

        assert _contar("lotes_precios") == 1

    def test_un_pedido_rechazado_no_consume_la_clave(self, tres_productos):
        usuario, a, *_ = tres_productos
        with pytest.raises(ProductoNoEncontradoError):
            servicio_precios.aplicar_actualizacion(usuario.id, MAS_10, "TODOS", {9999: 1}, "k1")

        lote = servicio_precios.aplicar_actualizacion(usuario.id, MAS_10, "TODOS", _esperados(a), "k1")

        assert lote.cantidad_productos == 1

    def test_dos_confirmaciones_simultaneas_con_la_misma_clave_aplican_una_sola(self, tres_productos):
        usuario, a, b, _ = tres_productos
        barrera = threading.Barrier(2)
        resultados, errores = [], []

        def confirmar():
            barrera.wait()
            try:
                resultados.append(
                    servicio_precios.aplicar_actualizacion(usuario.id, MAS_10, "TODOS", _esperados(a, b), "k1")
                )
            except Exception as error:  # noqa: BLE001 -- se inspecciona abajo
                errores.append(error)

        hilos = [threading.Thread(target=confirmar) for _ in range(2)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join()

        assert errores == []
        assert resultados[0].id == resultados[1].id
        assert _contar("lotes_precios") == 1 and _precio(a.id) == 11000


class TestRutas:
    def _formulario(self, productos, **extra):
        base = {
            "tipo": "PORCENTAJE",
            "direccion": "AUMENTAR",
            "valor": "1000",
            "redondeo": "NINGUNO",
            "alcance": "TODOS",
            "seleccion": [f"{p.id}:{p.precio_venta_centavos}" for p in productos],
            "clave_idempotencia": "r" * 32,
        }
        return {**base, **extra}

    def test_solo_el_owner_accede(self, tres_productos):
        cajera = _cookies("cajera", "CASHIER")
        a = tres_productos[1]

        for metodo, ruta, form in (
            ("GET", "/precios", None),
            ("GET", "/precios/vista-previa?tipo=PORCENTAJE&direccion=AUMENTAR&valor=10", None),
            ("POST", "/precios/aplicar", self._formulario([a])),
        ):
            assert solicitud(metodo, ruta, cookies=cajera, formulario=form).status == 403
            assert solicitud(metodo, ruta, formulario=form).status == 303  # sin sesión -> login

        assert _precio(a.id) == 10000

    def test_la_pantalla_lista_las_categorias_y_las_ultimas_actualizaciones(self, tres_productos):
        servicio_categorias.crear_categoria("Bebidas")
        cookies = _cookies("otro", "OWNER")

        pantalla = solicitud("GET", "/precios", cookies=cookies)

        assert pantalla.status == 200
        assert "Categoría: Bebidas" in pantalla.texto
        assert "Todavía no hay actualizaciones masivas" in pantalla.texto

    def test_vista_previa_muestra_actual_y_nuevo_con_clave_de_idempotencia(self, tres_productos):
        cookies = _cookies("otro", "OWNER")

        pantalla = solicitud(
            "GET", "/precios/vista-previa?tipo=PORCENTAJE&direccion=AUMENTAR&valor=10&redondeo=NINGUNO&alcance=TODOS",
            cookies=cookies,
        )

        assert pantalla.status == 200
        assert "100,00" in pantalla.texto and "110,00" in pantalla.texto
        assert 'name="clave_idempotencia"' in pantalla.texto
        assert "3</strong> de 3" in pantalla.texto
        assert _precio(tres_productos[1].id) == 10000  # la vista previa no escribe

    def test_vista_previa_con_valor_invalido_redirige_con_error(self, tres_productos):
        cookies = _cookies("otro", "OWNER")

        respuesta = solicitud("GET", "/precios/vista-previa?tipo=PORCENTAJE&direccion=AUMENTAR&valor=abc", cookies=cookies)

        assert respuesta.status == 303 and "tipo=error" in respuesta.header("location")

    def test_aplicar_actualiza_y_registra_el_usuario_autenticado(self, tres_productos):
        cookies = _cookies("otro", "OWNER")
        _, a, b, _ = tres_productos

        respuesta = solicitud("POST", "/precios/aplicar", cookies=cookies, formulario=self._formulario([a, b]))

        assert respuesta.status == 303 and "tipo=success" in respuesta.header("location")
        assert [_precio(a.id), _precio(b.id)] == [11000, 22000]
        with obtener_conexion() as conexion:
            nombre = conexion.execute(
                "SELECT u.nombre_completo FROM lotes_precios l JOIN usuarios u ON u.id = l.usuario_id"
            ).fetchone()[0]
        assert nombre == "Nombre otro"

    def test_doble_post_con_la_misma_clave_no_aplica_dos_veces(self, tres_productos):
        cookies = _cookies("otro", "OWNER")
        _, a, *_ = tres_productos
        formulario = self._formulario([a])

        r1 = solicitud("POST", "/precios/aplicar", cookies=cookies, formulario=formulario)
        r2 = solicitud("POST", "/precios/aplicar", cookies=cookies, formulario=formulario)

        assert "tipo=success" in r1.header("location") and "tipo=success" in r2.header("location")
        assert _precio(a.id) == 11000 and _contar("lotes_precios") == 1

    def test_seleccion_malformada_se_rechaza_sin_tocar_nada(self, tres_productos):
        cookies = _cookies("otro", "OWNER")
        _, a, *_ = tres_productos

        respuesta = solicitud(
            "POST", "/precios/aplicar", cookies=cookies, formulario=self._formulario([], seleccion=["basura"])
        )

        assert "tipo=error" in respuesta.header("location")
        assert _precio(a.id) == 10000

    def test_el_servidor_recalcula_el_precio_nuevo_no_lo_recibe_del_cliente(self, tres_productos):
        cookies = _cookies("otro", "OWNER")
        _, a, *_ = tres_productos

        solicitud(
            "POST",
            "/precios/aplicar",
            cookies=cookies,
            formulario=self._formulario([a], precio_nuevo="1", precio_nuevo_centavos="1"),
        )

        assert _precio(a.id) == 11000

    def test_el_menu_del_owner_incluye_precios(self, base_datos_temporal):
        cookies = _cookies("otro", "OWNER")

        assert 'href="/precios"' in solicitud("GET", "/", cookies=cookies).texto


class TestMigracion016:
    def test_estructura_y_unicidad_de_la_clave(self, base_datos_temporal):
        usuario = _usuario()
        with obtener_conexion() as conexion:
            columnas = {f["name"] for f in conexion.execute("PRAGMA table_info(historial_precios)").fetchall()}
        assert "lote_id" in columnas

        insertar = (
            "INSERT INTO lotes_precios (usuario_id, tipo, direccion, valor, redondeo, alcance, cantidad_productos,"
            " clave_idempotencia) VALUES (?, 'MONTO', 'AUMENTAR', 100, 'NINGUNO', 'TODOS', 1, 'dup')"
        )
        with sqlite3.connect(base_datos_temporal) as conexion:
            conexion.execute(insertar, (usuario.id,))
        with pytest.raises(sqlite3.IntegrityError):
            with sqlite3.connect(base_datos_temporal) as conexion:
                conexion.execute(insertar, (usuario.id,))

    def test_una_base_con_historial_previo_lo_conserva_al_migrar(self, tmp_path, monkeypatch):
        ruta = tmp_path / "test_pre_016.db"
        monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
        previas = [r for r in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")) if r.name < "016_lotes_precios.sql"]
        with modulo_conexion.obtener_conexion() as conexion:
            conexion.execute(modulo_conexion._TABLA_MIGRACIONES)
            for r in previas:
                conexion.executescript(r.read_text(encoding="utf-8"))
                conexion.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (r.name,))
            conexion.execute(
                "INSERT INTO productos (codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos) VALUES ('1','A',1,2)"
            )
            conexion.execute(
                "INSERT INTO historial_precios (producto_id, campo, precio_anterior_centavos, precio_nuevo_centavos, origen)"
                " VALUES (1, 'VENTA', 1, 2, 'EDICION')"
            )

        modulo_conexion.inicializar_base_datos()
        modulo_conexion.inicializar_base_datos()

        with modulo_conexion.obtener_conexion() as conexion:
            fila = conexion.execute("SELECT precio_nuevo_centavos, lote_id FROM historial_precios").fetchone()
            veces = conexion.execute(
                "SELECT COUNT(*) FROM schema_migraciones WHERE nombre_archivo = '016_lotes_precios.sql'"
            ).fetchone()[0]
        assert (fila["precio_nuevo_centavos"], fila["lote_id"]) == (2, None)
        assert veces == 1


class TestValidacionEnServidor:
    """La confirmación NO confía en lo que validó la vista previa: una petición
    manipulada recibe un error controlado (redirección con mensaje), nunca un 422
    crudo, un 500 ni un OverflowError, y no cambia nada."""

    def _post(self, cookies, formulario):
        return solicitud("POST", "/precios/aplicar", cookies=cookies, formulario=formulario)

    def _base(self, producto, **extra):
        return {
            "tipo": "PORCENTAJE",
            "direccion": "AUMENTAR",
            "valor": "1000",
            "redondeo": "NINGUNO",
            "alcance": "TODOS",
            "seleccion": [f"{producto.id}:{producto.precio_venta_centavos}"],
            "clave_idempotencia": "s" * 32,
            **extra,
        }

    @pytest.mark.parametrize(
        "campo, valor",
        [
            ("valor", str(10**12)),  # 10.000.000.000 % (tope: 1000 %)
            ("valor", str(10**30)),  # no cabe en un entero de SQLite
            ("valor", "100001"),  # 1000,01 %
            ("valor", "abc"),
            ("valor", "1e5"),
            ("valor", "<script>alert(1)</script>"),
            ("valor", "-5"),
            ("valor", "0"),
            ("valor", "9" * 5000),
            ("tipo", "OTRO"),
            ("tipo", "<b>x</b>"),
            ("direccion", "SUBIR"),
            ("redondeo", "TRUNCAR"),
            ("alcance", "TODOS<script>"),
            ("alcance", "CATEGORIA:abc"),
            ("alcance", "CATEGORIA:" + "9" * 30),
            ("alcance", "CATEGORIA:0"),
            ("alcance", "x" * 400),
            ("clave_idempotencia", "k" * 500),
        ],
    )
    def test_una_peticion_manipulada_recibe_un_error_controlado_y_no_cambia_nada(self, tres_productos, campo, valor):
        cookies = _cookies("otro", "OWNER")
        _, a, *_ = tres_productos

        respuesta = self._post(cookies, self._base(a, **{campo: valor}))

        assert respuesta.status == 303 and "tipo=error" in respuesta.header("location")
        assert _precio(a.id) == 10000
        assert _contar("lotes_precios") == 0 and _contar("historial_precios") == 0 and _contar("auditoria") == 0

    @pytest.mark.parametrize(
        "seleccion",
        [
            [f"{10**30}:10000"],  # id fuera de rango
            ["1:" + str(10**30)],  # precio fuera de rango
            ["1:-5"],
            ["0:100"],
            ["1"],
            ["a:b"],
            ["1:2:3"],
            ["<script>:1"],
        ],
    )
    def test_una_seleccion_manipulada_es_un_error_controlado(self, tres_productos, seleccion):
        cookies = _cookies("otro", "OWNER")

        respuesta = self._post(cookies, {**self._base(tres_productos[1]), "seleccion": seleccion})

        assert respuesta.status == 303 and "tipo=error" in respuesta.header("location")
        assert _contar("lotes_precios") == 0

    def test_el_monto_gigante_forjado_se_rechaza_en_el_criterio(self, base_datos_temporal):
        for valor in (10**30, 2**63):
            with pytest.raises(DatosInvalidosError):
                CriterioActualizacion("MONTO", "AUMENTAR", valor)

    def test_el_porcentaje_forjado_se_rechaza_en_el_criterio_no_solo_al_parsear_el_texto(self, base_datos_temporal):
        with pytest.raises(DatosInvalidosError):
            CriterioActualizacion("PORCENTAJE", "AUMENTAR", 100_001)
        CriterioActualizacion("PORCENTAJE", "AUMENTAR", 100_000)  # 1000 % exacto: permitido

    def test_un_producto_fuera_del_alcance_elegido_se_rechaza_y_no_cambia_nada(self, base_datos_temporal):
        usuario = _usuario()
        bebidas = servicio_categorias.crear_categoria("Bebidas")
        snacks = servicio_categorias.crear_categoria("Snacks")
        cola = _producto("1", "Cola", 10000, categoria_id=bebidas.id)
        papas = _producto("2", "Papas", 20000, categoria_id=snacks.id)

        with pytest.raises(DatosInvalidosError, match="no pertenece al alcance"):
            servicio_precios.aplicar_actualizacion(
                usuario.id, MAS_10, f"CATEGORIA:{bebidas.id}", _esperados(cola, papas)
            )

        assert (_precio(cola.id), _precio(papas.id)) == (10000, 20000) and _contar("lotes_precios") == 0

    def test_sin_categoria_no_acepta_un_producto_con_categoria(self, base_datos_temporal):
        usuario = _usuario()
        bebidas = servicio_categorias.crear_categoria("Bebidas")
        con = _producto("1", "Con", 10000, categoria_id=bebidas.id)

        with pytest.raises(DatosInvalidosError):
            servicio_precios.aplicar_actualizacion(usuario.id, MAS_10, "SIN_CATEGORIA", _esperados(con))

    def test_un_producto_sin_precio_no_entra_ni_forzando_la_seleccion(self, base_datos_temporal):
        usuario = _usuario()
        sin_precio = _producto("1", "Sin precio", 0)

        with pytest.raises(DatosInvalidosError):
            servicio_precios.aplicar_actualizacion(usuario.id, MAS_10, "TODOS", {sin_precio.id: 0})

        assert _precio(sin_precio.id) == 0 and _contar("historial_precios") == 0

    def test_un_alcance_de_categoria_inexistente_rechaza_cualquier_seleccion(self, tres_productos):
        usuario, a, *_ = tres_productos

        with pytest.raises(DatosInvalidosError):
            servicio_precios.aplicar_actualizacion(usuario.id, MAS_10, "CATEGORIA:987654", _esperados(a))

    def test_la_vista_previa_tambien_valida_el_alcance_manipulado(self, tres_productos):
        cookies = _cookies("otro", "OWNER")

        respuesta = solicitud(
            "GET",
            "/precios/vista-previa?tipo=PORCENTAJE&direccion=AUMENTAR&valor=10&alcance=TODOS%3Cscript%3E",
            cookies=cookies,
        )

        assert respuesta.status == 303 and "tipo=error" in respuesta.header("location")

    def test_el_redondeo_se_valida_en_la_confirmacion(self, tres_productos):
        cookies = _cookies("otro", "OWNER")
        _, a, *_ = tres_productos

        respuesta = self._post(cookies, self._base(a, redondeo="ENTERO", valor="1000"))

        assert respuesta.status == 303 and "tipo=success" in respuesta.header("location")  # ENTERO es válido
        assert _precio(a.id) == 11000
