import os
import sys
import threading
import time
import webbrowser
import uvicorn

# Configurar el directorio base correcto para cuando está empaquetado (.exe)
if getattr(sys, 'frozen', False):
    # PyInstaller crea una carpeta temporal y guarda los archivos ahí
    base_dir = sys._MEIPASS
    os.chdir(base_dir)
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)

from interfaces.web.app import app

def iniciar_servidor():
    # Usamos uvicorn pasando la app directamente y desactivando recarga
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")

if __name__ == "__main__":
    # Iniciar FastAPI en un hilo secundario
    hilo_servidor = threading.Thread(target=iniciar_servidor, daemon=True)
    hilo_servidor.start()

    # Dar 1.5 segundos para que el servidor levante
    time.sleep(1.5)
    
    # Abrir el navegador automáticamente
    webbrowser.open("http://127.0.0.1:8000")

    # Mantener la aplicación viva
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        sys.exit(0)