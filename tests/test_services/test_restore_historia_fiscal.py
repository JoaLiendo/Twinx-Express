"""RC4: un restore o un rollback nunca hace desaparecer en silencio historia fiscal ya emitida o
potencialmente emitida (CAE, INCIERTO/ENVIANDO, CAEA otorgado o pendiente de informar).

La comparación se hace contra la base REAL que trae el backup, nunca contra lo que declara su
manifiesto: un backup manipulado o sin manifiesto no puede saltar la protección."""

import shutil
import sqlite3
import zipfile

import pytest

import config
import db.conexion as modulo_conexion
import services.servicio_restore as modulo_restore
from db.repositorios import caja as repositorio_caja
from domain.caja import MovimientoCaja
from excepciones import ErrorActualizacion, ErrorRestore, RestoreConHistoriaFiscalError
from services import servicio_actualizacion, servicio_backup
from services.control_escrituras import ControlEscrituras
from services.fiscal import servicio_caea, servicio_parametros
from tests import utilidades_fiscales as uf
from version import VERSION

_REALES = sorted(config.DIRECTORIO_MIGRACIONES.glob("*.sql"))
_MIGRACIONES_V115 = 40  # 001..040: la última versión sin facturación ARCA
_VERSION_V115 = "1.15.0"


def _abrir_caja():
    repositorio_caja.registrar_movimiento(MovimientoCaja(tipo="APERTURA", monto_centavos=0))


@pytest.fixture
def instalacion(tmp_path, monkeypatch):
    datos = tmp_path / "KioscoApp" / "data"
    (datos / "imagenes_productos").mkdir(parents=True)
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", datos / "kiosco.db")
    monkeypatch.setattr(modulo_restore, "DIRECTORIO_DATA", datos)
    modulo_conexion.inicializar_base_datos()
    _abrir_caja()
    return datos


def _backup():
    return servicio_backup.crear_backup(servicio_backup.DIRECTORIO_BACKUPS, ControlEscrituras())


def _comprobante(numero, estado="AUTORIZADO"):
    venta = uf.vender([(uf.producto(12100, uf.IVA_21), 1)])
    comprobante_id = uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))
    if estado == "AUTORIZADO":
        uf.autorizar(comprobante_id, numero)
    else:
        uf.transicionar(comprobante_id, "ENVIANDO", numero=numero, fecha_comprobante="2026-11-01")
        if estado == "INCIERTO":
            uf.transicionar(comprobante_id, "INCIERTO")
    return comprobante_id


def _cantidad_comprobantes(datos):
    conexion = sqlite3.connect(datos / "kiosco.db")
    try:
        return conexion.execute("SELECT COUNT(*) FROM comprobantes_fiscales").fetchone()[0]
    finally:
        conexion.close()


def _poner_centinela(valor):
    with modulo_conexion.obtener_conexion() as conexion:
        conexion.execute(
            "INSERT INTO configuracion (clave, valor) VALUES ('centinela', ?) "
            "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
            (valor,),
        )


def _centinela(datos):
    conexion = sqlite3.connect(datos / "kiosco.db")
    try:
        fila = conexion.execute("SELECT valor FROM configuracion WHERE clave = 'centinela'").fetchone()
        return fila[0] if fila else None
    finally:
        conexion.close()


def _assert_bloqueado_sin_tocar_nada(ruta_backup, datos, comprobantes_antes):
    backups_antes = sorted(p.name for p in servicio_backup.DIRECTORIO_BACKUPS.glob("*.zip"))
    with pytest.raises(RestoreConHistoriaFiscalError) as error:
        modulo_restore.restaurar_desde_backup(ruta_backup)
    assert "anterior a comprobantes fiscales existentes" in str(error.value)
    assert _cantidad_comprobantes(datos) == comprobantes_antes
    assert _centinela(datos) == "ACTUAL"
    # Ni siquiera el backup de seguridad: el bloqueo ocurre antes de tocar nada.
    assert sorted(p.name for p in servicio_backup.DIRECTORIO_BACKUPS.glob("*.zip")) == backups_antes
    assert not list(datos.parent.glob(".kioscoapp_*"))
    return str(error.value)


# --- Restore -----------------------------------------------------------------------------------------


def test_sin_actividad_fiscal_el_restore_sigue_permitido(instalacion):
    _poner_centinela("ORIGINAL")
    ruta = _backup()
    _poner_centinela("MODIFICADO")

    modulo_restore.restaurar_desde_backup(ruta)

    assert _centinela(instalacion) == "ORIGINAL"


def test_un_rechazado_o_pendiente_no_es_historia_que_bloquee(instalacion):
    ruta = _backup()
    venta = uf.vender([(uf.producto(12100, uf.IVA_21), 1)])
    uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))  # PENDIENTE: nunca salió hacia ARCA

    modulo_restore.restaurar_desde_backup(ruta)

    assert _cantidad_comprobantes(instalacion) == 0


def test_cae_actual_tambien_presente_en_el_backup_permite_restaurar(instalacion):
    _comprobante(1)
    _poner_centinela("ORIGINAL")
    ruta = _backup()
    _poner_centinela("MODIFICADO")

    modulo_restore.restaurar_desde_backup(ruta)

    assert _centinela(instalacion) == "ORIGINAL" and _cantidad_comprobantes(instalacion) == 1


def test_cae_actual_ausente_del_backup_bloquea(instalacion):
    ruta = _backup()
    _comprobante(1)
    _poner_centinela("ACTUAL")

    mensaje = _assert_bloqueado_sin_tocar_nada(ruta, instalacion, 1)
    assert "1 comprobante(s) autorizado(s)" in mensaje
    assert "No se restauró nada" in mensaje


def test_incierto_ausente_del_backup_bloquea(instalacion):
    ruta = _backup()
    _comprobante(1, estado="INCIERTO")
    _poner_centinela("ACTUAL")

    mensaje = _assert_bloqueado_sin_tocar_nada(ruta, instalacion, 1)
    assert "resultado incierto" in mensaje


def test_enviando_ausente_del_backup_bloquea(instalacion):
    ruta = _backup()
    _comprobante(1, estado="ENVIANDO")
    _poner_centinela("ACTUAL")

    _assert_bloqueado_sin_tocar_nada(ruta, instalacion, 1)


def test_backup_con_el_comprobante_todavia_incierto_no_cubre_al_autorizado(instalacion):
    comprobante_id = _comprobante(1, estado="INCIERTO")
    ruta = _backup()
    uf.transicionar(
        comprobante_id, "AUTORIZADO", resultado="A", codigo_autorizacion="71234567890123", vencimiento_autorizacion="2026-11-11"
    )
    _poner_centinela("ACTUAL")

    _assert_bloqueado_sin_tocar_nada(ruta, instalacion, 1)


@pytest.fixture
def contingencia(instalacion, arca_falso):
    owner = uf.crear_owner()
    uf.guardar_perfil_validado(punto_venta_caea=4, contingencia_caea=True)
    servicio_parametros.refrescar(arca_falso)
    return owner


def test_caea_otorgado_ausente_del_backup_bloquea(instalacion, contingencia):
    ruta = _backup()
    servicio_caea.solicitar(contingencia)
    _poner_centinela("ACTUAL")

    mensaje = _assert_bloqueado_sin_tocar_nada(ruta, instalacion, 0)
    assert "CAEA otorgado" in mensaje


def test_comprobante_caea_pendiente_de_informar_ausente_bloquea(instalacion, contingencia, arca_falso):
    servicio_caea.solicitar(contingencia)
    ruta = _backup()  # el CAEA ya estaba; el comprobante emitido bajo CAEA todavía no
    arca_falso.caido = True
    venta = uf.vender([(uf.producto(12100, uf.IVA_21), 1)])
    comprobante = servicio_caea.fiscalizar_con_contingencia(venta.id, contingencia)
    assert comprobante.estado == "CAEA_PENDIENTE_INFORMAR"
    _poner_centinela("ACTUAL")
    comprobantes = _cantidad_comprobantes(instalacion)

    mensaje = _assert_bloqueado_sin_tocar_nada(ruta, instalacion, comprobantes)
    assert "pendiente(s) de informar" in mensaje


def _zip_con_base(ruta_zip, ruta_db, manifiesto=None):
    with zipfile.ZipFile(ruta_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(ruta_db, "kiosco.db")
        zf.writestr("imagenes_productos/", "")
        if manifiesto is not None:
            zf.writestr("manifiesto.json", manifiesto)
    return ruta_zip


def test_backup_sin_manifiesto_no_salta_la_proteccion(instalacion, tmp_path):
    viejo = _backup()
    _comprobante(1)
    _poner_centinela("ACTUAL")
    extraido = tmp_path / "extraido"
    with zipfile.ZipFile(viejo) as zf:
        zf.extract("kiosco.db", extraido)

    # Un ZIP "estilo V1.14" (sin manifiesto) es válido: la protección mira la base, no el manifiesto.
    manipulado = _zip_con_base(tmp_path / "sin_manifiesto.zip", extraido / "kiosco.db")

    _assert_bloqueado_sin_tocar_nada(manipulado, instalacion, 1)


def test_backup_con_cae_falsificado_para_el_mismo_numero_no_salta_la_proteccion(instalacion, tmp_path):
    _comprobante(1)
    _poner_centinela("ACTUAL")
    falsa = tmp_path / "falsa.db"
    shutil.copy(instalacion / "kiosco.db", falsa)
    conexion = sqlite3.connect(falsa)
    try:
        with conexion:
            # Quien manipula la copia puede saltear los triggers: igual no se lo cree.
            conexion.execute("DROP TRIGGER trg_comprobantes_fiscales_autorizado_inmutable")
            conexion.execute("UPDATE comprobantes_fiscales SET codigo_autorizacion = '79999999999999'")
    finally:
        conexion.close()
    manipulado = _zip_con_base(tmp_path / "falsificado.zip", falsa)

    _assert_bloqueado_sin_tocar_nada(manipulado, instalacion, 1)


def test_base_actual_ilegible_sin_certificado_fiscal_se_restaura_como_hoy(instalacion):
    _poner_centinela("ORIGINAL")
    ruta = _backup()
    (instalacion / "kiosco.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 50)

    resultado = modulo_restore.restaurar_desde_backup(ruta)

    assert resultado.datos_reemplazados is not None
    assert _centinela(instalacion) == "ORIGINAL"


def test_base_actual_ilegible_con_certificado_fiscal_bloquea(instalacion, fiscal_aislado):
    ruta = _backup()
    dañada = b"SQLite format 3\x00" + b"\x00" * 50
    (instalacion / "kiosco.db").write_bytes(dañada)
    (fiscal_aislado / "homologacion").mkdir(parents=True)
    (fiscal_aislado / "homologacion" / "certificado.pem").write_bytes(b"CERT")

    with pytest.raises(RestoreConHistoriaFiscalError, match="No se pudo leer la historia fiscal"):
        modulo_restore.restaurar_desde_backup(ruta)

    assert (instalacion / "kiosco.db").read_bytes() == dañada


def test_el_bloqueo_es_un_error_de_restore_que_el_lanzador_muestra_tal_cual(instalacion):
    ruta = _backup()
    _comprobante(1)
    with pytest.raises(ErrorRestore):
        modulo_restore.restaurar_desde_backup(ruta)


# --- Rollback a una versión sin facturación (V1.15) ----------------------------------------------------


def _programa(carpeta, version, migraciones):
    (carpeta / "_internal" / "db" / "migraciones").mkdir(parents=True)
    (carpeta / "KioscoApp.exe").write_bytes(f"EXE {version}".encode())
    (carpeta / "LEEME.txt").write_text(f"KioscoApp - Guia rapida\n=======\nTwinx Express {version}\n", encoding="utf-8")
    for ruta in migraciones:
        shutil.copy(ruta, carpeta / "_internal" / "db" / "migraciones" / ruta.name)
    return carpeta


@pytest.fixture
def actualizado_desde_v115(tmp_path, monkeypatch):
    """V1.15 instalada con datos -> actualizada por el técnico a esta versión (con facturación)."""
    datos = tmp_path / "KioscoApp" / "data"
    (datos / "imagenes_productos").mkdir(parents=True)
    migraciones = tmp_path / "migraciones_en_uso"
    migraciones.mkdir()
    for ruta in _REALES[:_MIGRACIONES_V115]:
        shutil.copy(ruta, migraciones / ruta.name)
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", datos / "kiosco.db")
    monkeypatch.setattr(modulo_conexion, "DIRECTORIO_MIGRACIONES", migraciones)
    monkeypatch.setattr(modulo_restore, "DIRECTORIO_DATA", datos)
    monkeypatch.setattr(config, "DIRECTORIO_DATA", datos)
    modulo_conexion.inicializar_base_datos()
    instalacion = _programa(tmp_path / "Programas" / "KioscoApp", _VERSION_V115, _REALES[:_MIGRACIONES_V115])
    nuevo = _programa(tmp_path / "Pendrive" / "KioscoApp", VERSION, _REALES)
    for ruta in _REALES[_MIGRACIONES_V115:]:
        shutil.copy(ruta, migraciones / ruta.name)
    servicio_actualizacion.actualizar(instalacion, nuevo, False)
    _abrir_caja()
    return {"datos": datos, "instalacion": instalacion}


def _rollback_a_v115():
    return servicio_actualizacion.rollback(_VERSION_V115, config.DIRECTORIO_VERSIONES / VERSION, False)


def test_rollback_a_v115_sin_actividad_fiscal_esta_permitido(actualizado_desde_v115):
    uf.guardar_perfil_validado()  # configurar el perfil no es historia fiscal emitida

    _rollback_a_v115()

    assert servicio_actualizacion.version_de_programa(actualizado_desde_v115["instalacion"]) == _VERSION_V115


def test_rollback_a_v115_despues_de_un_cae_se_bloquea_sin_cambiar_nada(actualizado_desde_v115):
    _comprobante(1)

    with pytest.raises(ErrorActualizacion) as error:
        _rollback_a_v115()

    mensaje = str(error.value)
    assert "no se cambió nada" in mensaje
    assert "versión sin facturación ARCA" in mensaje
    assert servicio_actualizacion.version_de_programa(actualizado_desde_v115["instalacion"]) == VERSION
    assert _cantidad_comprobantes(actualizado_desde_v115["datos"]) == 1
