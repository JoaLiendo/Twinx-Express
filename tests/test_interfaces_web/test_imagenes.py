"""Pruebas HTTP end-to-end de imágenes de producto (Fase 3D): alta,
reemplazo, eliminación, permisos OWNER/CASHIER y el mount de
`/media/productos`. Usa el cliente ASGI sin `httpx` (ver
_asgi_cliente.py, ahora con soporte de `multipart/form-data` real).
"""

import secrets
from pathlib import Path

import config as config_modulo
from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth, servicio_stock

from ._asgi_cliente import solicitud

import pytest

_JPEG_VALIDO = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 20
_PNG_VALIDO = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20


def _crear_usuario_y_loguearse(rol: str, nombre_usuario: str) -> str:
    repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre_usuario,
            nombre_completo="Usuario de prueba",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol=rol,
        )
    )
    return servicio_auth.iniciar_sesion(nombre_usuario, "clave-correcta-123").token


@pytest.fixture
def archivo_imagen_real():
    """Escribe un archivo directo en el directorio REAL de imágenes,
    no en el temporal de `directorio_imagenes_temporal`.

    El mount `/media/productos` de `interfaces.web.app` quedó fijado a
    `config.DIRECTORIO_IMAGENES_PRODUCTOS` al importarse ese módulo
    (una sola vez para toda la sesión de tests, vía `_asgi_cliente`),
    así que probar que el mount sirve archivos de verdad exige
    escribir ahí -- no alcanza con el directorio temporal que usan los
    tests de `servicio_imagenes`/`servicio_stock`. Se borra al terminar.
    """
    nombre_archivo = f"test_media_{secrets.token_hex(8)}.jpg"
    ruta = config_modulo.DIRECTORIO_IMAGENES_PRODUCTOS / nombre_archivo
    ruta.write_bytes(_JPEG_VALIDO)
    yield nombre_archivo
    ruta.unlink(missing_ok=True)


class TestServirImagen:
    def test_media_productos_sirve_el_archivo(self, archivo_imagen_real):
        respuesta = solicitud("GET", f"/media/productos/{archivo_imagen_real}")

        assert respuesta.status == 200
        assert respuesta.cuerpo == _JPEG_VALIDO

    def test_media_productos_archivo_inexistente_da_404(self):
        respuesta = solicitud("GET", "/media/productos/no-existe-123.jpg")

        assert respuesta.status == 404

    def test_media_productos_no_expone_kiosco_db(self):
        """Un intento de escapar del directorio de imágenes nunca debe
        devolver el contenido de `data/kiosco.db`."""
        respuesta = solicitud("GET", "/media/productos/../kiosco.db")

        assert respuesta.status in (400, 403, 404)
        assert b"SQLite format" not in respuesta.cuerpo


class TestAltaConImagen:
    def test_owner_puede_crear_producto_con_imagen(self, base_datos_temporal, directorio_imagenes_temporal):
        token = _crear_usuario_y_loguearse("OWNER", "ana")

        respuesta = solicitud(
            "POST",
            "/productos/nuevo",
            cookies={NOMBRE_COOKIE_SESION: token},
            formulario={
                "codigo_barras": "7790000000001",
                "nombre": "Con imagen",
                "precio_costo": "100",
                "precio_venta": "200",
            },
            archivos={"imagen": ("foto.jpg", _JPEG_VALIDO)},
        )

        assert respuesta.status == 303
        producto = servicio_stock.buscar_por_codigo_barras("7790000000001")
        assert producto.imagen_archivo is not None
        assert (directorio_imagenes_temporal / producto.imagen_archivo).exists()

    def test_owner_puede_crear_producto_sin_imagen(self, base_datos_temporal, directorio_imagenes_temporal):
        token = _crear_usuario_y_loguearse("OWNER", "ana")

        respuesta = solicitud(
            "POST",
            "/productos/nuevo",
            cookies={NOMBRE_COOKIE_SESION: token},
            formulario={
                "codigo_barras": "7790000000001",
                "nombre": "Sin imagen",
                "precio_costo": "100",
                "precio_venta": "200",
            },
        )

        assert respuesta.status == 303
        assert servicio_stock.buscar_por_codigo_barras("7790000000001").imagen_archivo is None

    def test_imagen_invalida_no_impide_crear_el_producto(self, base_datos_temporal, directorio_imagenes_temporal):
        token = _crear_usuario_y_loguearse("OWNER", "ana")

        respuesta = solicitud(
            "POST",
            "/productos/nuevo",
            cookies={NOMBRE_COOKIE_SESION: token},
            formulario={
                "codigo_barras": "7790000000001",
                "nombre": "Imagen mala",
                "precio_costo": "100",
                "precio_venta": "200",
            },
            archivos={"imagen": ("foto.gif", _JPEG_VALIDO)},
        )

        assert respuesta.status == 303
        assert "tipo=warning" in respuesta.header("location")
        producto = servicio_stock.buscar_por_codigo_barras("7790000000001")
        assert producto is not None  # el producto SÍ se creó
        assert producto.imagen_archivo is None  # pero sin imagen
        assert list(directorio_imagenes_temporal.iterdir()) == []  # sin huérfanos

    def test_fallo_de_escritura_no_impide_crear_el_producto(
        self, base_datos_temporal, directorio_imagenes_temporal, monkeypatch
    ):
        """Corrección del hallazgo menor #1 de la auditoría de Fase 3D, a
        nivel HTTP: un fallo de escritura en disco (disco lleno, permisos)
        debe dar la misma respuesta controlada -- 303 con aviso -- que ya
        existe para una imagen inválida, nunca un 500 sin manejar."""
        token = _crear_usuario_y_loguearse("OWNER", "ana")

        def _write_bytes_que_falla(self, contenido):
            raise OSError("disco lleno (simulado para el test)")

        monkeypatch.setattr(Path, "write_bytes", _write_bytes_que_falla)

        respuesta = solicitud(
            "POST",
            "/productos/nuevo",
            cookies={NOMBRE_COOKIE_SESION: token},
            formulario={
                "codigo_barras": "7790000000001",
                "nombre": "Con fallo de disco",
                "precio_costo": "100",
                "precio_venta": "200",
            },
            archivos={"imagen": ("foto.jpg", _JPEG_VALIDO)},
        )

        assert respuesta.status == 303
        assert "tipo=warning" in respuesta.header("location")
        producto = servicio_stock.buscar_por_codigo_barras("7790000000001")
        assert producto is not None  # el producto SÍ se creó
        assert producto.imagen_archivo is None  # pero sin imagen

    def test_cashier_no_puede_crear_producto_con_imagen(self, base_datos_temporal, directorio_imagenes_temporal):
        token = _crear_usuario_y_loguearse("CASHIER", "carlos")

        respuesta = solicitud(
            "POST",
            "/productos/nuevo",
            cookies={NOMBRE_COOKIE_SESION: token},
            formulario={
                "codigo_barras": "7790000000001",
                "nombre": "No debería crearse",
                "precio_costo": "100",
                "precio_venta": "200",
            },
            archivos={"imagen": ("foto.jpg", _JPEG_VALIDO)},
        )

        assert respuesta.status == 403
        assert servicio_stock.buscar_por_codigo_barras("7790000000001") is None
        assert list(directorio_imagenes_temporal.iterdir()) == []


class TestEdicionConImagen:
    def test_owner_puede_reemplazar_imagen(self, base_datos_temporal, directorio_imagenes_temporal):
        token = _crear_usuario_y_loguearse("OWNER", "ana")
        producto = servicio_stock.registrar_producto("7790000000001", "Producto", 100, 200)
        primera = servicio_stock.asignar_imagen(producto.id, _JPEG_VALIDO, "primera.jpg")
        ruta_primera = directorio_imagenes_temporal / primera.imagen_archivo

        respuesta = solicitud(
            "POST",
            f"/productos/{producto.id}/editar",
            cookies={NOMBRE_COOKIE_SESION: token},
            formulario={
                "codigo_barras": "7790000000001",
                "nombre": "Producto",
                "precio_costo": "100",
                "precio_venta": "200",
            },
            archivos={"imagen": ("segunda.png", _PNG_VALIDO)},
        )

        assert respuesta.status == 303
        actualizado = servicio_stock.obtener_por_id(producto.id)
        assert actualizado.imagen_archivo != primera.imagen_archivo
        assert not ruta_primera.exists()
        assert (directorio_imagenes_temporal / actualizado.imagen_archivo).exists()

    def test_owner_puede_quitar_imagen(self, base_datos_temporal, directorio_imagenes_temporal):
        token = _crear_usuario_y_loguearse("OWNER", "ana")
        producto = servicio_stock.registrar_producto("7790000000001", "Producto", 100, 200)
        servicio_stock.asignar_imagen(producto.id, _JPEG_VALIDO, "foto.jpg")

        respuesta = solicitud(
            "POST",
            f"/productos/{producto.id}/editar",
            cookies={NOMBRE_COOKIE_SESION: token},
            formulario={
                "codigo_barras": "7790000000001",
                "nombre": "Producto",
                "precio_costo": "100",
                "precio_venta": "200",
                "quitar_imagen": "true",
            },
        )

        assert respuesta.status == 303
        assert servicio_stock.obtener_por_id(producto.id).imagen_archivo is None
        assert list(directorio_imagenes_temporal.iterdir()) == []

    def test_editar_sin_tocar_imagen_la_conserva(self, base_datos_temporal, directorio_imagenes_temporal):
        token = _crear_usuario_y_loguearse("OWNER", "ana")
        producto = servicio_stock.registrar_producto("7790000000001", "Producto", 100, 200)
        con_imagen = servicio_stock.asignar_imagen(producto.id, _JPEG_VALIDO, "foto.jpg")

        respuesta = solicitud(
            "POST",
            f"/productos/{producto.id}/editar",
            cookies={NOMBRE_COOKIE_SESION: token},
            formulario={
                "codigo_barras": "7790000000001",
                "nombre": "Producto Renombrado",
                "precio_costo": "100",
                "precio_venta": "200",
            },
        )

        assert respuesta.status == 303
        assert servicio_stock.obtener_por_id(producto.id).imagen_archivo == con_imagen.imagen_archivo

    def test_cashier_no_puede_reemplazar_imagen(self, base_datos_temporal, directorio_imagenes_temporal):
        token_owner = _crear_usuario_y_loguearse("OWNER", "ana")
        producto = servicio_stock.registrar_producto("7790000000001", "Producto", 100, 200)
        original = servicio_stock.asignar_imagen(producto.id, _JPEG_VALIDO, "foto.jpg")
        token_cashier = _crear_usuario_y_loguearse("CASHIER", "carlos")

        respuesta = solicitud(
            "POST",
            f"/productos/{producto.id}/editar",
            cookies={NOMBRE_COOKIE_SESION: token_cashier},
            formulario={
                "codigo_barras": "7790000000001",
                "nombre": "Hackeado",
                "precio_costo": "100",
                "precio_venta": "200",
            },
            archivos={"imagen": ("hack.png", _PNG_VALIDO)},
        )

        assert respuesta.status == 403
        assert servicio_stock.obtener_por_id(producto.id).imagen_archivo == original.imagen_archivo

    def test_cashier_no_puede_quitar_imagen(self, base_datos_temporal, directorio_imagenes_temporal):
        _crear_usuario_y_loguearse("OWNER", "ana")
        producto = servicio_stock.registrar_producto("7790000000001", "Producto", 100, 200)
        servicio_stock.asignar_imagen(producto.id, _JPEG_VALIDO, "foto.jpg")
        token_cashier = _crear_usuario_y_loguearse("CASHIER", "carlos")

        respuesta = solicitud(
            "POST",
            f"/productos/{producto.id}/editar",
            cookies={NOMBRE_COOKIE_SESION: token_cashier},
            formulario={
                "codigo_barras": "7790000000001",
                "nombre": "Producto",
                "precio_costo": "100",
                "precio_venta": "200",
                "quitar_imagen": "true",
            },
        )

        assert respuesta.status == 403
        assert servicio_stock.obtener_por_id(producto.id).imagen_archivo is not None
