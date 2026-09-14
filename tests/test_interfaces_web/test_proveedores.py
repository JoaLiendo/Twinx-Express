"""Pruebas HTTP end-to-end de gestión de proveedores (Fase 4A).

Complementan (no reemplazan) la cobertura genérica de acceso por rol
de `test_proteccion_rutas.py` (que ya cubre GET /proveedores):
prueban el CRUD real -- alta, edición, baja lógica, reactivación -- y
que ninguna ruta quede accesible por un método HTTP no previsto.
"""

from db.conexion import obtener_conexion
from domain.usuario import Usuario
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth, servicio_proveedores

from ._asgi_cliente import solicitud


def _crear_usuario_y_loguearse(rol: str, nombre_usuario: str) -> str:
    from db.repositorios import usuarios as repositorio_usuarios

    repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre_usuario,
            nombre_completo="Usuario de prueba",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol=rol,
        )
    )
    return servicio_auth.iniciar_sesion(nombre_usuario, "clave-correcta-123").token


def _cookies_owner(nombre_usuario: str = "ana") -> dict[str, str]:
    return {NOMBRE_COOKIE_SESION: _crear_usuario_y_loguearse("OWNER", nombre_usuario)}


class TestListado:
    def test_owner_ve_proveedores_activos(self, base_datos_temporal):
        servicio_proveedores.crear_proveedor("Distribuidora SA")
        cookies = _cookies_owner()

        respuesta = solicitud("GET", "/proveedores", cookies=cookies)

        assert respuesta.status == 200
        assert "Distribuidora SA" in respuesta.texto

    def test_listado_no_muestra_inactivos_por_defecto(self, base_datos_temporal):
        proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
        servicio_proveedores.eliminar_proveedor(proveedor.id)
        cookies = _cookies_owner()

        respuesta = solicitud("GET", "/proveedores", cookies=cookies)

        assert "Distribuidora SA" not in respuesta.texto

    def test_mostrar_inactivos_lista_los_dados_de_baja(self, base_datos_temporal):
        proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
        with obtener_conexion() as conexion:
            conexion.execute("UPDATE proveedores SET activo = 0 WHERE id = ?", (proveedor.id,))
        cookies = _cookies_owner()

        respuesta = solicitud("GET", "/proveedores?mostrar_inactivos=true", cookies=cookies)

        assert "Distribuidora SA" in respuesta.texto


class TestAlta:
    def test_owner_ve_el_formulario_de_alta(self, base_datos_temporal):
        cookies = _cookies_owner()

        respuesta = solicitud("GET", "/proveedores/nuevo", cookies=cookies)

        assert respuesta.status == 200

    def test_owner_puede_crear_proveedor(self, base_datos_temporal):
        cookies = _cookies_owner()

        respuesta = solicitud(
            "POST",
            "/proveedores/nuevo",
            cookies=cookies,
            formulario={
                "nombre": "Distribuidora SA",
                "contacto_nombre": "Juan Pérez",
                "telefono": "1122334455",
                "email": "",
                "direccion": "",
                "notas": "",
            },
        )

        assert respuesta.status == 303
        creado = servicio_proveedores.listar_activos()
        assert len(creado) == 1
        assert creado[0].contacto_nombre == "Juan Pérez"

    def test_crear_proveedor_duplicado_no_rompe_y_no_duplica(self, base_datos_temporal):
        servicio_proveedores.crear_proveedor("Distribuidora SA")
        cookies = _cookies_owner()

        respuesta = solicitud(
            "POST", "/proveedores/nuevo", cookies=cookies, formulario={"nombre": "Distribuidora SA"}
        )

        assert respuesta.status == 303  # error traducido a redirect con toast, no un 500
        assert len(servicio_proveedores.listar_activos()) == 1

    def test_cashier_recibe_403_al_intentar_crear(self, base_datos_temporal):
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")

        respuesta = solicitud(
            "POST",
            "/proveedores/nuevo",
            cookies={NOMBRE_COOKIE_SESION: token},
            formulario={"nombre": "Distribuidora SA"},
        )

        assert respuesta.status == 403
        assert servicio_proveedores.listar_activos() == []


class TestEdicion:
    def test_owner_ve_el_formulario_con_los_datos_actuales(self, base_datos_temporal):
        proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA", telefono="123")
        cookies = _cookies_owner()

        respuesta = solicitud("GET", f"/proveedores/{proveedor.id}/editar", cookies=cookies)

        assert respuesta.status == 200
        assert "Distribuidora SA" in respuesta.texto

    def test_owner_puede_editar_proveedor(self, base_datos_temporal):
        proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
        cookies = _cookies_owner()

        respuesta = solicitud(
            "POST",
            f"/proveedores/{proveedor.id}/editar",
            cookies=cookies,
            formulario={
                "nombre": "Distribuidora SRL",
                "contacto_nombre": "",
                "telefono": "99999999",
                "email": "",
                "direccion": "",
                "notas": "",
            },
        )

        assert respuesta.status == 303
        actualizado = servicio_proveedores.obtener_por_id(proveedor.id)
        assert actualizado.nombre == "Distribuidora SRL"
        assert actualizado.telefono == "99999999"

    def test_cashier_recibe_403_al_intentar_editar(self, base_datos_temporal):
        proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")

        respuesta = solicitud(
            "POST",
            f"/proveedores/{proveedor.id}/editar",
            cookies={NOMBRE_COOKIE_SESION: token},
            formulario={"nombre": "Otro nombre"},
        )

        assert respuesta.status == 403
        assert servicio_proveedores.obtener_por_id(proveedor.id).nombre == "Distribuidora SA"


class TestBajaYReactivacion:
    def test_owner_puede_eliminar_proveedor_sin_uso(self, base_datos_temporal):
        proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
        cookies = _cookies_owner()

        respuesta = solicitud("POST", f"/proveedores/{proveedor.id}/eliminar", cookies=cookies)

        assert respuesta.status == 303
        assert servicio_proveedores.obtener_por_id(proveedor.id) is None

    def test_cashier_recibe_403_al_intentar_eliminar(self, base_datos_temporal):
        proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")

        respuesta = solicitud(
            "POST", f"/proveedores/{proveedor.id}/eliminar", cookies={NOMBRE_COOKIE_SESION: token}
        )

        assert respuesta.status == 403
        assert servicio_proveedores.obtener_por_id(proveedor.id) is not None

    def test_owner_puede_reactivar_proveedor(self, base_datos_temporal):
        proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
        with obtener_conexion() as conexion:
            conexion.execute("UPDATE proveedores SET activo = 0 WHERE id = ?", (proveedor.id,))
        cookies = _cookies_owner()

        respuesta = solicitud("POST", f"/proveedores/{proveedor.id}/reactivar", cookies=cookies)

        assert respuesta.status == 303
        assert servicio_proveedores.obtener_por_id(proveedor.id) is not None

    def test_cashier_recibe_403_al_intentar_reactivar(self, base_datos_temporal):
        proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
        with obtener_conexion() as conexion:
            conexion.execute("UPDATE proveedores SET activo = 0 WHERE id = ?", (proveedor.id,))
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")

        respuesta = solicitud(
            "POST", f"/proveedores/{proveedor.id}/reactivar", cookies={NOMBRE_COOKIE_SESION: token}
        )

        assert respuesta.status == 403


class TestSinSesion:
    def test_todas_las_rutas_redirigen_a_login_sin_sesion(self, base_datos_temporal):
        proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
        rutas = [
            ("GET", "/proveedores"),
            ("GET", "/proveedores/nuevo"),
            ("POST", "/proveedores/nuevo"),
            ("GET", f"/proveedores/{proveedor.id}/editar"),
            ("POST", f"/proveedores/{proveedor.id}/editar"),
            ("POST", f"/proveedores/{proveedor.id}/eliminar"),
            ("POST", f"/proveedores/{proveedor.id}/reactivar"),
        ]
        for metodo, ruta in rutas:
            respuesta = solicitud(metodo, ruta)
            assert respuesta.status == 303, f"{metodo} {ruta} debería redirigir a login, dio {respuesta.status}"
            assert respuesta.header("location").startswith("/login")


class TestMetodosNoPrevistos:
    def test_metodos_alternativos_no_quedan_accesibles(self, base_datos_temporal):
        proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
        cookies = _cookies_owner()

        casos = [
            ("POST", "/proveedores"),  # el listado es solo GET
            ("DELETE", f"/proveedores/{proveedor.id}/eliminar"),
            ("GET", f"/proveedores/{proveedor.id}/eliminar"),  # eliminar es solo POST
            ("GET", f"/proveedores/{proveedor.id}/reactivar"),  # reactivar es solo POST
            ("PUT", f"/proveedores/{proveedor.id}/editar"),
        ]
        for metodo, ruta in casos:
            respuesta = solicitud(metodo, ruta, cookies=cookies)
            assert respuesta.status == 405, f"{metodo} {ruta} debería dar 405, dio {respuesta.status}"
