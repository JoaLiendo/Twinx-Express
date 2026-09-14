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

from domain.dinero import texto_a_centavos
from excepciones import ArchivoImportacionInvalidoError, CodigoBarrasDuplicadoError, DatosInvalidosError
from services import servicio_categorias, servicio_stock

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


@dataclass
class ResultadoImportacion:
    """Resumen de una importación masiva de productos."""

    total_filas: int
    importados: int = 0
    duplicados: int = 0
    errores: list[str] = field(default_factory=list)


def procesar_archivo(nombre_archivo: str, contenido: bytes) -> ResultadoImportacion:
    """Importa productos desde un archivo `.csv` o `.xlsx` subido por el usuario.

    Raises:
        ArchivoImportacionInvalidoError: si la extensión no es
            soportada, el archivo está vacío/corrupto, o le faltan
            columnas obligatorias.
    """
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

    for numero_fila, fila in enumerate(filas, start=2):  # la fila 1 es el encabezado
        try:
            _importar_fila(fila)
            resultado.importados += 1
        except CodigoBarrasDuplicadoError:
            resultado.duplicados += 1
        except DatosInvalidosError as error:
            resultado.errores.append(f"Fila {numero_fila}: {error}")

    logger.info(
        "Importación de productos: %s importados, %s duplicados, %s errores (de %s filas)",
        resultado.importados,
        resultado.duplicados,
        len(resultado.errores),
        resultado.total_filas,
    )
    return resultado


def _importar_fila(fila: dict[str, str]) -> None:
    """Valida y persiste una fila. Toda la validación de negocio ocurre
    en `servicio_stock.registrar_producto` (vía `domain.producto.Producto`),
    incluida la de `unidad_medida` -- acá no se repite esa lista de valores
    válidos, se deja pasar el texto tal cual y que la capa de dominio la
    rechace si corresponde."""
    codigo_barras = (fila.get("codigo_barras") or "").strip()
    nombre = (fila.get("nombre") or "").strip()
    precio_costo_centavos = texto_a_centavos(fila.get("precio_costo") or "0")
    precio_venta_centavos = texto_a_centavos(fila.get("precio_venta") or "0")
    stock_actual = _texto_a_entero(fila.get("stock_actual"), "stock_actual")
    stock_minimo = _texto_a_entero(fila.get("stock_minimo"), "stock_minimo")
    categoria_id = _resolver_categoria(fila.get("categoria"))
    unidad_medida = (fila.get("unidad_medida") or "UNIDAD").strip().upper() or "UNIDAD"

    servicio_stock.registrar_producto(
        codigo_barras=codigo_barras,
        nombre=nombre,
        precio_costo_centavos=precio_costo_centavos,
        precio_venta_centavos=precio_venta_centavos,
        stock_actual=stock_actual,
        stock_minimo=stock_minimo,
        categoria_id=categoria_id,
        unidad_medida=unidad_medida,
    )


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
    except ValueError as error:
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
