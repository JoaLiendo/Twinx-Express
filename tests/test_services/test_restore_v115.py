"""V1.15-F: restore profesional.

Invariantes: un restore inválido nunca reemplaza la base activa (4); una base
restaurada pasa integrity/FK check (10); un backup corrupto jamás se restaura (12).
"""

import json
import sqlite3
import zipfile

import pytest

import db.conexion as modulo_conexion
import lanzador
import services.servicio_imagenes as modulo_imagenes
import services.servicio_restore as modulo_restore
from domain.backup import TIPO_SEGURIDAD
from excepciones import ErrorRestore
from services import formato_backup, servicio_backup
from services.control_escrituras import ControlEscrituras


@pytest.fixture
def instalacion(tmp_path, monkeypatch):
    datos = tmp_path / "KioscoApp" / "data"
    (datos / "imagenes_productos").mkdir(parents=True)
    (datos / "imagenes_productos" / "1_a.jpg").write_bytes(b"IMAGEN-ACTUAL")
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", datos / "kiosco.db")
    monkeypatch.setattr(modulo_imagenes, "DIRECTORIO_IMAGENES_PRODUCTOS", datos / "imagenes_productos")
    monkeypatch.setattr(modulo_restore, "DIRECTORIO_DATA", datos)
    modulo_conexion.inicializar_base_datos()
    _poner_centinela("ORIGINAL")
    return datos


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
        return conexion.execute("SELECT valor FROM configuracion WHERE clave = 'centinela'").fetchone()[0]
    finally:
        conexion.close()


def _backup():
    return servicio_backup.crear_backup(servicio_backup.DIRECTORIO_BACKUPS, ControlEscrituras())


def test_restore_real_vuelve_los_datos_exactos_y_protege_el_estado_reemplazado(instalacion):
    ruta = _backup()
    _poner_centinela("MODIFICADO")
    (instalacion / "imagenes_productos" / "1_a.jpg").write_bytes(b"IMAGEN-CAMBIADA")

    resultado = modulo_restore.restaurar_desde_backup(ruta)

    assert _centinela(instalacion) == "ORIGINAL"
    assert (instalacion / "imagenes_productos" / "1_a.jpg").read_bytes() == b"IMAGEN-ACTUAL"
    seguridad = formato_backup.validar_backup(resultado.backup_seguridad)
    assert seguridad.manifiesto.tipo == TIPO_SEGURIDAD
    conexion = sqlite3.connect(instalacion / "kiosco.db")
    try:
        assert conexion.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conexion.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conexion.execute("SELECT accion FROM auditoria ORDER BY id DESC LIMIT 1").fetchone()[0] == "RESTORE"
    finally:
        conexion.close()
    assert not list(instalacion.parent.glob(".kioscoapp_*"))


def test_el_backup_de_seguridad_permite_deshacer_el_restore(instalacion):
    ruta = _backup()
    _poner_centinela("MODIFICADO")
    resultado = modulo_restore.restaurar_desde_backup(ruta)

    modulo_restore.restaurar_desde_backup(resultado.backup_seguridad)

    assert _centinela(instalacion) == "MODIFICADO"


def test_backup_de_version_futura_se_rechaza_sin_tocar_nada(instalacion, tmp_path):
    ruta = _backup()
    destino = tmp_path / "futuro"
    destino.mkdir()
    formato_backup.validar_backup(ruta, destino)
    conexion = sqlite3.connect(destino / "data" / "kiosco.db")
    conexion.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES ('999_futura.sql')")
    conexion.commit()
    conexion.close()
    futuro = tmp_path / "futuro.zip"
    with zipfile.ZipFile(futuro, "w") as zf:
        zf.write(destino / "data" / "kiosco.db", "kiosco.db")
        zf.writestr("imagenes_productos/", "")
    _poner_centinela("ACTUAL")

    with pytest.raises(ErrorRestore, match="más nueva"):
        modulo_restore.restaurar_desde_backup(futuro)

    assert _centinela(instalacion) == "ACTUAL"


@pytest.mark.parametrize("dano", ["truncado", "bit", "inexistente", "incompleto", "manifiesto"])
def test_restore_invalido_nunca_reemplaza_la_base_activa(instalacion, tmp_path, dano):
    ruta = _backup()
    _poner_centinela("ACTUAL")
    contenido = ruta.read_bytes()
    malo = tmp_path / "malo.zip"
    if dano == "truncado":
        malo.write_bytes(contenido[: len(contenido) // 3])
    elif dano == "bit":
        datos = bytearray(contenido)
        datos[120] ^= 0xFF
        malo.write_bytes(bytes(datos))
    elif dano == "incompleto":
        with zipfile.ZipFile(ruta) as origen, zipfile.ZipFile(malo, "w") as zf:
            for info in origen.infolist():
                if info.filename != "kiosco.db":
                    zf.writestr(info, origen.read(info))
    elif dano == "manifiesto":
        with zipfile.ZipFile(ruta) as origen, zipfile.ZipFile(malo, "w") as zf:
            for info in origen.infolist():
                datos = origen.read(info)
                if info.filename == "manifiesto.json":
                    manifiesto = json.loads(datos)
                    manifiesto["migraciones"] = manifiesto["migraciones"][:3]
                    datos = json.dumps(manifiesto).encode()
                zf.writestr(info.filename, datos)
    antes = (instalacion / "kiosco.db").read_bytes()

    with pytest.raises(ErrorRestore):
        modulo_restore.restaurar_desde_backup(malo if dano != "inexistente" else tmp_path / "no.zip")

    assert (instalacion / "kiosco.db").read_bytes() == antes
    assert not list(instalacion.parent.glob(".kioscoapp_*"))


def test_restore_sobre_instalacion_nueva_sin_datos(instalacion, tmp_path, monkeypatch):
    ruta = _backup()
    vacia = tmp_path / "otra_pc" / "data"
    monkeypatch.setattr(modulo_restore, "DIRECTORIO_DATA", vacia)

    resultado = modulo_restore.restaurar_desde_backup(ruta)

    assert resultado.backup_seguridad is None and resultado.datos_reemplazados is None
    assert _centinela(vacia) == "ORIGINAL"


def test_base_actual_danada_se_conserva_como_evidencia(instalacion):
    ruta = _backup()
    (instalacion / "kiosco.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 50)

    resultado = modulo_restore.restaurar_desde_backup(ruta)

    assert resultado.backup_seguridad is None
    assert (resultado.datos_reemplazados / "kiosco.db").read_bytes().startswith(b"SQLite format 3")
    assert _centinela(instalacion) == "ORIGINAL"


def test_fallo_de_permisos_durante_el_reemplazo_deja_todo_como_estaba(instalacion, monkeypatch):
    ruta = _backup()
    _poner_centinela("ACTUAL")
    original = type(instalacion).rename
    llamadas = {"n": 0}

    def rename_que_falla(self, destino):
        llamadas["n"] += 1
        if llamadas["n"] == 2:  # mover la data restaurada a su lugar
            raise PermissionError(13, "Acceso denegado")
        return original(self, destino)

    monkeypatch.setattr(type(instalacion), "rename", rename_que_falla)

    with pytest.raises(PermissionError):
        modulo_restore.restaurar_desde_backup(ruta)

    monkeypatch.undo()
    assert _centinela(instalacion) == "ACTUAL"


def test_restore_con_la_aplicacion_abierta_se_niega(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *_: "")
    llamado = []

    codigo = lanzador._modo_restore("x.zip", lambda: llamado.append, aplicacion_en_uso=lambda: True)

    assert codigo == 1
    assert llamado == []
    assert "está abierto" in capsys.readouterr().out


def test_pagina_de_backup_lista_puntos_de_retorno_y_marca_los_danados(base_datos_temporal):
    from db.repositorios import usuarios as repositorio_usuarios
    from domain.usuario import Usuario
    from interfaces.web.auth import NOMBRE_COOKIE_SESION
    from services import servicio_auth
    from tests.test_interfaces_web._asgi_cliente import solicitud

    repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario="duenio",
            nombre_completo="Dueño",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol="OWNER",
        )
    )
    cookies = {NOMBRE_COOKIE_SESION: servicio_auth.iniciar_sesion("duenio", "clave-correcta-123").token}
    sano = servicio_backup.crear_backup(servicio_backup.DIRECTORIO_BACKUPS, ControlEscrituras(), tipo="PRE_UPDATE")
    roto = servicio_backup.crear_backup(servicio_backup.DIRECTORIO_BACKUPS, ControlEscrituras(), tipo="PRE_UPDATE")
    datos = bytearray(roto.read_bytes())
    datos[150] ^= 0xFF
    roto.write_bytes(bytes(datos))

    pagina = solicitud("GET", "/backup", cookies=cookies).texto

    assert 'id="backup-recuperacion"' in pagina
    assert pagina.count("Verificado") == 1  # invariante 12: el dañado no aparece como recuperable
    assert "Dañado: no sirve para recuperar" in pagina
    assert sano.exists()
