"""Clientes por HTTP (021): lista, búsqueda, alta, edición, baja/reactivación, ficha y buscador JSON,
con permisos OWNER/CASHIER. Contra la app real vía el cliente ASGI (sin `httpx`)."""

import pytest

from domain.venta import ItemVenta
from services import servicio_clientes, servicio_cuenta_corriente, servicio_ventas
from tests.utilidades_clientes import contar, consultar, crear_producto, forzar_cliente_inactivo, usuario_logueado

from ._asgi_cliente import solicitud

pytestmark = pytest.mark.usefixtures("caja_abierta")

CC = "CUENTA_CORRIENTE"


@pytest.fixture
def e(base_datos_temporal):
    owner, cookies_owner = usuario_logueado("OWNER", "duenio")
    cajera, cookies_cajera = usuario_logueado("CASHIER", "cajera")
    return type(
        "Escenario",
        (),
        {
            "ruta": base_datos_temporal,
            "owner": owner,
            "cajera": cajera,
            "co": cookies_owner,
            "cc": cookies_cajera,
            "producto": crear_producto(precio_centavos=250, stock=50),
        },
    )


def _crear(e, nombre="Ana", telefono=None):
    return servicio_clientes.crear_cliente(nombre, e.owner.id, telefono=telefono)


def _vender_a_cuenta(e, cliente, cantidad=1):
    return servicio_ventas.registrar_venta(
        [ItemVenta(e.producto.id, cantidad)], CC, usuario_id=e.cajera.id, cliente_id=cliente.id
    )


def _location(respuesta) -> str:
    return respuesta.header("location") or ""


# --- acceso ----------------------------------------------------------------------------------------


def test_sin_sesion_se_redirige_al_login(e):
    for ruta in ("/clientes", "/clientes/nuevo", "/clientes/1"):
        respuesta = solicitud("GET", ruta)
        assert respuesta.status == 303 and _location(respuesta).startswith("/login")


@pytest.mark.parametrize("rol", ["co", "cc"])
def test_owner_y_cashier_ven_la_lista_y_el_menu_de_clientes(e, rol):
    respuesta = solicitud("GET", "/clientes", cookies=getattr(e, rol))

    assert respuesta.status == 200
    assert 'href="/clientes"' in respuesta.texto  # el ítem de navegación


# --- lista, búsqueda y filtros ---------------------------------------------------------------------


def test_la_lista_muestra_activos_por_defecto_con_su_saldo(e):
    ana = _crear(e, "Ana", "1155551234")
    inactiva = _crear(e, "Beto Inactivo")
    servicio_clientes.desactivar_cliente(inactiva.id, e.owner.id)
    _vender_a_cuenta(e, ana, 2)  # debe 500

    texto = solicitud("GET", "/clientes", cookies=e.cc).texto

    assert "Ana" in texto and "1155551234" in texto
    assert "$5,00" in texto  # 2 unidades de 250 centavos
    assert "Beto Inactivo" not in texto


def test_el_filtro_de_estado_muestra_inactivos_y_todos(e):
    _crear(e, "Ana")
    inactiva = _crear(e, "Beto Inactivo")
    servicio_clientes.desactivar_cliente(inactiva.id, e.owner.id)

    inactivos = solicitud("GET", "/clientes?estado=inactivos", cookies=e.co).texto
    todos = solicitud("GET", "/clientes?estado=todos", cookies=e.co).texto

    assert "Beto Inactivo" in inactivos and ">Ana<" not in inactivos.replace(" ", "").replace("\n", "")
    assert "Beto Inactivo" in todos and "Ana" in todos


def test_un_estado_invalido_no_es_un_error_500(e):
    respuesta = solicitud("GET", "/clientes?estado=borrados", cookies=e.co)

    assert respuesta.status == 422


def test_busca_por_nombre_y_por_telefono(e):
    _crear(e, "Ana Gómez", "1155551234")
    _crear(e, "Beto", "1144449999")

    por_nombre = solicitud("GET", "/clientes?q=g%C3%B3mez", cookies=e.co).texto
    por_telefono = solicitud("GET", "/clientes?q=4444", cookies=e.co).texto

    assert "Ana Gómez" in por_nombre and "Beto" not in por_nombre
    assert "Beto" in por_telefono and "Ana Gómez" not in por_telefono


def test_los_comodines_de_la_busqueda_se_tratan_como_texto(e):
    _crear(e, "100% Leche")
    _crear(e, "Otro cliente")

    texto = solicitud("GET", "/clientes?q=%25", cookies=e.co).texto  # q = "%"

    assert "100% Leche" in texto and "Otro cliente" not in texto


# --- alta ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("rol", ["co", "cc"])
def test_owner_y_cashier_pueden_crear_clientes(e, rol):
    respuesta = solicitud(
        "POST",
        "/clientes/nuevo",
        cookies=getattr(e, rol),
        formulario={"nombre": " Ana ", "telefono": "123", "email": "sin-arroba", "direccion": "", "observaciones": ""},
    )

    assert respuesta.status == 303
    fila = consultar(e.ruta, "SELECT * FROM clientes")[0]
    assert (fila["nombre"], fila["telefono"], fila["email"], fila["direccion"]) == ("Ana", "123", "sin-arroba", None)
    assert _location(respuesta).startswith(f"/clientes/{fila['id']}?tipo=success")


def test_el_formulario_de_alta_se_muestra_con_los_limites_del_servidor(e):
    texto = solicitud("GET", "/clientes/nuevo", cookies=e.cc).texto

    assert 'maxlength="120"' in texto and 'maxlength="30"' in texto and 'maxlength="500"' in texto


def test_un_alta_invalida_no_crea_nada_y_avisa(e):
    respuesta = solicitud("POST", "/clientes/nuevo", cookies=e.cc, formulario={"nombre": "   "})

    assert respuesta.status == 303 and "tipo=error" in _location(respuesta)
    assert contar(e.ruta, "clientes") == 0


def test_el_servidor_rechaza_un_nombre_de_mas_de_120_caracteres_aunque_el_navegador_no_lo_frene(e):
    respuesta = solicitud("POST", "/clientes/nuevo", cookies=e.co, formulario={"nombre": "a" * 121})

    assert "tipo=error" in _location(respuesta)
    assert contar(e.ruta, "clientes") == 0


def test_se_permiten_nombres_duplicados(e):
    for _ in range(2):
        solicitud("POST", "/clientes/nuevo", cookies=e.cc, formulario={"nombre": "Ana"})

    assert contar(e.ruta, "clientes", "nombre = 'Ana'") == 2


# --- edición (solo OWNER) ----------------------------------------------------------------------------------


def test_owner_edita_un_cliente(e):
    cliente = _crear(e, "Ana")

    formulario = solicitud("GET", f"/clientes/{cliente.id}/editar", cookies=e.co)
    respuesta = solicitud(
        "POST", f"/clientes/{cliente.id}/editar", cookies=e.co, formulario={"nombre": "Ana María", "telefono": "999"}
    )

    assert formulario.status == 200 and 'value="Ana"' in formulario.texto
    assert respuesta.status == 303 and "tipo=success" in _location(respuesta)
    fila = consultar(e.ruta, "SELECT * FROM clientes WHERE id = ?", (cliente.id,))[0]
    assert (fila["nombre"], fila["telefono"]) == ("Ana María", "999")


def test_cashier_no_puede_editar_ni_ver_el_formulario_de_edicion(e):
    cliente = _crear(e, "Ana")

    get = solicitud("GET", f"/clientes/{cliente.id}/editar", cookies=e.cc)
    post = solicitud("POST", f"/clientes/{cliente.id}/editar", cookies=e.cc, formulario={"nombre": "Hackeado"})

    assert get.status == 403 and post.status == 403
    assert consultar(e.ruta, "SELECT nombre FROM clientes WHERE id = ?", (cliente.id,))[0]["nombre"] == "Ana"


def test_editar_un_cliente_inexistente_redirige_a_la_lista(e):
    respuesta = solicitud("GET", "/clientes/999/editar", cookies=e.co)

    assert respuesta.status == 303 and _location(respuesta).startswith("/clientes?")


# --- baja y reactivación --------------------------------------------------------------------------------------


def test_owner_desactiva_y_reactiva(e):
    cliente = _crear(e, "Ana")

    baja = solicitud("POST", f"/clientes/{cliente.id}/desactivar", cookies=e.co, formulario={})
    activo_tras_baja = consultar(e.ruta, "SELECT activo FROM clientes")[0]["activo"]
    alta = solicitud("POST", f"/clientes/{cliente.id}/reactivar", cookies=e.co, formulario={})

    assert baja.status == 303 and "tipo=success" in _location(baja) and activo_tras_baja == 0
    assert alta.status == 303 and consultar(e.ruta, "SELECT activo FROM clientes")[0]["activo"] == 1


def test_cashier_no_puede_desactivar_ni_reactivar(e):
    cliente = _crear(e, "Ana")

    baja = solicitud("POST", f"/clientes/{cliente.id}/desactivar", cookies=e.cc, formulario={})
    servicio_clientes.desactivar_cliente(cliente.id, e.owner.id)
    alta = solicitud("POST", f"/clientes/{cliente.id}/reactivar", cookies=e.cc, formulario={})

    assert baja.status == 403 and alta.status == 403
    assert consultar(e.ruta, "SELECT activo FROM clientes")[0]["activo"] == 0  # solo la baja del owner


def test_no_se_desactiva_un_cliente_con_saldo(e):
    cliente = _crear(e, "Ana")
    _vender_a_cuenta(e, cliente)

    respuesta = solicitud("POST", f"/clientes/{cliente.id}/desactivar", cookies=e.co, formulario={})

    assert "tipo=error" in _location(respuesta)
    assert consultar(e.ruta, "SELECT activo FROM clientes")[0]["activo"] == 1


# --- ficha -----------------------------------------------------------------------------------------------------


def test_la_ficha_muestra_datos_saldo_totales_y_movimientos_del_mas_reciente_al_mas_antiguo(e):
    cliente = servicio_clientes.crear_cliente("Ana", e.owner.id, telefono="1155551234", observaciones="buena pagadora")
    primera = _vender_a_cuenta(e, cliente, 2)  # +500
    servicio_cuenta_corriente.registrar_cobro(cliente.id, 200, e.cajera.id, descripcion="entrega inicial")
    segunda = _vender_a_cuenta(e, cliente, 1)  # +250

    texto = solicitud("GET", f"/clientes/{cliente.id}", cookies=e.cc).texto

    assert "1155551234" in texto and "buena pagadora" in texto and "Activo" in texto
    assert "$5,50" in texto  # saldo: 500 - 200 + 250 centavos
    assert "$7,50" in texto and "$2,00" in texto  # total de cargos y de cobros
    assert "entrega inicial" in texto
    assert f'href="/ventas/{primera.id}"' in texto and f'href="/ventas/{segunda.id}"' in texto
    assert texto.index(f"Venta #{segunda.id}") < texto.index("entrega inicial") < texto.index(f"Venta #{primera.id}")


def test_la_ficha_de_un_cliente_inexistente_redirige_a_la_lista(e):
    respuesta = solicitud("GET", "/clientes/999", cookies=e.co)

    assert respuesta.status == 303 and "tipo=error" in _location(respuesta)


def test_la_ficha_ofrece_editar_y_desactivar_solo_al_owner(e):
    cliente = _crear(e, "Ana")

    del_owner = solicitud("GET", f"/clientes/{cliente.id}", cookies=e.co).texto
    del_cashier = solicitud("GET", f"/clientes/{cliente.id}", cookies=e.cc).texto

    for enlace in (f"/clientes/{cliente.id}/editar", f"/clientes/{cliente.id}/desactivar"):
        assert enlace in del_owner
        assert enlace not in del_cashier


def test_la_ficha_bloquea_visualmente_la_baja_con_saldo_y_ofrece_cobrar(e):
    cliente = _crear(e, "Ana")
    _vender_a_cuenta(e, cliente)

    texto = solicitud("GET", f"/clientes/{cliente.id}", cookies=e.co).texto

    assert f"/clientes/{cliente.id}/desactivar" not in texto  # no hay formulario de baja
    assert "No se puede desactivar un cliente con saldo pendiente" in texto
    assert f"/clientes/{cliente.id}/cobro" in texto


def test_la_ficha_de_un_cliente_sin_deuda_no_ofrece_cobrar_y_la_de_un_inactivo_ofrece_reactivar(e):
    cliente = _crear(e, "Ana")
    servicio_clientes.desactivar_cliente(cliente.id, e.owner.id)

    texto = solicitud("GET", f"/clientes/{cliente.id}", cookies=e.co).texto

    assert f"/clientes/{cliente.id}/cobro" not in texto
    assert "Inactivo" in texto and f"/clientes/{cliente.id}/reactivar" in texto


def test_los_nombres_se_escapan_en_lista_y_ficha(e):
    cliente = _crear(e, "<script>window.__xss=1</script>")

    lista = solicitud("GET", "/clientes", cookies=e.co).texto
    ficha = solicitud("GET", f"/clientes/{cliente.id}", cookies=e.co).texto

    for html in (lista, ficha):
        assert "<script>window.__xss=1</script>" not in html
        assert "&lt;script&gt;window.__xss=1&lt;/script&gt;" in html


# --- buscador JSON del POS --------------------------------------------------------------------------------------


def test_el_buscador_devuelve_solo_clientes_activos_con_saldo(e):
    activa = _crear(e, "Ana", "1155551234")
    inactiva = _crear(e, "Ana Inactiva")
    servicio_clientes.desactivar_cliente(inactiva.id, e.owner.id)
    _vender_a_cuenta(e, activa)

    respuesta = solicitud("GET", "/api/clientes/buscar?q=ana", cookies=e.cc)

    assert respuesta.status == 200
    assert respuesta.json() == [
        {"id": activa.id, "nombre": "Ana", "telefono": "1155551234", "saldo_centavos": 250}
    ]


def test_el_buscador_busca_por_telefono_y_tiene_un_maximo_de_20(e):
    for numero in range(25):
        _crear(e, f"Cliente {numero:02d}", f"11-0000-{numero:04d}")

    todos = solicitud("GET", "/api/clientes/buscar?q=", cookies=e.co).json()
    uno = solicitud("GET", "/api/clientes/buscar?q=0007", cookies=e.co).json()

    assert len(todos) == 20
    assert [c["nombre"] for c in uno] == ["Cliente 07"]


def test_el_buscador_exige_sesion_y_responde_json(e):
    respuesta = solicitud("GET", "/api/clientes/buscar?q=a")

    assert respuesta.status == 401 and "error" in respuesta.json()


def test_el_buscador_no_devuelve_clientes_inactivos_aunque_tengan_deuda(e):
    cliente = _crear(e, "Ana")
    _vender_a_cuenta(e, cliente)
    forzar_cliente_inactivo(e.ruta, cliente.id)

    assert solicitud("GET", "/api/clientes/buscar?q=ana", cookies=e.co).json() == []
