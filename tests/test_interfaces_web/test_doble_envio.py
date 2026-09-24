"""C3 (V1.1) a nivel HTTP: los formularios que mueven dinero o stock llevan
una clave de idempotencia y un doble POST con esa clave no duplica nada.
También cubre la migración 014 y el guard de formularios en el cliente."""

import re

import db.conexion as modulo_conexion
from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth, servicio_caja, servicio_proveedores, servicio_stock

from ._asgi_cliente import solicitud


def _cookies_owner() -> dict[str, str]:
    repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario="duenio",
            nombre_completo="Dueño",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol="OWNER",
        )
    )
    token = servicio_auth.iniciar_sesion("duenio", "clave-correcta-123").token
    return {NOMBRE_COOKIE_SESION: token}


def _contar(tabla: str) -> int:
    with obtener_conexion() as conexion:
        return conexion.execute(f"SELECT COUNT(*) AS n FROM {tabla}").fetchone()["n"]


def _claves_del_formulario(html: str) -> list[str]:
    return re.findall(r'name="clave_idempotencia" value="([0-9a-f]{32})"', html)


class TestCajaIngresoYEgreso:
    def test_el_panel_entrega_una_clave_distinta_para_ingreso_y_egreso(self, base_datos_temporal):
        cookies = _cookies_owner()
        servicio_caja.abrir_caja(100_000)

        claves = _claves_del_formulario(solicitud("GET", "/caja", cookies=cookies).texto)

        assert len(claves) == 2
        assert claves[0] != claves[1]

    def test_cada_visita_al_panel_genera_claves_nuevas(self, base_datos_temporal):
        cookies = _cookies_owner()
        servicio_caja.abrir_caja(100_000)

        primera = _claves_del_formulario(solicitud("GET", "/caja", cookies=cookies).texto)
        segunda = _claves_del_formulario(solicitud("GET", "/caja", cookies=cookies).texto)

        assert set(primera).isdisjoint(segunda)

    def test_doble_post_de_egreso_con_la_misma_clave_registra_uno_solo(self, base_datos_temporal):
        cookies = _cookies_owner()
        servicio_caja.abrir_caja(100_000)
        formulario = {"monto": "20.00", "descripcion": "pago", "clave_idempotencia": "a" * 32}

        r1 = solicitud("POST", "/caja/egreso", cookies=cookies, formulario=formulario)
        r2 = solicitud("POST", "/caja/egreso", cookies=cookies, formulario=formulario)

        assert r1.status == r2.status == 303
        assert "tipo=success" in r2.header("location")
        with obtener_conexion() as conexion:
            egresos = conexion.execute("SELECT COUNT(*) AS n FROM caja_movimientos WHERE tipo = 'EGRESO'").fetchone()["n"]
        assert egresos == 1

    def test_doble_post_de_ingreso_con_la_misma_clave_registra_uno_solo(self, base_datos_temporal):
        cookies = _cookies_owner()
        servicio_caja.abrir_caja(100_000)
        formulario = {"monto": "50.00", "descripcion": "cambio", "clave_idempotencia": "b" * 32}

        solicitud("POST", "/caja/ingreso", cookies=cookies, formulario=formulario)
        solicitud("POST", "/caja/ingreso", cookies=cookies, formulario=formulario)

        with obtener_conexion() as conexion:
            ingresos = conexion.execute("SELECT COUNT(*) AS n FROM caja_movimientos WHERE tipo = 'INGRESO'").fetchone()["n"]
        assert ingresos == 1

    def test_sin_clave_el_formulario_sigue_funcionando(self, base_datos_temporal):
        cookies = _cookies_owner()
        servicio_caja.abrir_caja(100_000)

        respuesta = solicitud("POST", "/caja/egreso", cookies=cookies, formulario={"monto": "1.00", "descripcion": "x"})

        assert respuesta.status == 303
        assert "tipo=success" in respuesta.header("location")


class TestReenvioConOtrosDatos:
    def test_egreso_reenviado_con_otro_monto_avisa_error_y_no_registra_nada_nuevo(self, base_datos_temporal):
        cookies = _cookies_owner()
        servicio_caja.abrir_caja(100_000)
        base = {"descripcion": "pago", "clave_idempotencia": "e" * 32}

        solicitud("POST", "/caja/egreso", cookies=cookies, formulario={**base, "monto": "20.00"})
        r2 = solicitud("POST", "/caja/egreso", cookies=cookies, formulario={**base, "monto": "30.00"})

        assert "tipo=error" in r2.header("location")
        with obtener_conexion() as conexion:
            egresos = conexion.execute("SELECT monto_centavos FROM caja_movimientos WHERE tipo = 'EGRESO'").fetchall()
        assert [f["monto_centavos"] for f in egresos] == [2000]


class TestAjusteDeStock:
    def test_el_formulario_incluye_la_clave(self, base_datos_temporal):
        cookies = _cookies_owner()
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)

        html = solicitud("GET", f"/productos/{producto.id}/ajustar", cookies=cookies).texto

        assert len(_claves_del_formulario(html)) == 1

    def test_doble_post_con_la_misma_clave_ajusta_una_sola_vez(self, base_datos_temporal):
        cookies = _cookies_owner()
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        formulario = {
            "motivo": "MERMA",
            "direccion": "restar",
            "cantidad": "4",
            "observaciones": "",
            "clave_idempotencia": "c" * 32,
        }

        solicitud("POST", f"/productos/{producto.id}/ajustar", cookies=cookies, formulario=formulario)
        r2 = solicitud("POST", f"/productos/{producto.id}/ajustar", cookies=cookies, formulario=formulario)

        assert "tipo=success" in r2.header("location")
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 6
        assert _contar("ajustes_stock") == 1


class TestCompra:
    def test_el_formulario_incluye_la_clave(self, base_datos_temporal):
        cookies = _cookies_owner()
        servicio_proveedores.crear_proveedor("Distribuidora SA")
        servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=5)

        html = solicitud("GET", "/compras/nueva", cookies=cookies).texto

        assert len(_claves_del_formulario(html)) == 1

    def test_doble_post_con_la_misma_clave_registra_una_sola_compra(self, base_datos_temporal):
        cookies = _cookies_owner()
        proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=5)
        formulario = {
            "proveedor_id": str(proveedor.id),
            "observaciones": "",
            "producto_id": str(producto.id),
            "cantidad": "10",
            "costo_unitario": "1.20",
            "clave_idempotencia": "d" * 32,
        }

        r1 = solicitud("POST", "/compras/nueva", cookies=cookies, formulario=formulario)
        r2 = solicitud("POST", "/compras/nueva", cookies=cookies, formulario=formulario)

        assert r1.header("location") == r2.header("location")
        assert _contar("compras") == 1
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 15


class TestGuardDeFormularios:
    def test_la_base_carga_el_guard_de_doble_envio(self, base_datos_temporal):
        cookies = _cookies_owner()

        html = solicitud("GET", "/caja", cookies=cookies).texto

        assert "/static/js/guard_formularios.js" in html

    def test_el_guard_esta_disponible_como_archivo_estatico(self, base_datos_temporal):
        respuesta = solicitud("GET", "/static/js/guard_formularios.js")

        assert respuesta.status == 200
        assert "dataset.enviando" in respuesta.texto


class TestMigracion014:
    def test_agrega_la_clave_a_las_tres_tablas_con_indice_unico(self, base_datos_temporal):
        with obtener_conexion() as conexion:
            for tabla in ("caja_movimientos", "ajustes_stock", "compras"):
                columnas = {fila["name"] for fila in conexion.execute(f"PRAGMA table_info({tabla})").fetchall()}
                indices = {fila["name"] for fila in conexion.execute(f"PRAGMA index_list({tabla})").fetchall()}
                assert "clave_idempotencia" in columnas
                assert f"idx_{tabla}_clave_idempotencia" in indices

    def test_una_base_existente_conserva_sus_datos_y_queda_con_clave_null(self, tmp_path, monkeypatch):
        ruta_bd = tmp_path / "test_kiosco_pre_014.db"
        monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta_bd)
        previas = [
            ruta
            for ruta in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql"))
            if ruta.name < "014_idempotencia_operaciones.sql"
        ]
        with modulo_conexion.obtener_conexion() as conexion:
            conexion.execute(modulo_conexion._TABLA_MIGRACIONES)
            for ruta in previas:
                conexion.executescript(ruta.read_text(encoding="utf-8"))
                conexion.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (ruta.name,))
            conexion.execute(
                "INSERT INTO caja_movimientos (tipo, monto_centavos) VALUES ('APERTURA', 100000)"
            )

        modulo_conexion.inicializar_base_datos()

        with modulo_conexion.obtener_conexion() as conexion:
            fila = conexion.execute("SELECT monto_centavos, clave_idempotencia FROM caja_movimientos").fetchone()
            aplicadas = [f["nombre_archivo"] for f in conexion.execute("SELECT nombre_archivo FROM schema_migraciones")]
        assert fila["monto_centavos"] == 100000
        assert fila["clave_idempotencia"] is None
        assert aplicadas.count("014_idempotencia_operaciones.sql") == 1

    def test_no_se_aplica_dos_veces(self, base_datos_temporal):
        modulo_conexion.inicializar_base_datos()
        modulo_conexion.inicializar_base_datos()

        with obtener_conexion() as conexion:
            veces = conexion.execute(
                "SELECT COUNT(*) AS n FROM schema_migraciones WHERE nombre_archivo = '014_idempotencia_operaciones.sql'"
            ).fetchone()["n"]
        assert veces == 1
