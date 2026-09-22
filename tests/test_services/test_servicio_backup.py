"""Tests de `services/servicio_backup.py`: backup consistente de
`kiosco.db` + `imagenes_productos/` en un único ZIP, coordinado con
`ControlEscrituras` para no capturar una escritura a mitad de camino."""

import re
import sqlite3
import threading
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import services.servicio_backup as modulo_backup
from services.control_escrituras import ControlEscrituras

_PATRON_NOMBRE = re.compile(r"^KioscoApp_backup_\d{4}-\d{2}-\d{2}_\d{4}\.zip$")


@pytest.fixture
def datos_de_prueba(tmp_path, monkeypatch):
    """Arma una DB real (con una fila identificable) y una carpeta de
    imágenes real, y redirige `servicio_backup` a usarlas."""
    ruta_db = tmp_path / "origen" / "kiosco.db"
    ruta_db.parent.mkdir(parents=True)
    conexion = sqlite3.connect(ruta_db)
    conexion.execute("CREATE TABLE productos (id INTEGER PRIMARY KEY, nombre TEXT)")
    conexion.execute("INSERT INTO productos (nombre) VALUES ('SENTINEL_BACKUP')")
    conexion.commit()
    conexion.close()

    dir_imagenes = tmp_path / "origen" / "imagenes_productos"
    dir_imagenes.mkdir()
    (dir_imagenes / "1_abc.jpg").write_bytes(b"CONTENIDO-IMAGEN-SENTINEL")

    monkeypatch.setattr(modulo_backup, "RUTA_BASE_DATOS", ruta_db)
    monkeypatch.setattr(modulo_backup, "DIRECTORIO_IMAGENES_PRODUCTOS", dir_imagenes)

    return ruta_db, dir_imagenes


def test_genera_zip_con_nombre_correcto(datos_de_prueba, tmp_path):
    destino = tmp_path / "backups"

    ruta_zip = modulo_backup.crear_backup(destino, ControlEscrituras())

    assert ruta_zip.parent == destino
    assert _PATRON_NOMBRE.match(ruta_zip.name), ruta_zip.name
    assert zipfile.is_zipfile(ruta_zip)


def test_zip_contiene_db_restaurable_con_integridad_valida(datos_de_prueba, tmp_path):
    destino = tmp_path / "backups"
    ruta_zip = modulo_backup.crear_backup(destino, ControlEscrituras())

    extraido = tmp_path / "extraido"
    with zipfile.ZipFile(ruta_zip) as zf:
        zf.extractall(extraido)

    conexion = sqlite3.connect(extraido / "kiosco.db")
    try:
        assert conexion.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        fila = conexion.execute("SELECT nombre FROM productos").fetchone()
        assert fila[0] == "SENTINEL_BACKUP"
    finally:
        conexion.close()


def test_zip_incluye_las_imagenes(datos_de_prueba, tmp_path):
    destino = tmp_path / "backups"
    ruta_zip = modulo_backup.crear_backup(destino, ControlEscrituras())

    with zipfile.ZipFile(ruta_zip) as zf:
        contenido = zf.read("imagenes_productos/1_abc.jpg")

    assert contenido == b"CONTENIDO-IMAGEN-SENTINEL"


def test_error_durante_la_copia_no_publica_zip_incompleto(datos_de_prueba, tmp_path, monkeypatch):
    destino = tmp_path / "backups"

    def copytree_falso(*_args, **_kwargs):
        raise OSError("fallo simulado copiando imagenes_productos")

    monkeypatch.setattr(modulo_backup.shutil, "copytree", copytree_falso)

    with pytest.raises(OSError):
        modulo_backup.crear_backup(destino, ControlEscrituras())

    archivos_publicados = list(destino.glob("*.zip")) if destino.exists() else []
    assert archivos_publicados == []


def test_temporales_limpiados_incluso_ante_error(datos_de_prueba, tmp_path, monkeypatch):
    destino = tmp_path / "backups"

    def copytree_falso(*_args, **_kwargs):
        raise OSError("fallo simulado")

    monkeypatch.setattr(modulo_backup.shutil, "copytree", copytree_falso)

    with pytest.raises(OSError):
        modulo_backup.crear_backup(destino, ControlEscrituras())

    restos = list(destino.iterdir()) if destino.exists() else []
    assert restos == []


def test_libera_el_turno_incluso_si_falla_la_copia(datos_de_prueba, tmp_path, monkeypatch):
    destino = tmp_path / "backups"
    control = ControlEscrituras()

    def copytree_falso(*_args, **_kwargs):
        raise OSError("fallo simulado")

    monkeypatch.setattr(modulo_backup.shutil, "copytree", copytree_falso)

    with pytest.raises(OSError):
        modulo_backup.crear_backup(destino, control)

    assert control.backup_activo is False
    assert control.escrituras_de_negocio_en_curso == 0


def test_una_escritura_de_negocio_normal_funciona_despues_de_un_backup_fallido(
    datos_de_prueba, tmp_path, monkeypatch
):
    destino = tmp_path / "backups"
    control = ControlEscrituras()

    def copytree_falso(*_args, **_kwargs):
        raise OSError("fallo simulado")

    monkeypatch.setattr(modulo_backup.shutil, "copytree", copytree_falso)
    with pytest.raises(OSError):
        modulo_backup.crear_backup(destino, control)

    # una escritura de negocio normal debe volver a funcionar
    assert control.permitir_escritura_de_negocio() is True
    control.finalizar_escritura_de_negocio()


def test_un_backup_posterior_puede_ejecutarse_tras_uno_fallido(datos_de_prueba, tmp_path, monkeypatch):
    destino = tmp_path / "backups"
    control = ControlEscrituras()

    def copytree_falso(*_args, **_kwargs):
        raise OSError("fallo simulado")

    monkeypatch.setattr(modulo_backup.shutil, "copytree", copytree_falso)
    with pytest.raises(OSError):
        modulo_backup.crear_backup(destino, control)

    monkeypatch.undo()  # restaura shutil.copytree real
    ruta_zip = modulo_backup.crear_backup(destino, control)

    assert zipfile.is_zipfile(ruta_zip)


def test_dos_backups_simultaneos_solo_uno_genera_el_zip(datos_de_prueba, tmp_path):
    destino = tmp_path / "backups"
    control = ControlEscrituras()
    resultados = {}

    def intentar(nombre):
        try:
            resultados[nombre] = modulo_backup.crear_backup(destino, control)
        except modulo_backup.ErrorBackup as error:
            resultados[nombre] = error

    # El primer hilo deja el backup "activo" (nunca llama finalizar_backup
    # en este test) para simular que el segundo llega mientras el primero
    # sigue en curso -- exactamente el escenario de dos requests casi
    # simultáneos.
    control.iniciar_backup()

    hilo_b = threading.Thread(target=intentar, args=("B",))
    hilo_b.start()
    hilo_b.join()

    assert isinstance(resultados["B"], modulo_backup.ErrorBackup)
    assert not list(destino.glob("*.zip")) if destino.exists() else True


# --- ejecutar_backup_automatico_si_corresponde ------------------------------
#
# Backup automático V1 (ver auditoría de distribución/backup): al iniciar la
# app, si el último backup real supera una antigüedad mínima, se genera uno
# nuevo llamando a `crear_backup` -- ningún mecanismo de generación nuevo,
# ninguna coordinación de concurrencia nueva.


def _crear_archivo_backup_con_fecha(directorio: Path, fecha: datetime) -> Path:
    """Crea un archivo con el nombre exacto que produce `crear_backup`,
    para simular que ya existe un backup real con determinada antigüedad."""
    directorio.mkdir(parents=True, exist_ok=True)
    ruta = directorio / f"KioscoApp_backup_{fecha.strftime('%Y-%m-%d_%H%M')}.zip"
    ruta.write_bytes(b"contenido-zip-de-prueba")
    return ruta


def test_automatico_sin_backups_previos_ejecuta_uno_nuevo(datos_de_prueba, tmp_path):
    destino = tmp_path / "backups"  # todavia no existe

    resultado = modulo_backup.ejecutar_backup_automatico_si_corresponde(
        destino, ControlEscrituras(), antiguedad_minima_horas=24
    )

    assert resultado is not None
    assert zipfile.is_zipfile(resultado)


def test_automatico_con_backup_reciente_no_ejecuta_uno_nuevo(datos_de_prueba, tmp_path):
    destino = tmp_path / "backups"
    _crear_archivo_backup_con_fecha(destino, datetime.now() - timedelta(hours=1))

    resultado = modulo_backup.ejecutar_backup_automatico_si_corresponde(
        destino, ControlEscrituras(), antiguedad_minima_horas=24
    )

    assert resultado is None
    assert len(list(destino.glob("*.zip"))) == 1


def test_automatico_con_backup_viejo_ejecuta_uno_nuevo(datos_de_prueba, tmp_path):
    destino = tmp_path / "backups"
    _crear_archivo_backup_con_fecha(destino, datetime.now() - timedelta(hours=48))

    resultado = modulo_backup.ejecutar_backup_automatico_si_corresponde(
        destino, ControlEscrituras(), antiguedad_minima_horas=24
    )

    assert resultado is not None
    assert len(list(destino.glob("*.zip"))) == 2


def test_automatico_con_varios_backups_identifica_correctamente_el_ultimo(datos_de_prueba, tmp_path):
    destino = tmp_path / "backups"
    _crear_archivo_backup_con_fecha(destino, datetime.now() - timedelta(hours=48))
    _crear_archivo_backup_con_fecha(destino, datetime.now() - timedelta(hours=1))  # el mas reciente

    resultado = modulo_backup.ejecutar_backup_automatico_si_corresponde(
        destino, ControlEscrituras(), antiguedad_minima_horas=24
    )

    assert resultado is None  # el mas reciente todavia no supera la antiguedad minima
    assert len(list(destino.glob("*.zip"))) == 2


def test_automatico_ignora_archivos_que_no_son_backups_reconocibles(datos_de_prueba, tmp_path):
    destino = tmp_path / "backups"
    destino.mkdir()
    (destino / "notas.txt").write_text("no es un backup")
    (destino / "KioscoApp_backup_fecha-invalida.zip").write_bytes(b"nombre con formato invalido")

    resultado = modulo_backup.ejecutar_backup_automatico_si_corresponde(
        destino, ControlEscrituras(), antiguedad_minima_horas=24
    )

    # ningun archivo presente es un backup reconocible -> se trata como si
    # nunca hubiera existido uno, y se ejecuta.
    assert resultado is not None


def test_automatico_error_durante_la_creacion_no_propaga_y_queda_registrado(
    datos_de_prueba, tmp_path, monkeypatch, caplog
):
    destino = tmp_path / "backups"

    def copytree_falso(*_args, **_kwargs):
        raise OSError("fallo simulado copiando imagenes_productos")

    monkeypatch.setattr(modulo_backup.shutil, "copytree", copytree_falso)

    with caplog.at_level("ERROR", logger=modulo_backup.logger.name):
        resultado = modulo_backup.ejecutar_backup_automatico_si_corresponde(
            destino, ControlEscrituras(), antiguedad_minima_horas=24
        )

    assert resultado is None
    assert caplog.records, "el fallo debe quedar registrado por logging"
    assert not list(destino.glob("*.zip")) if destino.exists() else True


def test_automatico_no_ejecuta_si_ya_hay_un_backup_manual_en_curso(datos_de_prueba, tmp_path, caplog):
    destino = tmp_path / "backups"
    control = ControlEscrituras()
    control.iniciar_backup()  # simula un backup manual ya en curso en este mismo proceso

    with caplog.at_level("WARNING", logger=modulo_backup.logger.name):
        resultado = modulo_backup.ejecutar_backup_automatico_si_corresponde(
            destino, control, antiguedad_minima_horas=24
        )

    assert resultado is None
    assert caplog.records
    assert not list(destino.glob("*.zip")) if destino.exists() else True
