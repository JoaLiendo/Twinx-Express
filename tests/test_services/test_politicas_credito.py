"""V1.14-B: políticas de crédito por capas (migración 038).

Precedencia GENERAL -> TIPO -> INDIVIDUAL, representación explícita (sin configurar / sin crédito /
límite 0 / límite N), solo OWNER (servicio y POST manual), control de versión ante dos OWNER editando a
la vez, y el esquema como última defensa (no se borra, no cambia de capa, la versión solo avanza).
"""

import sqlite3
import threading

import pytest

from domain.credito import PoliticaCredito, resolver_politica
from excepciones import DatosInvalidosError, PermisoDenegadoError, PoliticaCreditoDesactualizadaError
from services import servicio_clientes, servicio_credito
from tests.test_interfaces_web._asgi_cliente import solicitud
from tests.utilidades_clientes import consultar, crear_owner, crear_usuario, escribir, usuario_logueado

L = "LIMITE"
EMPRESA = {
    "tipo_cliente": "EMPRESA", "condicion_fiscal": "EXENTO", "razon_social": "Norte SA", "cuit": "30-71234567-1",
    "telefono": "1", "direccion": "d", "contacto": "c",
}


@pytest.fixture
def e(base_datos_temporal):
    owner = crear_owner()
    persona = servicio_clientes.crear_cliente("Ana", owner.id)
    empresa = servicio_clientes.crear_cliente("Norte", owner.id, **EMPRESA)
    escribir(base_datos_temporal, "INSERT INTO clientes (id, nombre) VALUES (90, 'Histórico')")
    return type("E", (), {"ruta": base_datos_temporal, "owner": owner, "cajera": crear_usuario("cajera", "CASHIER"),
                          "persona": persona, "empresa": empresa, "historico_id": 90})


def _configurar(e, ambito, modo, limite=None, cliente_id=None):
    actual = (
        servicio_credito.obtener_politica_individual(cliente_id)
        if cliente_id is not None
        else next(p for p in servicio_credito.listar_politicas_globales() if p.ambito == ambito)
    )
    return servicio_credito.configurar_politica(
        ambito, modo, limite, e.owner.id, version_esperada=actual.version, cliente_id=cliente_id
    )


def _efectiva(cliente_id):
    p = servicio_credito.politica_credito_efectiva(cliente_id)
    return (p.habilitado, p.limite_centavos, p.origen)


def test_sin_ninguna_configuracion_nadie_tiene_credito(e):
    for cliente_id in (e.persona.id, e.empresa.id, e.historico_id):
        assert _efectiva(cliente_id) == (False, 0, "SIN_CONFIGURAR")


def test_general_aplica_a_todos_incluso_al_historico_sin_clasificar(e):
    _configurar(e, "GENERAL", L, 100_000)
    for cliente_id in (e.persona.id, e.empresa.id, e.historico_id):
        assert _efectiva(cliente_id) == (True, 100_000, "GENERAL")


def test_tipo_gana_sobre_general_y_el_historico_no_toma_capa_de_tipo(e):
    _configurar(e, "GENERAL", L, 100_000)
    _configurar(e, "EMPRESA", L, 500_000)
    _configurar(e, "PERSONA", "SIN_CREDITO")
    assert _efectiva(e.empresa.id) == (True, 500_000, "EMPRESA")
    assert _efectiva(e.persona.id) == (False, 0, "PERSONA")
    assert _efectiva(e.historico_id) == (True, 100_000, "GENERAL")


def test_individual_gana_sobre_todo_y_heredar_vuelve_a_la_capa_anterior(e):
    _configurar(e, "GENERAL", L, 100_000)
    _configurar(e, "PERSONA", L, 200_000)
    _configurar(e, "CLIENTE", "SIN_CREDITO", cliente_id=e.persona.id)
    assert _efectiva(e.persona.id) == (False, 0, "CLIENTE")
    _configurar(e, "CLIENTE", L, 999, cliente_id=e.persona.id)
    assert _efectiva(e.persona.id) == (True, 999, "CLIENTE")
    _configurar(e, "CLIENTE", "HEREDAR", cliente_id=e.persona.id)
    assert _efectiva(e.persona.id) == (True, 200_000, "PERSONA")
    _configurar(e, "PERSONA", "HEREDAR")
    assert _efectiva(e.persona.id) == (True, 100_000, "GENERAL")


def test_limite_cero_es_credito_habilitado_sin_disponible_distinto_de_sin_credito(e):
    _configurar(e, "GENERAL", L, 0)
    assert _efectiva(e.persona.id) == (True, 0, "GENERAL")
    _configurar(e, "GENERAL", "SIN_CREDITO")
    assert _efectiva(e.persona.id) == (False, 0, "GENERAL")


@pytest.mark.parametrize(
    ("ambito", "modo", "limite", "mensaje"),
    [
        ("GENERAL", "HEREDAR", None, "no hereda"),
        ("GENERAL", L, None, "Indicá el límite"),
        ("GENERAL", L, -1, "negativo"),
        ("GENERAL", L, 100_000_000_001, "demasiado alto"),
        ("GENERAL", "SIN_CREDITO", 5, "Solo el modo"),
        ("GENERAL", "ILIMITADO", None, "Modo"),
        ("VIP", L, 5, "Ámbito"),
    ],
)
def test_capas_invalidas_se_rechazan(e, ambito, modo, limite, mensaje):
    with pytest.raises(DatosInvalidosError, match=mensaje):
        servicio_credito.configurar_politica(ambito, modo, limite, e.owner.id, version_esperada=0)


def test_el_esquema_rechaza_ilimitado_negativo_y_general_heredado(e):
    for valores in (("GENERAL", None, "LIMITE", None), ("GENERAL", None, "LIMITE", -5), ("GENERAL", None, "HEREDAR", None),
                    ("CLIENTE", None, "LIMITE", 5), ("PERSONA", e.persona.id, "LIMITE", 5)):
        with pytest.raises(sqlite3.IntegrityError):
            escribir(e.ruta, "INSERT INTO politicas_credito (ambito, cliente_id, modo, limite_centavos, usuario_id)"
                             " VALUES (?, ?, ?, ?, ?)", (*valores, e.owner.id))


def test_cashier_no_configura_ni_por_servicio(e):
    with pytest.raises(PermisoDenegadoError):
        servicio_credito.configurar_politica("GENERAL", L, 1, e.cajera.id, version_esperada=0)
    with pytest.raises(PermisoDenegadoError):
        servicio_credito.configurar_politica("CLIENTE", L, 1, e.cajera.id, version_esperada=0, cliente_id=e.persona.id)
    assert _efectiva(e.persona.id)[2] == "SIN_CONFIGURAR"


def test_cashier_no_configura_con_post_manual(e):
    _, cookies = usuario_logueado("CASHIER", "caja2")
    general = solicitud("POST", "/configuracion/credito", cookies=cookies,
                        formulario={"ambito": "GENERAL", "modo": L, "limite": "999999", "version": "0"})
    individual = solicitud("POST", f"/clientes/{e.persona.id}/credito/politica", cookies=cookies,
                           formulario={"modo": L, "limite": "999999", "version": "0"})
    assert general.status == 403 and individual.status == 403
    assert _efectiva(e.persona.id)[2] == "SIN_CONFIGURAR"


def test_owner_configura_por_http_y_la_ficha_muestra_la_politica_efectiva(e):
    _, cookies = usuario_logueado("OWNER", "duenio2")
    respuesta = solicitud("POST", "/configuracion/credito", cookies=cookies,
                          formulario={"ambito": "PERSONA", "modo": L, "limite": "1500,50", "version": "0"})
    assert respuesta.status == 303 and "tipo=success" in respuesta.header("location")
    assert _efectiva(e.persona.id) == (True, 150_050, "PERSONA")
    ficha = solicitud("GET", f"/clientes/{e.persona.id}", cookies=cookies).texto
    assert "1.500,50" in ficha and "Por tipo (persona)" in ficha
    config = solicitud("GET", "/configuracion", cookies=cookies).texto
    assert 'id="config-credito"' in config and 'value="1500.50"' in config


def test_limite_vacio_por_http_no_se_toma_como_cero(e):
    _, cookies = usuario_logueado("OWNER", "duenio2")
    solicitud("POST", "/configuracion/credito", cookies=cookies,
              formulario={"ambito": "GENERAL", "modo": L, "limite": "", "version": "0"})
    assert _efectiva(e.persona.id)[2] == "SIN_CONFIGURAR"


def test_version_vieja_no_pisa_el_cambio_de_otro_owner(e):
    _configurar(e, "GENERAL", L, 100)
    with pytest.raises(PoliticaCreditoDesactualizadaError):
        servicio_credito.configurar_politica("GENERAL", L, 999, e.owner.id, version_esperada=0)
    assert _efectiva(e.persona.id) == (True, 100, "GENERAL")


def test_dos_owner_editando_a_la_vez_la_misma_version_solo_uno_gana(e):
    otro = crear_owner("socio")
    barrera = threading.Barrier(2)
    resultados = []

    def editar(usuario, limite):
        barrera.wait(timeout=10)
        try:
            resultados.append(servicio_credito.configurar_politica("GENERAL", L, limite, usuario.id, version_esperada=0))
        except PoliticaCreditoDesactualizadaError as error:
            resultados.append(error)

    hilos = [threading.Thread(target=editar, args=args) for args in ((e.owner, 111), (otro, 222))]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=20)
    ganadora = [r for r in resultados if isinstance(r, PoliticaCredito)]
    assert len(ganadora) == 1 and sum(isinstance(r, PoliticaCreditoDesactualizadaError) for r in resultados) == 1
    assert _efectiva(e.persona.id) == (True, ganadora[0].limite_centavos, "GENERAL")
    assert ganadora[0].version == 1


def test_cada_cambio_queda_auditado_y_el_esquema_impide_borrar_o_mover_capas(e):
    _configurar(e, "GENERAL", L, 100)
    _configurar(e, "GENERAL", "SIN_CREDITO")
    resumenes = [f["resumen"] for f in consultar(e.ruta, "SELECT resumen FROM auditoria WHERE accion = 'POLITICA_CREDITO_CAMBIADA' ORDER BY id")]
    assert resumenes == ["Crédito general: heredar -> límite $1.00", "Crédito general: límite $1.00 -> sin crédito"]
    for sql in ("DELETE FROM politicas_credito", "UPDATE politicas_credito SET ambito = 'PERSONA'",
                "UPDATE politicas_credito SET modo = 'LIMITE', limite_centavos = 5"):
        with pytest.raises(sqlite3.IntegrityError):
            escribir(e.ruta, sql)


def test_resolver_politica_es_pura_y_respeta_la_precedencia():
    general = PoliticaCredito("GENERAL", L, 10, version=1)
    tipo = PoliticaCredito("PERSONA", "HEREDAR")
    individual = PoliticaCredito("CLIENTE", L, 0, cliente_id=1, version=1)
    assert resolver_politica(general, tipo, individual).limite_centavos == 0
    assert resolver_politica(general, tipo, None).origen == "GENERAL"
    assert resolver_politica(None, None, None).habilitado is False
