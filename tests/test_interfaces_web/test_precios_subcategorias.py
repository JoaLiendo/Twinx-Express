"""RC4: la actualización masiva por categoría incluye todas sus subcategorías (misma semántica que el
filtro del listado), lo dice en la interfaz, muestra cuántos productos afecta antes de confirmar y conserva
vista previa, todo o nada, idempotencia y permisos."""

import pytest

from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from domain.precios_masivos import CriterioActualizacion
from domain.usuario import Usuario
from excepciones import DatosInvalidosError
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import servicio_auth, servicio_categorias, servicio_precios, servicio_stock

from ._asgi_cliente import solicitud

MAS_10 = CriterioActualizacion("PORCENTAJE", "AUMENTAR", 1000)


@pytest.fixture
def arbol(base_datos_temporal):
    usuario = repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario="duenio",
            nombre_completo="Dueño",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol="OWNER",
        )
    )
    bebidas = servicio_categorias.crear_categoria("Bebidas")
    gaseosas = servicio_categorias.crear_categoria("Gaseosas", bebidas.id)
    colas = servicio_categorias.crear_categoria("Colas", gaseosas.id)
    snacks = servicio_categorias.crear_categoria("Snacks")

    def producto(codigo, nombre, categoria):
        return servicio_stock.registrar_producto(codigo, nombre, 50, 10000, stock_actual=5, categoria_id=categoria.id)

    productos = {
        "agua": producto("1", "Agua", bebidas),
        "tonica": producto("2", "Tónica", gaseosas),
        "cola": producto("3", "Cola", colas),
        "papas": producto("4", "Papas", snacks),
    }
    cookies = {NOMBRE_COOKIE_SESION: servicio_auth.iniciar_sesion("duenio", "clave-correcta-123").token}
    return type("A", (), {"usuario": usuario, "bebidas": bebidas, "gaseosas": gaseosas, "colas": colas,
                          "snacks": snacks, "p": productos, "co": cookies})


def _nombres(propuestas):
    return sorted(p.nombre for p in propuestas)


def test_la_vista_previa_de_una_categoria_padre_incluye_toda_la_rama(arbol):
    assert _nombres(servicio_precios.proponer_actualizacion(MAS_10, f"CATEGORIA:{arbol.bebidas.id}")) == [
        "Agua", "Cola", "Tónica",
    ]
    assert _nombres(servicio_precios.proponer_actualizacion(MAS_10, f"CATEGORIA:{arbol.gaseosas.id}")) == ["Cola", "Tónica"]
    assert _nombres(servicio_precios.proponer_actualizacion(MAS_10, f"CATEGORIA:{arbol.colas.id}")) == ["Cola"]


def test_aplicar_sobre_la_rama_actualiza_todo_o_nada(arbol):
    rama = [arbol.p["agua"], arbol.p["tonica"], arbol.p["cola"]]
    esperados = {p.id: p.precio_venta_centavos for p in rama}

    servicio_precios.aplicar_actualizacion(arbol.usuario.id, MAS_10, f"CATEGORIA:{arbol.bebidas.id}", esperados)

    assert [servicio_stock.obtener_por_id(p.id).precio_venta_centavos for p in rama] == [11000, 11000, 11000]
    assert servicio_stock.obtener_por_id(arbol.p["papas"].id).precio_venta_centavos == 10000


def test_un_producto_de_otra_rama_sigue_quedando_fuera_del_alcance(arbol):
    esperados = {p.id: p.precio_venta_centavos for p in (arbol.p["cola"], arbol.p["papas"])}
    with pytest.raises(DatosInvalidosError, match="no pertenece al alcance"):
        servicio_precios.aplicar_actualizacion(arbol.usuario.id, MAS_10, f"CATEGORIA:{arbol.gaseosas.id}", esperados)
    assert servicio_stock.obtener_por_id(arbol.p["cola"].id).precio_venta_centavos == 10000


def test_mover_un_producto_fuera_de_la_rama_despues_de_la_vista_previa_rechaza_la_confirmacion(arbol):
    esperados = {p.id: p.precio_venta_centavos for p in (arbol.p["tonica"], arbol.p["cola"])}
    with obtener_conexion() as conexion:  # otra pestaña la recategorizó entre vista previa y confirmación
        conexion.execute("UPDATE productos SET categoria_id = ? WHERE id = ?", (arbol.snacks.id, arbol.p["cola"].id))

    with pytest.raises(DatosInvalidosError, match="no pertenece al alcance"):
        servicio_precios.aplicar_actualizacion(arbol.usuario.id, MAS_10, f"CATEGORIA:{arbol.gaseosas.id}", esperados)
    assert servicio_stock.obtener_por_id(arbol.p["tonica"].id).precio_venta_centavos == 10000


def test_la_interfaz_avisa_que_incluye_subcategorias_y_muestra_la_cantidad(arbol):
    formulario = solicitud("GET", "/precios", cookies=arbol.co).texto
    assert f'value="CATEGORIA:{arbol.bebidas.id}">Categoría: Bebidas (incluye subcategorías)' in formulario
    assert f'value="CATEGORIA:{arbol.colas.id}">Categoría: Bebidas › Gaseosas › Colas</option>' in formulario

    vista = solicitud(
        "GET",
        f"/precios/vista-previa?tipo=PORCENTAJE&direccion=AUMENTAR&valor=10&redondeo=NINGUNO&alcance=CATEGORIA:{arbol.bebidas.id}",
        cookies=arbol.co,
    ).texto
    assert 'id="alcance-vista-previa"' in vista and "Bebidas (incluye subcategorías)" in vista
    assert "<strong>3</strong> de 3 producto(s)" in vista
    for nombre in ("Agua", "Tónica", "Cola"):
        assert nombre in vista
    assert "Papas" not in vista
