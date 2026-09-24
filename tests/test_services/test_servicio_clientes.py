"""Casos de uso de clientes (migración 020): alta, edición, baja/alta lógica, permisos,
límites y auditoría."""

import contextlib

import pytest

from db.repositorios import auditoria as repositorio_auditoria
from domain.cliente import Cliente
from excepciones import (
    ClienteConSaldoError,
    ClienteNoEncontradoError,
    DatosInvalidosError,
    PermisoDenegadoError,
)
from services import servicio_clientes, servicio_cuenta_corriente, servicio_ventas
from domain.venta import ItemVenta
from tests.utilidades_clientes import consultar, contar, crear_owner, crear_producto, crear_usuario

pytestmark = pytest.mark.usefixtures("caja_abierta")


@pytest.fixture
def owner(base_datos_temporal):
    return crear_owner()


@pytest.fixture
def cajera(base_datos_temporal):
    return crear_usuario("cajera", "CASHIER")


def _auditoria(ruta, accion):
    return consultar(ruta, "SELECT * FROM auditoria WHERE accion = ? ORDER BY id", (accion,))


def _cliente_con_saldo(owner, cajera, total=200):
    cliente = servicio_clientes.crear_cliente("Ana", owner.id)
    producto = crear_producto(precio_centavos=total)
    servicio_ventas.registrar_venta(
        [ItemVenta(producto.id, 1)], "CUENTA_CORRIENTE", usuario_id=cajera.id, cliente_id=cliente.id
    )
    return cliente


# --- alta -------------------------------------------------------------------------


@pytest.mark.parametrize("rol", ["OWNER", "CASHIER"])
def test_owner_y_cashier_pueden_crear_clientes(base_datos_temporal, rol):
    usuario = crear_usuario("u", rol)

    cliente = servicio_clientes.crear_cliente("Ana", usuario.id, telefono="123")

    assert cliente.id is not None and cliente.activo is True and cliente.telefono == "123"
    assert servicio_clientes.obtener_cliente(cliente.id) == cliente


def test_crear_cliente_normaliza_espacios_y_opcionales_vacios(owner):
    cliente = servicio_clientes.crear_cliente(
        "  Ana Gómez  ", owner.id, telefono="  ", email=" a@b.c ", direccion="", observaciones=None
    )

    assert cliente.nombre == "Ana Gómez"
    assert cliente.telefono is None and cliente.direccion is None and cliente.observaciones is None
    assert cliente.email == "a@b.c"


def test_crear_cliente_sin_usuario_activo_es_denegado(base_datos_temporal):
    inactivo = crear_usuario("baja", "CASHIER", activo=False)

    with pytest.raises(PermisoDenegadoError):
        servicio_clientes.crear_cliente("Ana", inactivo.id)
    with pytest.raises(PermisoDenegadoError):
        servicio_clientes.crear_cliente("Ana", 9999)

    assert contar(base_datos_temporal, "clientes") == 0


def test_nombres_duplicados_permitidos(owner, base_datos_temporal):
    primero = servicio_clientes.crear_cliente("Ana", owner.id)
    segundo = servicio_clientes.crear_cliente("Ana", owner.id)

    assert primero.id != segundo.id
    assert contar(base_datos_temporal, "clientes", "nombre = 'Ana'") == 2


def test_el_email_no_se_valida_en_su_formato(owner):
    assert servicio_clientes.crear_cliente("Ana", owner.id, email="sin arroba").email == "sin arroba"


@pytest.mark.parametrize(
    ("campo", "limite"),
    [("nombre", 120), ("telefono", 30), ("email", 150), ("direccion", 200), ("observaciones", 500)],
)
def test_limites_de_campos_en_el_alta(owner, base_datos_temporal, campo, limite):
    datos = {"nombre": "Ana"}

    servicio_clientes.crear_cliente(**{**datos, campo: "a" * limite}, usuario_id=owner.id)
    with pytest.raises(DatosInvalidosError):
        servicio_clientes.crear_cliente(**{**datos, campo: "a" * (limite + 1)}, usuario_id=owner.id)

    assert contar(base_datos_temporal, "clientes") == 1


@pytest.mark.parametrize("nombre", ["", "   ", None])
def test_nombre_obligatorio(owner, nombre):
    with pytest.raises(DatosInvalidosError):
        servicio_clientes.crear_cliente(nombre, owner.id)


def test_alta_se_audita(owner, base_datos_temporal):
    cliente = servicio_clientes.crear_cliente("Ana", owner.id)

    filas = _auditoria(base_datos_temporal, "CLIENTE_CREADO")
    assert len(filas) == 1
    assert (filas[0]["usuario_id"], filas[0]["entidad"], filas[0]["entidad_id"]) == (owner.id, "CLIENTE", cliente.id)


# --- edición ------------------------------------------------------------------------


def test_solo_owner_edita_clientes(owner, cajera, base_datos_temporal):
    cliente = servicio_clientes.crear_cliente("Ana", cajera.id)

    editado = servicio_clientes.editar_cliente(cliente.id, "Ana María", owner.id, telefono="555")
    with pytest.raises(PermisoDenegadoError):
        servicio_clientes.editar_cliente(cliente.id, "Otro", cajera.id)

    assert editado.nombre == "Ana María" and editado.telefono == "555" and editado.activo is True
    assert servicio_clientes.obtener_cliente(cliente.id).nombre == "Ana María"
    assert len(_auditoria(base_datos_temporal, "CLIENTE_EDITADO")) == 1


def test_editar_un_cliente_inexistente_falla(owner):
    with pytest.raises(ClienteNoEncontradoError):
        servicio_clientes.editar_cliente(999, "Ana", owner.id)


def test_editar_valida_los_limites_y_no_cambia_nada(owner):
    cliente = servicio_clientes.crear_cliente("Ana", owner.id)

    with pytest.raises(DatosInvalidosError):
        servicio_clientes.editar_cliente(cliente.id, "a" * 121, owner.id)

    assert servicio_clientes.obtener_cliente(cliente.id).nombre == "Ana"


def test_editar_no_reactiva_a_un_cliente_inactivo(owner):
    cliente = servicio_clientes.crear_cliente("Ana", owner.id)
    servicio_clientes.desactivar_cliente(cliente.id, owner.id)

    assert servicio_clientes.editar_cliente(cliente.id, "Ana B", owner.id).activo is False


# --- desactivar / reactivar ----------------------------------------------------------


def test_solo_owner_desactiva_y_reactiva(owner, cajera, base_datos_temporal):
    cliente = servicio_clientes.crear_cliente("Ana", cajera.id)

    with pytest.raises(PermisoDenegadoError):
        servicio_clientes.desactivar_cliente(cliente.id, cajera.id)
    assert servicio_clientes.desactivar_cliente(cliente.id, owner.id).activo is False
    with pytest.raises(PermisoDenegadoError):
        servicio_clientes.reactivar_cliente(cliente.id, cajera.id)
    assert servicio_clientes.reactivar_cliente(cliente.id, owner.id).activo is True

    assert len(_auditoria(base_datos_temporal, "CLIENTE_DESACTIVADO")) == 1
    assert len(_auditoria(base_datos_temporal, "CLIENTE_REACTIVADO")) == 1


def test_no_se_desactiva_un_cliente_con_saldo(owner, cajera, base_datos_temporal):
    cliente = _cliente_con_saldo(owner, cajera)

    with pytest.raises(ClienteConSaldoError):
        servicio_clientes.desactivar_cliente(cliente.id, owner.id)

    assert servicio_clientes.obtener_cliente(cliente.id).activo is True
    assert _auditoria(base_datos_temporal, "CLIENTE_DESACTIVADO") == []


def test_se_desactiva_un_cliente_con_saldo_cero_tras_cobrar_todo(owner, cajera):
    cliente = _cliente_con_saldo(owner, cajera, total=200)
    servicio_cuenta_corriente.registrar_cobro(cliente.id, 200, cajera.id)

    assert servicio_clientes.desactivar_cliente(cliente.id, owner.id).activo is False


def test_desactivar_o_reactivar_dos_veces_no_duplica_la_auditoria(owner, base_datos_temporal):
    cliente = servicio_clientes.crear_cliente("Ana", owner.id)

    servicio_clientes.desactivar_cliente(cliente.id, owner.id)
    servicio_clientes.desactivar_cliente(cliente.id, owner.id)
    servicio_clientes.reactivar_cliente(cliente.id, owner.id)
    servicio_clientes.reactivar_cliente(cliente.id, owner.id)

    assert len(_auditoria(base_datos_temporal, "CLIENTE_DESACTIVADO")) == 1
    assert len(_auditoria(base_datos_temporal, "CLIENTE_REACTIVADO")) == 1


def test_desactivar_o_reactivar_un_cliente_inexistente_falla(owner):
    with pytest.raises(ClienteNoEncontradoError):
        servicio_clientes.desactivar_cliente(999, owner.id)
    with pytest.raises(ClienteNoEncontradoError):
        servicio_clientes.reactivar_cliente(999, owner.id)


# --- consultas -----------------------------------------------------------------------


def test_listar_clientes_ordena_por_nombre_y_filtra_activos(owner):
    beto = servicio_clientes.crear_cliente("beto", owner.id)
    ana = servicio_clientes.crear_cliente("Ana", owner.id)
    servicio_clientes.desactivar_cliente(beto.id, owner.id)

    assert [c.nombre for c in servicio_clientes.listar_clientes()] == ["Ana", "beto"]
    assert [c.id for c in servicio_clientes.listar_clientes(solo_activos=True)] == [ana.id]


def test_saldo_y_movimientos_de_un_cliente_inexistente_fallan(owner):
    with pytest.raises(ClienteNoEncontradoError):
        servicio_clientes.obtener_saldo(999)
    with pytest.raises(ClienteNoEncontradoError):
        servicio_clientes.listar_movimientos_cuenta(999)


def test_cliente_dataclass_valida_sin_base_de_datos():
    assert Cliente(nombre=" x ").nombre == "x"
    with pytest.raises(DatosInvalidosError):
        Cliente(nombre="x", observaciones="a" * 501)


# --- la auditoría comparte transacción: si falla, se revierte toda la operación --------------


@contextlib.contextmanager
def _auditoria_que_falla(monkeypatch, accion: str):
    """Hace que la auditoría de `accion` (y solo esa) levante una excepción."""
    original = repositorio_auditoria.registrar_en_conexion

    def auditar_o_fallar(conexion, usuario_id, accion_pedida, *resto):
        if accion_pedida == accion:
            raise RuntimeError(f"falla simulada en la auditoría de {accion}")
        return original(conexion, usuario_id, accion_pedida, *resto)

    with monkeypatch.context() as parche:
        parche.setattr(repositorio_auditoria, "registrar_en_conexion", auditar_o_fallar)
        yield


def _fila_cliente(ruta, cliente_id):
    return tuple(consultar(ruta, "SELECT * FROM clientes WHERE id = ?", (cliente_id,))[0])


def test_rollback_del_alta_si_falla_su_auditoria(owner, base_datos_temporal, monkeypatch):
    auditoria_antes = contar(base_datos_temporal, "auditoria")

    with _auditoria_que_falla(monkeypatch, "CLIENTE_CREADO"), pytest.raises(RuntimeError, match="CLIENTE_CREADO"):
        servicio_clientes.crear_cliente("Ana", owner.id, telefono="123")

    assert contar(base_datos_temporal, "clientes") == 0
    assert contar(base_datos_temporal, "auditoria") == auditoria_antes
    assert servicio_clientes.crear_cliente("Ana", owner.id).id is not None  # la operación sigue funcionando


def test_rollback_de_la_edicion_si_falla_su_auditoria(owner, base_datos_temporal, monkeypatch):
    cliente = servicio_clientes.crear_cliente("Ana", owner.id, telefono="123")
    antes = _fila_cliente(base_datos_temporal, cliente.id)
    auditoria_antes = contar(base_datos_temporal, "auditoria")

    with _auditoria_que_falla(monkeypatch, "CLIENTE_EDITADO"), pytest.raises(RuntimeError, match="CLIENTE_EDITADO"):
        servicio_clientes.editar_cliente(cliente.id, "Otro nombre", owner.id, telefono="999")

    assert _fila_cliente(base_datos_temporal, cliente.id) == antes
    assert contar(base_datos_temporal, "auditoria") == auditoria_antes


def test_rollback_de_la_desactivacion_si_falla_su_auditoria(owner, base_datos_temporal, monkeypatch):
    cliente = servicio_clientes.crear_cliente("Ana", owner.id)
    antes = _fila_cliente(base_datos_temporal, cliente.id)
    auditoria_antes = contar(base_datos_temporal, "auditoria")

    with _auditoria_que_falla(monkeypatch, "CLIENTE_DESACTIVADO"), pytest.raises(RuntimeError, match="CLIENTE_DESACTIVADO"):
        servicio_clientes.desactivar_cliente(cliente.id, owner.id)

    assert _fila_cliente(base_datos_temporal, cliente.id) == antes
    assert servicio_clientes.obtener_cliente(cliente.id).activo is True
    assert contar(base_datos_temporal, "auditoria") == auditoria_antes


def test_rollback_de_la_reactivacion_si_falla_su_auditoria(owner, base_datos_temporal, monkeypatch):
    cliente = servicio_clientes.crear_cliente("Ana", owner.id)
    servicio_clientes.desactivar_cliente(cliente.id, owner.id)
    antes = _fila_cliente(base_datos_temporal, cliente.id)
    auditoria_antes = contar(base_datos_temporal, "auditoria")

    with _auditoria_que_falla(monkeypatch, "CLIENTE_REACTIVADO"), pytest.raises(RuntimeError, match="CLIENTE_REACTIVADO"):
        servicio_clientes.reactivar_cliente(cliente.id, owner.id)

    assert _fila_cliente(base_datos_temporal, cliente.id) == antes
    assert servicio_clientes.obtener_cliente(cliente.id).activo is False
    assert contar(base_datos_temporal, "auditoria") == auditoria_antes
