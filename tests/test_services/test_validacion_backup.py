"""V1.15-A: backups con manifiesto y validación completa (`services.formato_backup`).

Invariantes: un backup declarado OK siempre pasó la validación; un backup
dañado, incompleto, inseguro o con metadata adulterada nunca valida.
"""

import errno
import hashlib
import json
import sqlite3
import zipfile
from datetime import datetime

import pytest

import services.servicio_imagenes as modulo_imagenes

import db.conexion as modulo_conexion

import services.servicio_backup as modulo_backup
from db.conexion import obtener_conexion
from domain.backup import TIPO_CIERRE_CAJA, TIPO_MANUAL
from excepciones import BackupInvalidoError, ErrorBackup
from services import formato_backup
from services.control_escrituras import ControlEscrituras
from version import VERSION


@pytest.fixture
def entorno(base_datos_temporal, tmp_path, monkeypatch):
    """Base real (esquema completo de migraciones) + imágenes aisladas."""
    dir_imagenes = tmp_path / "imagenes_productos"
    dir_imagenes.mkdir()
    (dir_imagenes / "1_abc.jpg").write_bytes(b"IMAGEN-1")
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", base_datos_temporal)
    monkeypatch.setattr(modulo_imagenes, "DIRECTORIO_IMAGENES_PRODUCTOS", dir_imagenes)
    return {"db": base_datos_temporal, "imagenes": dir_imagenes, "destino": tmp_path / "backups"}


def _crear(entorno, **kwargs):
    return modulo_backup.crear_backup(entorno["destino"], ControlEscrituras(), **kwargs)


def _leer_zip(ruta):
    with zipfile.ZipFile(ruta) as zf:
        return {info.filename: zf.read(info) for info in zf.infolist()}


def _escribir_zip(ruta, entradas):
    with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as zf:
        for nombre, contenido in entradas.items():
            zf.writestr(nombre, contenido)
    return ruta


def _manifiesto(entradas):
    return json.loads(entradas[formato_backup.NOMBRE_MANIFIESTO])


def _db_valida(tmp_path, nombre="otra.db", sql="CREATE TABLE t (id INTEGER PRIMARY KEY)"):
    ruta = tmp_path / nombre
    conexion = sqlite3.connect(ruta)
    conexion.executescript(sql)
    conexion.commit()
    conexion.close()
    return ruta.read_bytes()


# --- creación ---------------------------------------------------------------


def test_backup_normal_lleva_manifiesto_verificable(entorno):
    with obtener_conexion() as conexion:
        conexion.execute("INSERT INTO productos (codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos, stock_actual) VALUES ('1', 'A', 50, 100, 5)")

    ruta = _crear(entorno)

    info = formato_backup.validar_backup(ruta)
    manifiesto = info.manifiesto
    assert manifiesto.tipo == TIPO_MANUAL
    assert manifiesto.version == VERSION
    assert manifiesto.integridad == "ok"
    assert manifiesto.migraciones == info.migraciones
    assert manifiesto.migraciones[-1].startswith("0")
    assert set(manifiesto.archivos) == {"kiosco.db", "imagenes_productos/1_abc.jpg"}
    contenido = _leer_zip(ruta)
    assert manifiesto.archivos["kiosco.db"].sha256 == hashlib.sha256(contenido["kiosco.db"]).hexdigest()


def test_backup_de_base_vacia_sin_tablas(entorno, tmp_path, monkeypatch):
    vacia = tmp_path / "vacia.db"
    sqlite3.connect(vacia).close()
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", vacia)

    info = formato_backup.validar_backup(_crear(entorno))

    assert info.migraciones == ()


def test_backup_sin_imagenes_conserva_la_carpeta(entorno):
    (entorno["imagenes"] / "1_abc.jpg").unlink()

    contenido = _leer_zip(_crear(entorno))

    assert "imagenes_productos/" in contenido


def test_metadata_de_cierre_de_caja(entorno):
    info = formato_backup.validar_backup(_crear(entorno, tipo=TIPO_CIERRE_CAJA, sesion_caja_id=7))

    assert info.manifiesto.tipo == TIPO_CIERRE_CAJA
    assert info.manifiesto.sesion_caja_id == 7
    assert "cierre_caja" in info.ruta.name


def test_dos_backups_en_el_mismo_segundo_no_se_pisan(entorno):
    ahora = datetime(2026, 10, 31, 12, 0, 0)

    primero = _crear(entorno, ahora=ahora)
    segundo = _crear(entorno, ahora=ahora)

    assert primero != segundo
    assert primero.exists() and segundo.exists()
    assert segundo.name.endswith("_2.zip")


def test_base_corrupta_no_genera_backup(entorno, tmp_path, monkeypatch):
    corrupta = tmp_path / "corrupta.db"
    corrupta.write_bytes(b"SQLite format 3\x00" + b"\xff" * 4096)
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", corrupta)

    with pytest.raises(ErrorBackup):
        _crear(entorno)

    assert list(entorno["destino"].glob("*.zip")) == []


def test_base_con_referencias_rotas_no_genera_backup(entorno, tmp_path, monkeypatch):
    rota = tmp_path / "rota.db"
    conexion = sqlite3.connect(rota)
    conexion.executescript(
        "CREATE TABLE padre (id INTEGER PRIMARY KEY);"
        "CREATE TABLE hijo (id INTEGER PRIMARY KEY, padre_id INTEGER REFERENCES padre(id));"
        "INSERT INTO hijo (padre_id) VALUES (99);"
    )
    conexion.close()
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", rota)

    with pytest.raises(ErrorBackup, match="referencia"):
        _crear(entorno)

    assert list(entorno["destino"].glob("*.zip")) == []


def test_backup_que_no_valida_nunca_se_publica(entorno, monkeypatch):
    """Invariante 1: un backup declarado OK siempre pasó la validación."""

    def validar_que_falla(_ruta):
        raise BackupInvalidoError("fallo simulado de validación")

    monkeypatch.setattr(modulo_backup, "validar_backup", validar_que_falla)

    with pytest.raises(ErrorBackup, match="validación"):
        _crear(entorno)

    assert list(entorno["destino"].iterdir()) == []


def test_disco_lleno_da_error_claro_y_no_deja_restos(entorno, monkeypatch):
    def construir_sin_espacio(*_args):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(modulo_backup, "construir_paquete", construir_sin_espacio)

    with pytest.raises(ErrorBackup, match="espacio"):
        _crear(entorno)

    assert list(entorno["destino"].iterdir()) == []


def test_directorio_sin_permisos_da_error_claro(entorno, monkeypatch):
    def temporal_sin_permiso(*_args, **_kwargs):
        raise PermissionError(errno.EACCES, "Acceso denegado")

    monkeypatch.setattr(modulo_backup.tempfile, "TemporaryDirectory", temporal_sin_permiso)

    with pytest.raises(ErrorBackup, match="permiso"):
        _crear(entorno)


def test_ruta_con_unicode_y_espacios(entorno, tmp_path):
    destino = tmp_path / "Copias de seguridad ñandú"

    ruta = modulo_backup.crear_backup(destino, ControlEscrituras())

    assert formato_backup.validar_backup(ruta).manifiesto is not None


def test_archivo_ajeno_con_nombre_no_admitido_no_impide_el_backup(entorno):
    (entorno["imagenes"] / "foto#1.jpg").write_bytes(b"x")

    contenido = _leer_zip(_crear(entorno))

    assert "imagenes_productos/foto#1.jpg" not in contenido
    assert "imagenes_productos/1_abc.jpg" in contenido


# --- validación adversarial --------------------------------------------------


def test_archivo_inexistente(tmp_path):
    with pytest.raises(BackupInvalidoError, match="No se encontró"):
        formato_backup.validar_backup(tmp_path / "no_existe.zip")


@pytest.mark.parametrize("contenido", [b"", b"esto no es un zip", b"PK\x03\x04basura"])
def test_zip_corrupto(tmp_path, contenido):
    ruta = tmp_path / "roto.zip"
    ruta.write_bytes(contenido)

    with pytest.raises(BackupInvalidoError):
        formato_backup.validar_backup(ruta)


def test_zip_truncado(entorno, tmp_path):
    original = _crear(entorno).read_bytes()
    truncado = tmp_path / "truncado.zip"
    truncado.write_bytes(original[: len(original) // 2])

    with pytest.raises(BackupInvalidoError):
        formato_backup.validar_backup(truncado)


def test_bytes_alterados_dentro_del_zip(entorno, tmp_path):
    """Un bit podrido en los datos comprimidos: lo detecta el CRC o el hash."""
    ruta = _crear(entorno)
    datos = bytearray(ruta.read_bytes())
    with zipfile.ZipFile(ruta) as zf:
        info = zf.getinfo("kiosco.db")
    inicio_datos = info.header_offset + 30 + len(info.filename) + len(info.extra)
    datos[inicio_datos + 50] ^= 0xFF
    alterado = tmp_path / "alterado.zip"
    alterado.write_bytes(bytes(datos))

    with pytest.raises(BackupInvalidoError):
        formato_backup.validar_backup(alterado)


def test_falta_la_base(entorno, tmp_path):
    entradas = _leer_zip(_crear(entorno))
    del entradas["kiosco.db"]

    with pytest.raises(BackupInvalidoError, match="kiosco.db"):
        formato_backup.validar_backup(_escribir_zip(tmp_path / "x.zip", entradas))


def test_falta_la_carpeta_de_imagenes(tmp_path):
    ruta = _escribir_zip(tmp_path / "x.zip", {"kiosco.db": _db_valida(tmp_path)})

    with pytest.raises(BackupInvalidoError, match="imagenes_productos"):
        formato_backup.validar_backup(ruta)


def test_base_corrupta_con_crc_correcto(tmp_path):
    ruta = _escribir_zip(
        tmp_path / "x.zip",
        {"kiosco.db": b"SQLite format 3\x00" + b"\x13" * 8000, "imagenes_productos/": b""},
    )

    with pytest.raises(BackupInvalidoError, match="dañada|integridad"):
        formato_backup.validar_backup(ruta)


def test_backup_anterior_a_v115_sin_manifiesto_sigue_siendo_valido(tmp_path):
    ruta = _escribir_zip(tmp_path / "legado.zip", {"kiosco.db": _db_valida(tmp_path), "imagenes_productos/": b""})

    info = formato_backup.validar_backup(ruta)

    assert info.manifiesto is None
    assert info.tipo is None


@pytest.mark.parametrize(
    "adulterar",
    [
        lambda m: m["archivos"]["kiosco.db"].update(sha256="0" * 64),
        lambda m: m["archivos"]["kiosco.db"].update(tamano=1),
        lambda m: m.update(migraciones=m["migraciones"][:-1]),
        lambda m: m.update(migraciones=m["migraciones"] + ["999_futura.sql"]),
        lambda m: m["archivos"].pop("imagenes_productos/1_abc.jpg"),
        lambda m: m.update(tipo="INVENTADO"),
        lambda m: m.update(formato=3),
        lambda m: m.update(formato="2"),
        lambda m: m.update(aplicacion="Otra"),
        lambda m: m.update(fecha="ayer"),
        lambda m: m.update(integridad="dañada"),
        lambda m: m.pop("id"),
        lambda m: m.update(sesion_caja_id="7"),
    ],
)
def test_metadata_adulterada(entorno, tmp_path, adulterar):
    entradas = _leer_zip(_crear(entorno))
    manifiesto = _manifiesto(entradas)
    adulterar(manifiesto)
    entradas[formato_backup.NOMBRE_MANIFIESTO] = json.dumps(manifiesto).encode()

    with pytest.raises(BackupInvalidoError):
        formato_backup.validar_backup(_escribir_zip(tmp_path / "adulterado.zip", entradas))


def test_archivo_agregado_que_no_figura_en_el_manifiesto(entorno, tmp_path):
    entradas = _leer_zip(_crear(entorno))
    entradas["imagenes_productos/intrusa.jpg"] = b"x"

    with pytest.raises(BackupInvalidoError, match="manifiesto"):
        formato_backup.validar_backup(_escribir_zip(tmp_path / "x.zip", entradas))


def test_manifiesto_ilegible(entorno, tmp_path):
    entradas = _leer_zip(_crear(entorno))
    entradas[formato_backup.NOMBRE_MANIFIESTO] = b"\xff\xfe no json"

    with pytest.raises(BackupInvalidoError, match="manifiesto"):
        formato_backup.validar_backup(_escribir_zip(tmp_path / "x.zip", entradas))


@pytest.mark.parametrize(
    "nombre",
    [
        "../../archivo",
        "../kiosco.db",
        "imagenes_productos/../../fuera.txt",
        "imagenes_productos/..\\..\\fuera.txt",
        "..\\..\\archivo",
        "/etc/passwd",
        "C:/Windows/archivo",
        "C:archivo",
        "imagenes_productos/./x.jpg",
        "imagenes_productos//x.jpg",
        "imagenes_productos/con.jpg",
        "imagenes_productos/x.jpg ",
        "otro.txt",
    ],
)
def test_nombres_inseguros_o_inesperados(tmp_path, nombre):
    ruta = _escribir_zip(
        tmp_path / "malicioso.zip",
        {"kiosco.db": _db_valida(tmp_path), "imagenes_productos/": b"", nombre: b"x"},
    )

    with pytest.raises(BackupInvalidoError):
        formato_backup.validar_backup(ruta, tmp_path / "extraccion")

    assert not (tmp_path / "archivo").exists()
    assert not (tmp_path / "fuera.txt").exists()


def test_enlace_simbolico_dentro_del_zip(tmp_path):
    ruta = tmp_path / "symlink.zip"
    with zipfile.ZipFile(ruta, "w") as zf:
        zf.writestr("kiosco.db", _db_valida(tmp_path))
        zf.writestr("imagenes_productos/", b"")
        info = zipfile.ZipInfo("imagenes_productos/enlace.jpg")
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "../../../Windows/system.ini")

    with pytest.raises(BackupInvalidoError, match="enlace"):
        formato_backup.validar_backup(ruta)


def test_entrada_duplicada(tmp_path):
    ruta = tmp_path / "dup.zip"
    with pytest.warns(UserWarning):
        with zipfile.ZipFile(ruta, "w") as zf:
            zf.writestr("kiosco.db", _db_valida(tmp_path))
            zf.writestr("imagenes_productos/", b"")
            zf.writestr("kiosco.db", _db_valida(tmp_path, "b.db"))

    with pytest.raises(BackupInvalidoError, match="duplicado"):
        formato_backup.validar_backup(ruta)


def test_tamano_descomprimido_excesivo(tmp_path, monkeypatch):
    monkeypatch.setattr(formato_backup, "_LIMITE_TAMANO_DESCOMPRIMIDO", 50_000)
    ruta = _escribir_zip(
        tmp_path / "bomba.zip",
        {"kiosco.db": _db_valida(tmp_path), "imagenes_productos/": b"", "imagenes_productos/x.jpg": b"\0" * 100_000},
    )

    with pytest.raises(BackupInvalidoError, match="excesivo"):
        formato_backup.validar_backup(ruta)


def test_extraccion_valida_deja_los_datos_en_el_destino(entorno, tmp_path):
    destino = tmp_path / "extraccion"
    destino.mkdir()

    formato_backup.validar_backup(_crear(entorno), destino)

    assert (destino / "data" / "kiosco.db").is_file()
    assert (destino / "data" / "imagenes_productos" / "1_abc.jpg").read_bytes() == b"IMAGEN-1"
    assert not (destino / "data" / formato_backup.NOMBRE_MANIFIESTO).exists()


def test_base_inexistente_no_crea_una_vacia(entorno, tmp_path, monkeypatch):
    ausente = tmp_path / "no_existe.db"
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ausente)

    with pytest.raises(ErrorBackup):
        _crear(entorno)

    assert not ausente.exists()


def test_el_backup_copia_la_base_que_usa_la_aplicacion(entorno):
    """Regresión de aislamiento: el contenido sale de `db.conexion.RUTA_BASE_DATOS`."""
    with obtener_conexion() as conexion:
        conexion.execute("INSERT INTO configuracion (clave, valor) VALUES ('centinela', 'BASE-ACTIVA')")

    destino = entorno["destino"].parent / "extraido"
    destino.mkdir()
    formato_backup.validar_backup(_crear(entorno), destino)

    conexion = sqlite3.connect(destino / "data" / "kiosco.db")
    try:
        assert conexion.execute("SELECT valor FROM configuracion WHERE clave = 'centinela'").fetchone() == ("BASE-ACTIVA",)
    finally:
        conexion.close()
