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
