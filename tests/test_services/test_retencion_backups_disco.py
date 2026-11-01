"""V1.15-C: retención sobre la carpeta real de backups.

Invariantes: la retención nunca borra archivos desconocidos (6) ni un
pre-update protegido como si fuera diario (7); si falla, el backup recién
creado sigue siendo válido.
"""

import zipfile
from datetime import datetime, timedelta

import pytest

import db.conexion as modulo_conexion
import services.servicio_backup as modulo_backup
from domain.backup import TIPO_CIERRE_CAJA, TIPO_MANUAL, TIPO_PRE_UPDATE
from domain.retencion_backups import PoliticaRetencion
from services import formato_backup
from services.control_escrituras import ControlEscrituras


@pytest.fixture
def entorno(base_datos_temporal, tmp_path, monkeypatch):
    destino = tmp_path / "backups"
    monkeypatch.setattr(modulo_backup, "DIRECTORIO_BACKUPS", destino)
    return destino


def _crear(destino, fecha, tipo=TIPO_CIERRE_CAJA):
    return modulo_backup.crear_backup(destino, ControlEscrituras(), tipo=tipo, ahora=fecha)


def _nombres(destino):
    return sorted(p.name for p in destino.iterdir())


def test_crear_backups_durante_20_dias_conserva_14_diarios(entorno, monkeypatch):
    monkeypatch.setattr(modulo_backup, "POLITICA_RETENCION", PoliticaRetencion(semanales=0, mensuales=0))
    inicio = datetime(2026, 3, 1, 21, 0, 0)
    rutas = [_crear(entorno, inicio + timedelta(days=i)) for i in range(20)]

    assert {r.name for r in rutas[-14:]} == set(_nombres(entorno))


def test_no_borra_archivos_ajenos_ni_con_nombre_parecido(entorno):
    entorno.mkdir()
    ajenos = {
        "notas.txt": b"x",
        "KioscoApp_backup_cualquier_cosa.zip": b"x",
        "KioscoApp_backup_2020-01-01_100000.zip.bak": b"x",
        "otro_backup_2020-01-01_100000.zip": b"x",
    }
    for nombre, contenido in ajenos.items():
        (entorno / nombre).write_bytes(contenido)
    (entorno / "subcarpeta").mkdir()
    (entorno / "subcarpeta" / "KioscoApp_backup_2020-01-01_100000.zip").write_bytes(b"x")

    nuevo = _crear(entorno, datetime(2026, 1, 1, 10))
    modulo_backup.aplicar_retencion(entorno, nuevo, PoliticaRetencion(0, 0, 0, 0, 0, 0))

    assert set(ajenos) <= set(_nombres(entorno))
    assert (entorno / "subcarpeta" / "KioscoApp_backup_2020-01-01_100000.zip").exists()
    assert nuevo.exists()


def test_backup_legado_corrupto_con_nombre_valido_no_se_borra(entorno):
    entorno.mkdir()
    corrupto = entorno / "KioscoApp_backup_2020-01-01_100000.zip"
    corrupto.write_bytes(b"no es un zip")

    nuevo = _crear(entorno, datetime(2026, 1, 1, 10))
    modulo_backup.aplicar_retencion(entorno, nuevo, PoliticaRetencion(0, 0, 0, 0, 0, 0))

    assert corrupto.exists()


def test_backup_legado_valido_y_viejo_si_entra_en_la_retencion(entorno, tmp_path):
    entorno.mkdir()
    legado = entorno / "KioscoApp_backup_2020-01-01_1000.zip"
    with zipfile.ZipFile(legado, "w") as zf:
        zf.write(modulo_conexion.RUTA_BASE_DATOS, "kiosco.db")
        zf.writestr("imagenes_productos/", "")

    nuevo = _crear(entorno, datetime(2026, 1, 1, 10))
    borrados = modulo_backup.aplicar_retencion(entorno, nuevo, PoliticaRetencion(diarios=1, semanales=0, mensuales=0))

    assert borrados == [legado]


def test_el_recien_creado_nunca_se_borra_aunque_el_reloj_haya_retrocedido(entorno):
    for dia in range(1, 6):
        _crear(entorno, datetime(2026, 6, dia, 21))
    atrasado = _crear(entorno, datetime(2025, 1, 1, 21))  # reloj de la PC atrasado

    modulo_backup.aplicar_retencion(entorno, atrasado, PoliticaRetencion(diarios=2, semanales=0, mensuales=0))

    assert atrasado.exists()


def test_pre_update_y_manual_no_se_borran_como_diarios(entorno):
    pre = [_crear(entorno, datetime(2026, 1, d, 8), TIPO_PRE_UPDATE) for d in (1, 2)]
    manual = _crear(entorno, datetime(2026, 1, 3, 9), TIPO_MANUAL)
    for dia in range(4, 30):
        _crear(entorno, datetime(2026, 1, dia, 21))

    nombres = set(_nombres(entorno))

    assert {p.name for p in pre} <= nombres
    assert manual.name in nombres


def test_pre_update_protegido_sobrevive_a_la_familia(entorno, monkeypatch):
    real = modulo_backup.aplicar_retencion
    monkeypatch.setattr(modulo_backup, "aplicar_retencion", lambda *_a, **_k: [])
    pres = [_crear(entorno, datetime(2026, 1, d, 8), TIPO_PRE_UPDATE) for d in range(1, 6)]
    monkeypatch.setattr(modulo_backup, "aplicar_retencion", real)

    modulo_backup.aplicar_retencion(entorno, pres[-1], protegidos=frozenset({pres[0].name}))

    assert pres[0].exists()
    assert not pres[1].exists()


def test_la_clasificacion_usa_el_manifiesto_y_no_el_nombre(entorno):
    """Un pre-update renombrado como diario sigue siendo pre-update."""
    pre = _crear(entorno, datetime(2026, 2, 1, 8), TIPO_PRE_UPDATE)
    disfrazado = pre.with_name("KioscoApp_backup_2026-02-01_080000.zip")
    pre.rename(disfrazado)

    assert modulo_backup.clasificar_backup(disfrazado).tipo == TIPO_PRE_UPDATE


def test_un_fallo_al_borrar_no_invalida_el_backup_nuevo(entorno, monkeypatch):
    viejo = _crear(entorno, datetime(2026, 1, 1, 21))
    original = type(viejo).unlink

    def unlink_que_falla(self, *args, **kwargs):
        if self.name == viejo.name:
            raise PermissionError("archivo en uso")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(type(viejo), "unlink", unlink_que_falla)

    nuevo = _crear(entorno, datetime(2026, 1, 2, 21))
    borrados = modulo_backup.aplicar_retencion(entorno, nuevo, PoliticaRetencion(diarios=1, semanales=0, mensuales=0))

    assert borrados == []
    assert viejo.exists()
    assert formato_backup.validar_backup(nuevo).manifiesto is not None


def test_el_backup_de_cierre_cuenta_como_ultimo_backup_para_el_automatico(entorno):
    _crear(entorno, datetime.now() - timedelta(hours=1))

    assert modulo_backup.ejecutar_backup_automatico_si_corresponde(entorno, ControlEscrituras()) is None
