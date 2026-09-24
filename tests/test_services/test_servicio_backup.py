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

_PATRON_NOMBRE = re.compile(r"^KioscoApp_backup_\d{4}-\d{2}-\d{2}_\d{6}\.zip$")


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


# ---------------------------------------------------------------------------
# V1.1 -- B1: nombre con segundos y sin colisiones
# ---------------------------------------------------------------------------


def test_b1_el_nombre_lleva_segundos(datos_de_prueba, tmp_path):
    ruta_zip = modulo_backup.crear_backup(tmp_path / "backups", ControlEscrituras())

    assert re.match(r"^KioscoApp_backup_\d{4}-\d{2}-\d{2}_\d{6}\.zip$", ruta_zip.name)


def test_b1_dos_backups_con_el_mismo_nombre_no_se_pisan(datos_de_prueba, tmp_path):
    destino = tmp_path / "backups"
    nombre = "KioscoApp_backup_2026-01-10_101010.zip"

    primero = modulo_backup.crear_backup(destino, ControlEscrituras(), nombre_archivo=nombre)
    contenido_primero = primero.read_bytes()
    segundo = modulo_backup.crear_backup(destino, ControlEscrituras(), nombre_archivo=nombre)

    assert primero.name == nombre
    assert segundo.name == "KioscoApp_backup_2026-01-10_101010_2.zip"
    assert primero.read_bytes() == contenido_primero
    assert sorted(p.name for p in destino.glob("*.zip")) == [primero.name, segundo.name]


def test_b1_un_backup_manual_y_uno_automatico_en_el_mismo_segundo_coexisten(datos_de_prueba, tmp_path, monkeypatch):
    destino = tmp_path / "backups"

    class RelojCongelado(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 3, 5, 12, 0, 0)

    monkeypatch.setattr(modulo_backup, "datetime", RelojCongelado)

    manual = modulo_backup.crear_backup(destino, ControlEscrituras())
    automatico = modulo_backup.crear_backup(destino, ControlEscrituras())

    assert manual != automatico
    assert manual.exists() and automatico.exists()


def test_b1_los_nombres_anteriores_de_minutos_siguen_reconociendose_y_no_se_borran(datos_de_prueba, tmp_path):
    destino = tmp_path / "backups"
    viejo = _crear_archivo_backup_con_fecha(destino, datetime.now() - timedelta(hours=1))

    resultado = modulo_backup.ejecutar_backup_automatico_si_corresponde(
        destino, ControlEscrituras(), antiguedad_minima_horas=24
    )

    assert resultado is None  # el de minutos cuenta como "último backup"
    assert viejo.exists()


# ---------------------------------------------------------------------------
# V1.1 -- B2: retención
# ---------------------------------------------------------------------------


def _backup_falso(directorio: Path, nombre: str) -> Path:
    directorio.mkdir(parents=True, exist_ok=True)
    ruta = directorio / nombre
    ruta.write_bytes(b"zip-falso")
    return ruta


def _nombre_regular(dia: int) -> str:
    return f"KioscoApp_backup_2026-01-{dia:02d}_100000.zip"


def _nombre_preventivo(dia: int) -> str:
    return f"KioscoApp_backup_pre_migracion_2026-01-{dia:02d}_100000.zip"


def test_b2_conserva_los_mas_recientes_y_borra_los_anteriores(tmp_path):
    destino = tmp_path / "backups"
    rutas = [_backup_falso(destino, _nombre_regular(dia)) for dia in range(1, 11)]

    borrados = modulo_backup.aplicar_retencion(destino, rutas[-1], conservar=3)

    assert sorted(p.name for p in destino.glob("*.zip")) == [_nombre_regular(8), _nombre_regular(9), _nombre_regular(10)]
    assert len(borrados) == 7


def test_b2_nunca_borra_el_backup_recien_creado_aunque_no_sea_de_los_mas_nuevos(tmp_path):
    destino = tmp_path / "backups"
    for dia in range(2, 8):
        _backup_falso(destino, _nombre_regular(dia))
    recien_creado = _backup_falso(destino, _nombre_regular(1))  # el reloj retrocedió: parece el más viejo

    modulo_backup.aplicar_retencion(destino, recien_creado, conservar=2)

    assert recien_creado.exists()


def test_b2_un_limite_invalido_no_borra_todo(tmp_path):
    destino = tmp_path / "backups"
    rutas = [_backup_falso(destino, _nombre_regular(dia)) for dia in range(1, 5)]

    modulo_backup.aplicar_retencion(destino, rutas[-1], conservar=0, conservar_preventivos=0)

    assert rutas[-1].exists()
    assert len(list(destino.glob("*.zip"))) >= 1


def test_b2_no_toca_archivos_que_no_son_backups_reconocibles(tmp_path):
    destino = tmp_path / "backups"
    rutas = [_backup_falso(destino, _nombre_regular(dia)) for dia in range(1, 5)]
    ajenos = [
        _backup_falso(destino, "notas.txt"),
        _backup_falso(destino, "KioscoApp_backup_manual_importante.zip"),
        _backup_falso(destino, "otro_programa_2026-01-01.zip"),
    ]

    modulo_backup.aplicar_retencion(destino, rutas[-1], conservar=1)

    assert all(ajeno.exists() for ajeno in ajenos)


def test_b2_los_preventivos_tienen_su_propio_limite_y_no_compiten_con_los_regulares(tmp_path):
    destino = tmp_path / "backups"
    regulares = [_backup_falso(destino, _nombre_regular(dia)) for dia in range(1, 4)]
    preventivos = [_backup_falso(destino, _nombre_preventivo(dia)) for dia in range(1, 6)]

    modulo_backup.aplicar_retencion(destino, regulares[-1], conservar=3, conservar_preventivos=2)

    assert all(r.exists() for r in regulares)
    assert sorted(p.name for p in destino.glob("KioscoApp_backup_pre_*.zip")) == [
        _nombre_preventivo(4),
        _nombre_preventivo(5),
    ]


def test_b2_mezcla_nombres_de_minutos_y_de_segundos_ordenando_por_fecha(tmp_path):
    destino = tmp_path / "backups"
    viejo_minutos = _backup_falso(destino, "KioscoApp_backup_2026-01-01_1000.zip")
    medio_segundos = _backup_falso(destino, "KioscoApp_backup_2026-01-02_100000.zip")
    nuevo = _backup_falso(destino, "KioscoApp_backup_2026-01-03_100000.zip")

    modulo_backup.aplicar_retencion(destino, nuevo, conservar=2)

    assert not viejo_minutos.exists()
    assert medio_segundos.exists() and nuevo.exists()


def test_b2_un_fallo_al_borrar_no_interrumpe_ni_propaga(tmp_path, monkeypatch):
    destino = tmp_path / "backups"
    rutas = [_backup_falso(destino, _nombre_regular(dia)) for dia in range(1, 4)]

    def unlink_que_falla(self, *a, **k):
        raise PermissionError("archivo en uso")

    monkeypatch.setattr(Path, "unlink", unlink_que_falla)

    borrados = modulo_backup.aplicar_retencion(destino, rutas[-1], conservar=1)

    assert borrados == []
    assert all(r.exists() for r in rutas)


def test_b2_crear_backup_aplica_la_retencion_y_conserva_el_nuevo(datos_de_prueba, tmp_path, monkeypatch):
    destino = tmp_path / "backups"
    for dia in range(1, 6):
        _backup_falso(destino, _nombre_regular(dia))
    monkeypatch.setattr(modulo_backup, "BACKUPS_A_CONSERVAR", 3)
    # `aplicar_retencion` toma sus límites por defecto al definirse: se le pasan explícitos.
    original = modulo_backup.aplicar_retencion
    monkeypatch.setattr(
        modulo_backup, "aplicar_retencion", lambda d, r: original(d, r, conservar=3, conservar_preventivos=5)
    )

    nuevo = modulo_backup.crear_backup(destino, ControlEscrituras())

    assert nuevo.exists()
    assert len(list(destino.glob("KioscoApp_backup_2*.zip"))) == 3
