"""Repositorio de acceso a datos de productos.

Contiene únicamente consultas SQL parametrizadas (nunca se concatenan
strings para construir SQL). No contiene reglas de negocio: la
validación de los datos ocurre en `domain.producto.Producto` antes de
llegar acá. Cualquier caso de uso que necesite productos pasa por
`services.servicio_stock`, no por este módulo directamente.
"""

import sqlite3

from db.conexion import obtener_conexion
from domain.producto import Producto
from excepciones import CodigoBarrasDuplicadoError, ErrorBaseDatos, ProductoNoEncontradoError

_COLUMNAS = """
    id, codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos,
    stock_actual, stock_minimo, fecha_actualizacion, activo, categoria_id, unidad_medida,
    imagen_archivo
"""


def _fila_a_producto(fila: sqlite3.Row) -> Producto:
    """Mapea una fila de la tabla `productos` a la entidad de dominio."""
    return Producto(
        id=fila["id"],
        codigo_barras=fila["codigo_barras"],
        nombre=fila["nombre"],
        precio_costo_centavos=fila["precio_costo_centavos"],
        precio_venta_centavos=fila["precio_venta_centavos"],
        stock_actual=fila["stock_actual"],
        stock_minimo=fila["stock_minimo"],
        fecha_actualizacion=fila["fecha_actualizacion"],
        activo=bool(fila["activo"]),
        categoria_id=fila["categoria_id"],
        unidad_medida=fila["unidad_medida"],
        imagen_archivo=fila["imagen_archivo"],
    )


def _escapar_comodines_like(texto: str) -> str:
    """Escapa `%` y `_` para que una búsqueda por nombre no interprete
    comodines de SQL ingresados por el usuario como patrones."""
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def crear_producto(producto: Producto) -> Producto:
    """Inserta un nuevo producto y devuelve la entidad con su id asignado.

    Traduce la violación de UNIQUE sobre `codigo_barras` en
    `CodigoBarrasDuplicadoError`, para que la capa de servicios pueda
    reaccionar a ese caso puntual sin inspeccionar mensajes de error.
    """
    consulta = f"""
        INSERT INTO productos
            (codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos,
             stock_actual, stock_minimo, categoria_id, unidad_medida)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING {_COLUMNAS}
    """
    parametros = (
        producto.codigo_barras,
        producto.nombre,
        producto.precio_costo_centavos,
        producto.precio_venta_centavos,
        producto.stock_actual,
        producto.stock_minimo,
        producto.categoria_id,
        producto.unidad_medida,
    )
    try:
        with obtener_conexion() as conexion:
            fila = conexion.execute(consulta, parametros).fetchone()
    except ErrorBaseDatos as error:
        if isinstance(error.__cause__, sqlite3.IntegrityError):
            raise CodigoBarrasDuplicadoError(
                f"Ya existe un producto con el código de barras '{producto.codigo_barras}'."
            ) from error
        raise
    return _fila_a_producto(fila)


def obtener_por_id_en_conexion(conexion: sqlite3.Connection, producto_id: int) -> Producto | None:
    """Busca un producto por id usando una conexión ya abierta.

    Pensada para componerse dentro de una transacción más amplia (ver
    `services.servicio_ventas.registrar_venta`), donde hace falta leer
    el stock/precio vigente y luego descontarlo en la misma
    transacción.
    """
    consulta = f"SELECT {_COLUMNAS} FROM productos WHERE id = ? AND activo = 1"
    fila = conexion.execute(consulta, (producto_id,)).fetchone()
    return _fila_a_producto(fila) if fila is not None else None


def obtener_por_id_en_conexion_incluyendo_inactivos(
    conexion: sqlite3.Connection, producto_id: int
) -> Producto | None:
    """Igual que `obtener_por_id_en_conexion`, pero sin el filtro
    `activo = 1`.

    Existe únicamente para restaurar stock al anular una venta (ver
    `services.servicio_ventas.anular_venta`): un producto vendido puede
    haberse desactivado después de esa venta, y el estado del catálogo
    no debe condicionar la integridad del stock -- un producto
    discontinuado sigue teniendo un `stock_actual` real que hay que
    poder corregir. No usar para ningún otro caso: el resto de la
    aplicación (ventas nuevas, ajustes de stock) sigue exigiendo
    `activo = 1` a propósito, y `obtener_por_id_en_conexion` no cambia.
    """
    consulta = f"SELECT {_COLUMNAS} FROM productos WHERE id = ?"
    fila = conexion.execute(consulta, (producto_id,)).fetchone()
    return _fila_a_producto(fila) if fila is not None else None


def obtener_por_id(producto_id: int) -> Producto | None:
    """Busca un producto activo por id (usado por las pantallas de edición/baja)."""
    with obtener_conexion() as conexion:
        return obtener_por_id_en_conexion(conexion, producto_id)


def buscar_por_codigo_barras(codigo_barras: str) -> Producto | None:
    """Busca un producto activo por código de barras exacto (lectura por lector USB)."""
    consulta = f"SELECT {_COLUMNAS} FROM productos WHERE codigo_barras = ? AND activo = 1"
    with obtener_conexion() as conexion:
        fila = conexion.execute(consulta, (codigo_barras,)).fetchone()
    return _fila_a_producto(fila) if fila is not None else None


def buscar_por_nombre(texto: str) -> list[Producto]:
    """Busca productos activos cuyo nombre contiene `texto` (búsqueda manual)."""
    consulta = f"""
        SELECT {_COLUMNAS} FROM productos
        WHERE activo = 1 AND nombre LIKE ? ESCAPE '\\'
        ORDER BY nombre
    """
    patron = f"%{_escapar_comodines_like(texto)}%"
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta, (patron,)).fetchall()
    return [_fila_a_producto(fila) for fila in filas]


def actualizar_datos(producto: Producto) -> Producto:
    """Actualiza los datos editables de un producto existente (no su id ni su stock).

    Para modificar el stock usar `actualizar_stock`, que es la
    operación que se ejecuta con cada venta o ajuste de inventario.
    """
    if producto.id is None:
        raise ProductoNoEncontradoError("No se puede actualizar un producto sin id.")

    consulta = f"""
        UPDATE productos
        SET codigo_barras = ?,
            nombre = ?,
            precio_costo_centavos = ?,
            precio_venta_centavos = ?,
            stock_minimo = ?,
            categoria_id = ?,
            unidad_medida = ?,
            fecha_actualizacion = datetime('now', 'localtime')
        WHERE id = ?
        RETURNING {_COLUMNAS}
    """
    parametros = (
        producto.codigo_barras,
        producto.nombre,
        producto.precio_costo_centavos,
        producto.precio_venta_centavos,
        producto.stock_minimo,
        producto.categoria_id,
        producto.unidad_medida,
        producto.id,
    )
    try:
        with obtener_conexion() as conexion:
            fila = conexion.execute(consulta, parametros).fetchone()
    except ErrorBaseDatos as error:
        if isinstance(error.__cause__, sqlite3.IntegrityError):
            raise CodigoBarrasDuplicadoError(
                f"Ya existe un producto con el código de barras '{producto.codigo_barras}'."
            ) from error
        raise

    if fila is None:
        raise ProductoNoEncontradoError(f"No existe un producto con id {producto.id}.")
    return _fila_a_producto(fila)


def actualizar_imagen(producto_id: int, nombre_archivo: str | None) -> Producto:
    """Fija (o quita, con `None`) la imagen asociada a un producto.

    En su propia transacción, sin tocar ninguna otra columna -- mismo
    patrón que `actualizar_stock`: quien haga alta/edición de datos
    generales del producto pasa por `actualizar_datos`, quien
    solamente cambia la imagen pasa por acá.
    """
    consulta = f"""
        UPDATE productos
        SET imagen_archivo = ?,
            fecha_actualizacion = datetime('now', 'localtime')
        WHERE id = ?
        RETURNING {_COLUMNAS}
    """
    with obtener_conexion() as conexion:
        fila = conexion.execute(consulta, (nombre_archivo, producto_id)).fetchone()
    if fila is None:
        raise ProductoNoEncontradoError(f"No existe un producto con id {producto_id}.")
    return _fila_a_producto(fila)


def actualizar_stock_en_conexion(
    conexion: sqlite3.Connection, producto_id: int, nuevo_stock: int
) -> Producto:
    """Igual que `actualizar_stock`, pero usando una conexión ya abierta.

    Permite que el descuento de stock forme parte de una transacción
    más amplia junto con el alta de una venta (ver
    `services.servicio_ventas.registrar_venta`), en vez de confirmarse
    en una transacción propia. La tabla `productos` tiene un
    CHECK (stock_actual >= 0) como última línea de defensa; la
    validación de negocio normal ocurre antes, en
    `domain.producto.Producto.actualizar_stock`.
    """
    consulta = f"""
        UPDATE productos
        SET stock_actual = ?,
            fecha_actualizacion = datetime('now', 'localtime')
        WHERE id = ?
        RETURNING {_COLUMNAS}
    """
    fila = conexion.execute(consulta, (nuevo_stock, producto_id)).fetchone()
    if fila is None:
        raise ProductoNoEncontradoError(f"No existe un producto con id {producto_id}.")
    return _fila_a_producto(fila)


def actualizar_costo_en_conexion(
    conexion: sqlite3.Connection, producto_id: int, nuevo_costo_centavos: int
) -> Producto:
    """Fija el precio de costo vigente de un producto usando una conexión
    ya abierta, sin tocar ninguna otra columna (mismo patrón aislado que
    `actualizar_imagen`).

    Pensada para componerse dentro de la transacción atómica de un
    ingreso de mercadería (ver `services.servicio_compras.registrar_compra`):
    cada línea de una compra actualiza el costo vigente del producto al
    costo unitario de esa compra (Fase 4B: sin promedio ponderado).
    """
    consulta = f"""
        UPDATE productos
        SET precio_costo_centavos = ?,
            fecha_actualizacion = datetime('now', 'localtime')
        WHERE id = ?
        RETURNING {_COLUMNAS}
    """
    fila = conexion.execute(consulta, (nuevo_costo_centavos, producto_id)).fetchone()
    if fila is None:
        raise ProductoNoEncontradoError(f"No existe un producto con id {producto_id}.")
    return _fila_a_producto(fila)


def actualizar_stock(producto_id: int, nuevo_stock: int) -> Producto:
    """Fija el stock actual de un producto en su propia transacción.

    Para descontar stock como parte de una venta (junto con el alta de
    la venta y su detalle) usar `actualizar_stock_en_conexion` dentro
    de la misma conexión, no esta función.
    """
    with obtener_conexion() as conexion:
        return actualizar_stock_en_conexion(conexion, producto_id, nuevo_stock)


def listar_todos() -> list[Producto]:
    """Devuelve todos los productos activos, ordenados por nombre (grilla del POS)."""
    consulta = f"SELECT {_COLUMNAS} FROM productos WHERE activo = 1 ORDER BY nombre"
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta).fetchall()
    return [_fila_a_producto(fila) for fila in filas]


def listar_stock_critico() -> list[Producto]:
    """Devuelve los productos activos cuyo stock actual llegó al mínimo o está por debajo (alertas)."""
    consulta = f"""
        SELECT {_COLUMNAS} FROM productos
        WHERE activo = 1 AND stock_actual <= stock_minimo
        ORDER BY nombre
    """
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta).fetchall()
    return [_fila_a_producto(fila) for fila in filas]


def eliminar_producto(producto_id: int) -> bool:
    """Elimina un producto activo. Devuelve `True` si la baja fue lógica.

    Intenta primero un `DELETE` físico. Si el producto tiene ventas
    asociadas, la clave foránea `ON DELETE RESTRICT` de
    `detalle_venta` lo impide (`sqlite3.IntegrityError`): en ese caso
    se lo desactiva (`activo = 0`) en vez de borrarlo, para conservar
    el historial de ventas que lo referencia.
    """
    try:
        with obtener_conexion() as conexion:
            cursor = conexion.execute(
                "DELETE FROM productos WHERE id = ? AND activo = 1", (producto_id,)
            )
            fue_eliminado = cursor.rowcount > 0
        baja_logica = False
    except ErrorBaseDatos as error:
        if not isinstance(error.__cause__, sqlite3.IntegrityError):
            raise
        with obtener_conexion() as conexion:
            cursor = conexion.execute(
                """
                UPDATE productos
                SET activo = 0, fecha_actualizacion = datetime('now', 'localtime')
                WHERE id = ? AND activo = 1
                """,
                (producto_id,),
            )
            fue_eliminado = cursor.rowcount > 0
        baja_logica = True

    if not fue_eliminado:
        raise ProductoNoEncontradoError(f"No existe un producto activo con id {producto_id}.")
    return baja_logica


def listar_inactivos() -> list[Producto]:
    """Devuelve los productos dados de baja lógica (`activo=0`), para la
    pantalla de reactivación."""
    consulta = f"SELECT {_COLUMNAS} FROM productos WHERE activo = 0 ORDER BY nombre"
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta).fetchall()
    return [_fila_a_producto(fila) for fila in filas]


def reactivar_producto(producto_id: int) -> Producto:
    """Revierte una baja lógica (`activo` de 0 a 1).

    Simétrico al `UPDATE ... SET activo = 0` de `eliminar_producto`:
    no hay ninguna regla de negocio que validar más allá de "que
    exista y esté inactivo", así que es un `UPDATE` directo, sin pasar
    por `domain.producto.Producto`. No toca `stock_actual` ni ninguna
    otra columna.
    """
    consulta = f"""
        UPDATE productos
        SET activo = 1, fecha_actualizacion = datetime('now', 'localtime')
        WHERE id = ? AND activo = 0
        RETURNING {_COLUMNAS}
    """
    with obtener_conexion() as conexion:
        fila = conexion.execute(consulta, (producto_id,)).fetchone()
    if fila is None:
        raise ProductoNoEncontradoError(f"No existe un producto inactivo con id {producto_id}.")
    return _fila_a_producto(fila)
