"""Pruebas de integración de db.repositorios.usuarios contra una base de
datos SQLite real (temporal y aislada, ver tests/conftest.py).

Solo prueban persistencia (ver domain/usuario.py para las reglas de
negocio y tests/test_domain/test_usuario.py para esas pruebas). El
login/autenticación no existe todavía (fase 2C).
"""

import threading

import pytest

from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from excepciones import ErrorBaseDatos, NombreUsuarioDuplicadoError, UsuarioNoEncontradoError


def _usuario(**overrides):
    datos = dict(
        nombre_usuario="ana",
        nombre_completo="Ana Owner",
        password_hash="pbkdf2_sha256$600000$" + "a" * 32 + "$" + "b" * 64,
        rol="OWNER",
    )
    datos.update(overrides)
    return Usuario(**datos)


def test_crear_usuario_persiste_y_asigna_id(base_datos_temporal):
    creado = repositorio_usuarios.crear_usuario(_usuario())

    assert creado.id is not None
    assert creado.nombre_usuario == "ana"
    assert creado.fecha_creacion is not None


def test_crear_usuario_no_almacena_password_en_texto_plano(base_datos_temporal):
    hash_esperado = "pbkdf2_sha256$600000$" + "c" * 32 + "$" + "d" * 64
    repositorio_usuarios.crear_usuario(_usuario(nombre_usuario="bruno", password_hash=hash_esperado))

    with obtener_conexion() as conexion:
        columnas = {fila["name"] for fila in conexion.execute("PRAGMA table_info(usuarios)").fetchall()}
        fila = conexion.execute(
            "SELECT password_hash FROM usuarios WHERE nombre_usuario = ?", ("bruno",)
        ).fetchone()

    # No existe ninguna columna de contraseña en texto plano en el esquema.
    assert "password" not in columnas
    assert "password_hash" in columnas
    # Lo que se guardó es exactamente el hash recibido, nunca la contraseña original.
    assert fila["password_hash"] == hash_esperado


def test_obtener_por_id_devuelve_usuario_existente(base_datos_temporal):
    creado = repositorio_usuarios.crear_usuario(_usuario())

    encontrado = repositorio_usuarios.obtener_por_id(creado.id)

    assert encontrado is not None
    assert encontrado.nombre_usuario == "ana"
    assert encontrado.rol == "OWNER"


def test_obtener_por_id_inexistente_devuelve_none(base_datos_temporal):
    assert repositorio_usuarios.obtener_por_id(9999) is None


def test_obtener_por_nombre_usuario_devuelve_usuario_existente(base_datos_temporal):
    repositorio_usuarios.crear_usuario(_usuario(nombre_usuario="carla"))

    encontrado = repositorio_usuarios.obtener_por_nombre_usuario("carla")

    assert encontrado is not None
    assert encontrado.nombre_completo == "Ana Owner"


def test_obtener_por_nombre_usuario_inexistente_devuelve_none(base_datos_temporal):
    assert repositorio_usuarios.obtener_por_nombre_usuario("no-existe") is None


def test_nombre_usuario_duplicado_lanza_error(base_datos_temporal):
    repositorio_usuarios.crear_usuario(_usuario(nombre_usuario="duplicado"))

    with pytest.raises(NombreUsuarioDuplicadoError):
        repositorio_usuarios.crear_usuario(_usuario(nombre_usuario="duplicado"))


def test_listar_todos_devuelve_todos_los_usuarios(base_datos_temporal):
    repositorio_usuarios.crear_usuario(_usuario(nombre_usuario="user_b"))
    repositorio_usuarios.crear_usuario(_usuario(nombre_usuario="user_a"))

    resultado = repositorio_usuarios.listar_todos()

    assert [usuario.nombre_usuario for usuario in resultado] == ["user_a", "user_b"]


def test_usuario_creado_activo_por_defecto(base_datos_temporal):
    creado = repositorio_usuarios.crear_usuario(_usuario())
    assert creado.activo is True


def test_usuario_creado_inactivo_se_persiste_correctamente(base_datos_temporal):
    creado = repositorio_usuarios.crear_usuario(_usuario(activo=False))

    recargado = repositorio_usuarios.obtener_por_id(creado.id)

    assert recargado.activo is False


def test_rol_invalido_a_nivel_de_esquema_es_rechazado(base_datos_temporal):
    """El CHECK de la tabla es la segunda barrera, además de domain.Usuario.

    Se salta la validación de dominio con una inserción directa para
    probar que el propio esquema también protege la integridad.
    `obtener_conexion` traduce el `sqlite3.IntegrityError` del CHECK a
    `ErrorBaseDatos` (ver db/conexion.py), por eso se espera esa clase.
    """
    with pytest.raises(ErrorBaseDatos):
        with obtener_conexion() as conexion:
            conexion.execute(
                "INSERT INTO usuarios (nombre_usuario, nombre_completo, password_hash, rol) "
                "VALUES (?, ?, ?, ?)",
                ("x", "X", "hash", "SUPERADMIN"),
            )


# ---------------------------------------------------------------------------
# UPDATEs de gestión de usuarios (Etapa B.1 del MVP)
# ---------------------------------------------------------------------------


def test_actualizar_nombre_completo(base_datos_temporal):
    creado = repositorio_usuarios.crear_usuario(_usuario())

    actualizado = repositorio_usuarios.actualizar_nombre_completo(creado.id, "Ana Propietaria")

    assert actualizado.nombre_completo == "Ana Propietaria"
    assert actualizado.nombre_usuario == "ana"  # nunca cambia acá


def test_actualizar_nombre_completo_usuario_inexistente_lanza_error(base_datos_temporal):
    with pytest.raises(UsuarioNoEncontradoError):
        repositorio_usuarios.actualizar_nombre_completo(9999, "Nombre Cualquiera")


def test_actualizar_rol(base_datos_temporal):
    creado = repositorio_usuarios.crear_usuario(_usuario(rol="CASHIER"))

    actualizado = repositorio_usuarios.actualizar_rol(creado.id, "OWNER")

    assert actualizado.rol == "OWNER"


def test_actualizar_rol_usuario_inexistente_lanza_error(base_datos_temporal):
    with pytest.raises(UsuarioNoEncontradoError):
        repositorio_usuarios.actualizar_rol(9999, "OWNER")


def test_actualizar_activo(base_datos_temporal):
    creado = repositorio_usuarios.crear_usuario(_usuario())

    desactivado = repositorio_usuarios.actualizar_activo(creado.id, False)
    assert desactivado.activo is False

    reactivado = repositorio_usuarios.actualizar_activo(creado.id, True)
    assert reactivado.activo is True


def test_actualizar_activo_usuario_inexistente_lanza_error(base_datos_temporal):
    with pytest.raises(UsuarioNoEncontradoError):
        repositorio_usuarios.actualizar_activo(9999, False)


def test_actualizar_password(base_datos_temporal):
    creado = repositorio_usuarios.crear_usuario(_usuario())

    actualizado = repositorio_usuarios.actualizar_password(creado.id, "pbkdf2_sha256$600000$" + "c" * 32 + "$" + "d" * 64)

    assert actualizado.password_hash.startswith("pbkdf2_sha256$600000$" + "c" * 32)


def test_actualizar_password_usuario_inexistente_lanza_error(base_datos_temporal):
    with pytest.raises(UsuarioNoEncontradoError):
        repositorio_usuarios.actualizar_password(9999, "hash-cualquiera")


def test_contar_owners_activos(base_datos_temporal):
    assert repositorio_usuarios.contar_owners_activos() == 0

    owner_1 = repositorio_usuarios.crear_usuario(_usuario(nombre_usuario="ana", rol="OWNER"))
    repositorio_usuarios.crear_usuario(_usuario(nombre_usuario="beto", rol="OWNER"))
    repositorio_usuarios.crear_usuario(_usuario(nombre_usuario="carlos", rol="CASHIER"))
    assert repositorio_usuarios.contar_owners_activos() == 2

    repositorio_usuarios.actualizar_activo(owner_1.id, False)
    assert repositorio_usuarios.contar_owners_activos() == 1


def test_crear_primer_owner_crea_cuando_no_hay_ningun_owner_activo(base_datos_temporal):
    creado = repositorio_usuarios.crear_primer_owner(
        nombre_usuario="dueno",
        nombre_completo="Dueño Inicial",
        password_hash="pbkdf2_sha256$600000$" + "a" * 32 + "$" + "b" * 64,
    )

    assert creado is not None
    assert creado.id is not None
    assert creado.rol == "OWNER"
    assert creado.activo is True
    assert repositorio_usuarios.contar_owners_activos() == 1


def test_crear_primer_owner_no_crea_si_ya_existe_owner_activo(base_datos_temporal):
    repositorio_usuarios.crear_usuario(_usuario(nombre_usuario="ya_existente", rol="OWNER"))

    resultado = repositorio_usuarios.crear_primer_owner(
        nombre_usuario="segundo_dueno",
        nombre_completo="Otro",
        password_hash="pbkdf2_sha256$600000$" + "c" * 32 + "$" + "d" * 64,
    )

    assert resultado is None
    assert repositorio_usuarios.contar_owners_activos() == 1
    assert repositorio_usuarios.obtener_por_nombre_usuario("segundo_dueno") is None


def test_dos_intentos_concurrentes_de_crear_primer_owner_crean_exactamente_uno(base_datos_temporal):
    resultados = []

    def intentar(nombre_usuario):
        resultado = repositorio_usuarios.crear_primer_owner(
            nombre_usuario=nombre_usuario,
            nombre_completo="Dueño concurrente",
            password_hash="pbkdf2_sha256$600000$" + "e" * 32 + "$" + "f" * 64,
        )
        resultados.append(resultado)

    hilos = [
        threading.Thread(target=intentar, args=(f"dueno_{i}",)) for i in range(5)
    ]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    creados = [r for r in resultados if r is not None]
    assert len(creados) == 1
    assert repositorio_usuarios.contar_owners_activos() == 1
