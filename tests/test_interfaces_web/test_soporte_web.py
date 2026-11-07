"""Ayuda y soporte (V1.18): el manual es para los dos roles; el soporte técnico y el diagnóstico, solo OWNER."""

import io
import re
import zipfile
from pathlib import Path

import pytest

from tests.utilidades_clientes import usuario_logueado

from ._asgi_cliente import solicitud

RAIZ = Path(__file__).resolve().parents[2]


@pytest.fixture
def cookies(base_datos_temporal):
    _, owner = usuario_logueado("OWNER", "duenio")
    _, cajero = usuario_logueado("CASHIER", "cajero")
    return {"owner": owner, "cajero": cajero}


class TestAyuda:
    @pytest.mark.parametrize("rol", ["owner", "cajero"])
    def test_el_manual_es_visible_para_ambos_roles(self, cookies, rol):
        respuesta = solicitud("GET", "/ayuda", cookies=cookies[rol])

        assert respuesta.status == 200
        for ancla in ("guia-rapida", "ventas", "caja", "inventario", "backup", "restauracion", "problemas"):
            assert f'id="{ancla}"' in respuesta.texto
        assert 'href="/ayuda"' in respuesta.texto  # en el menú

    def test_el_cajero_no_ve_el_enlace_a_soporte(self, cookies):
        respuesta = solicitud("GET", "/ayuda", cookies=cookies["cajero"])
        assert 'href="/soporte"' not in respuesta.texto

    def test_las_secciones_del_manual_coinciden_con_el_manual_distribuible(self, cookies):
        """`docs/MANUAL.md` (va en el entregable) y `/ayuda` no deben desincronizarse."""
        manual = (RAIZ / "docs" / "MANUAL.md").read_text(encoding="utf-8")
        titulos_md = re.findall(r"^## (.+)$", manual, flags=re.M)
        respuesta = solicitud("GET", "/ayuda", cookies=cookies["owner"])
        titulos_html = re.findall(r"<h2>(.+?)</h2>", respuesta.texto)
        assert titulos_html == titulos_md


class TestSoporte:
    def test_owner_ve_version_rutas_estado_y_descarga_el_diagnostico(self, cookies):
        respuesta = solicitud("GET", "/soporte", cookies=cookies["owner"])

        assert respuesta.status == 200
        assert 'id="soporte-version"' in respuesta.texto and 'id="soporte-integridad"' in respuesta.texto
        assert ">ok<" in respuesta.texto or "ok</p>" in respuesta.texto
        assert 'href="/soporte/diagnostico"' in respuesta.texto

        descarga = solicitud("GET", "/soporte/diagnostico", cookies=cookies["owner"])

        assert descarga.status == 200 and descarga.header("content-type") == "application/zip"
        assert descarga.header("content-disposition").startswith('attachment; filename="diagnostico_twinx_')
        with zipfile.ZipFile(io.BytesIO(descarga.cuerpo)) as zip_diagnostico:
            assert "diagnostico.json" in zip_diagnostico.namelist()
            assert not any(nombre.endswith(".db") for nombre in zip_diagnostico.namelist())

    @pytest.mark.parametrize("ruta", ["/soporte", "/soporte/diagnostico"])
    def test_el_cajero_no_entra_al_soporte(self, cookies, ruta):
        assert solicitud("GET", ruta, cookies=cookies["cajero"]).status == 403

    def test_configuracion_enlaza_ayuda_y_soporte(self, cookies):
        respuesta = solicitud("GET", "/configuracion", cookies=cookies["owner"])
        assert 'id="config-soporte"' in respuesta.texto and 'href="/soporte"' in respuesta.texto
