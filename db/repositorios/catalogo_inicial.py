"""Repositorio de la carga del catálogo inicial de distribución.

Contiene únicamente SQL parametrizado: la validación de los datos ocurre
antes, en `services.servicio_catalogo_inicial`, y la entidad `Producto`
ya viene validada.

`PRAGMA user_version` se utiliza como marcador de versión del catálogo
inicial de distribución y NO representa la versión del esquema SQLite
(el esquema se versiona con `schema_migraciones`, ver `db.conexion`).
Vale 0 hasta que el catálogo se siembra, y queda en la versión sembrada.
"""

from db.conexion import obtener_conexion
from domain.categoria import Categoria
from domain.producto import Producto
from excepciones import ErrorCatalogoInicial

# Un producto del catálogo junto al nombre de su categoría: el id de la
# categoría no existe hasta insertarla, así que se resuelve por nombre
# dentro de la misma transacción (nunca hay ids hardcodeados).
ProductoConCategoria = tuple[Producto, str]


def _es_instalacion_nueva_en_conexion(conexion) -> bool:
    """Marcador en 0 y ni usuarios, ni productos, ni categorías cargados."""
    fila = conexion.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM usuarios) AS usuarios,
            (SELECT COUNT(*) FROM productos) AS productos,
            (SELECT COUNT(*) FROM categorias) AS categorias
        """
    ).fetchone()
    version_catalogo = conexion.execute("PRAGMA user_version").fetchone()[0]
    return version_catalogo == 0 and fila["usuarios"] == 0 and fila["productos"] == 0 and fila["categorias"] == 0


def es_instalacion_nueva() -> bool:
    """Lectura sin lock del mismo predicado que usa `sembrar_catalogo`.

    Sirve para no leer ni validar el archivo del catálogo en una
    instalación que ya no es nueva. La decisión real se vuelve a tomar
    dentro de la transacción de `sembrar_catalogo`.
    """
    with obtener_conexion() as conexion:
        return _es_instalacion_nueva_en_conexion(conexion)


def sembrar_catalogo(categorias: list[Categoria], productos: list[ProductoConCategoria], version: int) -> bool:
    """Inserta categorías y productos y fija el marcador, todo en una
    única transacción (`BEGIN IMMEDIATE`, un solo COMMIT).

    Solo actúa si la instalación es nueva (ver `_es_instalacion_nueva_en_conexion`,
    evaluado ya dentro de la transacción, sin carrera). Devuelve `True` si
    sembró y `False` si no correspondía (no modifica nada). Ante cualquier
    error se hace rollback completo: no quedan categorías, productos ni
    marcador.

    Raises:
        ErrorCatalogoInicial: un producto referencia una categoría que
            no está en `categorias`.
    """
    with obtener_conexion(inmediata=True) as conexion:
        if not _es_instalacion_nueva_en_conexion(conexion):
            return False

        id_por_nombre: dict[str, int] = {}
        for categoria in categorias:
            fila = conexion.execute(
                "INSERT INTO categorias (nombre) VALUES (?) RETURNING id", (categoria.nombre,)
            ).fetchone()
            id_por_nombre[categoria.nombre] = fila["id"]

        for producto, nombre_categoria in productos:
            categoria_id = id_por_nombre.get(nombre_categoria)
            if categoria_id is None:
                raise ErrorCatalogoInicial(
                    f"El producto '{producto.codigo_barras}' referencia una categoría inexistente: '{nombre_categoria}'."
                )
            conexion.execute(
                """
                INSERT INTO productos
                    (codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos,
                     stock_actual, stock_minimo, categoria_id, unidad_medida)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    producto.codigo_barras,
                    producto.nombre,
                    producto.precio_costo_centavos,
                    producto.precio_venta_centavos,
                    producto.stock_actual,
                    producto.stock_minimo,
                    categoria_id,
                    producto.unidad_medida,
                ),
            )

        # `PRAGMA` no admite parámetros (`?`): `int()` garantiza que lo
        # único que se interpola es un entero. Es transaccional: se
        # revierte junto con las inserciones si algo falla antes del COMMIT.
        conexion.execute(f"PRAGMA user_version = {int(version)}")
    return True
