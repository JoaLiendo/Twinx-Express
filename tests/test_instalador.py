"""Instalador (V1.18-A): `Instalar.ps1` instala en una carpeta aislada, crea accesos directos, actualiza a través
del propio programa cuando ya hay una instalación y rechaza un entregable contaminado. Corre el script real con
PowerShell contra carpetas temporales: nunca toca C:\\KioscoApp ni el Escritorio."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
INSTALADOR = RAIZ / "Instalar.ps1"

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="el instalador es un script de PowerShell para Windows")


def _bundle_falso(carpeta: Path) -> Path:
    """Una carpeta con la forma del entregable (sin un ejecutable real)."""
    carpeta.mkdir(parents=True)
    (carpeta / "KioscoApp.exe").write_bytes(b"MZ-falso")
    (carpeta / "_internal").mkdir()
    (carpeta / "_internal" / "base_library.zip").write_bytes(b"PK")
    for nombre in ("LEEME.txt", "Restaurar_backup.bat", "MANUAL.md", "Instalar.bat"):
        (carpeta / nombre).write_text(nombre, encoding="utf-8")
    shutil.copy(INSTALADOR, carpeta / "Instalar.ps1")
    return carpeta


def _ejecutar(bundle: Path, *argumentos: str) -> subprocess.CompletedProcess:
    comando = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(bundle / "Instalar.ps1"), *argumentos]
    return subprocess.run(comando, capture_output=True, text=True, timeout=120, env={**os.environ, "LOCALAPPDATA": str(bundle.parent / "localappdata")})


def test_instalacion_nueva_copia_el_programa_y_crea_el_acceso_directo(tmp_path):
    bundle = _bundle_falso(tmp_path / "entregable")
    destino = tmp_path / "instalacion"
    accesos = tmp_path / "accesos"

    resultado = _ejecutar(bundle, "-Destino", str(destino), "-CarpetaAccesosDirectos", str(accesos))

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert (destino / "KioscoApp.exe").read_bytes() == b"MZ-falso"
    assert (destino / "_internal" / "base_library.zip").exists()
    assert (destino / "LEEME.txt").exists() and (destino / "Instalar.ps1").exists()
    assert (accesos / "Twinx Express.lnk").exists()
    assert "LISTO" in resultado.stdout
    # Los datos del cliente no se crean ni se tocan (PowerShell puede crear carpetas propias en LOCALAPPDATA).
    assert not (tmp_path / "localappdata" / "KioscoApp").exists()


def test_con_instalacion_existente_delega_en_el_programa_para_actualizar(tmp_path):
    bundle = _bundle_falso(tmp_path / "entregable")
    destino = tmp_path / "instalacion"
    destino.mkdir()
    (destino / "KioscoApp.exe").write_bytes(b"MZ-viejo")
    registro = tmp_path / "llamada.txt"
    stub = tmp_path / "actualizador.cmd"
    stub.write_text(f'@echo off\r\necho %* > "{registro}"\r\nexit /b 0\r\n', encoding="utf-8")

    resultado = _ejecutar(bundle, "-Destino", str(destino), "-SinAccesosDirectos", "-EjecutableActualizador", str(stub))

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert registro.read_text(encoding="utf-8").strip() == f'--actualizar "{destino}"'
    # El instalador no copia nada por su cuenta en una actualización: eso lo hace KioscoApp.exe --actualizar.
    assert (destino / "KioscoApp.exe").read_bytes() == b"MZ-viejo"
    assert "ACTUALIZA" in resultado.stdout


def test_un_destino_con_barra_final_llega_entero_al_actualizador(tmp_path):
    bundle = _bundle_falso(tmp_path / "entregable")
    destino = tmp_path / "instalacion"
    destino.mkdir()
    (destino / "KioscoApp.exe").write_bytes(b"MZ-viejo")
    registro = tmp_path / "llamada.txt"
    stub = tmp_path / "actualizador.cmd"
    stub.write_text(f'@echo off\r\necho %* > "{registro}"\r\nexit /b 0\r\n', encoding="utf-8")

    resultado = _ejecutar(bundle, "-Destino", str(destino) + "\\", "-SinAccesosDirectos", "-EjecutableActualizador", str(stub))

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    # Sin la barra final: "...\" dentro de comillas rompia el argumento al pasar por cmd.
    assert registro.read_text(encoding="utf-8").strip() == f'--actualizar "{destino}"'


def test_si_la_actualizacion_falla_lo_informa_y_termina_con_error(tmp_path):
    bundle = _bundle_falso(tmp_path / "entregable")
    destino = tmp_path / "instalacion"
    destino.mkdir()
    (destino / "KioscoApp.exe").write_bytes(b"MZ-viejo")
    stub = tmp_path / "actualizador.cmd"
    stub.write_text("@echo off\r\nexit /b 3\r\n", encoding="utf-8")

    resultado = _ejecutar(bundle, "-Destino", str(destino), "-SinAccesosDirectos", "-EjecutableActualizador", str(stub))

    assert resultado.returncode == 1
    assert "no se completo (codigo 3)" in resultado.stdout


@pytest.mark.parametrize("contaminante", ["data", "kiosco.db", "clave.pfx", "KioscoApp_backup_2026.zip"])
def test_un_entregable_con_datos_o_secretos_no_se_instala(tmp_path, contaminante):
    bundle = _bundle_falso(tmp_path / "entregable")
    if "." in contaminante:
        (bundle / "_internal" / contaminante).write_bytes(b"x")
    else:
        (bundle / contaminante).mkdir()
    destino = tmp_path / "instalacion"

    resultado = _ejecutar(bundle, "-Destino", str(destino), "-SinAccesosDirectos")

    assert resultado.returncode == 1
    assert "no se instala" in resultado.stdout
    assert not destino.exists()


def test_no_se_instala_sobre_si_mismo_ni_en_una_carpeta_ocupada(tmp_path):
    bundle = _bundle_falso(tmp_path / "entregable")
    assert _ejecutar(bundle, "-Destino", str(bundle), "-SinAccesosDirectos").returncode == 1
    ocupada = tmp_path / "ocupada"
    ocupada.mkdir()
    (ocupada / "otro.txt").write_text("x", encoding="utf-8")
    resultado = _ejecutar(bundle, "-Destino", str(ocupada), "-SinAccesosDirectos")
    assert resultado.returncode == 1 and "no esta vacia" in resultado.stdout


# --- Cierre operativo (F3): desinstalación sin tocar los datos ---------------------------------------------

DESINSTALADOR = RAIZ / "Desinstalar.ps1"


def _ejecutar_desinstalador(carpeta: Path, localappdata: Path, *argumentos: str) -> subprocess.CompletedProcess:
    comando = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(carpeta / "Desinstalar.ps1"), "-Si", *argumentos]
    return subprocess.run(comando, capture_output=True, text=True, timeout=120, env={**os.environ, "LOCALAPPDATA": str(localappdata)})


def _instalacion_con_datos(tmp_path: Path):
    bundle = _bundle_falso(tmp_path / "entregable")
    shutil.copy(DESINSTALADOR, bundle / "Desinstalar.ps1")
    (bundle / "Desinstalar.bat").write_text("bat", encoding="utf-8")
    destino = tmp_path / "instalacion"
    accesos = tmp_path / "accesos"
    assert _ejecutar(bundle, "-Destino", str(destino), "-CarpetaAccesosDirectos", str(accesos)).returncode == 0
    localappdata = tmp_path / "localappdata"
    datos = localappdata / "KioscoApp"
    for carpeta in ("data", "backups", "fiscal_privado", "versiones"):
        (datos / carpeta).mkdir(parents=True)
    (datos / "data" / "kiosco.db").write_bytes(b"SQLite format 3\0")
    (datos / "backups" / "KioscoApp_backup_2026-11-09_210000.zip").write_bytes(b"PK")
    return destino, accesos, localappdata, datos


def test_desinstalar_quita_programa_y_accesos_y_conserva_los_datos(tmp_path):
    destino, accesos, localappdata, datos = _instalacion_con_datos(tmp_path)
    assert (destino / "Desinstalar.ps1").exists() and (accesos / "Twinx Express.lnk").exists()

    resultado = _ejecutar_desinstalador(destino, localappdata, "-Destino", str(destino), "-CarpetaAccesosDirectos", str(accesos))

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert not destino.exists() and not (accesos / "Twinx Express.lnk").exists()
    assert (datos / "data" / "kiosco.db").read_bytes().startswith(b"SQLite")
    assert (datos / "backups" / "KioscoApp_backup_2026-11-09_210000.zip").exists()
    assert (datos / "fiscal_privado").exists() and (datos / "versiones").exists()
    assert "Datos conservados" in resultado.stdout and str(datos) in resultado.stdout


def test_desinstalar_se_niega_sin_programa_o_sobre_los_datos(tmp_path):
    destino, accesos, localappdata, datos = _instalacion_con_datos(tmp_path)
    vacia = tmp_path / "otra"
    vacia.mkdir()
    (vacia / "algo.txt").write_text("x", encoding="utf-8")
    shutil.copy(DESINSTALADOR, vacia / "Desinstalar.ps1")

    sin_programa = _ejecutar_desinstalador(vacia, localappdata, "-Destino", str(vacia))
    assert sin_programa.returncode == 1 and "No se toco nada" in sin_programa.stdout
    assert (vacia / "algo.txt").exists()

    (datos / "KioscoApp.exe").write_bytes(b"MZ-falso")  # aunque hubiera un exe adentro, la carpeta de datos es intocable
    sobre_datos = _ejecutar_desinstalador(destino, localappdata, "-Destino", str(datos))
    assert sobre_datos.returncode == 1 and "DATOS" in sobre_datos.stdout
    assert (datos / "data" / "kiosco.db").exists() and destino.exists()
