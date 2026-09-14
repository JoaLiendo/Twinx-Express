"""Pruebas de integración de db.repositorios.proveedores contra una base
de datos SQLite real (temporal y aislada, ver tests/conftest.py)."""

import pytest

from db.conexion import obtener_conexion
from db.repositorios import proveedores as repositorio_proveedores
from domain.proveedor import Proveedor
from excepciones import NombreProveedorDuplicadoError, ProveedorNoEncontradoError


def test_crear_proveedor_persiste_y_asigna_id(base_datos_temporal):
    creado = repositorio_proveedores.crear_proveedor(Proveedor(nombre="Distribuidora SA"))

    assert creado.id is not None
    assert creado.nombre == "Distribuidora SA"
    assert creado.activo is True
    assert creado.fecha_creacion is not None


def test_crear_proveedor_persiste_datos_de_contacto(base_datos_temporal):
    creado = repositorio_proveedores.crear_proveedor(
        Proveedor(
            nombre="Distribuidora SA",
            contacto_nombre="Juan Pérez",
            telefono="1122334455",
            email="ventas@distribuidora.com",
            direccion="Av. Siempre Viva 742",
            notas="Entrega los martes",
        )
    )

    assert creado.contacto_nombre == "Juan Pérez"
    assert creado.telefono == "1122334455"
    assert creado.email == "ventas@distribuidora.com"
    assert creado.direccion == "Av. Siempre Viva 742"
    assert creado.notas == "Entrega los martes"


def test_nombre_duplicado_lanza_error(base_datos_temporal):
    repositorio_proveedores.crear_proveedor(Proveedor(nombre="Distribuidora SA"))

    with pytest.raises(NombreProveedorDuplicadoError):
        repositorio_proveedores.crear_proveedor(Proveedor(nombre="Distribuidora SA"))


def test_obtener_por_id_devuelve_proveedor_activo(base_datos_temporal):
    creado = repositorio_proveedores.crear_proveedor(Proveedor(nombre="Distribuidora SA"))

    encontrado = repositorio_proveedores.obtener_por_id(creado.id)

    assert encontrado is not None
    assert encontrado.nombre == "Distribuidora SA"


def test_obtener_por_id_inexistente_devuelve_none(base_datos_temporal):
    assert repositorio_proveedores.obtener_por_id(9999) is None


def test_listar_activos_no_incluye_inactivos(base_datos_temporal):
    activo = repositorio_proveedores.crear_proveedor(Proveedor(nombre="Distribuidora SA"))
    repositorio_proveedores.crear_proveedor(Proveedor(nombre="Mayorista Norte"))
    repositorio_proveedores.eliminar_proveedor(activo.id)  # sin uso, se borra físicamente

    nombres = [p.nombre for p in repositorio_proveedores.listar_activos()]
    assert nombres == ["Mayorista Norte"]


def test_listar_todos_incluye_activos_e_inactivos(base_datos_temporal):
    repositorio_proveedores.crear_proveedor(Proveedor(nombre="Distribuidora SA"))
    inactivo = repositorio_proveedores.crear_proveedor(Proveedor(nombre="Mayorista Norte"))

    # Se simula un proveedor ya dado de baja lógica (estado que en Fase 4B
    # se alcanzará vía la FK RESTRICT de compras.proveedor_id, todavía
    # inexistente): se marca directamente en la base, igual que
    # test_proteccion_rutas.TestUsuarioInactivoConSesionExistente hace con
    # usuarios.
    with obtener_conexion() as conexion:
        conexion.execute("UPDATE proveedores SET activo = 0 WHERE id = ?", (inactivo.id,))

    todos = {p.nombre: p.activo for p in repositorio_proveedores.listar_todos()}
    assert todos == {"Distribuidora SA": True, "Mayorista Norte": False}


def test_actualizar_datos_modifica_el_proveedor(base_datos_temporal):
    creado = repositorio_proveedores.crear_proveedor(Proveedor(nombre="Distribuidora SA"))

    actualizado = repositorio_proveedores.actualizar_datos(
        Proveedor(id=creado.id, nombre="Distribuidora SRL", telefono="99999999")
    )

    assert actualizado.nombre == "Distribuidora SRL"
    assert actualizado.telefono == "99999999"
    assert repositorio_proveedores.obtener_por_id(creado.id).nombre == "Distribuidora SRL"


def test_actualizar_datos_con_nombre_duplicado_falla(base_datos_temporal):
    repositorio_proveedores.crear_proveedor(Proveedor(nombre="Distribuidora SA"))
    otro = repositorio_proveedores.crear_proveedor(Proveedor(nombre="Mayorista Norte"))

    with pytest.raises(NombreProveedorDuplicadoError):
        repositorio_proveedores.actualizar_datos(Proveedor(id=otro.id, nombre="Distribuidora SA"))


def test_actualizar_datos_de_proveedor_inexistente_falla(base_datos_temporal):
    with pytest.raises(ProveedorNoEncontradoError):
        repositorio_proveedores.actualizar_datos(Proveedor(id=9999, nombre="No existe"))


def test_eliminar_proveedor_sin_uso_lo_borra_fisicamente(base_datos_temporal):
    proveedor = repositorio_proveedores.crear_proveedor(Proveedor(nombre="Distribuidora SA"))

    fue_baja_logica = repositorio_proveedores.eliminar_proveedor(proveedor.id)

    assert fue_baja_logica is False
    assert repositorio_proveedores.obtener_por_id(proveedor.id) is None


def test_eliminar_proveedor_inexistente_falla(base_datos_temporal):
    with pytest.raises(ProveedorNoEncontradoError):
        repositorio_proveedores.eliminar_proveedor(9999)


def test_eliminar_proveedor_ya_inactivo_falla(base_datos_temporal):
    proveedor = repositorio_proveedores.crear_proveedor(Proveedor(nombre="Distribuidora SA"))
    with obtener_conexion() as conexion:
        conexion.execute("UPDATE proveedores SET activo = 0 WHERE id = ?", (proveedor.id,))

    with pytest.raises(ProveedorNoEncontradoError):
        repositorio_proveedores.eliminar_proveedor(proveedor.id)


def test_reactivar_proveedor_dado_de_baja(base_datos_temporal):
    proveedor = repositorio_proveedores.crear_proveedor(Proveedor(nombre="Distribuidora SA"))
    with obtener_conexion() as conexion:
        conexion.execute("UPDATE proveedores SET activo = 0 WHERE id = ?", (proveedor.id,))

    reactivado = repositorio_proveedores.reactivar_proveedor(proveedor.id)

    assert reactivado.activo is True
    assert repositorio_proveedores.obtener_por_id(proveedor.id) is not None


def test_reactivar_proveedor_ya_activo_falla(base_datos_temporal):
    proveedor = repositorio_proveedores.crear_proveedor(Proveedor(nombre="Distribuidora SA"))

    with pytest.raises(ProveedorNoEncontradoError):
        repositorio_proveedores.reactivar_proveedor(proveedor.id)


def test_obtener_por_id_en_conexion_devuelve_proveedor_activo(base_datos_temporal):
    creado = repositorio_proveedores.crear_proveedor(Proveedor(nombre="Distribuidora SA"))

    with obtener_conexion() as conexion:
        encontrado = repositorio_proveedores.obtener_por_id_en_conexion(conexion, creado.id)

    assert encontrado is not None
    assert encontrado.nombre == "Distribuidora SA"


def test_obtener_por_id_en_conexion_no_devuelve_inactivo(base_datos_temporal):
    creado = repositorio_proveedores.crear_proveedor(Proveedor(nombre="Distribuidora SA"))
    with obtener_conexion() as conexion:
        conexion.execute("UPDATE proveedores SET activo = 0 WHERE id = ?", (creado.id,))

    with obtener_conexion() as conexion:
        encontrado = repositorio_proveedores.obtener_por_id_en_conexion(conexion, creado.id)

    assert encontrado is None
