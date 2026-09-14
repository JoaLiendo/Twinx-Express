import os
import sys
import threading
import time
import webbrowser
import traceback

from excepciones import ErrorRestore

if getattr(sys, 'frozen', False):
    base_dir = sys._MEIPASS
    os.chdir(base_dir)
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)

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
    from interfaces.web.app import app

    config = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="info")
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
    finally:
        servidor_terminado.set()
        hilo_vigilante.join(timeout=1)


def iniciar_servidor(evento_servidor_listo, ejecutar=_importar_y_ejecutar_servidor):
    """Arranca el servidor, con manejo de errores distinto según el caso.

    Un conflicto de migración de datos (`config.ErrorMigracionDatos`) ya
    trae un mensaje pensado para el usuario final: se muestra tal cual,
    sin traceback técnico. Cualquier otro error es inesperado y conserva
    el diagnóstico completo. Un fallo de arranque de Uvicorn (puerto ya
    ocupado, ver `except SystemExit` más abajo) muestra su propio
    mensaje claro. En todos los casos `evento_servidor_listo` queda
    sin marcar, para que el hilo principal no abra el navegador.
    """
    try:
        ejecutar(evento_servidor_listo)
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
    except SystemExit:
        # Uvicorn 0.52.4 (versión instalada, ver `server.py::Server.startup`)
        # señaliza un fallo de bind con `sys.exit(...)`, no con una
        # excepción común -- `SystemExit` no hereda de `Exception` a
        # propósito en Python, así que necesita su propia rama acá para
        # no dejar el hilo terminando en silencio sin ningún mensaje.
        print("\n--- EL SERVIDOR NO PUDO INICIAR ---")
        print(
            "Uvicorn no pudo arrancar (revisá el mensaje de arriba; "
            "es habitual que se deba a que el puerto 8000 ya está en uso "
            "por otro programa)."
        )
        input("\nPresiona Enter para cerrar esta ventana...")


def abrir_navegador_si_corresponde(evento_servidor_listo, abrir=webbrowser.open):
    """Abre el navegador solo si el servidor realmente llegó a iniciar."""
    if evento_servidor_listo.is_set():
        abrir("http://127.0.0.1:8000")


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

    time.sleep(1.5)
    abrir_navegador_si_corresponde(evento_servidor_listo)

    hilo_servidor.join()
