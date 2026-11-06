"""V1.17: pantalla de auditoría paginada y descarga CSV con los filtros de la pantalla (solo OWNER)."""

import csv
import io

import pytest

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth

from ._asgi_cliente import solicitud

_ACCION = "AJUSTE_STOCK"


def _usuario(nombre: str, rol: str) -> Usuario:
    return repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre,
            nombre_completo=f"Nombre {nombre}",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol=rol,
        )
    )


def _cookies(nombre: str) -> dict[str, str]:
    return {NOMBRE_COOKIE_SESION: servicio_auth.iniciar_sesion(nombre, "clave-correcta-123").token}


def _sembrar(cantidad: int, accion: str = _ACCION, usuario_id: int | None = None) -> None:
    with obtener_conexion() as conexion:
        for numero in range(cantidad):
            repositorio_auditoria.registrar_en_conexion(conexion, usuario_id, accion, "PRODUCTO", numero, f"evento-{numero}.")


@pytest.fixture
def owner(base_datos_temporal):
    usuario = _usuario("duenio", "OWNER")
    return usuario, _cookies("duenio")


class TestPantalla:
    def test_muestra_el_total_y_la_pagina_sin_el_aviso_de_200(self, owner):
        _sembrar(230)

        texto = solicitud("GET", "/auditoria", cookies=owner[1]).texto

        assert "230 operaciones · página 1 de 5" in texto
        assert "200 operaciones" not in texto
        assert "Siguiente" in texto
        assert "Anterior" not in texto

    def test_la_ultima_pagina_alcanza_las_filas_mas_viejas(self, owner):
        _sembrar(230)

        texto = solicitud("GET", "/auditoria?pagina=5", cookies=owner[1]).texto

        assert "evento-0." in texto
        assert "Anterior" in texto
        assert "Siguiente" not in texto

    def test_una_pagina_invalida_no_rompe(self, owner):
        _sembrar(60)

        for pagina in ("abc", "0", "-3", "9" * 5000):
            assert solicitud("GET", f"/auditoria?pagina={pagina}", cookies=owner[1]).status == 200

    def test_los_filtros_viajan_en_los_enlaces_de_pagina(self, owner):
        _sembrar(120, usuario_id=owner[0].id)

        texto = solicitud(
            "GET",
            f"/auditoria?accion={_ACCION}&usuario_id={owner[0].id}&fecha_desde=2020-01-01&fecha_hasta=2099-12-31",
            cookies=owner[1],
        ).texto

        esperado = (
            f"/auditoria?accion={_ACCION}&amp;usuario_id={owner[0].id}"
            "&amp;fecha_desde=2020-01-01&amp;fecha_hasta=2099-12-31&amp;pagina=2"
        )
        assert esperado in texto

    def test_el_filtro_acota_el_total(self, owner):
        _sembrar(60)
        _sembrar(3, accion="USUARIO_CREADO")

        texto = solicitud("GET", "/auditoria?accion=USUARIO_CREADO", cookies=owner[1]).texto

        assert "3 operaciones · página 1 de 1" in texto

    def test_el_enlace_de_exportar_lleva_los_filtros_activos(self, owner):
        _sembrar(2)

        texto = solicitud("GET", f"/auditoria?accion={_ACCION}", cookies=owner[1]).texto

        assert f'href="/auditoria/exportar?accion={_ACCION}"' in texto


class TestExportacion:
    def _filas(self, respuesta) -> list[dict[str, str]]:
        return list(csv.DictReader(io.StringIO(respuesta.cuerpo.decode("utf-8-sig"), newline="")))

    def test_descarga_todo_lo_filtrado_sin_tope(self, owner):
        _sembrar(230)
        _sembrar(4, accion="USUARIO_CREADO")

        respuesta = solicitud("GET", f"/auditoria/exportar?accion={_ACCION}", cookies=owner[1])

        assert respuesta.status == 200
        assert len(self._filas(respuesta)) == 230

    def test_cabeceras_de_descarga(self, owner):
        respuesta = solicitud("GET", "/auditoria/exportar", cookies=owner[1])

        assert respuesta.header("content-type") == "text/csv; charset=utf-8"
        assert respuesta.header("content-disposition").startswith('attachment; filename="auditoria_')

    def test_un_cashier_no_puede_descargar(self, owner):
        _usuario("cajero", "CASHIER")

        respuesta = solicitud("GET", "/auditoria/exportar", cookies=_cookies("cajero"))

        assert respuesta.status == 403

    def test_sin_sesion_va_al_login(self, base_datos_temporal):
        respuesta = solicitud("GET", "/auditoria/exportar")

        assert respuesta.status == 303
        assert respuesta.header("location").startswith("/login")


def test_un_cashier_no_ve_la_pantalla(owner):
    _usuario("cajero", "CASHIER")

    assert solicitud("GET", "/auditoria?pagina=2", cookies=_cookies("cajero")).status == 403


def test_un_usuario_id_desmedido_no_es_un_error_del_servidor(owner):
    for ruta in ("/auditoria", "/auditoria/exportar"):
        respuesta = solicitud("GET", f"{ruta}?usuario_id={'9' * 30}", cookies=owner[1])
        assert respuesta.status in (200, 303)
