"""Tests HTTP de la configuración inicial (creación del primer OWNER
en una instalación limpia, ver auditoría de Stage D).

El test de concurrencia real de la garantía atómica vive en
`tests/test_db/test_usuarios.py` (a nivel de repositorio, sin la
sobrecarga del cliente ASGI) -- acá solo se prueba el flujo HTTP.
"""

from db.repositorios import usuarios as repositorio_usuarios

from ._asgi_cliente import solicitud


def test_get_configuracion_inicial_sin_owner_muestra_formulario(base_datos_temporal):
    respuesta = solicitud("GET", "/configuracion-inicial")

    assert respuesta.status == 200
    assert "Crear propietario" in respuesta.texto


def test_get_configuracion_inicial_con_owner_redirige_a_login(base_datos_temporal):
    from db.repositorios import usuarios as repo
    from domain.usuario import Usuario

    repo.crear_usuario(
        Usuario(
            nombre_usuario="ya_existe",
            nombre_completo="Owner existente",
            password_hash="pbkdf2_sha256$600000$" + "a" * 32 + "$" + "b" * 64,
            rol="OWNER",
        )
    )

    respuesta = solicitud("GET", "/configuracion-inicial")

    assert respuesta.status == 303
    assert respuesta.header("location") == "/login"


def test_post_configuracion_inicial_valido_crea_exactamente_un_owner(base_datos_temporal):
    respuesta = solicitud(
        "POST",
        "/configuracion-inicial",
        formulario={
            "nombre_completo": "Dueño de Prueba",
            "nombre_usuario": "dueno_test",
            "password": "clave-inicial-123",
        },
    )

    assert respuesta.status == 303
    assert respuesta.header("location") == "/login"
    assert repositorio_usuarios.contar_owners_activos() == 1
    creado = repositorio_usuarios.obtener_por_nombre_usuario("dueno_test")
    assert creado is not None
    assert creado.rol == "OWNER"


def test_post_configuracion_inicial_cuando_ya_existe_owner_no_crea_otro(base_datos_temporal):
    from db.repositorios import usuarios as repo
    from domain.usuario import Usuario

    repo.crear_usuario(
        Usuario(
            nombre_usuario="owner_previo",
            nombre_completo="Previo",
            password_hash="pbkdf2_sha256$600000$" + "c" * 32 + "$" + "d" * 64,
            rol="OWNER",
        )
    )

    respuesta = solicitud(
        "POST",
        "/configuracion-inicial",
        formulario={
            "nombre_completo": "Intento Tardío",
            "nombre_usuario": "intento_tardio",
            "password": "clave-cualquiera",
        },
    )

    assert respuesta.status == 303
    assert respuesta.header("location") == "/login"
    assert repositorio_usuarios.contar_owners_activos() == 1
    assert repositorio_usuarios.obtener_por_nombre_usuario("intento_tardio") is None


def test_post_nombre_completo_vacio_muestra_error_y_no_crea_usuario(base_datos_temporal):
    # multipart, no urlencoded: `application/x-www-form-urlencoded` en
    # Starlette puede descartar campos con valor vacío antes de que
    # lleguen a la ruta (ver informe) -- multipart preserva el campo
    # vacío tal cual, que es el escenario real que este test necesita
    # ejercitar (la validación de dominio, no el parseo del framework).
    respuesta = solicitud(
        "POST",
        "/configuracion-inicial",
        formulario={"nombre_completo": "", "nombre_usuario": "dueno1", "password": "clave-123"},
        archivos={},
    )

    assert respuesta.status == 200
    assert "El nombre completo no puede estar vacío." in respuesta.texto
    assert repositorio_usuarios.contar_owners_activos() == 0
    assert repositorio_usuarios.obtener_por_nombre_usuario("dueno1") is None
    # el dato no sensible ya tipeado se conserva
    assert 'value="dueno1"' in respuesta.texto
    # la contraseña nunca se refleja en el HTML
    assert "clave-123" not in respuesta.texto


def test_post_nombre_usuario_vacio_muestra_error_y_no_crea_usuario(base_datos_temporal):
    respuesta = solicitud(
        "POST",
        "/configuracion-inicial",
        formulario={"nombre_completo": "Dueño Prueba", "nombre_usuario": "", "password": "clave-123"},
        archivos={},
    )

    assert respuesta.status == 200
    assert "El nombre de usuario no puede estar vacío." in respuesta.texto
    assert repositorio_usuarios.contar_owners_activos() == 0
    assert 'value="Dueño Prueba"' in respuesta.texto
    assert "clave-123" not in respuesta.texto


def test_post_password_vacia_muestra_error_y_no_crea_usuario(base_datos_temporal):
    respuesta = solicitud(
        "POST",
        "/configuracion-inicial",
        formulario={"nombre_completo": "Dueño Prueba", "nombre_usuario": "dueno2", "password": ""},
        archivos={},
    )

    assert respuesta.status == 200
    assert "La contraseña no puede estar vacía." in respuesta.texto
    assert repositorio_usuarios.contar_owners_activos() == 0
    assert repositorio_usuarios.obtener_por_nombre_usuario("dueno2") is None
    assert 'value="dueno2"' in respuesta.texto


def test_post_nombre_usuario_duplicado_muestra_error_y_no_crea_segundo_owner(base_datos_temporal):
    from db.repositorios import usuarios as repo
    from domain.usuario import Usuario

    repo.crear_usuario(
        Usuario(
            nombre_usuario="cajero_existente",
            nombre_completo="Cajero",
            password_hash="pbkdf2_sha256$600000$" + "a" * 32 + "$" + "b" * 64,
            rol="CASHIER",
        )
    )

    respuesta = solicitud(
        "POST",
        "/configuracion-inicial",
        formulario={
            "nombre_completo": "Dueño Nuevo",
            "nombre_usuario": "cajero_existente",
            "password": "clave-123",
        },
    )

    assert respuesta.status == 200
    assert "Ya existe un usuario con el nombre de usuario" in respuesta.texto
    assert repositorio_usuarios.contar_owners_activos() == 0
    assert "clave-123" not in respuesta.texto


def test_get_login_sin_owner_redirige_a_configuracion_inicial(base_datos_temporal):
    respuesta = solicitud("GET", "/login")

    assert respuesta.status == 303
    assert respuesta.header("location") == "/configuracion-inicial"


def test_get_login_con_owner_muestra_formulario_normal(base_datos_temporal):
    from db.repositorios import usuarios as repo
    from domain.usuario import Usuario

    repo.crear_usuario(
        Usuario(
            nombre_usuario="owner_normal",
            nombre_completo="Normal",
            password_hash="pbkdf2_sha256$600000$" + "e" * 32 + "$" + "f" * 64,
            rol="OWNER",
        )
    )

    respuesta = solicitud("GET", "/login")

    assert respuesta.status == 200
    assert "Ingresar" in respuesta.texto
