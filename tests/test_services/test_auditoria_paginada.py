"""V1.17: paginación real de la auditoría y su exportación por lotes (sin el corte de 200 filas)."""

import csv
import io

import pytest

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from services import servicio_auditoria, servicio_auth, servicio_exportacion_csv

_ACCION = "AJUSTE_STOCK"
_OTRA = "USUARIO_CREADO"


def _usuario(nombre: str) -> Usuario:
    return repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre,
            nombre_completo=f"Nombre {nombre}",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol="OWNER",
        )
    )


def _sembrar(cantidad: int, accion: str = _ACCION, usuario_id: int | None = None, resumen: str = "evento") -> None:
    with obtener_conexion() as conexion:
        for numero in range(cantidad):
            repositorio_auditoria.registrar_en_conexion(conexion, usuario_id, accion, "PRODUCTO", numero, f"{resumen} {numero}")


def _fechar(entidad_id: int, fecha: str) -> None:
    with obtener_conexion() as conexion:
        conexion.execute("UPDATE auditoria SET fecha = ? WHERE entidad_id = ?", (fecha, entidad_id))


def _filas(partes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(b"".join(partes).decode("utf-8-sig"), newline="")))


class TestRepositorio:
    def test_contar_devuelve_el_total_real_sin_tope(self, base_datos_temporal):
        _sembrar(230)

        assert repositorio_auditoria.contar() == 230

    def test_contar_respeta_todos_los_filtros(self, base_datos_temporal):
        usuario = _usuario("ana")
        _sembrar(3, usuario_id=usuario.id)
        _sembrar(2, accion=_OTRA)
        _fechar(0, "2026-01-15 10:00:00")

        assert repositorio_auditoria.contar(accion=_OTRA) == 2
        assert repositorio_auditoria.contar(usuario_id=usuario.id) == 3
        assert repositorio_auditoria.contar(fecha_desde="2026-01-15", fecha_hasta="2026-01-15") == 2

    def test_listar_recorta_la_pagina_en_sql_sin_repetir_filas(self, base_datos_temporal):
        _sembrar(5)

        primera = repositorio_auditoria.listar(limite=2, desplazamiento=0)
        segunda = repositorio_auditoria.listar(limite=2, desplazamiento=2)

        assert [e.entidad_id for e in primera] == [4, 3]
        assert [e.entidad_id for e in segunda] == [2, 1]

    def test_listar_anteriores_avanza_por_id(self, base_datos_temporal):
        _sembrar(5)
        primera = repositorio_auditoria.listar_anteriores(None, None, None, None, None, 2)

        siguiente = repositorio_auditoria.listar_anteriores(None, None, None, None, primera[-1].id, 2)

        assert [e.entidad_id for e in primera] == [4, 3]
        assert [e.entidad_id for e in siguiente] == [2, 1]


class TestPagina:
    def test_una_pagina_trae_su_tamano_y_el_total(self, base_datos_temporal):
        _sembrar(230)

        pagina = servicio_auditoria.listar_pagina(None, None, None, None, 1)

        assert len(pagina.entradas) == servicio_auditoria.ENTRADAS_POR_PAGINA
        assert pagina.total == 230
        assert pagina.total_paginas == 5
        assert pagina.pagina == 1

    def test_la_ultima_pagina_llega_a_lo_que_antes_cortaba_el_limite_de_200(self, base_datos_temporal):
        _sembrar(230)

        pagina = servicio_auditoria.listar_pagina(None, None, None, None, 5)

        assert len(pagina.entradas) == 30
        assert pagina.entradas[-1].entidad_id == 0

    def test_una_pagina_fuera_de_rango_cae_en_la_ultima(self, base_datos_temporal):
        _sembrar(60)

        pagina = servicio_auditoria.listar_pagina(None, None, None, None, 999)

        assert pagina.pagina == 2
        assert len(pagina.entradas) == 10

    def test_sin_registros_hay_una_pagina_vacia(self, base_datos_temporal):
        pagina = servicio_auditoria.listar_pagina(None, None, None, None, 1)

        assert (pagina.entradas, pagina.total, pagina.pagina, pagina.total_paginas) == ([], 0, 1, 1)


class TestCsv:
    def test_exporta_todo_sin_el_corte_de_200(self, base_datos_temporal):
        _sembrar(1205)

        filas = _filas(servicio_exportacion_csv.csv_auditoria(None, None, None, None))

        assert len(filas) == 1205
        assert filas[0]["entidad_id"] == "1204"
        assert filas[-1]["entidad_id"] == "0"

    def test_se_genera_por_lotes_y_no_en_un_solo_bloque(self, base_datos_temporal):
        _sembrar(1205)

        partes = list(servicio_exportacion_csv.csv_auditoria(None, None, None, None))

        assert len(partes) == 1 + 3  # encabezado + lotes

    def test_tiene_bom_utf8_y_columnas_fijas(self, base_datos_temporal):
        contenido = b"".join(servicio_exportacion_csv.csv_auditoria(None, None, None, None))

        assert contenido.startswith(b"\xef\xbb\xbf")
        assert contenido.decode("utf-8-sig").strip() == ",".join(servicio_exportacion_csv.COLUMNAS_AUDITORIA)

    def test_respeta_los_filtros(self, base_datos_temporal):
        usuario = _usuario("ana")
        _sembrar(2, usuario_id=usuario.id)
        _sembrar(3, accion=_OTRA)

        filas = _filas(servicio_exportacion_csv.csv_auditoria(_ACCION, usuario.id, None, None))

        assert len(filas) == 2
        assert {f["usuario"] for f in filas} == {"Nombre ana"}
        assert {f["accion"] for f in filas} == {_ACCION}

    def test_neutraliza_una_formula_en_el_resumen(self, base_datos_temporal):
        with obtener_conexion() as conexion:
            repositorio_auditoria.registrar_en_conexion(conexion, None, _ACCION, "PRODUCTO", 1, "=HYPERLINK(1)")

        [fila] = _filas(servicio_exportacion_csv.csv_auditoria(None, None, None, None))

        assert fila["resumen"] == "'=HYPERLINK(1)"

    def test_una_fila_sin_usuario_sale_vacia_sin_inventar_uno(self, base_datos_temporal):
        _sembrar(1)

        [fila] = _filas(servicio_exportacion_csv.csv_auditoria(None, None, None, None))

        assert fila["usuario"] == ""
        assert fila["usuario_id"] == ""

    def test_no_expone_contrasena_ni_hash_de_operaciones_reales(self, base_datos_temporal):
        from services import servicio_usuarios

        owner = _usuario("duenio")
        creado = servicio_usuarios.crear_usuario("cajera", "Cajera", "clave-secreta-777", "CASHIER", actor_id=owner.id)
        servicio_usuarios.resetear_password(creado.id, "otra-clave-secreta-888", actor_id=owner.id)

        texto = b"".join(servicio_exportacion_csv.csv_auditoria(None, None, None, None)).decode("utf-8-sig")

        assert "USUARIO_PASSWORD_RESETEADA" in texto
        assert "clave-secreta" not in texto
        assert "pbkdf2" not in texto


@pytest.mark.parametrize("cantidad, esperadas", [(0, 1), (1, 1), (50, 1), (51, 2)])
def test_total_de_paginas_en_los_bordes(base_datos_temporal, cantidad, esperadas):
    _sembrar(cantidad)

    assert servicio_auditoria.listar_pagina(None, None, None, None, 1).total_paginas == esperadas
