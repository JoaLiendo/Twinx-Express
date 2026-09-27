"""Pruebas de integración de services.servicio_proveedores contra una base
de datos SQLite real (temporal y aislada, ver tests/conftest.py)."""

import pytest

from db.conexion import obtener_conexion
from excepciones import DatosInvalidosError, NombreProveedorDuplicadoError, ProveedorNoEncontradoError
from services import servicio_proveedores


def test_crear_proveedor_lo_persiste_y_asigna_id(base_datos_temporal):
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")

    assert proveedor.id is not None
    assert proveedor.nombre == "Distribuidora SA"


def test_crear_proveedor_con_datos_de_contacto(base_datos_temporal):
    proveedor = servicio_proveedores.crear_proveedor(
        "Distribuidora SA",
        contacto_nombre="Juan Pérez",
        telefono="1122334455",
        email="ventas@distribuidora.com",
        direccion="Av. Siempre Viva 742",
        notas="Entrega los martes",
    )

    assert proveedor.contacto_nombre == "Juan Pérez"
    assert proveedor.telefono == "1122334455"
    assert proveedor.email == "ventas@distribuidora.com"
    assert proveedor.direccion == "Av. Siempre Viva 742"
    assert proveedor.notas == "Entrega los martes"


def test_crear_proveedor_duplicado_falla(base_datos_temporal):
    servicio_proveedores.crear_proveedor("Distribuidora SA")

    with pytest.raises(NombreProveedorDuplicadoError):
        servicio_proveedores.crear_proveedor("Distribuidora SA")


def test_crear_proveedor_con_nombre_vacio_falla(base_datos_temporal):
    with pytest.raises(DatosInvalidosError):
        servicio_proveedores.crear_proveedor("   ")


def test_listar_activos(base_datos_temporal):
    servicio_proveedores.crear_proveedor("Distribuidora SA")
    servicio_proveedores.crear_proveedor("Mayorista Norte")

    nombres = [p.nombre for p in servicio_proveedores.listar_activos()]

    assert nombres == ["Distribuidora SA", "Mayorista Norte"]


def test_listar_todos_incluye_inactivos(base_datos_temporal):
    servicio_proveedores.crear_proveedor("Distribuidora SA")
    inactivo = servicio_proveedores.crear_proveedor("Mayorista Norte")
    with obtener_conexion() as conexion:
        conexion.execute("UPDATE proveedores SET activo = 0 WHERE id = ?", (inactivo.id,))

    todos = {p.nombre: p.activo for p in servicio_proveedores.listar_todos()}

    assert todos == {"Distribuidora SA": True, "Mayorista Norte": False}


def test_obtener_por_id(base_datos_temporal):
    creado = servicio_proveedores.crear_proveedor("Distribuidora SA")

    assert servicio_proveedores.obtener_por_id(creado.id).nombre == "Distribuidora SA"
    assert servicio_proveedores.obtener_por_id(9999) is None


def test_actualizar_proveedor_modifica_los_datos(base_datos_temporal):
    creado = servicio_proveedores.crear_proveedor("Distribuidora SA")

    actualizado = servicio_proveedores.actualizar_proveedor(
        creado.id, nombre="Distribuidora SRL", telefono="99999999"
    )

    assert actualizado.nombre == "Distribuidora SRL"
    assert actualizado.telefono == "99999999"


def test_actualizar_proveedor_inexistente_falla(base_datos_temporal):
    with pytest.raises(ProveedorNoEncontradoError):
        servicio_proveedores.actualizar_proveedor(9999, nombre="No existe")


def test_actualizar_proveedor_con_nombre_vacio_falla(base_datos_temporal):
    creado = servicio_proveedores.crear_proveedor("Distribuidora SA")

    with pytest.raises(DatosInvalidosError):
        servicio_proveedores.actualizar_proveedor(creado.id, nombre="   ")


def test_eliminar_proveedor_sin_uso_lo_borra_fisicamente(base_datos_temporal):
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")

    fue_baja_logica = servicio_proveedores.eliminar_proveedor(proveedor.id)

    assert fue_baja_logica is False
    assert servicio_proveedores.obtener_por_id(proveedor.id) is None


def test_eliminar_proveedor_inexistente_falla(base_datos_temporal):
    with pytest.raises(ProveedorNoEncontradoError):
        servicio_proveedores.eliminar_proveedor(9999)


def test_reactivar_proveedor(base_datos_temporal):
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    with obtener_conexion() as conexion:
        conexion.execute("UPDATE proveedores SET activo = 0 WHERE id = ?", (proveedor.id,))

    reactivado = servicio_proveedores.reactivar_proveedor(proveedor.id)

    assert reactivado.activo is True


def test_reactivar_proveedor_inexistente_falla(base_datos_temporal):
    with pytest.raises(ProveedorNoEncontradoError):
        servicio_proveedores.reactivar_proveedor(9999)


# --- Saldo de proveedor (V1.9-B) ---------------------------------------------------------------
# El saldo se deriva siempre del libro `movimientos_proveedor`, nunca de una columna persistida
# (ver auditoría V1.9): `SUM(CARGO_COMPRA) - SUM(PAGO) - SUM(REVERSA_COMPRA)`.


def _crear_usuario():
    from db.repositorios import usuarios as repositorio_usuarios
    from domain.usuario import Usuario

    return repositorio_usuarios.crear_usuario(
        Usuario(nombre_usuario="duenio", nombre_completo="Dueño", password_hash="hash", rol="OWNER")
    )


def test_saldo_sin_movimientos_es_cero(base_datos_temporal):
    from db.repositorios import movimientos_proveedor as repositorio_movimientos_proveedor

    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")

    assert repositorio_movimientos_proveedor.obtener_saldo(proveedor.id) == 0
    assert servicio_proveedores.obtener_ficha(proveedor.id).saldo_centavos == 0


def test_saldo_con_un_cargo(base_datos_temporal):
    from domain.compra import ItemCompra
    from services import servicio_compras, servicio_stock

    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    usuario = _crear_usuario()
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)

    servicio_compras.registrar_compra(
        proveedor.id, usuario.id, [ItemCompra(producto.id, 10, 120)], condicion_pago="CREDITO"
    )

    assert servicio_proveedores.obtener_ficha(proveedor.id).saldo_centavos == 1200


def test_saldo_con_varios_cargos_se_acumula(base_datos_temporal):
    from domain.compra import ItemCompra
    from services import servicio_compras, servicio_stock

    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    usuario = _crear_usuario()
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)

    servicio_compras.registrar_compra(
        proveedor.id, usuario.id, [ItemCompra(producto.id, 10, 120)], condicion_pago="CREDITO"
    )
    servicio_compras.registrar_compra(
        proveedor.id, usuario.id, [ItemCompra(producto.id, 5, 100)], condicion_pago="CREDITO"
    )
    servicio_compras.registrar_compra(
        proveedor.id, usuario.id, [ItemCompra(producto.id, 3, 100)], condicion_pago="CONTADO"
    )  # no debe sumar al saldo

    assert servicio_proveedores.obtener_ficha(proveedor.id).saldo_centavos == 1200 + 500


def test_m6_saldo_con_cargo_y_reversa_se_cancela(base_datos_temporal):
    """M6: la reversa debe descontarse del saldo igual que un pago -- una expresión que la
    ignorara (ej. solo `SUM(CARGO) - SUM(PAGO)`) dejaría deuda fantasma tras anular."""
    from domain.compra import ItemCompra
    from services import servicio_compras, servicio_stock

    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    usuario = _crear_usuario()
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=20)

    compra = servicio_compras.registrar_compra(
        proveedor.id, usuario.id, [ItemCompra(producto.id, 10, 120)], condicion_pago="CREDITO"
    )
    assert servicio_proveedores.obtener_ficha(proveedor.id).saldo_centavos == 1200

    servicio_compras.anular_compra(compra.id, "ERROR_CARGA", None, usuario.id)

    assert servicio_proveedores.obtener_ficha(proveedor.id).saldo_centavos == 0


def test_saldo_de_un_proveedor_no_se_mezcla_con_el_de_otro(base_datos_temporal):
    from domain.compra import ItemCompra
    from services import servicio_compras, servicio_stock

    prov_a = servicio_proveedores.crear_proveedor("Distribuidora SA")
    prov_b = servicio_proveedores.crear_proveedor("Mayorista Norte")
    usuario = _crear_usuario()
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200)

    servicio_compras.registrar_compra(prov_a.id, usuario.id, [ItemCompra(producto.id, 10, 120)], condicion_pago="CREDITO")

    assert servicio_proveedores.obtener_ficha(prov_a.id).saldo_centavos == 1200
    assert servicio_proveedores.obtener_ficha(prov_b.id).saldo_centavos == 0
