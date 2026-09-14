"""Tests de la resolución de rutas de datos persistentes (`config.py`).

Cubren la migración entre el directorio `data/` embebido en el build de
PyInstaller (efímero: PyInstaller lo destruye en cada rebuild, ver
auditoría de distribución) y el directorio persistente fuera del bundle
(`%LOCALAPPDATA%\\KioscoApp\\data` en modo frozen). El modo desarrollo no
se ve afectado: sigue usando `RAIZ_PROYECTO/data`.
"""

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


def test_primera_ejecucion_crea_destino_vacio(tmp_path):
    """Caso 1: ni origen ni destino existen -> se crea el destino vacío."""
    origen = tmp_path / "origen"
    destino = tmp_path / "destino"

    resultado = config.resolver_directorio_datos_frozen(dir_origen=origen, dir_destino=destino)

    assert resultado == destino
    assert destino.is_dir()
    assert not origen.exists()


def test_migracion_copia_db_e_imagenes_sin_borrar_origen(tmp_path):
    """Caso 2: solo existe el origen -> se copia completo al destino, intacto."""
    origen = tmp_path / "origen"
    (origen / "imagenes_productos").mkdir(parents=True)
    (origen / "kiosco.db").write_bytes(b"contenido-db-real")
    (origen / "imagenes_productos" / "1_abc.jpg").write_bytes(b"contenido-imagen")
    destino = tmp_path / "destino"

    resultado = config.resolver_directorio_datos_frozen(dir_origen=origen, dir_destino=destino)

    assert resultado == destino
    assert (destino / "kiosco.db").read_bytes() == b"contenido-db-real"
    assert (destino / "imagenes_productos" / "1_abc.jpg").read_bytes() == b"contenido-imagen"
    # el origen nunca se borra
    assert (origen / "kiosco.db").read_bytes() == b"contenido-db-real"
    assert (origen / "imagenes_productos" / "1_abc.jpg").exists()


def test_destino_existente_no_se_sobrescribe(tmp_path):
    """Caso 3: solo existe el destino -> se usa tal cual, sin migrar nada."""
    origen_inexistente = tmp_path / "origen"
    destino = tmp_path / "destino"
    destino.mkdir()
    (destino / "kiosco.db").write_bytes(b"db-real-ya-migrada")

    resultado = config.resolver_directorio_datos_frozen(
        dir_origen=origen_inexistente, dir_destino=destino
    )

    assert resultado == destino
    assert (destino / "kiosco.db").read_bytes() == b"db-real-ya-migrada"


def test_conflicto_origen_y_destino_produce_error_fatal(tmp_path):
    """Caso 4: existen ambos -> error fatal, sin sobrescribir ni fusionar."""
    origen = tmp_path / "origen"
    origen.mkdir()
    (origen / "kiosco.db").write_bytes(b"db-vieja-del-bundle")

    destino = tmp_path / "destino"
    destino.mkdir()
    (destino / "kiosco.db").write_bytes(b"db-real-ya-migrada")

    with pytest.raises(config.ErrorMigracionDatos):
        config.resolver_directorio_datos_frozen(dir_origen=origen, dir_destino=destino)

    # ninguno de los dos se toca ante el conflicto
    assert (destino / "kiosco.db").read_bytes() == b"db-real-ya-migrada"
    assert (origen / "kiosco.db").read_bytes() == b"db-vieja-del-bundle"


def test_directorio_imagenes_deriva_del_directorio_data():
    assert config.DIRECTORIO_IMAGENES_PRODUCTOS == config.DIRECTORIO_DATA / "imagenes_productos"
