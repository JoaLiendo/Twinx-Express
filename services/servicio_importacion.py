"""Casos de uso de importación masiva de productos desde CSV o Excel.

Permite cargar muchos productos de una sola vez a partir de un archivo
con columnas `codigo_barras, nombre, precio_costo, precio_venta,
stock_actual, stock_minimo`, y dos columnas opcionales (Fase 3C):
`categoria` (nombre de una categoría ya existente; vacía o ausente =
sin categoría) y `unidad_medida` (uno de los valores válidos de
`domain.producto.UNIDADES_VALIDAS`; vacía o ausente = `UNIDAD`). Cada
fila se procesa con `services.servicio_stock.registrar_producto`, así
toda la validación de negocio (precios/stock no negativos, campos
obligatorios, unidad de medida válida) y la detección de códigos de
barras duplicados pasan por el mismo camino que el alta manual de un
producto. La importación nunca crea categorías nuevas: si el nombre
de la columna `categoria` no existe todavía, la fila se cuenta como
error.

Una fila inválida o duplicada no aborta el resto de la importación:
se cuenta en `ResultadoImportacion` y se sigue con la fila siguiente.
"""

import csv
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import productos as repositorio_productos
from domain.dinero import texto_a_centavos
from domain.producto import Producto
from excepciones import ArchivoImportacionInvalidoError, DatosInvalidosError
from services import servicio_categorias

logger = logging.getLogger(__name__)

COLUMNAS_REQUERIDAS = (
    "codigo_barras",
    "nombre",
    "precio_costo",
    "precio_venta",
    "stock_actual",
    "stock_minimo",
)

EXTENSIONES_SOPORTADAS = frozenset({".csv", ".xlsx"})


MODO_CREAR = "CREAR"
MODO_CREAR_Y_ACTUALIZAR = "CREAR_Y_ACTUALIZAR"
MODOS_IMPORTACION = frozenset({MODO_CREAR, MODO_CREAR_Y_ACTUALIZAR})


@dataclass
class ResultadoImportacion:
    """Resumen de una importación masiva de productos.

    `aplicado` es `False` cuando se pidió "todo o nada" y alguna fila falló: en
    ese caso no se escribió nada (los contadores de creados/actualizados quedan
    en 0) y `errores` lista todas las filas rechazadas.
    """

    total_filas: int
    importados: int = 0
    actualizados: int = 0
    sin_cambios: int = 0
    duplicados: int = 0
    errores: list[str] = field(default_factory=list)
    aplicado: bool = True


class _RevertirImportacion(Exception):
    """Interna: aborta la transacción de la importación ("todo o nada")."""


def procesar_archivo(
    nombre_archivo: str,
    contenido: bytes,
    *,
    modo: str = MODO_CREAR,
    todo_o_nada: bool = True,
    usuario_id: int | None = None,
) -> ResultadoImportacion:
    """Importa productos desde un archivo `.csv` o `.xlsx` subido por el usuario.

    Toda la importación corre en una única transacción (`BEGIN IMMEDIATE`).

    `modo`:
    - `CREAR` (default): crea los productos nuevos; los códigos de barras ya
      existentes se omiten sin tocarlos.
    - `CREAR_Y_ACTUALIZAR`: además actualiza los existentes con los datos del
      archivo. **Nunca modifica `stock_actual` de un producto existente** (el stock
      solo cambia con ventas, compras y ajustes con motivo). Una celda vacía en un
      producto existente conserva el valor actual (no lo borra). Un producto dado
      de baja se rechaza. Cada cambio de precio queda en el historial de precios
      (origen `IMPORTACION`).

    `todo_o_nada=True` (default, el comportamiento seguro): si cualquier fila es
    inválida no se aplica NADA. Con `False` (importación parcial, opt-in explícito)
    se aplican las filas válidas y se informan las rechazadas.

    Con `usuario_id` se deja un único resumen en la auditoría.

    Raises:
        ArchivoImportacionInvalidoError: si la extensión no es soportada, el
            archivo está vacío/corrupto, le faltan columnas obligatorias o el
            `modo` es inválido.
    """
    if modo not in MODOS_IMPORTACION:
        raise ArchivoImportacionInvalidoError(f"Modo de importación inválido: {modo!r}.")
    extension = Path(nombre_archivo).suffix.lower()
    if extension == ".csv":
        filas = _extraer_filas_csv(contenido)
    elif extension == ".xlsx":
        filas = _extraer_filas_xlsx(contenido)
    else:
        raise ArchivoImportacionInvalidoError(
            f"Formato de archivo no soportado: '{extension or nombre_archivo}'. Usar .csv o .xlsx."
        )

    resultado = ResultadoImportacion(total_filas=len(filas))
    categorias_por_nombre: dict[str, int | None] = {}
    fila_de_cada_codigo: dict[str, int] = {}

    try:
        with obtener_conexion(inmediata=True) as conexion:
            for numero_fila, fila in enumerate(filas, start=2):  # la fila 1 es el encabezado
                try:
                    _procesar_fila(
                        conexion, fila, numero_fila, modo, usuario_id, categorias_por_nombre, fila_de_cada_codigo, resultado
                    )
                except DatosInvalidosError as error:
                    resultado.errores.append(f"Fila {numero_fila}: {error}")
                except OverflowError:
                    resultado.errores.append(f"Fila {numero_fila}: hay un valor numérico fuera de rango.")
            if todo_o_nada and resultado.errores:
                raise _RevertirImportacion
            if usuario_id is not None and (resultado.importados or resultado.actualizados):
                repositorio_auditoria.registrar_en_conexion(
                    conexion,
                    usuario_id,
                    "PRODUCTOS_IMPORTADOS",
                    "PRODUCTO",
                    None,
                    f"{Path(nombre_archivo).name}: {resultado.importados} creado(s), "
                    f"{resultado.actualizados} actualizado(s)",
                )
    except _RevertirImportacion:
        resultado.importados = resultado.actualizados = resultado.sin_cambios = 0
        resultado.aplicado = False

    logger.info(
        "Importación de productos (%s): %s creados, %s actualizados, %s sin cambios, %s duplicados, "
        "%s errores (de %s filas)%s",
        modo,
        resultado.importados,
        resultado.actualizados,
        resultado.sin_cambios,
        resultado.duplicados,
        len(resultado.errores),
        resultado.total_filas,
        "" if resultado.aplicado else " -- NO se aplicó nada (todo o nada)",
    )
    return resultado


def _celda(fila: dict[str, str], columna: str) -> str:
    return (fila.get(columna) or "").strip()


def _procesar_fila(
    conexion,
    fila: dict[str, str],
    numero_fila: int,
    modo: str,
    usuario_id: int | None,
    categorias_por_nombre: dict[str, int | None],
    fila_de_cada_codigo: dict[str, int],
    resultado: ResultadoImportacion,
) -> None:
    """Valida y aplica una fila dentro de la transacción de la importación.
    Cualquier problema de la fila se informa como `DatosInvalidosError`. Toda la
    validación de negocio ocurre al construir `domain.producto.Producto`, incluida
    la de `unidad_medida`: acá no se repite esa lista de valores válidos."""
    codigo_barras = _celda(fila, "codigo_barras")
    if not codigo_barras:
        raise DatosInvalidosError("El código de barras no puede estar vacío.")
    if codigo_barras in fila_de_cada_codigo:
        raise DatosInvalidosError(
            f"El código '{codigo_barras}' está repetido en el archivo (ya aparece en la fila "
            f"{fila_de_cada_codigo[codigo_barras]})."
        )
    fila_de_cada_codigo[codigo_barras] = numero_fila

    existente = repositorio_productos.obtener_por_codigo_barras_en_conexion(conexion, codigo_barras)
    categoria_id = _categoria_de_la_fila(fila, categorias_por_nombre)

    if existente is None:
        producto = Producto(
            codigo_barras=codigo_barras,
            nombre=_celda(fila, "nombre"),
            precio_costo_centavos=texto_a_centavos(_celda(fila, "precio_costo") or "0"),
            precio_venta_centavos=texto_a_centavos(_celda(fila, "precio_venta") or "0"),
            stock_actual=_texto_a_entero(fila.get("stock_actual"), "stock_actual"),
            stock_minimo=_texto_a_entero(fila.get("stock_minimo"), "stock_minimo"),
            categoria_id=categoria_id,
            unidad_medida=(_celda(fila, "unidad_medida") or "UNIDAD").upper(),
        )
        repositorio_productos.crear_producto_en_conexion(conexion, producto)
        resultado.importados += 1
        return

    if modo == MODO_CREAR:
        resultado.duplicados += 1
        return

    if not existente.activo:
        raise DatosInvalidosError(
            f"El producto '{codigo_barras}' está dado de baja: reactivalo antes de actualizarlo."
        )
    # Celda vacía = conservar el valor actual. `stock_actual` del archivo se ignora.
    actualizado = Producto(
        id=existente.id,
        codigo_barras=existente.codigo_barras,
        nombre=_celda(fila, "nombre") or existente.nombre,
        precio_costo_centavos=(
            texto_a_centavos(_celda(fila, "precio_costo")) if _celda(fila, "precio_costo") else existente.precio_costo_centavos
        ),
        precio_venta_centavos=(
            texto_a_centavos(_celda(fila, "precio_venta")) if _celda(fila, "precio_venta") else existente.precio_venta_centavos
        ),
        stock_actual=existente.stock_actual,
        stock_minimo=(
            _texto_a_entero(fila.get("stock_minimo"), "stock_minimo")
            if _celda(fila, "stock_minimo")
            else existente.stock_minimo
        ),
        categoria_id=categoria_id if categoria_id is not None else existente.categoria_id,
        unidad_medida=(_celda(fila, "unidad_medida").upper() or existente.unidad_medida),
    )
    if _mismos_datos_editables(actualizado, existente):
        resultado.sin_cambios += 1
        return
    repositorio_productos.actualizar_datos_en_conexion(
        conexion, actualizado, usuario_id=usuario_id, origen="IMPORTACION", auditar=False
    )
    resultado.actualizados += 1


def _mismos_datos_editables(a: Producto, b: Producto) -> bool:
    return (
        a.nombre, a.precio_costo_centavos, a.precio_venta_centavos, a.stock_minimo, a.categoria_id, a.unidad_medida
    ) == (b.nombre, b.precio_costo_centavos, b.precio_venta_centavos, b.stock_minimo, b.categoria_id, b.unidad_medida)


def _categoria_de_la_fila(fila: dict[str, str], categorias_por_nombre: dict[str, int | None]) -> int | None:
    """Resuelve (con caché por importación) la categoría de la fila: `None` si la
    celda está vacía o ausente."""
    nombre = _celda(fila, "categoria")
    if nombre not in categorias_por_nombre:
        categorias_por_nombre[nombre] = _resolver_categoria(nombre)
    return categorias_por_nombre[nombre]


def _resolver_categoria(nombre_categoria: str | None) -> int | None:
    """Resuelve el nombre de categoría de una fila a su id.

    Columna opcional: vacía o ausente -> sin categoría (`None`). Si se
    especifica un nombre, la categoría debe existir ya -- la
    importación nunca crea categorías nuevas (ver docstring del
    módulo): un typo en un CSV no debe poder generar una categoría
    nueva sin que nadie la haya dado de alta a propósito.
    """
    nombre_categoria = (nombre_categoria or "").strip()
    if not nombre_categoria:
        return None
    categoria = servicio_categorias.obtener_por_nombre(nombre_categoria)
    if categoria is None:
        raise DatosInvalidosError(
            f"La categoría '{nombre_categoria}' no existe. Creála en Categorías antes de importar."
        )
    return categoria.id


def _texto_a_entero(texto: str | None, nombre_campo: str) -> int:
    texto = (texto or "").strip()
    if not texto:
        return 0
    try:
        return int(float(texto))
    except (ValueError, OverflowError) as error:  # OverflowError: 'inf', '1e400'
        raise DatosInvalidosError(f"Valor inválido para '{nombre_campo}': {texto!r}.") from error


def _validar_columnas(columnas: list[str]) -> None:
    faltantes = set(COLUMNAS_REQUERIDAS) - set(columnas)
    if faltantes:
        raise ArchivoImportacionInvalidoError(
            f"Faltan columnas obligatorias: {', '.join(sorted(faltantes))}."
        )


def _extraer_filas_csv(contenido: bytes) -> list[dict[str, str]]:
    try:
        texto = contenido.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ArchivoImportacionInvalidoError(
            "El archivo CSV debe estar codificado en UTF-8."
        ) from error

    try:
        dialecto = csv.Sniffer().sniff(texto[:4096], delimiters=",;\t")
    except csv.Error:
        dialecto = csv.excel

    lector = csv.reader(io.StringIO(texto), dialecto)
    try:
        encabezado = next(lector)
    except StopIteration as error:
        raise ArchivoImportacionInvalidoError("El archivo CSV está vacío.") from error

    columnas = [valor.strip().lower() for valor in encabezado]
    _validar_columnas(columnas)

    filas = []
    for valores in lector:
        if not any(valor.strip() for valor in valores):
            continue
        filas.append(dict(zip(columnas, valores)))
    return filas


def _extraer_filas_xlsx(contenido: bytes) -> list[dict[str, str]]:
    try:
        libro = load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
    except Exception as error:
        raise ArchivoImportacionInvalidoError(f"No se pudo leer el archivo Excel: {error}") from error

    hoja = libro.active
    filas_iter = hoja.iter_rows(values_only=True)
    try:
        encabezado = next(filas_iter)
    except StopIteration as error:
        raise ArchivoImportacionInvalidoError("El archivo Excel está vacío.") from error

    columnas = [str(valor).strip().lower() if valor is not None else "" for valor in encabezado]
    _validar_columnas(columnas)

    filas = []
    for fila in filas_iter:
        if fila is None or all(valor is None for valor in fila):
            continue
        filas.append({
            columna: ("" if valor is None else str(valor))
            for columna, valor in zip(columnas, fila)
        })
    return filas
