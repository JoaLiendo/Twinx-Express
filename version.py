"""Identidad y versión de la aplicación (única fuente de verdad).

No importa nada del proyecto ni tiene efectos secundarios, a propósito:
la leen la interfaz web (pie de página) y `KioscoApp.spec` (metadata del
ejecutable) -- este último no puede importar `config.py`, que crea
directorios al cargarse. Al cambiar la versión, actualizar también el
título de `LEEME.txt` (un test verifica que coincidan).
"""

NOMBRE_APLICACION = "Twinx Express"
VERSION = "1.0.0"
