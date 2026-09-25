"""Repositorio de acceso a datos de productos.

Contiene únicamente consultas SQL parametrizadas (nunca se concatenan
strings para construir SQL). No contiene reglas de negocio: la
validación de los datos ocurre en `domain.producto.Producto` antes de
llegar acá. Cualquier caso de uso que necesite productos pasa por
`services.servicio_stock`, no por este módulo directamente.
"""

import sqlite3

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import historial_precios as repositorio_historial_precios
from domain.producto import Producto
from excepciones import CodigoBarrasDuplicadoError, ProductoNoEncontradoError

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


def crear_producto_en_conexion(conexion: sqlite3.Connection, producto: Producto) -> Producto:
    """Inserta un nuevo producto dentro de la transacción recibida y devuelve la
    entidad con su id asignado. No hace *commit* (lo controla quien invoca: ver
    `services.servicio_importacion`, que crea/actualiza muchos productos en una
    única transacción).

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
        fila = conexion.execute(consulta, parametros).fetchone()
    except sqlite3.IntegrityError as error:
        raise CodigoBarrasDuplicadoError(
            f"Ya existe un producto con el código de barras '{producto.codigo_barras}'."
        ) from error
    return _fila_a_producto(fila)


def crear_producto(producto: Producto) -> Producto:
    """Inserta un nuevo producto en su propia transacción (ver `crear_producto_en_conexion`)."""
    with obtener_conexion() as conexion:
        return crear_producto_en_conexion(conexion, producto)


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


def actualizar_datos_en_conexion(
    conexion: sqlite3.Connection,
    producto: Producto,
    usuario_id: int | None = None,
    origen: str = "EDICION",
    auditar: bool = True,
) -> Producto:
    """Actualiza los datos editables de un producto existente (no su id ni su stock).

    Para modificar el stock usar `actualizar_stock`, que es la
    operación que se ejecuta con cada venta o ajuste de inventario.

    V1.2: si cambió el precio de venta o el de costo, el cambio queda en
    el historial de precios (con `usuario_id`, `None` si no hay usuario
    autenticado) dentro de la misma transacción que el `UPDATE`: el precio
    anterior se lee bajo `BEGIN IMMEDIATE`, así el historial nunca refleja
    un valor distinto del que realmente se pisó. Si el precio no cambió,
    no se registra nada.

    Recibe la conexión: la transacción (con `BEGIN IMMEDIATE`) la controla quien
    invoca. `origen` es el origen del cambio de precio en el historial y
    `auditar=False` evita la entrada individual de auditoría cuando el llamador
    (una importación masiva) registra un único resumen.
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
        anterior = conexion.execute(
            "SELECT codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos, stock_minimo,"
            " categoria_id, unidad_medida FROM productos WHERE id = ?",
            (producto.id,),
        ).fetchone()
        fila = conexion.execute(consulta, parametros).fetchone()
    except sqlite3.IntegrityError as error:
        raise CodigoBarrasDuplicadoError(
            f"Ya existe un producto con el código de barras '{producto.codigo_barras}'."
        ) from error

    if fila is not None and anterior is not None:
        for campo, valor_anterior, valor_nuevo in (
            ("VENTA", anterior["precio_venta_centavos"], producto.precio_venta_centavos),
            ("COSTO", anterior["precio_costo_centavos"], producto.precio_costo_centavos),
        ):
            repositorio_historial_precios.registrar_cambio_en_conexion(
                conexion, producto.id, campo, valor_anterior, valor_nuevo, usuario_id, origen
            )
        modificados = [
            etiqueta
            for etiqueta, valor_anterior, valor_nuevo in (
                ("código de barras", anterior["codigo_barras"], producto.codigo_barras),
                ("nombre", anterior["nombre"], producto.nombre),
                ("precio de venta", anterior["precio_venta_centavos"], producto.precio_venta_centavos),
                ("costo", anterior["precio_costo_centavos"], producto.precio_costo_centavos),
                ("stock mínimo", anterior["stock_minimo"], producto.stock_minimo),
                ("categoría", anterior["categoria_id"], producto.categoria_id),
                ("unidad de medida", anterior["unidad_medida"], producto.unidad_medida),
            )
            if valor_anterior != valor_nuevo
        ]
        if auditar and usuario_id is not None and modificados:
            repositorio_auditoria.registrar_en_conexion(
                conexion,
                usuario_id,
                "PRODUCTO_EDITADO",
                "PRODUCTO",
                producto.id,
                f"{producto.nombre}: se modificó {', '.join(modificados)}",
            )

    if fila is None:
        raise ProductoNoEncontradoError(f"No existe un producto con id {producto.id}.")
    return _fila_a_producto(fila)


def actualizar_datos(producto: Producto, usuario_id: int | None = None) -> Producto:
    """Actualiza los datos editables de un producto en su propia transacción
    (ver `actualizar_datos_en_conexion`)."""
    with obtener_conexion(inmediata=True) as conexion:
        return actualizar_datos_en_conexion(conexion, producto, usuario_id)


def obtener_por_codigo_barras_en_conexion(conexion: sqlite3.Connection, codigo_barras: str) -> Producto | None:
    """Producto con ese código de barras, activo o no, dentro de la transacción
    recibida (una importación necesita distinguir "no existe" de "está dado de baja")."""
    fila = conexion.execute(
        f"SELECT {_COLUMNAS} FROM productos WHERE codigo_barras = ?", (codigo_barras,)
    ).fetchone()
    return _fila_a_producto(fila) if fila is not None else None


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


def listar_activos_con_precio(
    categoria_id: int | None = None,
    sin_categoria: bool = False,
    conexion: sqlite3.Connection | None = None,
) -> list[Producto]:
    """Productos activos con precio de venta configurado (> 0), por nombre,
    opcionalmente de una categoría o sin categoría. Base de la actualización
    masiva de precios: un producto sin precio no entra. Con `conexion` la
    lectura ocurre dentro de esa transacción (la confirmación de una
    actualización masiva revalida el alcance bajo el mismo lock)."""
    condiciones = ["activo = 1", "precio_venta_centavos > 0"]
    parametros: list[int] = []
    if categoria_id is not None:
        condiciones.append("categoria_id = ?")
        parametros.append(categoria_id)
    elif sin_categoria:
        condiciones.append("categoria_id IS NULL")
    consulta = f"SELECT {_COLUMNAS} FROM productos WHERE {' AND '.join(condiciones)} ORDER BY nombre"
    if conexion is not None:
        return [_fila_a_producto(fila) for fila in conexion.execute(consulta, parametros).fetchall()]
    with obtener_conexion() as conexion_propia:
        return [_fila_a_producto(fila) for fila in conexion_propia.execute(consulta, parametros).fetchall()]


_COLUMNA_DE_PRECIO = {"VENTA": "precio_venta_centavos", "COSTO": "precio_costo_centavos"}


def cambiar_precio_en_conexion(
    conexion: sqlite3.Connection,
    producto_id: int,
    campo: str,
    nuevo_valor_centavos: int,
    usuario_id: int | None,
    origen: str,
    lote_id: int | None = None,
) -> bool:
    """Único punto de escritura de un cambio de precio de venta o de costo
    "suelto" (compras y actualización masiva): lee el valor anterior, lo
    actualiza y registra el cambio en el historial, todo dentro de la
    transacción recibida. Devuelve `False` (sin escribir nada) si el valor no
    cambia. La edición completa de un producto (`actualizar_datos_en_conexion`)
    es el otro punto de escritura y registra el historial por su cuenta; un test
    estructural verifica que no exista ningún otro `UPDATE` de precios.

    `campo`: `"VENTA"` o `"COSTO"`.
    """
    columna = _COLUMNA_DE_PRECIO[campo]  # KeyError ante un campo inválido: error de programación
    fila = conexion.execute(f"SELECT {columna} FROM productos WHERE id = ?", (producto_id,)).fetchone()
    if fila is None:
        raise ProductoNoEncontradoError(f"No existe un producto con id {producto_id}.")
    valor_anterior = fila[0]
    if valor_anterior == nuevo_valor_centavos:
        return False
    conexion.execute(
        f"UPDATE productos SET {columna} = ?, fecha_actualizacion = datetime('now', 'localtime') WHERE id = ?",
        (nuevo_valor_centavos, producto_id),
    )
    repositorio_historial_precios.registrar_cambio_en_conexion(
        conexion, producto_id, campo, valor_anterior, nuevo_valor_centavos, usuario_id, origen, lote_id
    )
    return True


def actualizar_costo_en_conexion(
    conexion: sqlite3.Connection,
    producto_id: int,
    nuevo_costo_centavos: int,
    usuario_id: int | None = None,
    origen: str = "COMPRA",
) -> Producto:
    """Fija el costo vigente de un producto usando una conexión ya abierta,
    sin tocar ninguna otra columna. Pensada para componerse dentro de la
    transacción de un ingreso de mercadería (ver
    `services.servicio_compras.registrar_compra`): cada línea actualiza el costo
    al costo unitario de esa compra (sin promedio ponderado). Delega en
    `cambiar_precio_en_conexion`, así el historial no depende del llamador."""
    cambiar_precio_en_conexion(conexion, producto_id, "COSTO", nuevo_costo_centavos, usuario_id, origen)
    fila = conexion.execute(f"SELECT {_COLUMNAS} FROM productos WHERE id = ?", (producto_id,)).fetchone()
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
    """Devuelve los productos activos con stock que llegó al mínimo o está por debajo (alertas).

    Excluye el stock 0 (ver `Producto.tiene_stock_critico`)."""
    consulta = f"""
        SELECT {_COLUMNAS} FROM productos
        WHERE activo = 1 AND stock_actual > 0 AND stock_actual <= stock_minimo
        ORDER BY nombre
    """
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta).fetchall()
    return [_fila_a_producto(fila) for fila in filas]


def listar_para_reposicion() -> list[Producto]:
    """Productos activos con stock mínimo configurado (> 0) cuyo stock está
    **estrictamente por debajo** del mínimo, incluido el stock 0 (a diferencia de
    `listar_stock_critico`, que es la alerta del dashboard y lo excluye). Un stock
    exactamente igual al mínimo no entra; sin mínimo configurado tampoco.
    Primero los más urgentes (menos stock), luego por nombre."""
    consulta = f"""
        SELECT {_COLUMNAS} FROM productos
        WHERE activo = 1 AND stock_minimo > 0 AND stock_actual < stock_minimo
        ORDER BY stock_actual, nombre
    """
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta).fetchall()
    return [_fila_a_producto(fila) for fila in filas]


def listar_valorizables() -> list[Producto]:
    """Productos con stock físico a valorizar (Valorización de Inventario):
    activos e inactivos por igual, mientras `stock_actual > 0` -- un
    producto discontinuado con mercadería remanente sigue siendo
    capital real inmovilizado (decisión cerrada, ver auditoría de
    diseño). No filtra por `activo` a propósito, a diferencia de
    `listar_todos()`.
    """
    consulta = f"SELECT {_COLUMNAS} FROM productos WHERE stock_actual > 0 ORDER BY nombre"
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta).fetchall()
    return [_fila_a_producto(fila) for fila in filas]


_CONSULTAS_DE_MOTIVOS = (
    ("ventas", "SELECT 1 FROM detalle_venta WHERE producto_id = ? LIMIT 1"),
    ("compras", "SELECT 1 FROM detalle_compra WHERE producto_id = ? LIMIT 1"),
    ("ajustes de stock", "SELECT 1 FROM ajustes_stock WHERE producto_id = ? LIMIT 1"),
    ("cambios de precio", "SELECT 1 FROM historial_precios WHERE producto_id = ? LIMIT 1"),
    ("proveedores vinculados", "SELECT 1 FROM producto_proveedor WHERE producto_id = ? LIMIT 1"),
    ("inventarios", "SELECT 1 FROM inventario_lineas WHERE producto_id = ? LIMIT 1"),
)


def listar_motivos_de_conservacion_en_conexion(conexion: sqlite3.Connection, producto_id: int) -> list[str]:
    """Qué registros asociados impiden borrar físicamente un producto (ventas,
    compras, ajustes de stock, cambios de precio, proveedores vinculados, inventarios)."""
    return [
        motivo
        for motivo, consulta in _CONSULTAS_DE_MOTIVOS
        if conexion.execute(consulta, (producto_id,)).fetchone() is not None
    ]


def listar_motivos_de_conservacion(producto_id: int) -> list[str]:
    with obtener_conexion() as conexion:
        return listar_motivos_de_conservacion_en_conexion(conexion, producto_id)


def eliminar_producto_en_conexion(conexion: sqlite3.Connection, producto_id: int) -> list[str] | None:
    """Da de baja un producto activo dentro de la transacción recibida.

    Intenta primero un `DELETE` físico. Si el producto tiene registros asociados
    (ventas, compras, ajustes de stock o cambios de precio), la clave foránea
    `ON DELETE RESTRICT` lo impide (`sqlite3.IntegrityError`; el `DELETE` fallido
    no deja ningún efecto) y se lo desactiva (`activo = 0`) para conservar ese
    historial.

    Devuelve `None` si no existe un producto activo con ese id, `[]` si se
    eliminó físicamente, o la lista de motivos de la baja lógica.
    """
    existe = conexion.execute("SELECT 1 FROM productos WHERE id = ? AND activo = 1", (producto_id,)).fetchone()
    if existe is None:
        return None
    try:
        conexion.execute("DELETE FROM productos WHERE id = ?", (producto_id,))
        return []
    except sqlite3.IntegrityError:
        conexion.execute(
            "UPDATE productos SET activo = 0, fecha_actualizacion = datetime('now', 'localtime') WHERE id = ?",
            (producto_id,),
        )
        return listar_motivos_de_conservacion_en_conexion(conexion, producto_id)


def eliminar_producto(producto_id: int) -> bool:
    """Elimina un producto activo en su propia transacción. Devuelve `True` si
    la baja fue lógica (ver `eliminar_producto_en_conexion`)."""
    with obtener_conexion() as conexion:
        motivos = eliminar_producto_en_conexion(conexion, producto_id)
    if motivos is None:
        raise ProductoNoEncontradoError(f"No existe un producto activo con id {producto_id}.")
    return bool(motivos)


def listar_inactivos() -> list[Producto]:
    """Devuelve los productos dados de baja lógica (`activo=0`), para la
    pantalla de reactivación."""
    consulta = f"SELECT {_COLUMNAS} FROM productos WHERE activo = 0 ORDER BY nombre"
    with obtener_conexion() as conexion:
        filas = conexion.execute(consulta).fetchall()
    return [_fila_a_producto(fila) for fila in filas]


def reactivar_producto_en_conexion(conexion: sqlite3.Connection, producto_id: int) -> Producto:
    """Revierte una baja lógica (`activo` de 0 a 1) dentro de la transacción
    recibida. Simétrico al `UPDATE ... SET activo = 0` de la baja: no hay ninguna
    regla de negocio que validar más allá de "que exista y esté inactivo". No toca
    `stock_actual` ni ninguna otra columna."""
    consulta = f"""
        UPDATE productos
        SET activo = 1, fecha_actualizacion = datetime('now', 'localtime')
        WHERE id = ? AND activo = 0
        RETURNING {_COLUMNAS}
    """
    fila = conexion.execute(consulta, (producto_id,)).fetchone()
    if fila is None:
        raise ProductoNoEncontradoError(f"No existe un producto inactivo con id {producto_id}.")
    return _fila_a_producto(fila)


def reactivar_producto(producto_id: int) -> Producto:
    """Reactiva un producto en su propia transacción (ver `reactivar_producto_en_conexion`)."""
    with obtener_conexion() as conexion:
        return reactivar_producto_en_conexion(conexion, producto_id)

