"""Una sola instancia de Twinx Express por base de datos, con procesos reales del sistema operativo.

Cierra H5: dos procesos sobre la misma SQLite, donde el segundo tomaba como muerto un ENVIANDO fiscal que
el primero seguía procesando. Ver `db/instancia_unica.py` y `tests/proceso_instancia.py`.
"""

import errno
import hashlib
import json
import os
import queue
import re
import runpy
import socket
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

import config
import db.conexion as modulo_conexion
import lanzador
from db import instancia_unica
from db.repositorios import fiscal as repositorio_fiscal
from excepciones import ErrorBaseDatos, InstanciaEnUsoError
from services import servicio_actualizacion, servicio_backup, servicio_restore
from services.control_escrituras import ControlEscrituras
from tests import utilidades_fiscales as uf
from tests.proceso_instancia import CODIGO_RECHAZADA

RAIZ = Path(__file__).resolve().parent.parent
ESPERA_MAXIMA_SEGUNDOS = 120


class Proceso:
    """Un `tests.proceso_instancia` vivo: sus eventos llegan por una cola para poder esperarlos con límite."""

    def __init__(self, *argumentos: object) -> None:
        self.popen = subprocess.Popen(
            [sys.executable, "-m", "tests.proceso_instancia", *map(str, argumentos)],
            cwd=RAIZ,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        self.salida: list[str] = []
        self.eventos: list[dict] = []
        self._cola: queue.Queue = queue.Queue()
        threading.Thread(target=self._leer, daemon=True).start()

    def _leer(self) -> None:
        for linea in self.popen.stdout:
            self.salida.append(linea)
            if linea.startswith('{"evento"'):
                evento = json.loads(linea)
                self.eventos.append(evento)
                self._cola.put(evento)
        self._cola.put(None)

    def esperar(self, *nombres: str) -> dict:
        """Primer evento de `nombres`; falla con la salida del proceso si termina o tarda sin mandarlo."""
        limite = time.monotonic() + ESPERA_MAXIMA_SEGUNDOS
        while True:
            try:
                evento = self._cola.get(timeout=max(0.0, limite - time.monotonic()))
            except queue.Empty:
                pytest.fail(f"El proceso no informó {nombres} a tiempo:\n{''.join(self.salida)}")
            if evento is None:
                pytest.fail(f"El proceso terminó sin informar {nombres}:\n{''.join(self.salida)}")
            if evento["evento"] in nombres:
                return evento

    def terminar(self) -> int:
        """Cierre normal: se le cierra el stdin y sale por su cuenta."""
        if self.popen.stdin and not self.popen.stdin.closed:
            self.popen.stdin.close()
        return self.popen.wait(timeout=ESPERA_MAXIMA_SEGUNDOS)

    def matar(self) -> None:
        """Muerte abrupta: sin `finally`, sin shutdown, sin ninguna limpieza del programa."""
        self.popen.kill()
        self.popen.wait(timeout=ESPERA_MAXIMA_SEGUNDOS)

    def informo(self, nombre: str) -> bool:
        return any(evento["evento"] == nombre for evento in self.eventos)


@pytest.fixture
def procesos():
    """Fábrica de procesos: ninguno sobrevive al test, pase lo que pase con él."""
    creados: list[Proceso] = []

    def crear(*argumentos: object) -> Proceso:
        proceso = Proceso(*argumentos)
        creados.append(proceso)
        return proceso

    yield crear
    for proceso in creados:
        if proceso.popen.poll() is None:
            proceso.popen.kill()
            proceso.popen.wait(timeout=ESPERA_MAXIMA_SEGUNDOS)


@pytest.fixture
def ruta_db(tmp_path) -> Path:
    directorio = tmp_path / "instalacion" / "data"
    directorio.mkdir(parents=True)
    return directorio / "kiosco.db"


@pytest.fixture
def propietario(procesos, ruta_db):
    """Otro proceso vivo que es dueño de `ruta_db`."""
    proceso = procesos("tomar", ruta_db)
    proceso.esperar("listo")
    return proceso


@pytest.fixture
def base_con_propietario(base_datos_temporal, procesos):
    """Base real ya migrada, con otro proceso vivo como dueño."""
    proceso = procesos("tomar", base_datos_temporal)
    proceso.esperar("listo")
    return base_datos_temporal


@pytest.fixture
def comprobante_pendiente(base_datos_temporal, caja_abierta) -> int:
    uf.guardar_perfil_validado()
    venta = uf.vender([(uf.producto(tratamiento=uf.IVA_21), 1)])
    return uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))


def _huella(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def _comprobante(comprobante_id: int):
    with modulo_conexion.obtener_conexion() as conexion:
        return repositorio_fiscal.obtener_comprobante_en_conexion(conexion, comprobante_id)


def _puerto_libre() -> int:
    with socket.socket() as candidato:
        candidato.bind(("127.0.0.1", 0))
        return candidato.getsockname()[1]


def _escucha(puerto: int) -> bool:
    with socket.socket() as candidato:
        candidato.settimeout(0.5)
        return candidato.connect_ex(("127.0.0.1", puerto)) == 0


# --- H5: test de aceptación principal --------------------------------------------------------------------


def test_h5_el_segundo_proceso_no_toca_el_enviando_vivo_del_primero(procesos, base_datos_temporal, comprobante_pendiente):
    proceso_a = procesos("arrancar", base_datos_temporal, comprobante_pendiente)
    proceso_a.esperar("listo")
    assert _comprobante(comprobante_pendiente).estado == "ENVIANDO"
    huella = _huella(base_datos_temporal)

    proceso_b = procesos("arrancar", base_datos_temporal)
    rechazo = proceso_b.esperar("rechazada", "listo")

    assert rechazo["evento"] == "rechazada" and rechazo["tipo"] == "InstanciaEnUsoError"
    assert proceso_b.terminar() == CODIGO_RECHAZADA
    assert not proceso_b.informo("recuperacion")
    assert not proceso_b.informo("migracion")
    assert _comprobante(comprobante_pendiente).estado == "ENVIANDO"
    assert _huella(base_datos_temporal) == huella


@pytest.mark.parametrize("cierre", ["terminar", "matar"])
def test_h5_al_irse_el_primero_el_tercero_recupera_con_la_logica_conservadora(
    procesos, base_datos_temporal, comprobante_pendiente, cierre
):
    proceso_a = procesos("arrancar", base_datos_temporal, comprobante_pendiente)
    proceso_a.esperar("listo")
    getattr(proceso_a, cierre)()
    assert _comprobante(comprobante_pendiente).estado == "ENVIANDO"

    proceso_c = procesos("arrancar", base_datos_temporal)

    assert proceso_c.esperar("recuperacion")["comprobantes"] == 1
    proceso_c.esperar("listo")
    comprobante = _comprobante(comprobante_pendiente)
    assert comprobante.estado == "INCIERTO"
    assert comprobante.numero == 1


# --- Exclusión entre procesos ----------------------------------------------------------------------------


def test_misma_base_solo_un_proceso_la_adquiere(procesos, ruta_db, propietario):
    segundo = procesos("tomar", ruta_db)

    assert segundo.esperar("rechazada", "listo")["evento"] == "rechazada"
    assert segundo.terminar() == CODIGO_RECHAZADA


def test_bases_distintas_conviven(procesos, ruta_db, propietario):
    otra = procesos("tomar", ruta_db.with_name("otra.db"))

    assert otra.esperar("rechazada", "listo")["evento"] == "listo"


def test_el_propietario_termina_normalmente_y_otro_adquiere(procesos, ruta_db, propietario):
    assert propietario.terminar() == 0

    assert procesos("tomar", ruta_db).esperar("rechazada", "listo")["evento"] == "listo"


def test_el_propietario_muere_abruptamente_y_otro_adquiere(procesos, ruta_db, propietario):
    propietario.matar()

    assert procesos("tomar", ruta_db).esperar("rechazada", "listo")["evento"] == "listo"


def test_un_archivo_de_lock_abandonado_no_bloquea(procesos, ruta_db):
    """La existencia del archivo nunca es el lock, por viejo que sea."""
    ruta_lock = instancia_unica.ruta_lock(ruta_db)
    ruta_lock.parent.mkdir(parents=True)
    ruta_lock.touch()
    os.utime(ruta_lock, (0, 0))

    assert procesos("tomar", ruta_db).esperar("rechazada", "listo")["evento"] == "listo"


def test_el_cierre_no_borra_el_archivo_de_lock(procesos, ruta_db, propietario):
    """Borrarlo abriría una carrera: otro proceso podría estar bloqueando el archivo que se borra."""
    assert propietario.terminar() == 0

    assert instancia_unica.ruta_lock(ruta_db).is_file()


def test_intentos_casi_simultaneos_gana_exactamente_uno(procesos, ruta_db):
    inicio = time.time() + 6
    competidores = [procesos("tomar", ruta_db, inicio) for _ in range(6)]

    resultados = [competidor.esperar("rechazada", "listo")["evento"] for competidor in competidores]

    assert resultados.count("listo") == 1
    assert resultados.count("rechazada") == 5


def test_la_segunda_instancia_no_migra_ni_recupera(procesos, base_datos_temporal):
    primero = procesos("arrancar", base_datos_temporal)
    primero.esperar("listo")
    assert primero.informo("migracion") and primero.informo("recuperacion")

    segundo = procesos("arrancar", base_datos_temporal)
    segundo.esperar("rechazada")

    assert segundo.terminar() == CODIGO_RECHAZADA
    assert not segundo.informo("migracion")
    assert not segundo.informo("recuperacion")


def test_otro_puerto_no_esquiva_el_lock(procesos, base_datos_temporal):
    """Uvicorn real en dos puertos: era el agujero documentado, el puerto no protege la base."""
    puerto_a, puerto_b = _puerto_libre(), _puerto_libre()
    servidor_a = procesos("servir", base_datos_temporal, puerto_a)
    servidor_a.esperar("listo")
    assert _escucha(puerto_a)

    servidor_b = procesos("servir", base_datos_temporal, puerto_b)

    assert servidor_b.esperar("rechazada", "listo")["tipo"] == "InstanciaEnUsoError"
    assert servidor_b.terminar() == CODIGO_RECHAZADA
    assert not servidor_b.informo("recuperacion")
    assert not _escucha(puerto_b)
    assert _escucha(puerto_a)


# --- Identidad de la base --------------------------------------------------------------------------------


def _variantes_de_ruta(ruta_db: Path) -> list[str]:
    variantes = [
        str(ruta_db.parent / ".." / ruta_db.parent.name / ruta_db.name),
        str(ruta_db).replace(os.sep, "/"),
        os.path.relpath(ruta_db),
    ]
    if sys.platform == "win32":
        variantes += [str(ruta_db).upper(), str(ruta_db).lower()]
    return variantes


def test_rutas_equivalentes_son_la_misma_instancia(ruta_db, propietario):
    for variante in _variantes_de_ruta(ruta_db):
        assert instancia_unica.ruta_lock(variante) == instancia_unica.ruta_lock(ruta_db), variante
        with pytest.raises(InstanciaEnUsoError):
            with instancia_unica.exclusiva(Path(variante)):
                pass


def test_un_enlace_a_la_carpeta_de_datos_es_la_misma_instancia(tmp_path, ruta_db, propietario):
    enlace = tmp_path / "enlace_a_datos"
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(enlace), str(ruta_db.parent)], check=True, capture_output=True)
    else:
        enlace.symlink_to(ruta_db.parent, target_is_directory=True)

    with pytest.raises(InstanciaEnUsoError):
        with instancia_unica.exclusiva(enlace / ruta_db.name):
            pass


def test_el_lock_no_guarda_nada_sensible(ruta_db, propietario):
    ruta_lock = instancia_unica.ruta_lock(ruta_db)

    assert re.fullmatch(r"[0-9a-f]{64}\.lock", ruta_lock.name)
    assert ruta_lock.stat().st_size == 0
    propietario.terminar()
    assert ruta_lock.read_bytes() == b""


def test_el_lock_queda_fuera_de_la_carpeta_de_datos(ruta_db):
    """`data/` es lo que viaja en los backups y lo que el restore renombra: un handle abierto ahí adentro
    haría fallar el renombrado en Windows."""
    assert ruta_db.parent not in instancia_unica.ruta_lock(ruta_db).parents


# --- Dentro de un mismo proceso --------------------------------------------------------------------------


def test_el_mismo_proceso_puede_anidar_y_conserva_la_instancia_hasta_el_ultimo_cierre(procesos, ruta_db):
    with instancia_unica.exclusiva(ruta_db):
        with instancia_unica.exclusiva(ruta_db):
            pass
        ajeno = procesos("tomar", ruta_db)
        assert ajeno.esperar("rechazada", "listo")["evento"] == "rechazada"

    assert procesos("tomar", ruta_db).esperar("rechazada", "listo")["evento"] == "listo"


def test_una_excepcion_dentro_libera_la_instancia(procesos, ruta_db):
    with pytest.raises(RuntimeError):
        with instancia_unica.exclusiva(ruta_db):
            raise RuntimeError("fallo durante el arranque")

    assert procesos("tomar", ruta_db).esperar("rechazada", "listo")["evento"] == "listo"


def test_un_rechazo_no_deja_una_tenencia_logica_sin_lock_real(procesos, ruta_db, propietario):
    """Si el rechazo dejara la tenencia anotada, el próximo intento entraría sin bloquear nada en el sistema."""
    with pytest.raises(InstanciaEnUsoError):
        with instancia_unica.exclusiva(ruta_db):
            pytest.fail("No debió entrar: la base tiene dueño.")
    propietario.terminar()

    with instancia_unica.exclusiva(ruta_db):
        assert procesos("tomar", ruta_db).esperar("rechazada", "listo")["evento"] == "rechazada"


def test_un_fallo_del_sistema_al_bloquear_cierra_el_descriptor(procesos, ruta_db, monkeypatch):
    descriptores = []

    def fallar(descriptor: int) -> None:
        descriptores.append(descriptor)
        raise OSError(errno.EIO, "fallo simulado")

    with monkeypatch.context() as parche:
        parche.setattr(instancia_unica, "_bloquear", fallar)
        with pytest.raises(ErrorBaseDatos):
            with instancia_unica.exclusiva(ruta_db):
                pytest.fail("No debió entrar sin lock.")

    with pytest.raises(OSError):
        os.fstat(descriptores[0])
    with instancia_unica.exclusiva(ruta_db):
        assert procesos("tomar", ruta_db).esperar("rechazada", "listo")["evento"] == "rechazada"


def test_hilos_concurrentes_comparten_la_tenencia_y_la_sueltan_una_sola_vez(procesos, ruta_db):
    """Sin el candado, dos hilos abrirían dos descriptores y el segundo chocaría con el lock del primero."""
    cantidad_hilos = 8
    barrera = threading.Barrier(cantidad_hilos)
    errores: list[Exception] = []

    def competir() -> None:
        barrera.wait()
        try:
            for _ in range(200):
                with instancia_unica.exclusiva(ruta_db):
                    pass
        except (OSError, KeyError, ErrorBaseDatos, InstanciaEnUsoError) as error:
            errores.append(error)

    hilos = [threading.Thread(target=competir) for _ in range(cantidad_hilos)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    assert errores == []
    assert procesos("tomar", ruta_db).esperar("rechazada", "listo")["evento"] == "listo"


@pytest.fixture
def base_sin_lock_posible(tmp_path) -> Path:
    """Un archivo ocupa el lugar de la carpeta `locks/`: el lock no se puede crear."""
    ocupa_el_lugar = tmp_path / "instalacion" / "locks"
    ocupa_el_lugar.parent.mkdir()
    ocupa_el_lugar.write_text("un archivo donde debería ir la carpeta", encoding="utf-8")
    return tmp_path / "instalacion" / "data" / "kiosco.db"


def test_sin_poder_crear_el_lock_no_arranca(base_sin_lock_posible):
    """Sin lock no hay garantía de exclusión: falla cerrado, con un error distinto del de instancia en uso."""
    with pytest.raises(ErrorBaseDatos):
        with instancia_unica.exclusiva(base_sin_lock_posible):
            pytest.fail("No debió entrar sin lock.")


def test_sin_lock_el_operador_ve_la_causa_sin_traceback(base_sin_lock_posible, monkeypatch, capsys):
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", base_sin_lock_posible)
    monkeypatch.setattr("builtins.input", lambda *_: "")
    evento_listo = threading.Event()

    lanzador.iniciar_servidor(evento_listo)

    salida = capsys.readouterr().out
    assert "TWINX EXPRESS NO PUDO INICIAR" in salida
    assert "No se pudo comprobar si Twinx Express ya está abierto" in salida
    assert "Traceback" not in salida
    assert not evento_listo.is_set()


def test_sin_lock_el_restore_lo_explica_sin_traceback(base_sin_lock_posible, monkeypatch, capsys):
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", base_sin_lock_posible)
    monkeypatch.setattr("builtins.input", lambda *_: "")
    monkeypatch.setattr(lanzador, "_configurar_logging", lambda: None)

    codigo = lanzador._modo_restore(
        "backup.zip", lanzador._importar_restaurar_desde_backup, aplicacion_en_uso=lambda: False
    )

    salida = capsys.readouterr().out
    assert codigo == 1
    assert "No se pudo comprobar si Twinx Express ya está abierto" in salida
    assert "Traceback" not in salida


# --- Operador --------------------------------------------------------------------------------------------


def test_el_operador_ve_un_mensaje_claro_al_abrir_dos_veces(procesos, ruta_db, propietario):
    segundo = procesos("lanzador", ruta_db)

    fin = segundo.esperar("fin")
    salida = "".join(segundo.salida)

    assert "Twinx Express ya está abierto usando esta base de datos." in salida
    assert "Traceback" not in salida
    assert "sqlite" not in salida.lower()
    assert not fin["servidor_listo"]
    assert not fin["app_importada"]


def test_el_lanzador_es_dueno_de_la_base_mientras_corre_el_servidor(procesos, ruta_db, monkeypatch):
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta_db)
    durante_el_servidor = []

    def servidor_falso(_evento) -> None:
        durante_el_servidor.append(procesos("tomar", ruta_db).esperar("rechazada", "listo")["evento"])

    monkeypatch.setattr(lanzador, "_ejecutar_servidor", servidor_falso)

    lanzador._importar_y_ejecutar_servidor(threading.Event())

    assert durante_el_servidor == ["rechazada"]
    assert procesos("tomar", ruta_db).esperar("rechazada", "listo")["evento"] == "listo"


# --- Operaciones técnicas --------------------------------------------------------------------------------


@pytest.fixture
def sin_consola(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: "")
    monkeypatch.setattr(lanzador, "_configurar_logging", lambda: None)


def test_restore_no_corre_con_una_instancia_viva(base_con_propietario, tmp_path, monkeypatch):
    monkeypatch.setattr(servicio_restore, "DIRECTORIO_DATA", base_con_propietario.parent)
    huella = _huella(base_con_propietario)

    with pytest.raises(InstanciaEnUsoError):
        servicio_restore.restaurar_desde_backup(tmp_path / "cualquiera.zip")

    assert _huella(base_con_propietario) == huella


def test_el_comando_restore_lo_explica_sin_traceback(base_con_propietario, sin_consola, capsys):
    codigo = lanzador._modo_restore(
        "backup.zip", lanzador._importar_restaurar_desde_backup, aplicacion_en_uso=lambda: False
    )

    salida = capsys.readouterr().out
    assert codigo == 1
    assert "NO SE RESTAURÓ NADA" in salida
    assert "Traceback" not in salida


def test_rollback_no_corre_con_una_instancia_viva(base_con_propietario, tmp_path):
    with pytest.raises(InstanciaEnUsoError):
        servicio_actualizacion.rollback("0.0.1", tmp_path, False)


def test_actualizar_no_corre_con_una_instancia_viva(base_con_propietario, tmp_path):
    with pytest.raises(InstanciaEnUsoError):
        servicio_actualizacion.actualizar(tmp_path / "instalada", tmp_path / "nueva", False)


def test_habilitar_produccion_no_corre_con_una_instancia_viva(base_con_propietario, sin_consola, capsys):
    uf.guardar_perfil_validado()
    huella = _huella(base_con_propietario)

    codigo = lanzador._ejecutar_mantenimiento(
        "--habilitar-produccion-fiscal", uf.CUIT_EMISOR, aplicacion_en_uso=lambda: False
    )

    assert codigo == 1
    assert "ya está abierto" in capsys.readouterr().out
    assert _huella(base_con_propietario) == huella


def test_la_cli_no_abre_una_base_con_una_instancia_viva(base_con_propietario, monkeypatch, capsys):
    from interfaces.cli import main as cli

    monkeypatch.setattr(cli, "inicializar_base_datos", lambda: pytest.fail("No debió tocar la base."))
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("No debió mostrar el menú."))

    cli.main()

    assert "ya está abierto" in capsys.readouterr().out


@pytest.mark.filterwarnings("ignore:.*found in sys.modules.*:RuntimeWarning")
def test_migrar_a_mano_no_corre_con_una_instancia_viva(base_con_propietario, monkeypatch):
    """`python -m db.conexion`. Esa copia del módulo lee la ruta de `config`, no la parcheada de `db.conexion`."""
    monkeypatch.setattr(config, "RUTA_BASE_DATOS", base_con_propietario)
    huella = _huella(base_con_propietario)

    with pytest.raises(InstanciaEnUsoError):
        runpy.run_module("db.conexion", run_name="__main__")

    assert _huella(base_con_propietario) == huella


def test_el_backup_sigue_funcionando_con_la_aplicacion_abierta(base_con_propietario, tmp_path):
    """Usa la Online Backup API de SQLite: copiar con la app abierta es seguro y no se bloquea."""
    ruta = servicio_backup.crear_backup(tmp_path / "backups_con_app_abierta", ControlEscrituras())

    with zipfile.ZipFile(ruta) as paquete:
        assert not [nombre for nombre in paquete.namelist() if nombre.endswith(".lock")]
