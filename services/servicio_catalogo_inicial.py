"""Catálogo inicial de distribución de Twinx Express.

Una instalación nueva del ejecutable arranca con categorías y productos
genéricos de kiosco, sin usuarios ni datos operativos. El catálogo vive
en `db/seed/catalogo_inicial.json` (versionado en Git, independiente de
`data/kiosco.db`) y se carga en una única transacción, una sola vez.

Todos los productos nacen con precio de venta, costo y stock en 0: no se
inventan precios ni stock. Un producto sin precio de venta no se puede
vender (ver `PrecioVentaNoConfiguradoError`) y un producto sin stock no
aparece como alerta de stock crítico.

Nunca se vuelve a sembrar sobre una instalación existente: el marcador
es `PRAGMA user_version` (ver `db.repositorios.catalogo_inicial`).
"""

import json
import logging
from pathlib import Path

from config import RUTA_CATALOGO_INICIAL
from db.repositorios import catalogo_inicial as repositorio_catalogo
from db.repositorios.catalogo_inicial import ProductoConCategoria
from domain.categoria import Categoria
from domain.producto import Producto
from excepciones import DatosInvalidosError, ErrorBaseDatos, ErrorCatalogoInicial

logger = logging.getLogger(__name__)

VERSION_CATALOGO_SOPORTADA = 1
MINIMO_PRODUCTOS = 1
MAXIMO_PRODUCTOS = 100

# Claves permitidas de un producto del archivo. Que no existan precios,
# costos, stock ni imagen es lo que garantiza que el catálogo distribuido
# nunca los traiga: un archivo con cualquier otra clave se rechaza.
_CLAVES_PRODUCTO = frozenset({"codigo", "nombre", "categoria", "unidad"})
_CLAVES_CATALOGO = frozenset({"version", "categorias", "productos"})


def _leer_json(ruta: Path) -> dict:
    try:
        contenido = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise ErrorCatalogoInicial(f"No se pudo leer el catálogo inicial ({ruta.name}): {error}") from error
    except json.JSONDecodeError as error:
        raise ErrorCatalogoInicial(f"El catálogo inicial ({ruta.name}) no es un JSON válido: {error}") from error
    if not isinstance(contenido, dict):
        raise ErrorCatalogoInicial("El catálogo inicial debe ser un objeto JSON.")
    return contenido


def _texto(valor: object, descripcion: str) -> str:
    if not isinstance(valor, str):
        raise ErrorCatalogoInicial(f"{descripcion} debe ser un texto.")
    return valor


def _validar_categorias(valores: object) -> list[Categoria]:
    if not isinstance(valores, list) or not valores:
        raise ErrorCatalogoInicial("El catálogo inicial debe tener al menos una categoría.")
    categorias: list[Categoria] = []
    vistos: set[str] = set()
    for valor in valores:
        nombre = _texto(valor, "El nombre de una categoría")
        try:
            categoria = Categoria(nombre=nombre)
        except DatosInvalidosError as error:
            raise ErrorCatalogoInicial(f"Categoría inválida: {error}") from error
        if categoria.nombre in vistos:
            raise ErrorCatalogoInicial(f"Categoría repetida en el catálogo inicial: '{categoria.nombre}'.")
        vistos.add(categoria.nombre)
        categorias.append(categoria)
    return categorias


def _validar_producto(datos: object, indice: int) -> ProductoConCategoria:
    if not isinstance(datos, dict):
        raise ErrorCatalogoInicial(f"El producto #{indice} debe ser un objeto.")
    if set(datos) != _CLAVES_PRODUCTO:
        raise ErrorCatalogoInicial(
            f"El producto #{indice} debe tener exactamente las claves {sorted(_CLAVES_PRODUCTO)} "
            "(el catálogo inicial no incluye precios, costos, stock ni imágenes)."
        )
    try:
        producto = Producto(
            codigo_barras=_texto(datos["codigo"], f"El código del producto #{indice}"),
            nombre=_texto(datos["nombre"], f"El nombre del producto #{indice}"),
            precio_costo_centavos=0,
            precio_venta_centavos=0,
            stock_actual=0,
            stock_minimo=0,
            unidad_medida=_texto(datos["unidad"], f"La unidad del producto #{indice}"),
        )
    except DatosInvalidosError as error:
        raise ErrorCatalogoInicial(f"Producto #{indice} inválido: {error}") from error
    return producto, _texto(datos["categoria"], f"La categoría del producto #{indice}")


def leer_catalogo(ruta: Path) -> tuple[int, list[Categoria], list[ProductoConCategoria]]:
    """Lee y valida por completo el archivo del catálogo, sin tocar la base.

    Devuelve `(version, categorias, productos)`.

    Raises:
        ErrorCatalogoInicial: JSON ilegible o inválido, versión no
            soportada, categorías repetidas o vacías, código repetido,
            unidad inválida, categoría inexistente, claves extra
            (precios, stock, imagen) o cantidad de productos fuera de
            rango.
    """
    contenido = _leer_json(ruta)
    if set(contenido) != _CLAVES_CATALOGO:
        raise ErrorCatalogoInicial(f"El catálogo inicial debe tener exactamente las claves {sorted(_CLAVES_CATALOGO)}.")

    version = contenido["version"]
    if isinstance(version, bool) or version != VERSION_CATALOGO_SOPORTADA:
        raise ErrorCatalogoInicial(
            f"Versión de catálogo no soportada: {version!r} (se espera {VERSION_CATALOGO_SOPORTADA})."
        )

    categorias = _validar_categorias(contenido["categorias"])

    valores_productos = contenido["productos"]
    if not isinstance(valores_productos, list) or not (MINIMO_PRODUCTOS <= len(valores_productos) <= MAXIMO_PRODUCTOS):
        raise ErrorCatalogoInicial(
            f"El catálogo inicial debe tener entre {MINIMO_PRODUCTOS} y {MAXIMO_PRODUCTOS} productos."
        )

    nombres_categorias = {categoria.nombre for categoria in categorias}
    productos: list[ProductoConCategoria] = []
    codigos_vistos: set[str] = set()
    for indice, datos in enumerate(valores_productos, start=1):
        producto, nombre_categoria = _validar_producto(datos, indice)
        if producto.codigo_barras in codigos_vistos:
            raise ErrorCatalogoInicial(f"Código repetido en el catálogo inicial: '{producto.codigo_barras}'.")
        codigos_vistos.add(producto.codigo_barras)
        if nombre_categoria not in nombres_categorias:
            raise ErrorCatalogoInicial(
                f"El producto '{producto.codigo_barras}' referencia una categoría inexistente: '{nombre_categoria}'."
            )
        productos.append((producto, nombre_categoria))

    usadas = {nombre_categoria for _producto, nombre_categoria in productos}
    for categoria in categorias:
        if categoria.nombre not in usadas:
            raise ErrorCatalogoInicial(f"La categoría '{categoria.nombre}' no tiene ningún producto.")

    return version, categorias, productos


def sembrar_si_corresponde(ruta: Path = RUTA_CATALOGO_INICIAL) -> bool:
    """Carga el catálogo inicial si (y solo si) la instalación es nueva.

    Devuelve `True` si sembró y `False` si no correspondía. En una
    instalación que ya no es nueva no lee ni valida el archivo.

    Raises:
        ErrorCatalogoInicial: el catálogo es inválido o no pudo
            cargarse. La transacción se revierte por completo (no
            quedan datos parciales ni marcador) y el error queda
            registrado con nivel ERROR; el llamador decide no dejar
            arrancar la aplicación como si todo estuviera bien.
    """
    try:
        if not repositorio_catalogo.es_instalacion_nueva():
            logger.info("Catálogo inicial omitido: la instalación ya no es nueva.")
            return False

        version, categorias, productos = leer_catalogo(ruta)
        if not repositorio_catalogo.sembrar_catalogo(categorias, productos, version):
            logger.info("Catálogo inicial omitido: la instalación ya no es nueva.")
            return False
    except ErrorCatalogoInicial:
        logger.exception("No se pudo cargar el catálogo inicial de distribución.")
        raise
    except ErrorBaseDatos as error:
        logger.exception("No se pudo cargar el catálogo inicial de distribución.")
        raise ErrorCatalogoInicial(f"No se pudo cargar el catálogo inicial en la base de datos: {error}") from error

    logger.info(
        "Catálogo inicial cargado: %s categorías, %s productos (versión %s).", len(categorias), len(productos), version
    )
    return True
