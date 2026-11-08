"""`scripts/piloto_ollama.py`: el relevamiento no necesita Ollama y el guion de medición corre con el proveedor
falso sobre una base temporal, sin tocar la base real ni la red."""

import importlib.util
from pathlib import Path

import pytest

from services.asistente.proveedores import LlamadaHerramienta, ProveedorFalso, RespuestaModelo

RUTA = Path(__file__).resolve().parent.parent / "scripts" / "piloto_ollama.py"


@pytest.fixture
def piloto():
    especificacion = importlib.util.spec_from_file_location("piloto_ollama", RUTA)
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


def test_detectar_releva_sin_instalar_nada(piloto, base_datos_temporal):
    datos = piloto.detectar()
    assert {"cpu", "nucleos", "disco_libre_gb", "ollama_responde", "modelos_locales", "configuracion_asistente", "carpeta_modelos"} <= set(datos)
    assert isinstance(datos["modelos_locales"], list)
    assert datos["configuracion_asistente"]["motor"] == "integrado"


def test_medir_con_motor_apagado_no_mide(piloto, tmp_path, base_datos_temporal):
    informe = piloto.medir(proveedor=ProveedorFalso(disponible=False), directorio=tmp_path)
    assert informe.motor_disponible is False and informe.resultados == [] and informe.carga_inicial_segundos is None


def test_medir_registra_latencia_herramientas_y_permisos(piloto, tmp_path, base_datos_temporal):
    guion = (
        ("resumen rápido de la jornada", "OWNER", "ventas_del_periodo"),
        ("en qué artículos estoy perdiendo plata", "CASHIER", None),
        ("qué tiempo va a hacer mañana", "OWNER", None),
    )
    falso = ProveedorFalso(
        [
            RespuestaModelo("", (LlamadaHerramienta("ventas_del_periodo", {"periodo": "hoy"}),)),
            RespuestaModelo("Hoy se vendió lo informado."),
            RespuestaModelo("", (LlamadaHerramienta("margen_negativo", {}),)),
            RespuestaModelo("No tengo acceso a los márgenes."),
            RespuestaModelo("No tengo datos del clima."),
        ]
    )
    informe = piloto.medir(proveedor=falso, guion=guion, directorio=tmp_path)

    assert informe.motor_disponible and informe.modelo == "falso:test"
    assert [r.correcta for r in informe.resultados] == [True, True, True]
    assert informe.resultados[0].herramientas_usadas == ["ventas_del_periodo"]
    assert informe.resultados[1].herramientas_usadas == [] and not informe.resultados[1].fuga_de_permisos
    assert all(r.segundos >= 0 for r in informe.resultados)
    resumen = informe.resumen()
    assert resumen["preguntas"] == 3 and resumen["herramienta_correcta"] == 1 and resumen["fugas_de_permisos"] == 0
    assert (tmp_path / "piloto.db").exists()
