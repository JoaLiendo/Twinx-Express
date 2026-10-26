"""V1.14-A: tipo de cliente, condición fiscal y compradores autorizados (migración 037).

Adversarial: el tipo y la condición fiscal son independientes, una empresa exige su identidad completa
(servicio y esquema), el CUIT es único entre clientes pero no contra proveedores, nunca hay más de 2
autorizados activos (ni por servicio, ni por SQL directo, ni con dos altas simultáneas), el historial
de autorizaciones no se reescribe y solo el OWNER administra.
"""

import sqlite3
import threading

import pytest

import db.conexion as modulo_conexion
from excepciones import (
    ClienteInactivoError,
    CompradorAutorizadoNoEncontradoError,
    CuitClienteDuplicadoError,
    DatosInvalidosError,
    LimiteCompradoresAutorizadosError,
    PermisoDenegadoError,
)
from services import servicio_clientes, servicio_proveedores
from tests.utilidades_clientes import consultar, contar, crear_owner, crear_usuario, escribir

CUIT_EMPRESA = "30-71234567-1"
OTRO_CUIT = "20-12345678-6"


@pytest.fixture
def e(base_datos_temporal):
    return type(
        "Escenario",
        (),
        {"ruta": base_datos_temporal, "owner": crear_owner(), "cajera": crear_usuario("cajera", "CASHIER")},
    )


def _empresa(e, cuit=CUIT_EMPRESA, condicion="RESPONSABLE_INSCRIPTO", **cambios):
    datos = {
        "telefono": "4444-5555",
        "direccion": "Av. Siempre Viva 742",
        "tipo_cliente": "EMPRESA",
        "condicion_fiscal": condicion,
        "razon_social": "Distribuidora Norte SRL",
        "cuit": cuit,
        "contacto": "Marta (compras)",
    }
    datos.update(cambios)
    return servicio_clientes.crear_cliente("Norte", e.owner.id, **datos)


# --- tipo y condición fiscal ------------------------------------------------------------------------


def test_persona_por_defecto_sin_condicion_declarada(e):
    cliente = servicio_clientes.crear_cliente("Ana", e.cajera.id)
    assert (cliente.tipo_cliente, cliente.condicion_fiscal, cliente.tipo_etiqueta) == ("PERSONA", None, "Persona")
    assert cliente.condicion_fiscal_etiqueta == "Sin declarar"


def test_persona_responsable_inscripta_es_valida_porque_tipo_y_condicion_son_independientes(e):
    cliente = servicio_clientes.crear_cliente(
        "Ana", e.owner.id, condicion_fiscal="RESPONSABLE_INSCRIPTO", cuit=OTRO_CUIT
    )
    assert (cliente.tipo_cliente, cliente.condicion_fiscal, cliente.cuit) == (
        "PERSONA", "RESPONSABLE_INSCRIPTO", "20123456786"
    )


def test_empresa_consumidor_final_la_permite_el_modelo(e):
    assert _empresa(e, condicion="CONSUMIDOR_FINAL").condicion_fiscal == "CONSUMIDOR_FINAL"


def test_empresa_completa_guarda_cuit_normalizado(e):
    empresa = _empresa(e)
    assert (empresa.tipo_cliente, empresa.cuit, empresa.cuit_formateado) == ("EMPRESA", "30712345671", CUIT_EMPRESA)


@pytest.mark.parametrize("campo", ["razon_social", "cuit", "direccion", "telefono", "contacto", "condicion_fiscal"])
def test_empresa_incompleta_se_rechaza(e, campo):
    with pytest.raises(DatosInvalidosError, match="Una empresa requiere"):
        _empresa(e, **{campo: None})
    assert contar(e.ruta, "clientes") == 0


def test_empresa_sin_cuit_por_sql_directo_la_rechaza_el_esquema(e):
    with pytest.raises(sqlite3.IntegrityError, match="Una empresa requiere"):
        escribir(
            e.ruta,
            "INSERT INTO clientes (nombre, telefono, direccion, tipo_cliente, condicion_fiscal, razon_social, contacto)"
            " VALUES ('X', '1', 'd', 'EMPRESA', 'EXENTO', 'X SA', 'c')",
        )


@pytest.mark.parametrize("cuit", ["30-71234567-2", "123", "30712345671999", "ab-cdefghij-k"])
def test_cuit_invalido_se_rechaza(e, cuit):
    with pytest.raises(DatosInvalidosError, match="CUIT"):
        _empresa(e, cuit=cuit)


def test_cuit_duplicado_entre_clientes_se_rechaza_aunque_venga_con_otro_formato(e):
    _empresa(e)
    with pytest.raises(CuitClienteDuplicadoError):
        _empresa(e, cuit="30712345671")
    with pytest.raises(CuitClienteDuplicadoError):
        servicio_clientes.crear_cliente("Otra", e.owner.id, cuit="30.71234567.1")


def test_el_mismo_cuit_de_un_proveedor_no_cuenta_padrones_independientes(e):
    servicio_proveedores.crear_proveedor(
        "Norte proveedor", usuario_id=e.owner.id, fiscales=servicio_proveedores.DatosFiscales(cuit=CUIT_EMPRESA)
    )
    assert _empresa(e).cuit == "30712345671"


def test_tipo_y_condicion_invalidos_se_rechazan(e):
    with pytest.raises(DatosInvalidosError):
        servicio_clientes.crear_cliente("Ana", e.owner.id, tipo_cliente="ONG")
    with pytest.raises(DatosInvalidosError):
        servicio_clientes.crear_cliente("Ana", e.owner.id, condicion_fiscal="OTRA")
    with pytest.raises(DatosInvalidosError, match="persona o una empresa"):
        servicio_clientes.crear_cliente("Ana", e.owner.id, tipo_cliente="")


# --- clientes históricos y edición -------------------------------------------------------------------


def test_historico_sin_clasificar_editado_sin_tipo_sigue_sin_clasificar(e):
    escribir(e.ruta, "INSERT INTO clientes (id, nombre) VALUES (50, 'Histórico')")
    editado = servicio_clientes.editar_cliente(50, "Histórico", e.owner.id, telefono="111")
    assert (editado.tipo_cliente, editado.condicion_fiscal, editado.tipo_etiqueta) == (None, None, "Sin clasificar")


def test_historico_se_clasifica_al_editar_y_ya_no_puede_desclasificarse(e):
    escribir(e.ruta, "INSERT INTO clientes (id, nombre) VALUES (50, 'Histórico')")
    servicio_clientes.editar_cliente(50, "Histórico", e.owner.id, tipo_cliente="PERSONA", condicion_fiscal="MONOTRIBUTO")
    # Editar sin tipo conserva la clasificación (no la borra).
    assert servicio_clientes.editar_cliente(50, "Histórico", e.owner.id).tipo_cliente == "PERSONA"
    with pytest.raises(sqlite3.IntegrityError, match="sin tipo"):
        escribir(e.ruta, "UPDATE clientes SET tipo_cliente = NULL WHERE id = 50")
    with pytest.raises(sqlite3.IntegrityError, match="sin tipo"):
        escribir(e.ruta, "UPDATE clientes SET condicion_fiscal = NULL WHERE id = 50")


def test_persona_pasa_a_empresa_solo_con_identidad_completa(e):
    cliente = servicio_clientes.crear_cliente("Ana", e.owner.id)
    with pytest.raises(DatosInvalidosError, match="Una empresa requiere"):
        servicio_clientes.editar_cliente(cliente.id, "Ana", e.owner.id, tipo_cliente="EMPRESA")
    empresa = servicio_clientes.editar_cliente(
        cliente.id, "Ana", e.owner.id, tipo_cliente="EMPRESA", condicion_fiscal="EXENTO", razon_social="Ana SA",
        cuit=CUIT_EMPRESA, telefono="1", direccion="d", contacto="Ana",
    )
    assert empresa.es_empresa


def test_cashier_no_edita_clientes(e):
    cliente = servicio_clientes.crear_cliente("Ana", e.cajera.id)
    with pytest.raises(PermisoDenegadoError):
        servicio_clientes.editar_cliente(cliente.id, "Ana", e.cajera.id, tipo_cliente="EMPRESA")


# --- compradores autorizados -------------------------------------------------------------------------


def test_maximo_dos_autorizados_activos(e):
    empresa = _empresa(e)
    servicio_clientes.agregar_comprador_autorizado(empresa.id, "Juan", e.owner.id, documento="30111222")
    servicio_clientes.agregar_comprador_autorizado(empresa.id, "Pedro", e.owner.id, contacto="11-5555")
    with pytest.raises(LimiteCompradoresAutorizadosError):
        servicio_clientes.agregar_comprador_autorizado(empresa.id, "Tercero", e.owner.id)
    assert contar(e.ruta, "compradores_autorizados") == 2


def test_un_tercer_autorizado_por_sql_directo_lo_rechaza_el_esquema(e):
    empresa = _empresa(e)
    for nombre in ("Juan", "Pedro"):
        servicio_clientes.agregar_comprador_autorizado(empresa.id, nombre, e.owner.id)
    with pytest.raises(sqlite3.IntegrityError, match="2 compradores"):
        escribir(
            e.ruta,
            "INSERT INTO compradores_autorizados (cliente_id, nombre, usuario_alta_id) VALUES (?, 'X', ?)",
            (empresa.id, e.owner.id),
        )


def test_baja_libera_el_cupo_y_la_reactivacion_es_un_periodo_nuevo(e):
    empresa = _empresa(e)
    juan = servicio_clientes.agregar_comprador_autorizado(empresa.id, "Juan", e.owner.id)
    servicio_clientes.agregar_comprador_autorizado(empresa.id, "Pedro", e.owner.id)
    baja = servicio_clientes.dar_de_baja_comprador_autorizado(empresa.id, juan.id, e.owner.id)
    assert (baja.activo, baja.usuario_baja) == (False, e.owner.nombre_completo) and baja.fecha_baja
    # Dar de baja dos veces no hace nada.
    assert servicio_clientes.dar_de_baja_comprador_autorizado(empresa.id, juan.id, e.owner.id).fecha_baja == baja.fecha_baja
    reactivado = servicio_clientes.agregar_comprador_autorizado(empresa.id, "Juan", e.owner.id)
    assert reactivado.id != juan.id
    periodos = servicio_clientes.listar_compradores_autorizados(empresa.id)
    assert [(p.nombre, p.activo) for p in periodos] == [("Juan", True), ("Pedro", True), ("Juan", False)]
    acciones = [f["accion"] for f in consultar(e.ruta, "SELECT accion FROM auditoria WHERE accion LIKE 'CLIENTE_AUTORIZADO_%' ORDER BY id")]
    assert acciones == ["CLIENTE_AUTORIZADO_ALTA", "CLIENTE_AUTORIZADO_ALTA", "CLIENTE_AUTORIZADO_BAJA", "CLIENTE_AUTORIZADO_ALTA"]


def test_el_historial_de_autorizados_no_se_reescribe_ni_se_borra(e):
    empresa = _empresa(e)
    juan = servicio_clientes.agregar_comprador_autorizado(empresa.id, "Juan", e.owner.id)
    servicio_clientes.dar_de_baja_comprador_autorizado(empresa.id, juan.id, e.owner.id)
    for sql in (
        "UPDATE compradores_autorizados SET activo = 1, fecha_baja = NULL, usuario_baja_id = NULL WHERE id = ?",
        "UPDATE compradores_autorizados SET nombre = 'Otro' WHERE id = ?",
        "DELETE FROM compradores_autorizados WHERE id = ?",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            escribir(e.ruta, sql, (juan.id,))


def test_una_persona_o_una_empresa_inactiva_no_tienen_autorizados(e):
    persona = servicio_clientes.crear_cliente("Ana", e.owner.id)
    with pytest.raises(DatosInvalidosError, match="Solo una empresa"):
        servicio_clientes.agregar_comprador_autorizado(persona.id, "Juan", e.owner.id)
    empresa = _empresa(e)
    servicio_clientes.desactivar_cliente(empresa.id, e.owner.id)
    with pytest.raises(ClienteInactivoError):
        servicio_clientes.agregar_comprador_autorizado(empresa.id, "Juan", e.owner.id)


def test_empresa_con_autorizados_activos_no_cambia_de_tipo(e):
    empresa = _empresa(e)
    servicio_clientes.agregar_comprador_autorizado(empresa.id, "Juan", e.owner.id)
    with pytest.raises(DatosInvalidosError, match="compradores autorizados"):
        servicio_clientes.editar_cliente(empresa.id, "Norte", e.owner.id, tipo_cliente="PERSONA")
    with pytest.raises(sqlite3.IntegrityError, match="compradores autorizados"):
        escribir(e.ruta, "UPDATE clientes SET tipo_cliente = 'PERSONA' WHERE id = ?", (empresa.id,))


def test_cashier_no_administra_autorizados_ni_da_de_baja_los_de_otra_empresa(e):
    empresa = _empresa(e)
    with pytest.raises(PermisoDenegadoError):
        servicio_clientes.agregar_comprador_autorizado(empresa.id, "Juan", e.cajera.id)
    juan = servicio_clientes.agregar_comprador_autorizado(empresa.id, "Juan", e.owner.id)
    with pytest.raises(PermisoDenegadoError):
        servicio_clientes.dar_de_baja_comprador_autorizado(empresa.id, juan.id, e.cajera.id)
    otra = _empresa(e, cuit=OTRO_CUIT)
    with pytest.raises(CompradorAutorizadoNoEncontradoError):
        servicio_clientes.dar_de_baja_comprador_autorizado(otra.id, juan.id, e.owner.id)


def test_dos_altas_simultaneas_por_el_ultimo_cupo_solo_una_gana(e):
    empresa = _empresa(e)
    servicio_clientes.agregar_comprador_autorizado(empresa.id, "Juan", e.owner.id)
    barrera = threading.Barrier(2)
    resultados: list[object] = []

    def alta(nombre):
        barrera.wait(timeout=10)
        try:
            resultados.append(servicio_clientes.agregar_comprador_autorizado(empresa.id, nombre, e.owner.id))
        except LimiteCompradoresAutorizadosError as error:
            resultados.append(error)

    hilos = [threading.Thread(target=alta, args=(nombre,)) for nombre in ("Pedro", "Lucas")]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=20)
    assert sorted(type(r).__name__ for r in resultados) == ["CompradorAutorizado", "LimiteCompradoresAutorizadosError"]
    assert contar(e.ruta, "compradores_autorizados", "activo = 1") == 2


# --- migración 037 -----------------------------------------------------------------------------------


def test_migracion_037_deja_a_los_historicos_sin_clasificar_y_sin_cambios(tmp_path, monkeypatch):
    ruta = tmp_path / "v113.db"
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
    con = sqlite3.connect(ruta)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(modulo_conexion._TABLA_MIGRACIONES)
    for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
        if migracion.name < "037":
            con.executescript(migracion.read_text(encoding="utf-8"))
            con.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (migracion.name,))
    con.execute(
        "INSERT INTO clientes (id, nombre, telefono, direccion, activo) VALUES (1, 'Ana', '1', 'd', 1), (2, 'Viejo', NULL, NULL, 0)"
    )
    con.commit()
    antes = con.execute("SELECT id, nombre, telefono, direccion, activo, fecha_creacion FROM clientes ORDER BY id").fetchall()
    con.close()

    modulo_conexion.inicializar_base_datos()
    modulo_conexion.inicializar_base_datos()  # idempotente: no reaplica nada

    con = sqlite3.connect(ruta)
    try:
        assert con.execute(
            "SELECT id, nombre, telefono, direccion, activo, fecha_creacion FROM clientes ORDER BY id"
        ).fetchall() == antes
        assert con.execute(
            "SELECT COUNT(*) FROM clientes WHERE tipo_cliente IS NULL AND condicion_fiscal IS NULL AND cuit IS NULL"
        ).fetchone()[0] == 2
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        con.close()
