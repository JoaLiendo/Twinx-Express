"""Tests de la resolución de rutas de datos persistentes (`config.py`).

En modo frozen los datos del cliente viven siempre en
`%LOCALAPPDATA%\\KioscoApp\\data`, fuera del bundle de PyInstaller: un
`data/` embebido dentro del build nunca se lee ni se copia. El modo
desarrollo no se ve afectado: sigue usando `RAIZ_PROYECTO/data`.
"""

import os
import shutil
import subprocess
import sys

import pytest

import config


def test_modo_dev_conserva_ruta_actual():
    """Sin `sys.frozen`, la app sigue usando `RAIZ_PROYECTO/data`."""
    assert config.DIRECTORIO_DATA == config.RAIZ_PROYECTO / "data"
    assert config.RUTA_BASE_DATOS == config.DIRECTORIO_DATA / "kiosco.db"


def test_modo_frozen_resuelve_localappdata(monkeypatch):
    """En frozen, el destino persistente es `%LOCALAPPDATA%\\KioscoApp\\data`."""
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\prueba\AppData\Local")

    ruta = config.ruta_datos_persistentes_frozen()

    assert ruta == config.Path(r"C:\Users\prueba\AppData\Local") / "KioscoApp" / "data"


def test_modo_frozen_sin_localappdata_falla(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    with pytest.raises(config.ErrorMigracionDatos):
        config.ruta_datos_persistentes_frozen()


def _importar_config_en_modo_frozen(tmp_path):
    """Importa una copia de `config.py` en un proceso aparte con
    `sys.frozen = True`, junto a un `data/` "embebido" con una DB y una
    imagen (lo que dejaría un build contaminado). Es la única forma de
    ejercitar la rama frozen a nivel de módulo sin tocar el `config`
    real de este proceso. Devuelve `(raiz, local_appdata, salida)`."""
    raiz = tmp_path / "bundle"
    (raiz / "data" / "imagenes_productos").mkdir(parents=True)
    (raiz / "data" / "kiosco.db").write_bytes(b"DB-EMBEBIDA-DE-DESARROLLO")
    (raiz / "data" / "imagenes_productos" / "1_abc.jpg").write_bytes(b"IMAGEN-EMBEBIDA")
    shutil.copy(config.RAIZ_PROYECTO / "config.py", raiz / "config.py")
    local_appdata = tmp_path / "local"
    local_appdata.mkdir()

    entorno = {**os.environ, "LOCALAPPDATA": str(local_appdata)}
    resultado = subprocess.run(
        [sys.executable, "-B", "-c", "import sys; sys.frozen = True; import config; print(config.DIRECTORIO_DATA)"],
        cwd=raiz,
        env=entorno,
        capture_output=True,
        text=True,
        check=True,
    )
    return raiz, local_appdata, resultado.stdout.strip()


def test_frozen_no_copia_el_data_embebido_en_el_bundle(tmp_path):
    """Un `data/` dentro del bundle (build contaminado) NO se copia a la
    persistencia del cliente: la instalación nueva nace vacía."""
    raiz, local_appdata, _salida = _importar_config_en_modo_frozen(tmp_path)

    destino = local_appdata / "KioscoApp" / "data"
    assert destino.is_dir()
    assert not (destino / "kiosco.db").exists()
    assert list((destino / "imagenes_productos").iterdir()) == []
    assert not (destino / "imagenes_productos" / "1_abc.jpg").exists()


def test_frozen_usa_siempre_localappdata_aunque_haya_data_embebido(tmp_path):
    raiz, local_appdata, salida = _importar_config_en_modo_frozen(tmp_path)

    assert salida == str(local_appdata / "KioscoApp" / "data")


def test_frozen_no_toca_ni_borra_el_data_embebido(tmp_path):
    """El bundle se ignora, no se modifica."""
    raiz, _local_appdata, _salida = _importar_config_en_modo_frozen(tmp_path)

    assert (raiz / "data" / "kiosco.db").read_bytes() == b"DB-EMBEBIDA-DE-DESARROLLO"
    assert (raiz / "data" / "imagenes_productos" / "1_abc.jpg").exists()


def test_config_ya_no_expone_la_migracion_de_data_embebido():
    assert not hasattr(config, "resolver_directorio_datos_frozen")


def test_directorio_imagenes_deriva_del_directorio_data():
    assert config.DIRECTORIO_IMAGENES_PRODUCTOS == config.DIRECTORIO_DATA / "imagenes_productos"


def _valor_de_config_en_modo(tmp_path, expresion: str, *, frozen: bool) -> str:
    """Importa una copia de `config.py` en un proceso aparte, con o sin
    `sys.frozen`, y devuelve el valor de `expresion` impreso."""
    raiz = tmp_path / "bundle"
    raiz.mkdir()
    shutil.copy(config.RAIZ_PROYECTO / "config.py", raiz / "config.py")
    local_appdata = tmp_path / "local"
    local_appdata.mkdir()
    marca = "sys.frozen = True; " if frozen else ""
    resultado = subprocess.run(
        [sys.executable, "-B", "-c", f"import sys; {marca}import config; print({expresion})"],
        cwd=raiz,
        env={**os.environ, "LOCALAPPDATA": str(local_appdata)},
        capture_output=True,
        text=True,
        check=True,
    )
    return resultado.stdout.strip()


def test_el_seed_del_catalogo_esta_deshabilitado_en_desarrollo(tmp_path):
    assert config.SEMBRAR_CATALOGO_INICIAL is False  # también en este proceso de tests
    assert _valor_de_config_en_modo(tmp_path, "config.SEMBRAR_CATALOGO_INICIAL", frozen=False) == "False"


def test_el_seed_del_catalogo_esta_habilitado_en_frozen(tmp_path):
    assert _valor_de_config_en_modo(tmp_path, "config.SEMBRAR_CATALOGO_INICIAL", frozen=True) == "True"


def test_la_ruta_del_catalogo_inicial_esta_en_db_seed():
    assert config.RUTA_CATALOGO_INICIAL == config.RAIZ_PROYECTO / "db" / "seed" / "catalogo_inicial.json"
    assert config.RUTA_CATALOGO_INICIAL.is_file()
