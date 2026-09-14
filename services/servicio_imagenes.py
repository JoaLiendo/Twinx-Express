"""Validación y almacenamiento de imágenes de producto (Fase 3D).

Encapsula todo el manejo de archivos: ningún otro módulo (ni
`services/servicio_stock.py` ni `interfaces/web/`) debe tocar el
filesystem de imágenes directamente, mismo principio que
`db/conexion.py` siendo el único lugar que abre conexiones sqlite3.

No depende de Pillow ni de ninguna librería de imágenes: valida por
firma binaria (magic bytes) con bytes planos de la librería estándar.
`imghdr` -- el módulo histórico para esto -- fue removido de Python
(deprecado desde 3.11, quitado en 3.13); no está disponible en este
proyecto (ver auditoría de Fase 3D), así que la detección de formato
se hace a mano acá.
"""

import logging
import secrets
from pathlib import Path

from config import DIRECTORIO_IMAGENES_PRODUCTOS
from excepciones import ArchivoImagenInvalidoError

logger = logging.getLogger(__name__)

# 2MB: suficiente para una foto de producto de kiosco sin redimensionar
# (sin Pillow no hay redimensionado del lado del servidor -- lo que se
# sube es lo que se guarda tal cual), y bajo para no permitir que un
# archivo cualquiera infle `data/` sin control.
TAMANO_MAXIMO_BYTES = 2 * 1024 * 1024

EXTENSIONES_PERMITIDAS = frozenset({"jpg", "png", "webp"})

# Firmas binarias (magic bytes) de cada formato soportado. WEBP es un
# contenedor RIFF: hace falta chequear también el subtipo en el byte 8,
# o un archivo RIFF que no sea WEBP (ej. un .wav renombrado) pasaría el
# primer chequeo solo por el prefijo "RIFF".
_FIRMA_JPEG = b"\xff\xd8\xff"
_FIRMA_PNG = b"\x89PNG\r\n\x1a\n"
_FIRMA_RIFF = b"RIFF"
_FIRMA_WEBP = b"WEBP"


def _normalizar_extension(extension: str) -> str:
    """'.JPEG'/'jpeg' -> 'jpg'; el resto, en minúscula tal cual."""
    extension = extension.lower().lstrip(".")
    return "jpg" if extension == "jpeg" else extension


def _detectar_formato_real(contenido: bytes) -> str | None:
    """Determina el formato de imagen a partir de los bytes reales del
    archivo, no de lo que declare su nombre o `Content-Type`."""
    if contenido.startswith(_FIRMA_JPEG):
        return "jpg"
    if contenido.startswith(_FIRMA_PNG):
        return "png"
    if contenido[:4] == _FIRMA_RIFF and contenido[8:12] == _FIRMA_WEBP:
        return "webp"
    return None


def validar_y_guardar(producto_id: int, contenido: bytes, nombre_original: str) -> str:
    """Valida una imagen subida y la guarda en disco.

    Tres validaciones independientes, ninguna confía en la otra:
    tamaño máximo, extensión declarada permitida, y que la firma
    binaria real del archivo coincida con esa extensión (nunca se
    confía solo en la extensión ni en el `Content-Type` del navegador).

    Devuelve el nombre de archivo generado por el servidor (nunca el
    nombre original) -- listo para guardar en `productos.imagen_archivo`.

    Raises:
        ArchivoImagenInvalidoError: archivo vacío, demasiado grande,
            extensión no soportada, o contenido que no coincide con
            ningún formato de imagen soportado.
    """
    if not contenido:
        raise ArchivoImagenInvalidoError("El archivo de imagen está vacío.")
    if len(contenido) > TAMANO_MAXIMO_BYTES:
        raise ArchivoImagenInvalidoError(
            f"La imagen supera el tamaño máximo permitido ({TAMANO_MAXIMO_BYTES // (1024 * 1024)}MB)."
        )

    extension = _normalizar_extension(Path(nombre_original or "").suffix)
    if extension not in EXTENSIONES_PERMITIDAS:
        raise ArchivoImagenInvalidoError(
            f"Formato de imagen no soportado: '{extension or nombre_original}'. Usar JPG, PNG o WEBP."
        )

    formato_real = _detectar_formato_real(contenido)
    if formato_real is None:
        raise ArchivoImagenInvalidoError(
            "El contenido del archivo no coincide con ningún formato de imagen soportado."
        )
    if formato_real != extension:
        raise ArchivoImagenInvalidoError(
            f"El archivo dice ser '.{extension}' pero su contenido real es '.{formato_real}'."
        )

    # Nombre generado por el servidor: nunca depende del nombre original
    # subido (evita path traversal y que el nombre del producto o del
    # archivo del usuario terminen en el filesystem).
    nombre_archivo = f"{producto_id}_{secrets.token_hex(8)}.{extension}"
    ruta = DIRECTORIO_IMAGENES_PRODUCTOS / nombre_archivo
    try:
        ruta.write_bytes(contenido)
    except OSError as error:
        # Disco lleno, permisos, ruta demasiado larga, etc. (hallazgo
        # menor #1 de la auditoría de Fase 3D): se traduce a la misma
        # excepción de aplicación que ya usan el resto de las
        # validaciones de esta función, para que quede controlada por
        # el manejador genérico de `app.py` en vez de propagar un
        # `OSError` crudo como un 500 sin manejar. Ocurre antes de
        # tocar la base de datos y antes de borrar ninguna imagen
        # anterior, así que no hay nada que revertir.
        raise ArchivoImagenInvalidoError(f"No se pudo guardar la imagen en el disco: {error}") from error
    logger.info("Imagen guardada para producto id=%s: %s", producto_id, nombre_archivo)
    return nombre_archivo


def eliminar_archivo(nombre_archivo: str | None) -> None:
    """Borra un archivo de imagen si existe.

    Nunca lanza: `nombre_archivo` en `None`, el archivo ya no
    existiendo, o un error del sistema de archivos al borrar, no deben
    poder bloquear ni revertir la operación de negocio que llamó a
    esto (dar de baja un producto, reemplazar su imagen, etc.) --
    se registra como advertencia y se sigue. Como defensa adicional
    (aunque `nombre_archivo` siempre debería venir de la base, nunca
    de un usuario), se verifica que la ruta resuelta siga estando
    dentro de `DIRECTORIO_IMAGENES_PRODUCTOS` antes de borrar.
    """
    if not nombre_archivo:
        return

    ruta = (DIRECTORIO_IMAGENES_PRODUCTOS / nombre_archivo).resolve()
    if not ruta.is_relative_to(DIRECTORIO_IMAGENES_PRODUCTOS.resolve()):
        logger.warning("Se ignoró un intento de borrar fuera del directorio de imágenes: %s", nombre_archivo)
        return

    try:
        ruta.unlink(missing_ok=True)
    except OSError as error:
        logger.warning("No se pudo borrar el archivo de imagen '%s': %s", nombre_archivo, error)
