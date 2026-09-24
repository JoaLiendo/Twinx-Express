"""Pruebas de integración de services.servicio_stock contra una base de
datos SQLite real (temporal y aislada, ver tests/conftest.py)."""

import threading
from pathlib import Path

import pytest

from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from domain.venta import ItemVenta
from excepciones import (
    ArchivoImagenInvalidoError,
    CategoriaNoEncontradaError,
    CodigoBarrasDuplicadoError,
    DatosInvalidosError,
    ProductoNoEncontradoError,
    StockInsuficienteError,
)
from services import servicio_categorias, servicio_stock, servicio_ventas

pytestmark = pytest.mark.usefixtures("caja_abierta")


def _crear_usuario(nombre_usuario="duenio", rol="OWNER"):
    return repositorio_usuarios.crear_usuario(
        Usuario(nombre_usuario=nombre_usuario, nombre_completo="Test", password_hash="hash", rol=rol)
    )

_JPEG_VALIDO = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 20
_PNG_VALIDO = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20


def test_registrar_producto_lo_persiste_y_asigna_id(base_datos_temporal):
    producto = servicio_stock.registrar_producto(
        codigo_barras="7790000000001",
        nombre="Alfajor",
        precio_costo_centavos=100,
        precio_venta_centavos=200,
        stock_actual=10,
        stock_minimo=2,
    )
    assert producto.id is not None

    encontrado = servicio_stock.buscar_por_codigo_barras("7790000000001")
    assert encontrado is not None
    assert encontrado.nombre == "Alfajor"
    assert encontrado.precio_venta_centavos == 200


def test_registrar_producto_con_codigo_duplicado_falla(base_datos_temporal):
    servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)

    with pytest.raises(CodigoBarrasDuplicadoError):
        servicio_stock.registrar_producto("7790000000001", "Otro producto", 50, 90)


def test_registrar_producto_con_precio_negativo_no_llega_a_la_base(base_datos_temporal):
    with pytest.raises(DatosInvalidosError):
        servicio_stock.registrar_producto("7790000000002", "Inválido", -1, 200)

    assert servicio_stock.buscar_por_codigo_barras("7790000000002") is None


def test_buscar_por_codigo_barras_inexistente_devuelve_none(base_datos_temporal):
    assert servicio_stock.buscar_por_codigo_barras("no-existe") is None


def test_buscar_por_nombre_encuentra_coincidencia_parcial(base_datos_temporal):
    servicio_stock.registrar_producto("7790000000001", "Alfajor Triple", 100, 200)
    servicio_stock.registrar_producto("7790000000002", "Alfajor Blanco", 90, 180)
    servicio_stock.registrar_producto("7790000000003", "Gaseosa", 200, 350)

    resultados = servicio_stock.buscar_por_nombre("alfajor")

    nombres = {producto.nombre for producto in resultados}
    assert nombres == {"Alfajor Triple", "Alfajor Blanco"}


def test_buscar_por_nombre_sin_coincidencias_devuelve_lista_vacia(base_datos_temporal):
    assert servicio_stock.buscar_por_nombre("inexistente") == []


def test_listar_stock_critico_solo_devuelve_productos_bajo_el_minimo(base_datos_temporal):
    servicio_stock.registrar_producto(
        "7790000000001", "Con stock ok", 100, 200, stock_actual=10, stock_minimo=2
    )
    servicio_stock.registrar_producto(
        "7790000000002", "Con stock crítico", 100, 200, stock_actual=1, stock_minimo=5
    )

    criticos = servicio_stock.listar_stock_critico()

    assert [producto.nombre for producto in criticos] == ["Con stock crítico"]


def test_actualizar_producto_modifica_datos_editables_sin_tocar_stock(base_datos_temporal):
    producto = servicio_stock.registrar_producto(
        "7790000000001", "Alfajor", 100, 200, stock_actual=10, stock_minimo=2
    )

    actualizado = servicio_stock.actualizar_producto(
        producto_id=producto.id,
        codigo_barras="7790000000099",
        nombre="Alfajor Renombrado",
        precio_costo_centavos=120,
        precio_venta_centavos=250,
        stock_minimo=5,
    )

    assert actualizado.nombre == "Alfajor Renombrado"
    assert actualizado.codigo_barras == "7790000000099"
    assert actualizado.precio_venta_centavos == 250
    assert actualizado.stock_minimo == 5
    assert actualizado.stock_actual == 10  # no se modifica al editar


def test_actualizar_producto_inexistente_falla(base_datos_temporal):
    with pytest.raises(ProductoNoEncontradoError):
        servicio_stock.actualizar_producto(
            producto_id=9999,
            codigo_barras="7790000000001",
            nombre="Fantasma",
            precio_costo_centavos=100,
            precio_venta_centavos=200,
            stock_minimo=0,
        )


def test_actualizar_producto_con_precio_negativo_falla(base_datos_temporal):
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)

    with pytest.raises(DatosInvalidosError):
        servicio_stock.actualizar_producto(
            producto_id=producto.id,
            codigo_barras=producto.codigo_barras,
            nombre=producto.nombre,
            precio_costo_centavos=-1,
            precio_venta_centavos=200,
            stock_minimo=0,
        )


def test_actualizar_producto_con_codigo_de_otro_producto_falla(base_datos_temporal):
    servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)
    otro = servicio_stock.registrar_producto("7790000000002", "Gaseosa", 200, 350)

    with pytest.raises(CodigoBarrasDuplicadoError):
        servicio_stock.actualizar_producto(
            producto_id=otro.id,
            codigo_barras="7790000000001",
            nombre=otro.nombre,
            precio_costo_centavos=otro.precio_costo_centavos,
            precio_venta_centavos=otro.precio_venta_centavos,
            stock_minimo=otro.stock_minimo,
        )


def test_eliminar_producto_sin_ventas_lo_borra_fisicamente(base_datos_temporal):
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)

    fue_baja_logica = servicio_stock.eliminar_producto(producto.id)

    assert fue_baja_logica is False
    assert servicio_stock.obtener_por_id(producto.id) is None
    assert servicio_stock.buscar_por_codigo_barras("7790000000001") is None


def test_eliminar_producto_inexistente_falla(base_datos_temporal):
    with pytest.raises(ProductoNoEncontradoError):
        servicio_stock.eliminar_producto(9999)


def test_eliminar_producto_con_ventas_asociadas_hace_baja_logica(base_datos_temporal):
    producto = servicio_stock.registrar_producto(
        "7790000000001", "Alfajor", 100, 200, stock_actual=10, stock_minimo=1
    )
    servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")

    fue_baja_logica = servicio_stock.eliminar_producto(producto.id)

    assert fue_baja_logica is True
    # Ya no aparece en listados ni búsquedas normales...
    assert servicio_stock.obtener_por_id(producto.id) is None
    assert servicio_stock.buscar_por_codigo_barras("7790000000001") is None
    assert producto.nombre not in [p.nombre for p in servicio_stock.listar_todos()]
    # ...pero no se puede volver a "eliminar" (ya no está activo).
    with pytest.raises(ProductoNoEncontradoError):
        servicio_stock.eliminar_producto(producto.id)


def test_reactivar_producto_activa_un_producto_dado_de_baja(base_datos_temporal):
    producto = servicio_stock.registrar_producto(
        "7790000000001", "Alfajor", 100, 200, stock_actual=10, stock_minimo=1
    )
    servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
    servicio_stock.eliminar_producto(producto.id)  # baja lógica, tiene ventas asociadas

    reactivado = servicio_stock.reactivar_producto(producto.id)

    assert reactivado.activo is True
    assert reactivado.id == producto.id


def test_producto_reactivado_vuelve_a_aparecer_como_activo(base_datos_temporal):
    producto = servicio_stock.registrar_producto(
        "7790000000001", "Alfajor", 100, 200, stock_actual=10, stock_minimo=1
    )
    servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
    servicio_stock.eliminar_producto(producto.id)
    assert servicio_stock.obtener_por_id(producto.id) is None  # confirmado inactivo

    servicio_stock.reactivar_producto(producto.id)

    assert servicio_stock.obtener_por_id(producto.id) is not None
    assert servicio_stock.buscar_por_codigo_barras("7790000000001") is not None
    assert producto.nombre in [p.nombre for p in servicio_stock.listar_todos()]


def test_reactivar_producto_ya_activo_falla_sin_romper_nada(base_datos_temporal):
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)

    with pytest.raises(ProductoNoEncontradoError):
        servicio_stock.reactivar_producto(producto.id)

    # El producto sigue existiendo y activo, tal cual estaba.
    assert servicio_stock.obtener_por_id(producto.id) is not None


def test_reactivar_producto_inexistente_falla(base_datos_temporal):
    with pytest.raises(ProductoNoEncontradoError):
        servicio_stock.reactivar_producto(9999)


def test_reactivar_producto_no_modifica_stock_actual(base_datos_temporal):
    producto = servicio_stock.registrar_producto(
        "7790000000001", "Alfajor", 100, 200, stock_actual=10, stock_minimo=1
    )
    servicio_ventas.registrar_venta([ItemVenta(producto.id, 3)], "EFECTIVO")  # stock_actual -> 7
    servicio_stock.eliminar_producto(producto.id)

    reactivado = servicio_stock.reactivar_producto(producto.id)

    assert reactivado.stock_actual == 7


def test_reactivar_producto_no_modifica_ventas_ni_su_detalle(base_datos_temporal):
    producto = servicio_stock.registrar_producto(
        "7790000000001", "Alfajor", 100, 200, stock_actual=10, stock_minimo=1
    )
    venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 2)], "EFECTIVO")
    servicio_stock.eliminar_producto(producto.id)

    with obtener_conexion() as conexion:
        antes_ventas = conexion.execute("SELECT * FROM ventas WHERE id = ?", (venta.id,)).fetchone()
        antes_detalle = conexion.execute(
            "SELECT * FROM detalle_venta WHERE venta_id = ?", (venta.id,)
        ).fetchall()

    servicio_stock.reactivar_producto(producto.id)

    with obtener_conexion() as conexion:
        despues_ventas = conexion.execute("SELECT * FROM ventas WHERE id = ?", (venta.id,)).fetchone()
        despues_detalle = conexion.execute(
            "SELECT * FROM detalle_venta WHERE venta_id = ?", (venta.id,)
        ).fetchall()

    assert dict(despues_ventas) == dict(antes_ventas)
    assert [dict(fila) for fila in despues_detalle] == [dict(fila) for fila in antes_detalle]


def test_producto_creado_sin_categoria_ni_unidad_usa_los_valores_compatibles(base_datos_temporal):
    """Los productos existentes (creados antes de Fase 3C, o que
    simplemente no especifican estos campos nuevos) deben seguir
    funcionando exactamente igual: sin categoría y con unidad UNIDAD."""
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)

    assert producto.categoria_id is None
    assert producto.unidad_medida == "UNIDAD"

    encontrado = servicio_stock.buscar_por_codigo_barras("7790000000001")
    assert encontrado.categoria_id is None
    assert encontrado.unidad_medida == "UNIDAD"


def test_registrar_producto_con_categoria_valida(base_datos_temporal):
    categoria = servicio_categorias.crear_categoria("Bebidas")

    producto = servicio_stock.registrar_producto(
        "7790000000001", "Gaseosa", 100, 200, categoria_id=categoria.id
    )

    assert producto.categoria_id == categoria.id
    assert servicio_stock.obtener_por_id(producto.id).categoria_id == categoria.id


def test_registrar_producto_con_categoria_inexistente_falla(base_datos_temporal):
    with pytest.raises(CategoriaNoEncontradaError):
        servicio_stock.registrar_producto("7790000000001", "Gaseosa", 100, 200, categoria_id=9999)

    assert servicio_stock.buscar_por_codigo_barras("7790000000001") is None


@pytest.mark.parametrize("unidad", ["UNIDAD", "KG", "G", "LITRO", "ML"])
def test_registrar_producto_acepta_todas_las_unidades_validas(base_datos_temporal, unidad):
    producto = servicio_stock.registrar_producto(
        "7790000000001", "Producto", 100, 200, unidad_medida=unidad
    )
    assert producto.unidad_medida == unidad


def test_registrar_producto_con_unidad_invalida_falla(base_datos_temporal):
    with pytest.raises(DatosInvalidosError):
        servicio_stock.registrar_producto("7790000000001", "Producto", 100, 200, unidad_medida="TONELADA")

    assert servicio_stock.buscar_por_codigo_barras("7790000000001") is None


def test_actualizar_producto_permite_cambiar_categoria_y_unidad(base_datos_temporal):
    categoria = servicio_categorias.crear_categoria("Bebidas")
    producto = servicio_stock.registrar_producto("7790000000001", "Gaseosa", 100, 200)

    actualizado = servicio_stock.actualizar_producto(
        producto_id=producto.id,
        codigo_barras=producto.codigo_barras,
        nombre=producto.nombre,
        precio_costo_centavos=producto.precio_costo_centavos,
        precio_venta_centavos=producto.precio_venta_centavos,
        stock_minimo=producto.stock_minimo,
        categoria_id=categoria.id,
        unidad_medida="LITRO",
    )

    assert actualizado.categoria_id == categoria.id
    assert actualizado.unidad_medida == "LITRO"


def test_actualizar_producto_con_categoria_inexistente_falla(base_datos_temporal):
    producto = servicio_stock.registrar_producto("7790000000001", "Gaseosa", 100, 200)

    with pytest.raises(CategoriaNoEncontradaError):
        servicio_stock.actualizar_producto(
            producto_id=producto.id,
            codigo_barras=producto.codigo_barras,
            nombre=producto.nombre,
            precio_costo_centavos=producto.precio_costo_centavos,
            precio_venta_centavos=producto.precio_venta_centavos,
            stock_minimo=producto.stock_minimo,
            categoria_id=9999,
        )


def test_listar_inactivos_solo_devuelve_productos_dados_de_baja(base_datos_temporal):
    activo = servicio_stock.registrar_producto("7790000000001", "Activo", 100, 200)
    inactivo = servicio_stock.registrar_producto(
        "7790000000002", "Inactivo", 100, 200, stock_actual=5, stock_minimo=1
    )
    servicio_ventas.registrar_venta([ItemVenta(inactivo.id, 1)], "EFECTIVO")
    servicio_stock.eliminar_producto(inactivo.id)

    inactivos = servicio_stock.listar_inactivos()

    nombres = [producto.nombre for producto in inactivos]
    assert nombres == ["Inactivo"]
    assert activo.nombre not in nombres


# ---------------------------------------------------------------------------
# Imágenes de producto (Fase 3D)
# ---------------------------------------------------------------------------


def test_producto_creado_sin_imagen_por_defecto(base_datos_temporal):
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)
    assert producto.imagen_archivo is None


def test_asignar_imagen_a_producto_la_persiste(base_datos_temporal, directorio_imagenes_temporal):
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)

    actualizado = servicio_stock.asignar_imagen(producto.id, _JPEG_VALIDO, "foto.jpg")

    assert actualizado.imagen_archivo is not None
    assert actualizado.imagen_archivo.endswith(".jpg")
    assert (directorio_imagenes_temporal / actualizado.imagen_archivo).exists()
    assert servicio_stock.obtener_por_id(producto.id).imagen_archivo == actualizado.imagen_archivo


def test_asignar_imagen_invalida_falla_y_no_deja_archivo_huerfano(
    base_datos_temporal, directorio_imagenes_temporal
):
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)

    with pytest.raises(ArchivoImagenInvalidoError):
        servicio_stock.asignar_imagen(producto.id, _JPEG_VALIDO, "foto.gif")

    assert list(directorio_imagenes_temporal.iterdir()) == []
    assert servicio_stock.obtener_por_id(producto.id).imagen_archivo is None


def test_asignar_imagen_a_producto_inexistente_falla(base_datos_temporal, directorio_imagenes_temporal):
    with pytest.raises(ProductoNoEncontradoError):
        servicio_stock.asignar_imagen(9999, _JPEG_VALIDO, "foto.jpg")

    assert list(directorio_imagenes_temporal.iterdir()) == []


def test_reemplazar_imagen_borra_la_anterior_recien_despues_de_guardar_la_nueva(
    base_datos_temporal, directorio_imagenes_temporal
):
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)
    primera = servicio_stock.asignar_imagen(producto.id, _JPEG_VALIDO, "foto.jpg")
    ruta_primera = directorio_imagenes_temporal / primera.imagen_archivo
    assert ruta_primera.exists()

    segunda = servicio_stock.asignar_imagen(producto.id, _PNG_VALIDO, "foto.png")

    assert segunda.imagen_archivo != primera.imagen_archivo
    assert not ruta_primera.exists()  # la vieja se borró
    assert (directorio_imagenes_temporal / segunda.imagen_archivo).exists()  # la nueva quedó


def test_reemplazar_imagen_con_fallo_de_escritura_conserva_la_anterior_y_no_toca_la_db(
    base_datos_temporal, directorio_imagenes_temporal, monkeypatch
):
    """Corrección del hallazgo menor #1 de la auditoría de Fase 3D, a nivel
    `servicio_stock`: si la escritura de la imagen nueva falla (disco lleno,
    permisos, etc.), la imagen anterior no debe borrarse ni la base de datos
    debe quedar apuntando a un archivo que nunca se llegó a guardar."""
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)
    primera = servicio_stock.asignar_imagen(producto.id, _JPEG_VALIDO, "foto.jpg")
    ruta_primera = directorio_imagenes_temporal / primera.imagen_archivo
    assert ruta_primera.exists()

    def _write_bytes_que_falla(self, contenido):
        raise OSError("disco lleno (simulado para el test)")

    monkeypatch.setattr(Path, "write_bytes", _write_bytes_que_falla)

    with pytest.raises(ArchivoImagenInvalidoError):
        servicio_stock.asignar_imagen(producto.id, _PNG_VALIDO, "foto.png")

    assert ruta_primera.exists()  # la imagen anterior no se tocó
    assert servicio_stock.obtener_por_id(producto.id).imagen_archivo == primera.imagen_archivo  # DB intacta


def test_quitar_imagen_la_borra_del_disco_y_de_la_db(base_datos_temporal, directorio_imagenes_temporal):
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)
    con_imagen = servicio_stock.asignar_imagen(producto.id, _JPEG_VALIDO, "foto.jpg")
    ruta = directorio_imagenes_temporal / con_imagen.imagen_archivo

    resultado = servicio_stock.quitar_imagen(producto.id)

    assert resultado.imagen_archivo is None
    assert not ruta.exists()
    assert servicio_stock.obtener_por_id(producto.id).imagen_archivo is None


def test_quitar_imagen_de_producto_sin_imagen_no_falla(base_datos_temporal, directorio_imagenes_temporal):
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)

    resultado = servicio_stock.quitar_imagen(producto.id)  # no debe lanzar

    assert resultado.imagen_archivo is None


def test_baja_logica_conserva_la_imagen(base_datos_temporal, directorio_imagenes_temporal):
    producto = servicio_stock.registrar_producto(
        "7790000000001", "Alfajor", 100, 200, stock_actual=10, stock_minimo=1
    )
    con_imagen = servicio_stock.asignar_imagen(producto.id, _JPEG_VALIDO, "foto.jpg")
    servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")

    fue_baja_logica = servicio_stock.eliminar_producto(producto.id)

    assert fue_baja_logica is True
    ruta = directorio_imagenes_temporal / con_imagen.imagen_archivo
    assert ruta.exists()  # la imagen se conserva en disco
    inactivo = servicio_stock.listar_inactivos()[0]
    assert inactivo.imagen_archivo == con_imagen.imagen_archivo  # y en el registro


def test_reactivar_producto_conserva_la_imagen(base_datos_temporal, directorio_imagenes_temporal):
    producto = servicio_stock.registrar_producto(
        "7790000000001", "Alfajor", 100, 200, stock_actual=10, stock_minimo=1
    )
    con_imagen = servicio_stock.asignar_imagen(producto.id, _JPEG_VALIDO, "foto.jpg")
    servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
    servicio_stock.eliminar_producto(producto.id)

    reactivado = servicio_stock.reactivar_producto(producto.id)

    assert reactivado.imagen_archivo == con_imagen.imagen_archivo
    assert (directorio_imagenes_temporal / con_imagen.imagen_archivo).exists()


def test_baja_fisica_elimina_el_archivo_de_imagen(base_datos_temporal, directorio_imagenes_temporal):
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)
    con_imagen = servicio_stock.asignar_imagen(producto.id, _JPEG_VALIDO, "foto.jpg")
    ruta = directorio_imagenes_temporal / con_imagen.imagen_archivo
    assert ruta.exists()

    fue_baja_logica = servicio_stock.eliminar_producto(producto.id)  # sin ventas -> baja física

    assert fue_baja_logica is False
    assert not ruta.exists()


def test_baja_fisica_de_producto_sin_imagen_no_falla(base_datos_temporal, directorio_imagenes_temporal):
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)

    fue_baja_logica = servicio_stock.eliminar_producto(producto.id)  # no debe lanzar

    assert fue_baja_logica is False


class TestAjustarStock:
    """Ajuste manual de stock (merma/rotura/vencimiento/pérdida/robo/
    recuento): corrige `stock_actual` sin pasar por una venta ni una
    compra, dejando un registro histórico en `ajustes_stock`."""

    def test_ajuste_positivo_incrementa_el_stock(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        usuario = _crear_usuario()

        ajuste = servicio_stock.ajustar_stock(
            producto.id, delta=5, motivo="RECUENTO", usuario_id=usuario.id
        )

        assert ajuste.stock_anterior == 10
        assert ajuste.stock_resultante == 15
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 15

    def test_ajuste_negativo_decrementa_el_stock(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        usuario = _crear_usuario()

        ajuste = servicio_stock.ajustar_stock(
            producto.id, delta=-4, motivo="MERMA", usuario_id=usuario.id
        )

        assert ajuste.stock_anterior == 10
        assert ajuste.stock_resultante == 6
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 6

    def test_ajuste_puede_dejar_el_stock_en_cero(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=5)
        usuario = _crear_usuario()

        ajuste = servicio_stock.ajustar_stock(
            producto.id, delta=-5, motivo="ROTURA", usuario_id=usuario.id
        )

        assert ajuste.stock_resultante == 0
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 0

    def test_ajuste_que_dejaria_stock_negativo_es_rechazado(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=3)
        usuario = _crear_usuario()

        with pytest.raises(StockInsuficienteError):
            servicio_stock.ajustar_stock(producto.id, delta=-5, motivo="MERMA", usuario_id=usuario.id)

        # nada quedó modificado: ni el stock ni el historial.
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 3
        with obtener_conexion() as conexion:
            total = conexion.execute("SELECT COUNT(*) AS n FROM ajustes_stock").fetchone()["n"]
        assert total == 0

    def test_producto_inexistente_es_rechazado(self, base_datos_temporal):
        usuario = _crear_usuario()

        with pytest.raises(ProductoNoEncontradoError):
            servicio_stock.ajustar_stock(9999, delta=-1, motivo="MERMA", usuario_id=usuario.id)

    def test_ajuste_persiste_usuario_motivo_y_delta(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        usuario = _crear_usuario()

        ajuste = servicio_stock.ajustar_stock(
            producto.id, delta=-2, motivo="ROBO", usuario_id=usuario.id, observaciones="visto en cámara"
        )

        assert ajuste.usuario_id == usuario.id
        assert ajuste.motivo == "ROBO"
        assert ajuste.delta == -2
        assert ajuste.observaciones == "visto en cámara"

    def test_fallo_al_persistir_el_ajuste_no_deja_el_stock_modificado(self, base_datos_temporal, monkeypatch):
        """Atomicidad: si el INSERT del historial falla, el UPDATE de
        stock (misma transacción) se revierte -- nunca queda el stock
        cambiado sin su registro correspondiente."""
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        usuario = _crear_usuario()

        from db.repositorios import ajustes_stock as repositorio_ajustes

        def falla(*_args, **_kwargs):
            raise sqlite3.IntegrityError("fallo simulado")

        import sqlite3

        monkeypatch.setattr(repositorio_ajustes, "registrar_ajuste_en_conexion", falla)

        with pytest.raises(Exception):
            servicio_stock.ajustar_stock(producto.id, delta=-3, motivo="MERMA", usuario_id=usuario.id)

        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 10

    def test_dos_ajustes_concurrentes_no_generan_lost_update(self, base_datos_temporal):
        """Mismo patrón de test ya usado para ventas
        (`test_dos_ventas_concurrentes_del_ultimo_stock_no_generan_lost_update`):
        dos hilos reales, dos conexiones SQLite reales, restando del
        mismo stock -- `inmediata=True` debe serializarlos."""
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=10)
        usuario = _crear_usuario()

        barrera = threading.Barrier(2)
        errores = []

        def ajustar():
            barrera.wait()
            try:
                servicio_stock.ajustar_stock(producto.id, delta=-5, motivo="MERMA", usuario_id=usuario.id)
            except Exception as error:  # noqa: BLE001 -- se inspecciona más abajo, no se ignora
                errores.append(error)

        hilo_a = threading.Thread(target=ajustar)
        hilo_b = threading.Thread(target=ajustar)
        hilo_a.start()
        hilo_b.start()
        hilo_a.join(timeout=10)
        hilo_b.join(timeout=10)

        assert not hilo_a.is_alive()
        assert not hilo_b.is_alive()
        # Los dos restan 5 sobre un stock de 10: ambos caben exactamente,
        # ninguno debería fallar, y el resultado final tiene que ser 0 --
        # nunca -5 (lost update) ni 5 (un ajuste "perdido").
        assert errores == []
        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 0
        with obtener_conexion() as conexion:
            total = conexion.execute("SELECT COUNT(*) AS n FROM ajustes_stock").fetchone()["n"]
        assert total == 2


class TestCalcularValorizacionInventario:
    """Valorización de Inventario: `stock_actual * precio_costo_centavos`
    a HOY, sin depender de ningún período (la función no toma
    fecha_desde/fecha_hasta)."""

    def test_sin_productos_devuelve_ceros(self, base_datos_temporal):
        valorizacion = servicio_stock.calcular_valorizacion_inventario()

        assert valorizacion.unidades_totales == 0
        assert valorizacion.valor_total_centavos == 0
        assert valorizacion.cantidad_productos_valorizados == 0
        assert valorizacion.cantidad_productos_costo_cero == 0
        assert valorizacion.detalle == []

    def test_un_producto_activo(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto(
            "7790000000001", "Alfajor", 100, 200, stock_actual=5
        )

        valorizacion = servicio_stock.calcular_valorizacion_inventario()

        assert valorizacion.unidades_totales == 5
        assert valorizacion.valor_total_centavos == 500  # 5 * 100
        assert valorizacion.cantidad_productos_valorizados == 1
        assert len(valorizacion.detalle) == 1
        linea = valorizacion.detalle[0]
        assert linea.producto_id == producto.id
        assert linea.codigo_barras == "7790000000001"
        assert linea.stock_actual == 5
        assert linea.costo_unitario_centavos == 100
        assert linea.valor_centavos == 500

    def test_incluye_producto_inactivo_con_stock(self, base_datos_temporal):
        producto = servicio_stock.registrar_producto(
            "7790000000001", "Discontinuado", 100, 200, stock_actual=3
        )
        with obtener_conexion() as conexion:
            conexion.execute("UPDATE productos SET activo = 0 WHERE id = ?", (producto.id,))

        valorizacion = servicio_stock.calcular_valorizacion_inventario()

        assert valorizacion.cantidad_productos_valorizados == 1
        assert valorizacion.valor_total_centavos == 300  # 3 * 100

    def test_excluye_producto_con_stock_cero(self, base_datos_temporal):
        servicio_stock.registrar_producto("7790000000001", "Sin stock", 100, 200, stock_actual=0)

        valorizacion = servicio_stock.calcular_valorizacion_inventario()

        assert valorizacion.cantidad_productos_valorizados == 0
        assert valorizacion.valor_total_centavos == 0
        assert valorizacion.detalle == []

    def test_costo_cero_se_incluye_en_el_total_y_se_cuenta_aparte(self, base_datos_temporal):
        servicio_stock.registrar_producto("7790000000001", "Promocional", 0, 200, stock_actual=10)
        servicio_stock.registrar_producto("7790000000002", "Con costo", 100, 200, stock_actual=2)

        valorizacion = servicio_stock.calcular_valorizacion_inventario()

        assert valorizacion.cantidad_productos_valorizados == 2
        assert valorizacion.cantidad_productos_costo_cero == 1
        assert valorizacion.valor_total_centavos == 200  # 10*0 + 2*100
        assert valorizacion.unidades_totales == 12  # 10 + 2, el de costo 0 sigue contando

    def test_unidades_totales_suma_todo_el_stock_considerado(self, base_datos_temporal):
        servicio_stock.registrar_producto("7790000000001", "A", 100, 200, stock_actual=4)
        servicio_stock.registrar_producto("7790000000002", "B", 50, 100, stock_actual=6)

        valorizacion = servicio_stock.calcular_valorizacion_inventario()

        assert valorizacion.unidades_totales == 10

    def test_detalle_ordenado_por_valor_descendente(self, base_datos_temporal):
        servicio_stock.registrar_producto("7790000000001", "Bajo valor", 10, 20, stock_actual=1)
        servicio_stock.registrar_producto("7790000000002", "Alto valor", 500, 900, stock_actual=2)
        servicio_stock.registrar_producto("7790000000003", "Valor medio", 100, 200, stock_actual=1)

        valorizacion = servicio_stock.calcular_valorizacion_inventario()

        valores = [linea.valor_centavos for linea in valorizacion.detalle]
        assert valores == sorted(valores, reverse=True)
        assert valorizacion.detalle[0].nombre == "Alto valor"  # 2 * 500 = 1000
        assert valorizacion.detalle[-1].nombre == "Bajo valor"  # 1 * 10 = 10


# ---------------------------------------------------------------------------
# Stock crítico: el stock 0 no es una alerta
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stock_actual", "stock_minimo", "esperado"),
    [(0, 0, False), (0, 5, False), (3, 5, True), (5, 5, True), (10, 5, False)],
)
def test_listar_stock_critico_aplica_stock_positivo_y_menor_o_igual_al_minimo(
    base_datos_temporal, stock_actual, stock_minimo, esperado
):
    servicio_stock.registrar_producto(
        "TX-C-001", "Producto", 100, 200, stock_actual=stock_actual, stock_minimo=stock_minimo
    )

    nombres = [producto.nombre for producto in servicio_stock.listar_stock_critico()]

    assert (nombres == ["Producto"]) is esperado
    assert (nombres == []) is (not esperado)


def test_productos_sin_stock_no_aparecen_en_las_alertas_aunque_sean_muchos(base_datos_temporal):
    for i in range(5):
        servicio_stock.registrar_producto(f"TX-C-10{i}", f"Sin stock {i}", 0, 0)
    servicio_stock.registrar_producto("TX-C-200", "Por agotarse", 100, 200, stock_actual=1, stock_minimo=2)

    assert [p.nombre for p in servicio_stock.listar_stock_critico()] == ["Por agotarse"]
