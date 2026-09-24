"""V1.2 Fase 4: importación CSV/XLSX -- actualización de productos existentes,
todo o nada, duplicados dentro del archivo, preservación de datos, historial
de precios, auditoría y reporte completo de filas rechazadas."""

import io

import pytest
from openpyxl import Workbook

from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from excepciones import ArchivoImportacionInvalidoError
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth, servicio_categorias, servicio_importacion, servicio_stock
from services.servicio_importacion import MODO_CREAR, MODO_CREAR_Y_ACTUALIZAR

from ._asgi_cliente import solicitud

ENCABEZADO = "codigo_barras,nombre,precio_costo,precio_venta,stock_actual,stock_minimo,categoria,unidad_medida\n"


def _csv(*filas: str) -> bytes:
    return (ENCABEZADO + "\n".join(filas) + "\n").encode("utf-8")


def _usuario():
    return repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario="duenio",
            nombre_completo="Dueño",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol="OWNER",
        )
    )


def _por_codigo(codigo):
    return servicio_stock.buscar_por_codigo_barras(codigo)


def _contar(tabla):
    with obtener_conexion() as conexion:
        return conexion.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]


@pytest.fixture
def existente(base_datos_temporal):
    return servicio_stock.registrar_producto(
        "111", "Alfajor", 100, 200, stock_actual=7, stock_minimo=2, unidad_medida="UNIDAD"
    )


class TestActualizacionDeExistentes:
    def test_actualiza_datos_precios_y_preserva_el_stock(self, existente):
        usuario = _usuario()

        resultado = servicio_importacion.procesar_archivo(
            "a.csv", _csv("111,Alfajor triple,1.20,2.50,999,5,,UNIDAD"), modo=MODO_CREAR_Y_ACTUALIZAR, usuario_id=usuario.id
        )

        producto = _por_codigo("111")
        assert (resultado.actualizados, resultado.importados, resultado.errores) == (1, 0, [])
        assert (producto.nombre, producto.precio_costo_centavos, producto.precio_venta_centavos, producto.stock_minimo) == (
            "Alfajor triple", 120, 250, 5,
        )
        assert producto.stock_actual == 7  # el stock del archivo (999) se ignora
        assert producto.id == existente.id

    def test_el_modo_crear_no_toca_los_existentes(self, existente):
        resultado = servicio_importacion.procesar_archivo("a.csv", _csv("111,Otro nombre,9,9,0,0,,"), modo=MODO_CREAR)

        assert (resultado.duplicados, resultado.actualizados, resultado.importados) == (1, 0, 0)
        assert _por_codigo("111").nombre == "Alfajor"

    def test_las_celdas_vacias_conservan_el_valor_actual(self, existente):
        resultado = servicio_importacion.procesar_archivo(
            "a.csv", _csv("111,,,2.50,,,,"), modo=MODO_CREAR_Y_ACTUALIZAR
        )

        producto = _por_codigo("111")
        assert resultado.actualizados == 1
        assert (producto.nombre, producto.precio_costo_centavos, producto.precio_venta_centavos, producto.stock_minimo) == (
            "Alfajor", 100, 250, 2,
        )
        assert producto.unidad_medida == "UNIDAD"

    def test_una_fila_igual_a_lo_existente_es_sin_cambios(self, existente):
        resultado = servicio_importacion.procesar_archivo(
            "a.csv", _csv("111,Alfajor,1.00,2.00,7,2,,UNIDAD"), modo=MODO_CREAR_Y_ACTUALIZAR
        )

        assert (resultado.sin_cambios, resultado.actualizados) == (1, 0)
        assert _contar("historial_precios") == 0

    def test_reimportar_el_mismo_archivo_es_inocuo(self, existente):
        archivo = _csv("111,Alfajor,1.20,2.50,0,2,,", "222,Nuevo,1,2,3,0,,")

        primero = servicio_importacion.procesar_archivo("a.csv", archivo, modo=MODO_CREAR_Y_ACTUALIZAR)
        segundo = servicio_importacion.procesar_archivo("a.csv", archivo, modo=MODO_CREAR_Y_ACTUALIZAR)

        assert (primero.importados, primero.actualizados) == (1, 1)
        assert (segundo.importados, segundo.actualizados, segundo.sin_cambios) == (0, 0, 2)

    def test_asigna_categoria_existente_y_conserva_la_actual_si_la_celda_esta_vacia(self, existente):
        bebidas = servicio_categorias.crear_categoria("Bebidas")
        servicio_importacion.procesar_archivo("a.csv", _csv("111,,,,,,Bebidas,"), modo=MODO_CREAR_Y_ACTUALIZAR)
        assert _por_codigo("111").categoria_id == bebidas.id

        servicio_importacion.procesar_archivo("a.csv", _csv("111,Alfajor 2,,,,,,"), modo=MODO_CREAR_Y_ACTUALIZAR)

        assert _por_codigo("111").categoria_id == bebidas.id

    def test_un_producto_dado_de_baja_se_rechaza(self, existente):
        with obtener_conexion() as conexion:
            conexion.execute("UPDATE productos SET activo = 0 WHERE id = ?", (existente.id,))

        resultado = servicio_importacion.procesar_archivo(
            "a.csv", _csv("111,Nuevo nombre,,,,,,"), modo=MODO_CREAR_Y_ACTUALIZAR
        )

        assert resultado.actualizados == 0
        assert "dado de baja" in resultado.errores[0] and resultado.errores[0].startswith("Fila 2:")

    def test_el_cambio_de_precio_queda_en_el_historial_con_origen_importacion_y_usuario(self, existente):
        usuario = _usuario()

        servicio_importacion.procesar_archivo(
            "a.csv", _csv("111,,1.50,2.50,,,,"), modo=MODO_CREAR_Y_ACTUALIZAR, usuario_id=usuario.id
        )

        with obtener_conexion() as conexion:
            filas = conexion.execute(
                "SELECT campo, precio_anterior_centavos, precio_nuevo_centavos, origen, usuario_id"
                " FROM historial_precios ORDER BY campo"
            ).fetchall()
        assert [tuple(f) for f in filas] == [
            ("COSTO", 100, 150, "IMPORTACION", usuario.id),
            ("VENTA", 200, 250, "IMPORTACION", usuario.id),
        ]


class TestTodoONada:
    def test_con_todo_o_nada_una_fila_invalida_no_aplica_ninguna(self, existente):
        archivo = _csv("111,Nuevo nombre,,,,,,", "222,Producto nuevo,1,2,3,0,,", "333,,abc,2,0,0,,")

        resultado = servicio_importacion.procesar_archivo(
            "a.csv", archivo, modo=MODO_CREAR_Y_ACTUALIZAR, todo_o_nada=True
        )

        assert resultado.aplicado is False
        assert (resultado.importados, resultado.actualizados) == (0, 0)
        assert len(resultado.errores) == 1 and resultado.errores[0].startswith("Fila 4:")
        assert _por_codigo("111").nombre == "Alfajor" and _por_codigo("222") is None
        assert _contar("auditoria") == 0 and _contar("historial_precios") == 0

    def test_con_todo_o_nada_y_todo_valido_aplica_todo(self, existente):
        resultado = servicio_importacion.procesar_archivo(
            "a.csv", _csv("111,Nuevo nombre,,,,,,", "222,Producto nuevo,1,2,3,0,,"),
            modo=MODO_CREAR_Y_ACTUALIZAR, todo_o_nada=True,
        )

        assert resultado.aplicado is True and (resultado.importados, resultado.actualizados) == (1, 1)

    def test_el_comportamiento_por_defecto_es_todo_o_nada(self, existente):
        resultado = servicio_importacion.procesar_archivo(
            "a.csv", _csv("222,Bien,1,2,3,0,,", "333,,abc,2,0,0,,")
        )

        assert resultado.aplicado is False and resultado.importados == 0
        assert _por_codigo("222") is None

    def test_la_importacion_parcial_es_opt_in_explicito_y_aplica_las_validas(self, existente):
        resultado = servicio_importacion.procesar_archivo(
            "a.csv", _csv("222,Bien,1,2,3,0,,", "333,,abc,2,0,0,,", "444,Tambien bien,1,2,0,0,,"), todo_o_nada=False
        )

        assert resultado.aplicado is True and resultado.importados == 2
        assert len(resultado.errores) == 1 and "Fila 3" in resultado.errores[0]

    def test_reporta_todas_las_filas_rechazadas_no_solo_las_primeras(self, base_datos_temporal):
        filas = [f"{i},,,,,,," for i in range(1, 9)]  # nombre vacío en las 8

        resultado = servicio_importacion.procesar_archivo("a.csv", _csv(*filas))

        assert len(resultado.errores) == 8 and resultado.importados == 0

    def test_un_error_de_categoria_o_unidad_rechaza_la_fila_con_mensaje_claro(self, base_datos_temporal):
        resultado = servicio_importacion.procesar_archivo(
            "a.csv", _csv("1,A,1,2,0,0,NoExiste,", "2,B,1,2,0,0,,LITROS")
        )

        assert "categoría 'NoExiste' no existe" in resultado.errores[0]
        assert "unidad" in resultado.errores[1].lower()


class TestDuplicadosEnElArchivo:
    def test_un_codigo_repetido_en_el_archivo_se_rechaza_la_segunda_vez(self, base_datos_temporal):
        resultado = servicio_importacion.procesar_archivo(
            "a.csv", _csv("1,Primero,1,2,0,0,,", "1,Repetido,1,2,0,0,,"), todo_o_nada=False
        )

        assert resultado.importados == 1
        assert "repetido en el archivo" in resultado.errores[0] and "fila 2" in resultado.errores[0]
        assert _por_codigo("1").nombre == "Primero"

    def test_codigo_vacio_es_error(self, base_datos_temporal):
        resultado = servicio_importacion.procesar_archivo("a.csv", _csv(",Sin codigo,1,2,0,0,,"))

        assert "código de barras no puede estar vacío" in resultado.errores[0]

    def test_los_existentes_en_modo_crear_se_cuentan_como_duplicados_no_como_error(self, existente):
        resultado = servicio_importacion.procesar_archivo("a.csv", _csv("111,X,1,2,0,0,,"))

        assert (resultado.duplicados, resultado.errores) == (1, [])


class TestAuditoriaYFormatos:
    def test_un_unico_resumen_de_auditoria_por_importacion(self, existente):
        usuario = _usuario()

        servicio_importacion.procesar_archivo(
            "productos.csv", _csv("111,Nuevo nombre,,,,,,", "222,Otro,1,2,3,0,,", "333,Otro mas,1,2,3,0,,"),
            modo=MODO_CREAR_Y_ACTUALIZAR, usuario_id=usuario.id,
        )

        with obtener_conexion() as conexion:
            filas = conexion.execute("SELECT accion, usuario_id, resumen FROM auditoria").fetchall()
        assert len(filas) == 1
        assert filas[0]["accion"] == "PRODUCTOS_IMPORTADOS" and filas[0]["usuario_id"] == usuario.id
        assert "productos.csv" in filas[0]["resumen"] and "2 creado(s)" in filas[0]["resumen"] and "1 actualizado(s)" in filas[0]["resumen"]

    def test_sin_cambios_efectivos_no_audita(self, existente):
        usuario = _usuario()

        servicio_importacion.procesar_archivo(
            "a.csv", _csv("111,Alfajor,1.00,2.00,7,2,,UNIDAD"), modo=MODO_CREAR_Y_ACTUALIZAR, usuario_id=usuario.id
        )

        assert _contar("auditoria") == 0

    def test_xlsx_tambien_actualiza(self, existente):
        libro = Workbook()
        hoja = libro.active
        hoja.append(["codigo_barras", "nombre", "precio_costo", "precio_venta", "stock_actual", "stock_minimo"])
        hoja.append(["111", "Alfajor XLSX", 1.1, 2.2, 0, 4])
        buffer = io.BytesIO()
        libro.save(buffer)

        resultado = servicio_importacion.procesar_archivo("a.xlsx", buffer.getvalue(), modo=MODO_CREAR_Y_ACTUALIZAR)

        assert resultado.actualizados == 1 and _por_codigo("111").stock_minimo == 4

    def test_un_modo_invalido_se_rechaza(self, base_datos_temporal):
        with pytest.raises(ArchivoImportacionInvalidoError):
            servicio_importacion.procesar_archivo("a.csv", _csv("1,A,1,2,0,0,,"), modo="BORRAR_TODO")

    def test_columnas_faltantes_siguen_rechazando_el_archivo(self, base_datos_temporal):
        with pytest.raises(ArchivoImportacionInvalidoError):
            servicio_importacion.procesar_archivo("a.csv", b"codigo_barras,nombre\n1,A\n", modo=MODO_CREAR_Y_ACTUALIZAR)


class TestRuta:
    def _cookies(self):
        _usuario()
        return {NOMBRE_COOKIE_SESION: servicio_auth.iniciar_sesion("duenio", "clave-correcta-123").token}

    def _subir(self, cookies, contenido: bytes, **campos):
        return solicitud(
            "POST",
            "/productos/importar",
            cookies=cookies,
            formulario=campos,
            archivos={"archivo": ("productos.csv", contenido)},
        )

    def test_muestra_todas_las_filas_rechazadas_y_no_importa_con_todo_o_nada(self, existente):
        cookies = self._cookies()
        filas = [f"{i},,,,,,," for i in range(10, 20)]

        respuesta = self._subir(cookies, _csv("111,Nuevo,,,,,,", *filas), modo="CREAR_Y_ACTUALIZAR")

        assert respuesta.status == 200
        assert respuesta.texto.count("Fila ") == 10
        assert "No se importó nada" in respuesta.texto
        assert _por_codigo("111").nombre == "Alfajor"

    def test_importa_y_registra_al_usuario_autenticado(self, existente):
        cookies = self._cookies()

        respuesta = self._subir(cookies, _csv("111,Nuevo nombre,,,,,,", "222,Otro,1,2,3,0,,"), modo="CREAR_Y_ACTUALIZAR")

        assert respuesta.status == 200 and "Importación completa" in respuesta.texto
        assert _por_codigo("111").nombre == "Nuevo nombre" and _por_codigo("222") is not None
        with obtener_conexion() as conexion:
            assert conexion.execute("SELECT COUNT(*) FROM auditoria WHERE accion = 'PRODUCTOS_IMPORTADOS'").fetchone()[0] == 1

    def test_el_formulario_ofrece_los_dos_modos_y_todo_o_nada(self, base_datos_temporal):
        cookies = self._cookies()

        html = solicitud("GET", "/productos/importar", cookies=cookies).texto

        assert 'value="CREAR_Y_ACTUALIZAR"' in html
        # la importación parcial es opt-in: la casilla existe y NO viene marcada
        assert 'name="permitir_parcial"' in html and 'name="permitir_parcial" value="true" class="mt-1" checked' not in html
        assert "checked" not in html.split('name="permitir_parcial"')[1].split(">")[0]

    def test_extension_no_soportada_redirige_con_error(self, base_datos_temporal):
        cookies = self._cookies()

        respuesta = solicitud(
            "POST", "/productos/importar", cookies=cookies,
            archivos={"archivo": ("productos.txt", b"x")},
        )

        assert respuesta.status == 303 and "tipo=error" in respuesta.header("location")


class TestValoresNumericosExtremos:
    """`inf`, `1e400` y valores fuera de rango son errores de FILA controlados,
    nunca un OverflowError / HTTP 500."""

    @pytest.mark.parametrize("valor", ["inf", "-inf", "Infinity", "1e400", "-1e400", "nan", "1e30", "99999999999999999999999"])
    @pytest.mark.parametrize("columna", ["stock_actual", "stock_minimo"])
    def test_stock_extremo_es_error_de_fila(self, base_datos_temporal, valor, columna):
        fila = {"stock_actual": "0", "stock_minimo": "0"}
        fila[columna] = valor

        resultado = servicio_importacion.procesar_archivo(
            "a.csv", _csv(f"1,Prod,1,2,{fila['stock_actual']},{fila['stock_minimo']},,")
        )

        assert resultado.importados == 0 and len(resultado.errores) == 1
        assert resultado.errores[0].startswith("Fila 2:")
        assert _por_codigo("1") is None

    @pytest.mark.parametrize("valor", ["1e400", "9" * 40, "1" + "0" * 30])
    @pytest.mark.parametrize("columna", ["precio_costo", "precio_venta"])
    def test_precio_extremo_es_error_de_fila(self, base_datos_temporal, valor, columna):
        precios = {"precio_costo": "1", "precio_venta": "2"}
        precios[columna] = valor

        resultado = servicio_importacion.procesar_archivo(
            "a.csv", _csv(f"1,Prod,{precios['precio_costo']},{precios['precio_venta']},0,0,,")
        )

        assert resultado.importados == 0 and len(resultado.errores) == 1
        assert _por_codigo("1") is None

    def test_en_modo_actualizar_un_valor_extremo_no_rompe_ni_cambia_el_existente(self, existente):
        resultado = servicio_importacion.procesar_archivo(
            "a.csv", _csv("111,Nuevo nombre,,,,inf,,"), modo=MODO_CREAR_Y_ACTUALIZAR
        )

        assert resultado.actualizados == 0 and len(resultado.errores) == 1
        assert _por_codigo("111").nombre == "Alfajor"

    def test_con_filas_validas_e_invalidas_el_valor_extremo_solo_cuenta_como_error(self, base_datos_temporal):
        resultado = servicio_importacion.procesar_archivo(
            "a.csv", _csv("1,Bien,1,2,3,0,,", "2,Mal,1,2,inf,0,,", "3,Tambien mal,1,2,1e400,0,,"), todo_o_nada=False
        )

        assert resultado.importados == 1 and len(resultado.errores) == 2
        assert [e.split(":")[0] for e in resultado.errores] == ["Fila 3", "Fila 4"]

    def test_la_ruta_no_devuelve_500_ante_valores_extremos(self, base_datos_temporal):
        _usuario()
        cookies = {NOMBRE_COOKIE_SESION: servicio_auth.iniciar_sesion("duenio", "clave-correcta-123").token}

        respuesta = solicitud(
            "POST",
            "/productos/importar",
            cookies=cookies,
            archivos={"archivo": ("p.csv", _csv("1,A,1,2,inf,0,,", "2,B,1e400,2,0,0,,"))},
        )

        assert respuesta.status == 200
        assert "Fila 2" in respuesta.texto and "Fila 3" in respuesta.texto and "No se importó nada" in respuesta.texto


class TestTodoONadaEsElValorPorDefectoEnTodasLasCapas:
    def test_el_servicio_es_todo_o_nada_por_defecto(self, base_datos_temporal):
        import inspect

        assert inspect.signature(servicio_importacion.procesar_archivo).parameters["todo_o_nada"].default is True

    def test_el_formulario_no_marca_la_importacion_parcial(self, base_datos_temporal):
        _usuario()
        cookies = {NOMBRE_COOKIE_SESION: servicio_auth.iniciar_sesion("duenio", "clave-correcta-123").token}

        html = solicitud("GET", "/productos/importar", cookies=cookies).texto

        casilla = html.split('name="permitir_parcial"')[1].split(">")[0]
        assert "checked" not in casilla
        assert 'name="todo_o_nada"' not in html  # ya no existe una casilla con el default invertido

    def test_la_ruta_sin_marcar_nada_no_aplica_nada_con_una_fila_mala(self, base_datos_temporal):
        _usuario()
        cookies = {NOMBRE_COOKIE_SESION: servicio_auth.iniciar_sesion("duenio", "clave-correcta-123").token}

        respuesta = solicitud(
            "POST", "/productos/importar", cookies=cookies,
            archivos={"archivo": ("p.csv", _csv("1,Bien,1,2,3,0,,", "2,,abc,2,0,0,,"))},
        )

        assert "No se importó nada" in respuesta.texto
        assert _por_codigo("1") is None

    def test_la_ruta_con_permitir_parcial_marcado_aplica_las_validas_y_lo_dice(self, base_datos_temporal):
        _usuario()
        cookies = {NOMBRE_COOKIE_SESION: servicio_auth.iniciar_sesion("duenio", "clave-correcta-123").token}

        respuesta = solicitud(
            "POST", "/productos/importar", cookies=cookies, formulario={"permitir_parcial": "true"},
            archivos={"archivo": ("p.csv", _csv("1,Bien,1,2,3,0,,", "2,,abc,2,0,0,,"))},
        )

        assert "Importación parcial" in respuesta.texto
        assert _por_codigo("1") is not None and _por_codigo("2") is None

    def test_en_modo_actualizar_nunca_se_da_de_baja_ni_se_toca_el_stock(self, existente):
        servicio_importacion.procesar_archivo(
            "a.csv", _csv("111,Alfajor,1,2,99999,2,,"), modo=MODO_CREAR_Y_ACTUALIZAR
        )

        producto = _por_codigo("111")
        assert producto.activo is True and producto.stock_actual == 7
