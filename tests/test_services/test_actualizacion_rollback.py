"""V1.15-F: actualización controlada y rollback programa + base.

Invariantes 8 y 9: el rollback usa la base compatible con la versión anterior
(su backup PRE_UPDATE) y antes protege el estado actual. Programas simulados
(carpeta con KioscoApp.exe, LEEME.txt y migraciones) sobre el esquema real.
"""

import shutil
import sqlite3

import pytest

import config
import db.conexion as modulo_conexion
import services.servicio_backup as modulo_backup
import services.servicio_imagenes as modulo_imagenes
import services.servicio_restore as modulo_restore
from domain.backup import TIPO_SEGURIDAD
from excepciones import ErrorActualizacion, ErrorBackup
from services import formato_backup, registro_versiones, servicio_actualizacion
from services.control_escrituras import ControlEscrituras
from version import VERSION

_REALES = sorted(config.DIRECTORIO_MIGRACIONES.glob("*.sql"))
_VIEJAS = 38
_VERSION_VIEJA = "1.13.9"


def _programa(carpeta, version, migraciones):
    (carpeta / "_internal" / "db" / "migraciones").mkdir(parents=True)
    (carpeta / "KioscoApp.exe").write_bytes(f"EXE {version}".encode())
    (carpeta / "LEEME.txt").write_text(f"KioscoApp - Guia rapida\n=======\nTwinx Express {version}\n", encoding="utf-8")
    for ruta in migraciones:
        shutil.copy(ruta, carpeta / "_internal" / "db" / "migraciones" / ruta.name)
    return carpeta


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    datos = tmp_path / "KioscoApp" / "data"
    (datos / "imagenes_productos").mkdir(parents=True)
    migraciones = tmp_path / "migraciones_en_uso"
    migraciones.mkdir()
    for ruta in _REALES[:_VIEJAS]:
        shutil.copy(ruta, migraciones / ruta.name)
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", datos / "kiosco.db")
    monkeypatch.setattr(modulo_conexion, "DIRECTORIO_MIGRACIONES", migraciones)
    monkeypatch.setattr(modulo_imagenes, "DIRECTORIO_IMAGENES_PRODUCTOS", datos / "imagenes_productos")
    monkeypatch.setattr(modulo_restore, "DIRECTORIO_DATA", datos)
    monkeypatch.setattr(config, "DIRECTORIO_DATA", datos)

    # Versión vieja instalada, usando su base.
    modulo_conexion.inicializar_base_datos()
    _insertar_producto("VIEJO", 10)
    instalacion = _programa(tmp_path / "Programas" / "KioscoApp", _VERSION_VIEJA, _REALES[:_VIEJAS])
    # El técnico trae la versión nueva en un pendrive; el código que corre es el nuevo.
    nuevo = _programa(tmp_path / "Pendrive" / "KioscoApp", VERSION, _REALES)
    for ruta in _REALES[_VIEJAS:]:
        shutil.copy(ruta, migraciones / ruta.name)
    return {"datos": datos, "instalacion": instalacion, "nuevo": nuevo, "migraciones": migraciones}


def _insertar_producto(codigo, stock):
    with modulo_conexion.obtener_conexion() as conexion:
        conexion.execute(
            "INSERT INTO productos (codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos, stock_actual)"
            " VALUES (?, ?, 100, 200, ?)",
            (codigo, codigo, stock),
        )


def _codigos(ruta_db):
    conexion = sqlite3.connect(ruta_db)
    try:
        return sorted(f[0] for f in conexion.execute("SELECT codigo_barras FROM productos"))
    finally:
        conexion.close()


def _aplicadas(ruta_db):
    conexion = sqlite3.connect(ruta_db)
    try:
        return [f[0] for f in conexion.execute("SELECT nombre_archivo FROM schema_migraciones ORDER BY 1")]
    finally:
        conexion.close()


def _version_instalada(entorno):
    return servicio_actualizacion.version_de_programa(entorno["instalacion"])


def _actualizar(entorno, en_uso=False):
    return servicio_actualizacion.actualizar(entorno["instalacion"], entorno["nuevo"], en_uso)


def test_actualizacion_completa_crea_pre_update_archiva_y_migra(entorno):
    resultado = _actualizar(entorno)

    info = formato_backup.validar_backup(resultado.backup_pre_update)
    assert len(info.migraciones) == _VIEJAS
    assert info.manifiesto.version_destino == VERSION
    assert _version_instalada(entorno) == VERSION
    assert _aplicadas(entorno["datos"] / "kiosco.db") == [r.name for r in _REALES]
    registro = registro_versiones.leer()
    assert registro.activa == VERSION
    vieja = registro.buscar(_VERSION_VIEJA)
    assert vieja.backup_pre_update == resultado.backup_pre_update.name
    assert len(vieja.migraciones) == _VIEJAS
    assert (config.DIRECTORIO_VERSIONES / _VERSION_VIEJA / "KioscoApp.exe").is_file()
    assert (config.DIRECTORIO_VERSIONES / VERSION / "KioscoApp.exe").is_file()
    # el arranque posterior no necesita otro pre-update
    assert modulo_backup.migrar_base_datos_con_backup_preventivo(modulo_backup.DIRECTORIO_BACKUPS, ControlEscrituras()) is None


def test_rollback_vuelve_programa_y_base_compatibles_y_protege_el_estado_actual(entorno):
    _actualizar(entorno)
    _insertar_producto("POST_UPDATE", 3)
    programa_actual = config.DIRECTORIO_VERSIONES / VERSION

    resultado = servicio_actualizacion.rollback(_VERSION_VIEJA, programa_actual, False)

    db = entorno["datos"] / "kiosco.db"
    assert _version_instalada(entorno) == _VERSION_VIEJA
    assert len(_aplicadas(db)) == _VIEJAS
    assert _codigos(db) == ["VIEJO"]
    seguridad = formato_backup.validar_backup(resultado.backup_seguridad)
    assert seguridad.manifiesto.tipo == TIPO_SEGURIDAD
    assert len(seguridad.migraciones) == len(_REALES)  # el estado actualizado, a salvo
    conexion = sqlite3.connect(db)
    try:
        assert conexion.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conexion.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conexion.execute("SELECT accion FROM auditoria ORDER BY id DESC LIMIT 1").fetchone()[0] == "ROLLBACK"
    finally:
        conexion.close()
    assert registro_versiones.leer().activa == _VERSION_VIEJA


def test_actualizar_con_la_aplicacion_abierta_no_toca_nada(entorno):
    with pytest.raises(ErrorActualizacion, match="abierto"):
        _actualizar(entorno, en_uso=True)

    assert _version_instalada(entorno) == _VERSION_VIEJA
    assert not registro_versiones.ruta_registro().exists()


def test_si_el_pre_update_falla_la_actualizacion_no_se_hace(entorno, monkeypatch):
    def falla(*_a, **_k):
        raise ErrorBackup("No hay espacio suficiente en el disco para guardar el backup.")

    monkeypatch.setattr(modulo_backup, "crear_backup", falla)

    with pytest.raises(ErrorActualizacion, match="NO se hizo"):
        _actualizar(entorno)

    assert _version_instalada(entorno) == _VERSION_VIEJA
    assert len(_aplicadas(entorno["datos"] / "kiosco.db")) == _VIEJAS


def test_migracion_fallida_vuelve_sola_a_la_version_anterior(entorno):
    (entorno["migraciones"] / "999_rota.sql").write_text("ESTO NO ES SQL;", encoding="utf-8")

    with pytest.raises(ErrorActualizacion, match="volvió automáticamente"):
        _actualizar(entorno)

    assert _version_instalada(entorno) == _VERSION_VIEJA
    assert len(_aplicadas(entorno["datos"] / "kiosco.db")) == _VIEJAS
    assert _codigos(entorno["datos"] / "kiosco.db") == ["VIEJO"]


def test_no_se_actualiza_desde_dentro_de_la_instalacion(entorno):
    with pytest.raises(ErrorActualizacion, match="carpeta de la versión nueva"):
        servicio_actualizacion.actualizar(entorno["instalacion"], entorno["instalacion"], False)


def test_rollback_con_pre_update_corrupto_no_cambia_nada(entorno):
    resultado = _actualizar(entorno)
    datos = bytearray(resultado.backup_pre_update.read_bytes())
    datos[300] ^= 0xFF
    resultado.backup_pre_update.write_bytes(bytes(datos))

    with pytest.raises(ErrorActualizacion, match="no es válido"):
        servicio_actualizacion.rollback(_VERSION_VIEJA, config.DIRECTORIO_VERSIONES / VERSION, False)

    assert _version_instalada(entorno) == VERSION
    assert len(_aplicadas(entorno["datos"] / "kiosco.db")) == len(_REALES)


def test_rollback_a_una_version_que_no_conoce_el_esquema_del_backup_no_cambia_nada(entorno):
    _actualizar(entorno)
    registro = registro_versiones.leer()
    vieja = registro.buscar(_VERSION_VIEJA)
    registro.reemplazar(vieja.__class__(**{**vieja.__dict__, "migraciones": vieja.migraciones[:-1]}))
    registro_versiones.guardar(registro)

    with pytest.raises(ErrorActualizacion, match="no se cambió nada"):
        servicio_actualizacion.rollback(_VERSION_VIEJA, config.DIRECTORIO_VERSIONES / VERSION, False)

    assert _version_instalada(entorno) == VERSION
    assert len(_aplicadas(entorno["datos"] / "kiosco.db")) == len(_REALES)


def test_rollback_desde_la_carpeta_instalada_se_rechaza(entorno):
    _actualizar(entorno)

    with pytest.raises(ErrorActualizacion, match="copia archivada"):
        servicio_actualizacion.rollback(_VERSION_VIEJA, entorno["instalacion"], False)


def test_rollback_a_version_desconocida_lista_las_disponibles(entorno):
    _actualizar(entorno)

    with pytest.raises(ErrorActualizacion, match=_VERSION_VIEJA):
        servicio_actualizacion.rollback("0.0.1", config.DIRECTORIO_VERSIONES / VERSION, False)


def test_el_pre_update_de_una_version_conservada_no_lo_borra_la_retencion(entorno):
    resultado = _actualizar(entorno)
    for _ in range(5):  # más que los 3 pre-update que conserva la familia
        modulo_backup.crear_backup(modulo_backup.DIRECTORIO_BACKUPS, ControlEscrituras(), tipo="PRE_UPDATE")

    assert resultado.backup_pre_update.exists()


def test_se_conservan_la_actual_y_dos_anteriores(entorno, tmp_path):
    registro = registro_versiones.Registro(instalacion=str(entorno["instalacion"]), activa="1.13.9")
    for i, version in enumerate(["1.10.0", "1.11.0", "1.12.0"]):
        carpeta = _programa(config.DIRECTORIO_VERSIONES / version, version, _REALES[:1])
        registro.reemplazar(registro_versiones.VersionConservada(version, str(carpeta), ("001",), f"2026-0{i + 1}-01", "x.zip"))
    registro_versiones.guardar(registro)

    _actualizar(entorno)

    conservadas = {v.version for v in registro_versiones.leer().versiones}
    assert conservadas == {VERSION, _VERSION_VIEJA, "1.12.0"}
    assert not (config.DIRECTORIO_VERSIONES / "1.10.0").exists()


def test_registro_ilegible_protege_todos_los_pre_update(entorno):
    registro_versiones.ruta_registro().parent.mkdir(parents=True, exist_ok=True)
    registro_versiones.ruta_registro().write_text("{roto", encoding="utf-8")

    assert registro_versiones.backups_protegidos() is None
