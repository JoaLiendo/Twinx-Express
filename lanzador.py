import logging
import os
import socket
import sys
import threading
import time
import webbrowser
import traceback

from excepciones import ErrorInicioServidor, ErrorRestore, PuertoOcupadoError

if getattr(sys, 'frozen', False):
    base_dir = sys._MEIPASS
    os.chdir(base_dir)
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)

HOST = "127.0.0.1"
PUERTO = 8000
URL_APLICACION = f"http://{HOST}:{PUERTO}"

# Tiempo máximo que se espera a que el servidor esté disponible antes de
# renunciar a abrir el navegador (un primer arranque con antivirus, o una
# actualización con backup + migración previos, puede tardar bastante más
# que un arranque normal).
ESPERA_MAXIMA_ARRANQUE_SEGUNDOS = 30.0
INTERVALO_ESPERA_ARRANQUE_SEGUNDOS = 0.25


def _puerto_ocupado(host: str = HOST, puerto: int = PUERTO) -> bool:
    """True si no se puede hacer bind en `host:puerto` (ya hay algo escuchando)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidato:
        try:
            candidato.bind((host, puerto))
        except OSError:
            return True
    return False


def _configurar_logging() -> None:
    """Activa el log persistente en `%LOCALAPPDATA%/KioscoApp/logs/`. Un fallo
    al crear el archivo (permisos, disco) se avisa por consola pero nunca
    impide arrancar: el registro es una ayuda de soporte, no un requisito."""
    try:
        from config import DIRECTORIO_LOGS
        from logging_config import configurar_logging
        from version import VERSION

        ruta_log = configurar_logging(DIRECTORIO_LOGS)
    except OSError as error:
        print(f"Aviso: no se pudo crear el archivo de registro ({error}). Se continúa sin él.")
        return
    logging.getLogger("lanzador").info("Twinx Express %s: registro en %s", VERSION, ruta_log)


def _resumen_de_causa(causa: BaseException | None) -> str:
    if causa is None:
        return "sin detalle (ver el mensaje de arriba)"
    texto = f"{type(causa).__name__}: {causa}"
    return texto if len(texto) <= 300 else texto[:297] + "..."


def _importar_y_ejecutar_servidor(evento_servidor_listo):
    """Importa la app y arranca uvicorn.

    Aislado en su propia función para poder reemplazarla en tests sin
    depender de FastAPI/uvicorn reales. `evento_servidor_listo` se
    marca recién cuando Uvicorn confirma que el servidor realmente
    arrancó (`server.started`, atributo público de `uvicorn.Server`
    pensado exactamente para esto: ver `uvicorn/server.py`), nunca
    antes -- si se marcara solo por haber importado la app con éxito,
    el hilo principal abriría el navegador igual aunque el puerto 8000
    ya estuviera ocupado y el servidor nunca hubiera llegado a
    escuchar (bug real encontrado en auditoría de Stage D).

    Se usa `uvicorn.Config`/`uvicorn.Server` en vez del atajo
    `uvicorn.run(...)` únicamente para poder observar `server.started`
    desde un hilo vigilante mientras `server.run()` sigue bloqueando
    este hilo (arranca y sirve hasta que se cierra la app) -- no
    cambia ningún comportamiento de red del servidor en sí.
    """
    import threading
    import time

    import uvicorn

    # Antes de importar la app y correr el lifespan (migraciones, seed, backup):
    # una segunda instancia no debe tocar la base de datos si el puerto ya
    # está en uso, y el mensaje correcto es el de puerto ocupado.
    if _puerto_ocupado():
        raise PuertoOcupadoError(f"El puerto {PUERTO} ya está en uso.")

    from interfaces.web.app import app

    config = uvicorn.Config(app, host=HOST, port=PUERTO, log_level="info")
    # Después de `uvicorn.Config`: reconfigura los loggers de uvicorn.
    _configurar_logging()
    server = uvicorn.Server(config)
    servidor_terminado = threading.Event()

    def _vigilar_arranque():
        while not server.started and not servidor_terminado.is_set():
            time.sleep(0.05)
        if server.started:
            evento_servidor_listo.set()

    hilo_vigilante = threading.Thread(target=_vigilar_arranque, daemon=True)
    hilo_vigilante.start()
    try:
        server.run()
    except SystemExit as salida:
        # Uvicorn señaliza con `SystemExit` tanto un bind fallido como un fallo
        # del lifespan. Si el puerto está libre, no fue el bind: la causa real
        # la dejó registrada `interfaces.web.app.lifespan` en `app.state`.
        if server.started:
            raise
        if _puerto_ocupado():
            raise PuertoOcupadoError(f"El puerto {PUERTO} ya está en uso.") from None
        causa = getattr(app.state, "error_de_inicio", None)
        raise ErrorInicioServidor(_resumen_de_causa(causa)) from causa
    finally:
        servidor_terminado.set()
        hilo_vigilante.join(timeout=1)


def _imprimir_orientacion_de_datos() -> None:
    """Dónde están los datos y qué hacer si la base está dañada. `config`
    se importa acá y no arriba por el mismo motivo que el resto de imports
    diferidos de este módulo; si no se puede importar, solo se da la pista
    general."""
    try:
        from config import DIRECTORIO_BACKUPS, DIRECTORIO_DATA
    except Exception:  # noqa: BLE001 -- solo se degrada el texto de ayuda
        DIRECTORIO_BACKUPS = DIRECTORIO_DATA = None
    if DIRECTORIO_DATA is not None:
        print(f"Datos:   {DIRECTORIO_DATA}")
        print(f"Backups: {DIRECTORIO_BACKUPS}")
    print(
        "Si el problema es la base de datos, podés volver a un backup anterior con "
        "Restaurar_backup.bat (arrastrá el .zip más reciente de la carpeta de backups)."
    )


def iniciar_servidor(evento_servidor_listo, ejecutar=_importar_y_ejecutar_servidor):
    """Arranca el servidor, con manejo de errores distinto según el caso.

    Un conflicto de migración de datos (`config.ErrorMigracionDatos`) ya
    trae un mensaje pensado para el usuario final: se muestra tal cual,
    sin traceback técnico. Un puerto ocupado (`PuertoOcupadoError`) y un
    fallo real de arranque (`ErrorInicioServidor`: base de datos,
    migración, seed o backup) se distinguen y cada uno muestra su propio
    mensaje. Cualquier otro error es inesperado y conserva el diagnóstico
    completo. En todos los casos `evento_servidor_listo` queda sin marcar,
    para que el hilo principal no abra el navegador.
    """
    try:
        ejecutar(evento_servidor_listo)
    except PuertoOcupadoError:
        print("\n--- EL SERVIDOR NO PUDO INICIAR ---")
        print(f"Twinx Express ya está ejecutándose o el puerto {PUERTO} está ocupado por otro programa.")
        print(f"Si ya lo tenés abierto, usá esa ventana o entrá a {URL_APLICACION} desde el navegador.")
        input("\nPresiona Enter para cerrar esta ventana...")
    except ErrorInicioServidor as error:
        print("\n--- TWINX EXPRESS NO PUDO INICIAR ---")
        print(f"Causa: {error}")
        _imprimir_orientacion_de_datos()
        input("\nPresiona Enter para cerrar esta ventana...")
    except Exception as error:
        # No se importa `config.ErrorMigracionDatos` para comparar por
        # tipo: si el conflicto ya rompió la carga de `config`, Python
        # descarta ese módulo de `sys.modules`, y reimportarlo acá
        # dispararía otra vez el mismo error en lugar de compararlo. Se
        # identifica por nombre de clase sobre la excepción ya capturada.
        if type(error).__name__ == "ErrorMigracionDatos":
            print("\n--- CONFLICTO DE DATOS AL INICIAR ---")
            print(str(error))
        else:
            print("\n--- ERROR CRITICO AL INICIAR EL SERVIDOR ---")
            traceback.print_exc()
        input("\nPresiona Enter para cerrar esta ventana...")


def abrir_navegador_cuando_este_listo(
    evento_servidor_listo,
    abrir=webbrowser.open,
    *,
    servidor_activo=lambda: True,
    espera_maxima_segundos: float = ESPERA_MAXIMA_ARRANQUE_SEGUNDOS,
    intervalo_segundos: float = INTERVALO_ESPERA_ARRANQUE_SEGUNDOS,
    reloj=time.monotonic,
) -> bool:
    """Espera a que el servidor confirme que arrancó y recién entonces abre
    el navegador. Devuelve `True` si lo abrió.

    Reemplaza la espera fija de 1.5 s: un arranque lento (antivirus,
    migración, backup preventivo) ya no deja al operador sin navegador. La
    espera es bloqueante sobre el evento (sin polling agresivo), se corta en
    cuanto el hilo del servidor termina (un arranque fallido no hace esperar
    los 30 s) y nunca es indefinida.
    """
    limite = reloj() + espera_maxima_segundos
    while reloj() < limite:
        if evento_servidor_listo.wait(intervalo_segundos):
            abrir(URL_APLICACION)
            return True
        if not servidor_activo():
            return False
    return False


def _ruta_zip_restore_o_none(argv):
    """Devuelve la ruta del ZIP si `argv` es `[..., "--restore", ruta]`,
    o `None` en cualquier otro caso (incluida la ejecución normal)."""
    if len(argv) >= 3 and argv[1] == "--restore":
        return argv[2]
    return None


def _importar_restaurar_desde_backup():
    """Import diferido: mismo motivo que en `iniciar_servidor` (evitar
    disparar la resolución de rutas de `config.py` antes de tiempo)."""
    from services.servicio_restore import restaurar_desde_backup

    return restaurar_desde_backup


def _modo_restore(ruta_zip, obtener_ejecutar_restore):
    """Ejecuta `KioscoApp.exe --restore <ruta_zip>`: no inicia FastAPI
    ni abre el navegador, solo corre la restauración y termina.

    Un ZIP inválido (`ErrorRestore`) o un conflicto de datos ya
    detectado por `config.py` (`ErrorMigracionDatos`) muestran un
    mensaje claro sin traceback; cualquier otro error es inesperado y
    conserva el diagnóstico completo. Devuelve el código de salida del
    proceso (0 éxito, 1 error).
    """
    try:
        _configurar_logging()
        ejecutar_restore = obtener_ejecutar_restore()
        ejecutar_restore(ruta_zip)
    except Exception as error:
        if isinstance(error, ErrorRestore) or type(error).__name__ == "ErrorMigracionDatos":
            print("\n--- ERROR AL RESTAURAR EL BACKUP ---")
            print(str(error))
        else:
            print("\n--- ERROR CRITICO AL RESTAURAR EL BACKUP ---")
            traceback.print_exc()
        input("\nPresiona Enter para cerrar esta ventana...")
        return 1

    print(f"\nRestore completado correctamente desde: {ruta_zip}")
    print("Se recomienda reiniciar la aplicación antes de usarla.")
    input("\nPresiona Enter para cerrar esta ventana...")
    return 0


if __name__ == "__main__":
    _ruta_zip_restore = _ruta_zip_restore_o_none(sys.argv)
    if _ruta_zip_restore is not None:
        sys.exit(_modo_restore(_ruta_zip_restore, _importar_restaurar_desde_backup))

    evento_servidor_listo = threading.Event()
    hilo_servidor = threading.Thread(
        target=iniciar_servidor, args=(evento_servidor_listo,), daemon=False
    )
    hilo_servidor.start()

    abrir_navegador_cuando_este_listo(evento_servidor_listo, servidor_activo=hilo_servidor.is_alive)

    hilo_servidor.join()
