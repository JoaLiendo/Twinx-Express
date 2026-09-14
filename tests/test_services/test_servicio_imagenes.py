"""Pruebas de services.servicio_imagenes (Fase 3D): validación y
almacenamiento de imágenes de producto, contra archivos reales en un
directorio temporal (ver `directorio_imagenes_temporal` en conftest.py).
No usa mocks: son bytes de imagen reales, mínimos pero válidos.
"""

from pathlib import Path

import pytest

from excepciones import ArchivoImagenInvalidoError
from services import servicio_imagenes

# Firmas reales mínimas de cada formato (no imágenes completas/decodificables,
# alcanza para que la detección por magic bytes las reconozca).
_JPEG_VALIDO = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 20
_PNG_VALIDO = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
_WEBP_VALIDO = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 20
_TEXTO_PLANO = b"esto no es una imagen, es texto plano" * 5


def test_jpeg_valido_se_guarda_y_devuelve_nombre_de_archivo(directorio_imagenes_temporal):
    nombre_archivo = servicio_imagenes.validar_y_guardar(1, _JPEG_VALIDO, "foto.jpg")

    assert nombre_archivo.startswith("1_")
    assert nombre_archivo.endswith(".jpg")
    assert (directorio_imagenes_temporal / nombre_archivo).exists()
    assert (directorio_imagenes_temporal / nombre_archivo).read_bytes() == _JPEG_VALIDO


def test_png_valido_se_guarda(directorio_imagenes_temporal):
    nombre_archivo = servicio_imagenes.validar_y_guardar(2, _PNG_VALIDO, "foto.png")

    assert nombre_archivo.endswith(".png")
    assert (directorio_imagenes_temporal / nombre_archivo).exists()


def test_webp_valido_se_guarda(directorio_imagenes_temporal):
    nombre_archivo = servicio_imagenes.validar_y_guardar(3, _WEBP_VALIDO, "foto.webp")

    assert nombre_archivo.endswith(".webp")
    assert (directorio_imagenes_temporal / nombre_archivo).exists()


def test_nombre_de_archivo_no_depende_del_nombre_original(directorio_imagenes_temporal):
    nombre_archivo = servicio_imagenes.validar_y_guardar(1, _JPEG_VALIDO, "../../etc/passwd.jpg")

    assert "passwd" not in nombre_archivo
    assert ".." not in nombre_archivo
    assert "/" not in nombre_archivo


def test_dos_guardados_del_mismo_producto_generan_nombres_distintos(directorio_imagenes_temporal):
    nombre_1 = servicio_imagenes.validar_y_guardar(1, _JPEG_VALIDO, "foto.jpg")
    nombre_2 = servicio_imagenes.validar_y_guardar(1, _JPEG_VALIDO, "foto.jpg")

    assert nombre_1 != nombre_2


def test_extension_no_soportada_falla(directorio_imagenes_temporal):
    with pytest.raises(ArchivoImagenInvalidoError):
        servicio_imagenes.validar_y_guardar(1, _JPEG_VALIDO, "foto.gif")

    assert list(directorio_imagenes_temporal.iterdir()) == []


def test_magic_bytes_incorrectos_fallan_aunque_la_extension_sea_valida(directorio_imagenes_temporal):
    """El archivo dice ser un .jpg pero el contenido es texto plano."""
    with pytest.raises(ArchivoImagenInvalidoError):
        servicio_imagenes.validar_y_guardar(1, _TEXTO_PLANO, "foto.jpg")

    assert list(directorio_imagenes_temporal.iterdir()) == []


def test_extension_no_coincide_con_el_contenido_real(directorio_imagenes_temporal):
    """Firma real de PNG, pero declarado como .jpg -- debe rechazarse,
    no aceptarse "porque total es una imagen válida"."""
    with pytest.raises(ArchivoImagenInvalidoError):
        servicio_imagenes.validar_y_guardar(1, _PNG_VALIDO, "foto.jpg")


def test_archivo_demasiado_grande_falla(directorio_imagenes_temporal):
    contenido_grande = _JPEG_VALIDO + b"\x00" * servicio_imagenes.TAMANO_MAXIMO_BYTES

    with pytest.raises(ArchivoImagenInvalidoError):
        servicio_imagenes.validar_y_guardar(1, contenido_grande, "foto.jpg")

    assert list(directorio_imagenes_temporal.iterdir()) == []


def test_archivo_vacio_falla(directorio_imagenes_temporal):
    with pytest.raises(ArchivoImagenInvalidoError):
        servicio_imagenes.validar_y_guardar(1, b"", "foto.jpg")


def test_archivo_justo_en_el_limite_de_tamano_se_acepta(directorio_imagenes_temporal):
    relleno = servicio_imagenes.TAMANO_MAXIMO_BYTES - len(_JPEG_VALIDO)
    contenido_al_limite = _JPEG_VALIDO + b"\x00" * relleno

    nombre_archivo = servicio_imagenes.validar_y_guardar(1, contenido_al_limite, "foto.jpg")

    assert (directorio_imagenes_temporal / nombre_archivo).stat().st_size == servicio_imagenes.TAMANO_MAXIMO_BYTES


class TestEliminarArchivo:
    def test_elimina_un_archivo_existente(self, directorio_imagenes_temporal):
        nombre_archivo = servicio_imagenes.validar_y_guardar(1, _JPEG_VALIDO, "foto.jpg")
        assert (directorio_imagenes_temporal / nombre_archivo).exists()

        servicio_imagenes.eliminar_archivo(nombre_archivo)

        assert not (directorio_imagenes_temporal / nombre_archivo).exists()

    def test_no_falla_si_el_archivo_no_existe(self, directorio_imagenes_temporal):
        servicio_imagenes.eliminar_archivo("no-existe_123.jpg")  # no debe lanzar

    def test_no_falla_con_none(self, directorio_imagenes_temporal):
        servicio_imagenes.eliminar_archivo(None)  # no debe lanzar

    def test_no_permite_escapar_del_directorio_de_imagenes(self, directorio_imagenes_temporal, tmp_path):
        """Ni siquiera si alguien lograra que `nombre_archivo` contuviera
        una secuencia de escape, no debería poder borrar algo fuera del
        directorio de imágenes."""
        archivo_ajeno = tmp_path / "afuera.txt"
        archivo_ajeno.write_text("no debería borrarse")

        servicio_imagenes.eliminar_archivo("../afuera.txt")

        assert archivo_ajeno.exists()


class TestFalloDeEscrituraEnDisco:
    """Corrección del hallazgo menor #1 de la auditoría de Fase 3D:
    `write_bytes` puede fallar por motivos del sistema de archivos
    (disco lleno, permisos, etc.) -- eso no debe propagar un `OSError`
    crudo, sino traducirse a la misma `ArchivoImagenInvalidoError` que
    ya usan las demás validaciones de este módulo."""

    def _forzar_fallo_de_escritura(self, monkeypatch):
        def _write_bytes_que_falla(self, contenido):
            raise OSError("disco lleno (simulado para el test)")

        monkeypatch.setattr(Path, "write_bytes", _write_bytes_que_falla)

    def test_error_de_escritura_se_traduce_a_archivo_imagen_invalido(
        self, directorio_imagenes_temporal, monkeypatch
    ):
        self._forzar_fallo_de_escritura(monkeypatch)

        with pytest.raises(ArchivoImagenInvalidoError):
            servicio_imagenes.validar_y_guardar(1, _JPEG_VALIDO, "foto.jpg")

    def test_error_de_escritura_no_deja_ningun_archivo_en_disco(
        self, directorio_imagenes_temporal, monkeypatch
    ):
        self._forzar_fallo_de_escritura(monkeypatch)

        with pytest.raises(ArchivoImagenInvalidoError):
            servicio_imagenes.validar_y_guardar(1, _JPEG_VALIDO, "foto.jpg")

        assert list(directorio_imagenes_temporal.iterdir()) == []
