"""Migración 022 (inventario físico): upgrade desde una base V1.3, `productos.version_stock` y su
trigger, tablas de inventario y los triggers que impiden estados imposibles por acceso directo.

Nunca toca `data/kiosco.db`: las bases son temporales.
"""

import sqlite3

import pytest

import db.conexion as modulo_conexion
from excepciones import ErrorBaseDatos

NOMBRE_022 = "022_inventario_fisico.sql"


def _con_fk(ruta) -> sqlite3.Connection:
    con = sqlite3.connect(ruta)
    con.execute("PRAGMA foreign_keys = ON")
    con.row_factory = sqlite3.Row
    return con


# --- upgrade V1.3 -> V1.4 -----------------------------------------------------------------------------


@pytest.fixture
def base_v13(tmp_path, monkeypatch):
    """Base con el esquema previo a la 022 (001-021) y datos existentes."""
    ruta = tmp_path / "v13.db"
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
    con = sqlite3.connect(ruta)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(modulo_conexion._TABLA_MIGRACIONES)
    for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
        if migracion.name < NOMBRE_022:
            con.executescript(migracion.read_text(encoding="utf-8"))
            con.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (migracion.name,))
    con.execute(
        "INSERT INTO usuarios (id, nombre_usuario, nombre_completo, password_hash, rol)"
        " VALUES (1, 'duenio', 'Dueño', 'h', 'OWNER')"
    )
    for producto_id, stock in ((1, 10), (2, 0), (3, 7)):
        con.execute(
            "INSERT INTO productos (id, codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos,"
            " stock_actual) VALUES (?, ?, ?, 10, 100, ?)",
            (producto_id, f"c{producto_id}", f"P{producto_id}", stock),
        )
    con.execute(
        "INSERT INTO ajustes_stock (producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante)"
        " VALUES (1, 1, 'MERMA', -1, 11, 10)"
    )
    con.commit()
    con.close()
    return ruta


def test_upgrade_v13_conserva_datos_e_inicia_la_version_en_cero(base_v13):
    modulo_conexion.inicializar_base_datos()

    con = _con_fk(base_v13)
    try:
        assert [tuple(f) for f in con.execute("SELECT id, stock_actual, version_stock FROM productos ORDER BY id")] == [
            (1, 10, 0),
            (2, 0, 0),
            (3, 7, 0),
        ]
        ajuste = con.execute("SELECT motivo, delta, inventario_id FROM ajustes_stock").fetchone()
        assert tuple(ajuste) == ("MERMA", -1, None)
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT 1 FROM schema_migraciones WHERE nombre_archivo = ?", (NOMBRE_022,)).fetchone()
        for tabla in ("inventarios", "inventario_lineas"):
            assert con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0] == 0
    finally:
        con.close()


def test_el_primer_inventario_tras_el_upgrade_funciona(base_v13):
    modulo_conexion.inicializar_base_datos()
    from services import servicio_inventario

    inventario = servicio_inventario.crear_inventario(1, [1, 3])
    servicio_inventario.registrar_conteo(inventario.id, 1, 9, 1)
    resultado = servicio_inventario.confirmar_inventario(inventario.id, 1)

    assert resultado.ajustes_generados == 1
    con = _con_fk(base_v13)
    try:
        assert con.execute("SELECT stock_actual, version_stock FROM productos WHERE id = 1").fetchone()[:] == (9, 1)
    finally:
        con.close()


def test_una_migracion_022_que_falla_a_mitad_no_deja_cambios(base_v13, monkeypatch):
    script = (modulo_conexion.DIRECTORIO_MIGRACIONES / NOMBRE_022).read_text(encoding="utf-8")
    roto = script + "\nINSERT INTO tabla_que_no_existe VALUES (1);\n"
    monkeypatch.setattr(
        type(modulo_conexion.DIRECTORIO_MIGRACIONES / NOMBRE_022),
        "read_text",
        lambda self, *a, **k: roto if self.name == NOMBRE_022 else self.read_bytes().decode("utf-8"),
    )

    with pytest.raises(ErrorBaseDatos, match="tabla_que_no_existe"):
        modulo_conexion.inicializar_base_datos()

    con = _con_fk(base_v13)
    try:
        columnas = [f[1] for f in con.execute("PRAGMA table_info(productos)").fetchall()]
        assert "version_stock" not in columnas
        assert con.execute("SELECT 1 FROM sqlite_master WHERE name = 'inventarios'").fetchone() is None
        assert con.execute("SELECT 1 FROM schema_migraciones WHERE nombre_archivo = ?", (NOMBRE_022,)).fetchone() is None
    finally:
        con.close()


# --- version_stock y su trigger ---------------------------------------------------------------------------


@pytest.fixture
def bd(base_datos_temporal):
    con = _con_fk(base_datos_temporal)
    con.execute(
        "INSERT INTO usuarios (id, nombre_usuario, nombre_completo, password_hash, rol)"
        " VALUES (1, 'duenio', 'Dueño', 'h', 'OWNER'), (2, 'cajera', 'Cajera', 'h', 'CASHIER')"
    )
    for producto_id in (1, 2):
        con.execute(
            "INSERT INTO productos (id, codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos,"
            " stock_actual) VALUES (?, ?, ?, 10, 100, 10)",
            (producto_id, f"c{producto_id}", f"P{producto_id}"),
        )
    con.commit()
    yield con
    con.close()


def _version(con, producto_id=1) -> int:
    return con.execute("SELECT version_stock FROM productos WHERE id = ?", (producto_id,)).fetchone()[0]


def test_cada_cambio_real_de_stock_incrementa_la_version_exactamente_en_uno(bd):
    bd.execute("UPDATE productos SET stock_actual = 9 WHERE id = 1")
    bd.execute("UPDATE productos SET stock_actual = 10 WHERE id = 1")  # vuelve al mismo número (ABA)

    assert _version(bd) == 2 and _version(bd, 2) == 0


@pytest.mark.parametrize(
    "sentencia",
    [
        "UPDATE productos SET nombre = 'otro' WHERE id = 1",
        "UPDATE productos SET precio_costo_centavos = 99, precio_venta_centavos = 199 WHERE id = 1",
        "UPDATE productos SET imagen_archivo = 'x.png' WHERE id = 1",
        "UPDATE productos SET activo = 0 WHERE id = 1",
        "UPDATE productos SET stock_minimo = 3 WHERE id = 1",
        "UPDATE productos SET fecha_actualizacion = datetime('now') WHERE id = 1",
        "UPDATE productos SET stock_actual = stock_actual WHERE id = 1",
        "UPDATE productos SET stock_actual = 10 WHERE id = 1",
    ],
)
def test_solo_los_cambios_reales_de_stock_mueven_la_version(bd, sentencia):
    bd.execute(sentencia)

    assert _version(bd) == 0


def test_el_trigger_no_recursa_ni_toca_otras_filas(bd):
    bd.execute("UPDATE productos SET stock_actual = 5")  # ambas filas

    assert (_version(bd, 1), _version(bd, 2)) == (1, 1)


def test_el_trigger_funciona_aunque_recursive_triggers_este_activo(bd):
    bd.execute("PRAGMA recursive_triggers = ON")

    bd.execute("UPDATE productos SET stock_actual = 4 WHERE id = 1")

    assert _version(bd) == 1


def test_los_returning_de_productos_no_dependen_de_version_stock():
    from db.repositorios import productos as repositorio_productos
    from domain.producto import Producto

    assert "version_stock" not in repositorio_productos._COLUMNAS
    assert "version_stock" not in Producto.__dataclass_fields__


def test_los_escritores_reales_de_stock_incrementan_la_version(base_datos_temporal, caja_abierta):
    from domain.compra import ItemCompra
    from domain.venta import ItemVenta
    from services import servicio_compras, servicio_proveedores, servicio_stock, servicio_ventas
    from tests.utilidades_clientes import consultar, crear_owner, crear_producto

    owner = crear_owner()
    producto = crear_producto(stock=50)
    version = lambda: consultar(base_datos_temporal, "SELECT version_stock FROM productos")[0][0]  # noqa: E731
    assert version() == 0

    venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 2)], "EFECTIVO", usuario_id=owner.id)
    assert version() == 1
    servicio_ventas.anular_venta(venta.id, "ERROR_CARGA", None, owner.id)
    assert version() == 2
    proveedor = servicio_proveedores.crear_proveedor("P")
    servicio_compras.registrar_compra(proveedor.id, owner.id, [ItemCompra(producto.id, 3, 10)])
    assert version() == 3
    servicio_stock.ajustar_stock(producto.id, -1, "MERMA", owner.id)
    assert version() == 4
    # cambios que no son de stock: precio (por edición) y baja/reactivación
    servicio_stock.actualizar_producto(producto.id, producto.codigo_barras, "Nuevo nombre", 20, 300, 2)
    servicio_stock.eliminar_producto(producto.id)
    servicio_stock.reactivar_producto(producto.id)
    assert version() == 4


# --- esquema de inventario ------------------------------------------------------------------------------------


def _abrir(con, usuario=1) -> int:
    return con.execute("INSERT INTO inventarios (usuario_id) VALUES (?) RETURNING id", (usuario,)).fetchone()[0]


def _linea(con, inventario, producto=1) -> int:
    return con.execute(
        "INSERT INTO inventario_lineas (inventario_id, producto_id) VALUES (?, ?) RETURNING id", (inventario, producto)
    ).fetchone()[0]


def _contar(con, inventario, producto=1, esperado=10, contada=8) -> None:
    con.execute(
        "UPDATE inventario_lineas SET stock_esperado = ?, version_esperada = 0, cantidad_contada = ?,"
        " costo_unitario_centavos = 10, usuario_conteo_id = 1, fecha_conteo = '2026-01-01 10:00:00'"
        " WHERE inventario_id = ? AND producto_id = ?",
        (esperado, contada, inventario, producto),
    )


def _ajuste(con, inventario, producto=1, delta=-2, anterior=10, motivo="RECUENTO"):
    return con.execute(
        "INSERT INTO ajustes_stock (producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante,"
        " inventario_id) VALUES (?, 1, ?, ?, ?, ?, ?) RETURNING id",
        (producto, motivo, delta, anterior, anterior + delta, inventario),
    ).fetchone()[0]


def _cerrar(con, inventario, estado="CONFIRMADO") -> None:
    con.execute(
        "UPDATE inventarios SET estado = ?, usuario_cierre_id = 1, fecha_cierre = '2026-01-01 11:00:00' WHERE id = ?",
        (estado, inventario),
    )


def _inventario_confirmado(con) -> int:
    inventario = _abrir(con)
    _linea(con, inventario)
    _contar(con, inventario)
    ajuste = _ajuste(con, inventario)
    con.execute("UPDATE inventario_lineas SET ajuste_id = ?", (ajuste,))
    _cerrar(con, inventario)
    return inventario


def test_un_solo_inventario_abierto(bd):
    _abrir(bd)

    with pytest.raises(sqlite3.IntegrityError):
        _abrir(bd, usuario=2)


def test_se_puede_abrir_otro_tras_cerrar_y_conviven_muchos_cerrados(bd):
    for _ in range(3):
        inventario = _abrir(bd)
        _linea(bd, inventario)
        _contar(bd, inventario, contada=10)
        _cerrar(bd, inventario)

    assert _abrir(bd) is not None


def test_estado_y_datos_de_cierre_deben_ser_coherentes(bd):
    with pytest.raises(sqlite3.IntegrityError):
        bd.execute("INSERT INTO inventarios (usuario_id, estado) VALUES (1, 'CONFIRMADO')")
    with pytest.raises(sqlite3.IntegrityError):
        bd.execute("INSERT INTO inventarios (usuario_id, estado) VALUES (1, 'BORRADO')")
    inventario = _abrir(bd)
    with pytest.raises(sqlite3.IntegrityError):
        bd.execute("UPDATE inventarios SET estado = 'CANCELADO' WHERE id = ?", (inventario,))  # sin fecha/usuario de cierre


def test_un_inventario_cerrado_es_inmutable_y_no_se_borra(bd):
    inventario = _inventario_confirmado(bd)

    for sentencia in (
        "UPDATE inventarios SET observaciones = 'x' WHERE id = ?",
        "UPDATE inventarios SET estado = 'ABIERTO', fecha_cierre = NULL, usuario_cierre_id = NULL WHERE id = ?",
        "UPDATE inventarios SET estado = 'CANCELADO' WHERE id = ?",
        "DELETE FROM inventarios WHERE id = ?",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            bd.execute(sentencia, (inventario,))


def test_no_se_borra_un_inventario_abierto_ni_se_cambia_su_usuario_o_fecha_de_inicio(bd):
    inventario = _abrir(bd)

    for sentencia in (
        "DELETE FROM inventarios WHERE id = ?",
        "UPDATE inventarios SET usuario_id = 2 WHERE id = ?",
        "UPDATE inventarios SET fecha_inicio = '2000-01-01' WHERE id = ?",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            bd.execute(sentencia, (inventario,))


def test_las_lineas_de_un_inventario_cerrado_no_se_agregan_modifican_ni_borran(bd):
    inventario = _inventario_confirmado(bd)

    with pytest.raises(sqlite3.IntegrityError):
        _linea(bd, inventario, producto=2)
    with pytest.raises(sqlite3.IntegrityError):
        bd.execute("UPDATE inventario_lineas SET cantidad_contada = 1 WHERE inventario_id = ?", (inventario,))
    with pytest.raises(sqlite3.IntegrityError):
        bd.execute("DELETE FROM inventario_lineas WHERE inventario_id = ?", (inventario,))


def test_las_lineas_nunca_se_borran_ni_cambian_de_identidad(bd):
    inventario = _abrir(bd)
    _linea(bd, inventario)

    with pytest.raises(sqlite3.IntegrityError):
        bd.execute("DELETE FROM inventario_lineas")
    with pytest.raises(sqlite3.IntegrityError):
        bd.execute("UPDATE inventario_lineas SET producto_id = 2")
    with pytest.raises(sqlite3.IntegrityError):
        _linea(bd, inventario)  # UNIQUE (inventario, producto)


def test_una_linea_nueva_no_puede_venir_contada(bd):
    inventario = _abrir(bd)

    with pytest.raises(sqlite3.IntegrityError):
        bd.execute(
            "INSERT INTO inventario_lineas (inventario_id, producto_id, stock_esperado, version_esperada,"
            " cantidad_contada, costo_unitario_centavos, usuario_conteo_id, fecha_conteo)"
            " VALUES (?, 1, 10, 0, 8, 10, 1, 'x')",
            (inventario,),
        )


@pytest.mark.parametrize(
    "asignacion",
    [
        "stock_esperado = 10",
        "cantidad_contada = 8",
        "stock_esperado = 10, version_esperada = 0, cantidad_contada = 8",
        "stock_esperado = 10, version_esperada = 0, cantidad_contada = 8, costo_unitario_centavos = 10",
        "stock_esperado = -1, version_esperada = 0, cantidad_contada = 8, costo_unitario_centavos = 10,"
        " usuario_conteo_id = 1, fecha_conteo = 'x'",
        "stock_esperado = 10, version_esperada = 0, cantidad_contada = -8, costo_unitario_centavos = 10,"
        " usuario_conteo_id = 1, fecha_conteo = 'x'",
        "ajuste_id = 1",
    ],
)
def test_el_conteo_debe_ser_coherente_todo_o_nada(bd, asignacion):
    inventario = _abrir(bd)
    _linea(bd, inventario)

    with pytest.raises(sqlite3.IntegrityError):
        bd.execute(f"UPDATE inventario_lineas SET {asignacion}")


def test_no_se_confirma_sin_lineas_contadas(bd):
    inventario = _abrir(bd)
    _linea(bd, inventario)

    with pytest.raises(sqlite3.IntegrityError):
        _cerrar(bd, inventario)
    _cerrar(bd, inventario, "CANCELADO")  # cancelar sí


def test_no_se_confirma_con_diferencias_sin_ajuste(bd):
    inventario = _abrir(bd)
    _linea(bd, inventario)
    _contar(bd, inventario)

    with pytest.raises(sqlite3.IntegrityError):
        _cerrar(bd, inventario)


def test_no_se_confirma_con_un_ajuste_que_no_coincide_con_la_diferencia(bd):
    inventario = _abrir(bd)
    _linea(bd, inventario)
    _contar(bd, inventario)  # diferencia -2
    ajuste_de_otro = bd.execute(
        "INSERT INTO ajustes_stock (producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante)"
        " VALUES (1, 1, 'MERMA', -1, 10, 9) RETURNING id"
    ).fetchone()[0]
    bd.execute("UPDATE inventario_lineas SET ajuste_id = ?", (ajuste_de_otro,))

    with pytest.raises(sqlite3.IntegrityError):
        _cerrar(bd, inventario)


def test_no_se_confirma_con_ajuste_en_una_linea_sin_diferencia(bd):
    inventario = _abrir(bd)
    _linea(bd, inventario)
    _contar(bd, inventario, contada=10)
    otro = bd.execute(
        "INSERT INTO ajustes_stock (producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante)"
        " VALUES (1, 1, 'MERMA', -1, 10, 9) RETURNING id"
    ).fetchone()[0]
    bd.execute("UPDATE inventario_lineas SET ajuste_id = ?", (otro,))

    with pytest.raises(sqlite3.IntegrityError):
        _cerrar(bd, inventario)


def test_un_ajuste_de_inventario_debe_ser_recuento_de_una_linea_contada_de_un_inventario_abierto(bd):
    inventario = _abrir(bd)
    _linea(bd, inventario)

    with pytest.raises(sqlite3.IntegrityError):  # la línea todavía no está contada
        _ajuste(bd, inventario)
    _contar(bd, inventario)
    with pytest.raises(sqlite3.IntegrityError):  # motivo distinto de RECUENTO
        _ajuste(bd, inventario, motivo="MERMA")
    with pytest.raises(sqlite3.IntegrityError):  # delta que no explica la línea
        _ajuste(bd, inventario, delta=-1, anterior=10)
    with pytest.raises(sqlite3.IntegrityError):  # stock anterior distinto del esperado
        _ajuste(bd, inventario, delta=-2, anterior=11)
    with pytest.raises(sqlite3.IntegrityError):  # producto que no está en el inventario
        _ajuste(bd, inventario, producto=2)
    _ajuste(bd, inventario)  # el correcto sí


def test_a_lo_sumo_un_ajuste_por_inventario_y_producto(bd):
    inventario = _abrir(bd)
    _linea(bd, inventario)
    _contar(bd, inventario)
    _ajuste(bd, inventario)

    with pytest.raises(sqlite3.IntegrityError):
        _ajuste(bd, inventario)


def test_no_se_agregan_ajustes_a_un_inventario_cerrado_y_su_origen_es_inmutable(bd):
    inventario = _inventario_confirmado(bd)

    with pytest.raises(sqlite3.IntegrityError):
        _ajuste(bd, inventario)
    with pytest.raises(sqlite3.IntegrityError):
        bd.execute("UPDATE ajustes_stock SET inventario_id = NULL WHERE inventario_id = ?", (inventario,))


def test_los_ajustes_manuales_sin_inventario_no_se_ven_afectados(bd):
    bd.execute(
        "INSERT INTO ajustes_stock (producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante)"
        " VALUES (1, 1, 'RECUENTO', -1, 10, 9)"
    )
    bd.execute(
        "INSERT INTO ajustes_stock (producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante)"
        " VALUES (1, 1, 'RECUENTO', -1, 9, 8)"
    )  # sin inventario_id no rige el índice único parcial


def test_integridad_referencial_restrict(bd):
    inventario = _inventario_confirmado(bd)

    with pytest.raises(sqlite3.IntegrityError):
        bd.execute("DELETE FROM productos WHERE id = 1")
    with pytest.raises(sqlite3.IntegrityError):
        bd.execute("DELETE FROM usuarios WHERE id = 1")
    with pytest.raises(sqlite3.IntegrityError):
        bd.execute("DELETE FROM ajustes_stock WHERE inventario_id = ?", (inventario,))
    with pytest.raises(sqlite3.IntegrityError):
        _linea(bd, 9999)
    with pytest.raises(sqlite3.IntegrityError):
        _abrir(bd, usuario=9999)
    assert bd.execute("PRAGMA foreign_key_check").fetchall() == []


def test_la_clave_de_idempotencia_del_inventario_es_unica(bd):
    primero = _abrir(bd)
    bd.execute("UPDATE inventarios SET clave_idempotencia = 'k' WHERE id = ?", (primero,))
    _linea(bd, primero)
    _contar(bd, primero, contada=10)
    _cerrar(bd, primero)
    segundo = _abrir(bd)

    with pytest.raises(sqlite3.IntegrityError):
        bd.execute("UPDATE inventarios SET clave_idempotencia = 'k' WHERE id = ?", (segundo,))
