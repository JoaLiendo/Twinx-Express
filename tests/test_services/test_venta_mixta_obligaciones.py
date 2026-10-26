"""V1.14-C: venta 100% contado, 100% crédito y mixta, con su obligación causal (migración 039).

Adversarial y transaccional: la venta, el stock, las partidas, el comprador, el CARGO y la obligación
se confirman o se revierten juntos; la caja solo recibe el efectivo efectivamente pagado; el crédito
solicitado nunca supera el disponible (ni con dos ventas simultáneas); una empresa identifica a su
comprador; y el esquema rechaza obligaciones, saldos o cargos fabricados por SQL directo.
"""

import sqlite3
import threading

import pytest

from db.repositorios import clientes as repositorio_clientes
from db.repositorios import obligaciones as repositorio_obligaciones
from domain.venta import CompradorVenta, ItemVenta
from excepciones import (
    ClaveIdempotenciaReutilizadaError,
    CompradorInvalidoError,
    CreditoNoDisponibleError,
    DatosInvalidosError,
    ErrorBaseDatos,
    PrecioVentaNoConfiguradoError,
    VentaACuentaNoAnulableError,
)
from services import servicio_caja, servicio_clientes, servicio_credito, servicio_stock, servicio_ventas
from tests.utilidades_clientes import (
    consultar,
    contar,
    crear_owner,
    crear_producto,
    crear_usuario,
    escribir,
    violaciones_de_invariantes,
)

CC = "CUENTA_CORRIENTE"
PRECIO = 1_000_000  # $10.000 por unidad
EMPRESA = {
    "tipo_cliente": "EMPRESA", "condicion_fiscal": "RESPONSABLE_INSCRIPTO", "razon_social": "Norte SRL",
    "cuit": "30-71234567-1", "telefono": "1", "direccion": "d", "contacto": "c",
}


@pytest.fixture
def e(base_datos_temporal, caja_abierta):
    owner = crear_owner()
    cajera = crear_usuario("cajera", "CASHIER")
    return type("E", (), {
        "ruta": base_datos_temporal, "owner": owner, "cajera": cajera,
        "cliente": servicio_clientes.crear_cliente("Ana", owner.id),
        "producto": crear_producto(precio_centavos=PRECIO, stock=20),
    })


def _limite(e, centavos, ambito="GENERAL", cliente_id=None):
    actual = (servicio_credito.obtener_politica_individual(cliente_id) if cliente_id
              else next(p for p in servicio_credito.listar_politicas_globales() if p.ambito == ambito))
    servicio_credito.configurar_politica(ambito, "LIMITE", centavos, e.owner.id,
                                         version_esperada=actual.version, cliente_id=cliente_id)


def _vender(e, cantidad=5, tipo=CC, contado=0, cliente=None, comprador=None, clave=None, usuario=None):
    return servicio_ventas.registrar_venta(
        [ItemVenta(e.producto.id, cantidad)], tipo, clave_idempotencia=clave, usuario_id=(usuario or e.cajera).id,
        cliente_id=(cliente or e.cliente).id, pago_contado_centavos=contado, comprador=comprador,
    )


def _foto(e) -> dict:
    """Todo lo que una venta toca: si falla, nada de esto puede cambiar."""
    return {
        tabla: contar(e.ruta, tabla)
        for tabla in ("ventas", "detalle_venta", "movimientos_cuenta", "obligaciones_cliente", "compradores_venta",
                      "caja_movimientos", "auditoria", "movimientos_partida")
    } | {"stock": servicio_stock.obtener_por_id(e.producto.id).stock_actual,
         "efectivo": servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos}


def _empresa(e):
    return servicio_clientes.crear_cliente("Norte", e.owner.id, **EMPRESA)


# --- tres modalidades ------------------------------------------------------------------------------------


def test_venta_100_contado_con_cliente_no_crea_obligacion_y_entra_entera_a_caja(e):
    antes = _foto(e)
    venta = _vender(e, tipo="EFECTIVO")
    assert (venta.pago_contado_centavos, venta.credito_centavos, venta.efectivo_centavos) == (0, 0, 5 * PRECIO)
    assert contar(e.ruta, "obligaciones_cliente") == 0
    assert _foto(e)["efectivo"] == antes["efectivo"] + 5 * PRECIO
    assert violaciones_de_invariantes(e.ruta) == []


def test_venta_100_credito_crea_su_obligacion_y_no_toca_la_caja(e):
    _limite(e, 10 * PRECIO)
    antes = _foto(e)
    venta = _vender(e)
    obligacion = repositorio_obligaciones.obtener_por_venta(venta.id)
    assert (obligacion.monto_original_centavos, obligacion.saldo_pendiente_centavos, obligacion.cliente_id) == (
        5 * PRECIO, 5 * PRECIO, e.cliente.id)
    assert repositorio_clientes.obtener_saldo(e.cliente.id) == 5 * PRECIO
    despues = _foto(e)
    assert (despues["efectivo"], despues["caja_movimientos"]) == (antes["efectivo"], antes["caja_movimientos"])
    assert violaciones_de_invariantes(e.ruta) == []


def test_venta_mixta_50000_paga_20000_financia_30000(e):
    _limite(e, 10 * PRECIO)
    antes = _foto(e)
    venta = _vender(e, contado=2 * PRECIO)
    assert (venta.total_centavos, venta.pago_contado_centavos, venta.credito_centavos) == (5 * PRECIO, 2 * PRECIO, 3 * PRECIO)
    obligacion = repositorio_obligaciones.obtener_por_venta(venta.id)
    assert obligacion.monto_original_centavos == 3 * PRECIO
    assert repositorio_clientes.obtener_saldo(e.cliente.id) == 3 * PRECIO
    despues = _foto(e)
    assert despues["efectivo"] == antes["efectivo"] + 2 * PRECIO
    assert despues["caja_movimientos"] == antes["caja_movimientos"]  # el contado entra por la venta, no por un ingreso
    assert despues["stock"] == antes["stock"] - 5
    resumen = consultar(e.ruta, "SELECT resumen FROM auditoria WHERE accion = 'VENTA_A_CUENTA'")[0]["resumen"]
    assert "contado $20000.00, a cuenta $30000.00" in resumen
    assert violaciones_de_invariantes(e.ruta) == []


def test_el_stock_y_las_partidas_no_dependen_de_la_modalidad(e):
    _limite(e, 100 * PRECIO)
    for tipo, contado in (("EFECTIVO", 0), (CC, 0), (CC, PRECIO)):
        antes = servicio_stock.obtener_por_id(e.producto.id).stock_actual
        _vender(e, cantidad=2, tipo=tipo, contado=contado)
        assert servicio_stock.obtener_por_id(e.producto.id).stock_actual == antes - 2
    consumido = consultar(e.ruta, "SELECT -SUM(delta) AS n FROM movimientos_partida WHERE detalle_venta_id IS NOT NULL")[0]["n"]
    assert consumido == 6


# --- montos inválidos --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tipo", "contado", "error"),
    [
        (CC, 5 * PRECIO, "cubre el total"),
        (CC, 5 * PRECIO + 1, "cubre el total"),
        (CC, -1, "negativo"),
        ("EFECTIVO", 100, "Solo una venta a cuenta"),
    ],
)
def test_contado_invalido_no_deja_nada(e, tipo, contado, error):
    _limite(e, 100 * PRECIO)
    antes = _foto(e)
    with pytest.raises(DatosInvalidosError, match=error):
        _vender(e, tipo=tipo, contado=contado)
    assert _foto(e) == antes


def test_venta_de_cero_pesos_no_existe(e):
    _limite(e, 100 * PRECIO)
    gratis = crear_producto("7790000000099", precio_centavos=0, stock=5)
    with pytest.raises(PrecioVentaNoConfiguradoError):
        servicio_ventas.registrar_venta([ItemVenta(gratis.id, 1)], CC, usuario_id=e.cajera.id, cliente_id=e.cliente.id)


# --- crédito disponible --------------------------------------------------------------------------------------


def test_sin_politica_configurada_no_hay_venta_a_cuenta_y_no_queda_nada(e):
    antes = _foto(e)
    with pytest.raises(CreditoNoDisponibleError, match="no está configurado"):
        _vender(e)
    assert _foto(e) == antes


def test_credito_deshabilitado_rechaza_pero_permite_contado(e):
    actual = servicio_credito.listar_politicas_globales()[0]
    servicio_credito.configurar_politica("GENERAL", "SIN_CREDITO", None, e.owner.id, version_esperada=actual.version)
    with pytest.raises(CreditoNoDisponibleError, match="no tiene crédito habilitado"):
        _vender(e, contado=PRECIO)
    assert _vender(e, tipo="EFECTIVO").estado == "ACTIVA"


def test_venta_por_exactamente_el_disponible_y_un_centavo_mas(e):
    _limite(e, 3 * PRECIO)
    _vender(e, contado=2 * PRECIO)  # financia exactamente 3 * PRECIO
    assert servicio_credito.situacion_crediticia(e.cliente.id).disponible_centavos == 0
    with pytest.raises(CreditoNoDisponibleError, match="supera el crédito disponible"):
        _vender(e, cantidad=1, contado=PRECIO - 1)  # 1 centavo a crédito


def test_limite_cero_es_habilitado_pero_sin_disponible(e):
    _limite(e, 0)
    with pytest.raises(CreditoNoDisponibleError, match="supera el crédito disponible"):
        _vender(e, cantidad=1, contado=PRECIO - 1)


def test_bajar_el_limite_por_debajo_de_la_deuda_no_toca_la_deuda_y_bloquea_credito_nuevo(e):
    _limite(e, 10 * PRECIO)
    _vender(e)
    _limite(e, PRECIO)
    situacion = servicio_credito.situacion_crediticia(e.cliente.id)
    assert (situacion.saldo_centavos, situacion.disponible_centavos) == (5 * PRECIO, 0)
    with pytest.raises(CreditoNoDisponibleError):
        _vender(e, cantidad=1)
    assert _vender(e, cantidad=1, tipo="EFECTIVO").estado == "ACTIVA"


def test_limite_individual_gana_sobre_el_general_al_vender(e):
    _limite(e, 100 * PRECIO)
    _limite(e, PRECIO, ambito="CLIENTE", cliente_id=e.cliente.id)
    with pytest.raises(CreditoNoDisponibleError):
        _vender(e, cantidad=2)


def test_dos_ventas_simultaneas_por_el_ultimo_credito_solo_una_pasa(e):
    _limite(e, 5 * PRECIO)
    barrera = threading.Barrier(2)
    resultados = []

    def vender():
        barrera.wait(timeout=10)
        try:
            resultados.append(_vender(e, cantidad=5))
        except CreditoNoDisponibleError as error:
            resultados.append(error)

    hilos = [threading.Thread(target=vender) for _ in range(2)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=30)
    assert sorted(type(r).__name__ for r in resultados) == ["CreditoNoDisponibleError", "Venta"]
    assert repositorio_clientes.obtener_saldo(e.cliente.id) == 5 * PRECIO
    assert contar(e.ruta, "obligaciones_cliente") == 1
    assert violaciones_de_invariantes(e.ruta) == []


# --- idempotencia ---------------------------------------------------------------------------------------------


def test_doble_envio_de_una_venta_mixta_es_una_sola_venta_y_una_sola_obligacion(e):
    _limite(e, 100 * PRECIO)
    primera = _vender(e, contado=PRECIO, clave="k-1")
    segunda = _vender(e, contado=PRECIO, clave="k-1")
    assert primera.id == segunda.id
    assert (contar(e.ruta, "ventas"), contar(e.ruta, "obligaciones_cliente")) == (1, 1)


def test_misma_clave_con_otro_reparto_contado_credito_es_uso_indebido(e):
    _limite(e, 100 * PRECIO)
    _vender(e, contado=PRECIO, clave="k-1")
    with pytest.raises(ClaveIdempotenciaReutilizadaError):
        _vender(e, contado=2 * PRECIO, clave="k-1")


# --- comprador de empresa ----------------------------------------------------------------------------------------


def test_empresa_a_cuenta_sin_comprador_se_rechaza(e):
    _limite(e, 100 * PRECIO)
    empresa = _empresa(e)
    antes = _foto(e)
    with pytest.raises(CompradorInvalidoError, match="es una empresa"):
        _vender(e, cliente=empresa)
    assert _foto(e) == antes


def test_empresa_con_comprador_autorizado_vigente(e):
    _limite(e, 100 * PRECIO)
    empresa = _empresa(e)
    juan = servicio_clientes.agregar_comprador_autorizado(empresa.id, "Juan", e.owner.id)
    venta = _vender(e, cliente=empresa, comprador=CompradorVenta(comprador_autorizado_id=juan.id))
    registrado = repositorio_obligaciones.obtener_comprador_de_venta(venta.id)
    assert (registrado.nombre, registrado.excepcional, registrado.usuario) == ("Juan", False, e.cajera.nombre_completo)


def test_autorizado_dado_de_baja_o_de_otra_empresa_no_sirve(e):
    _limite(e, 100 * PRECIO)
    empresa = _empresa(e)
    otra = servicio_clientes.crear_cliente("Sur", e.owner.id, **(EMPRESA | {"cuit": "20-12345678-6"}))
    juan = servicio_clientes.agregar_comprador_autorizado(empresa.id, "Juan", e.owner.id)
    ajeno = servicio_clientes.agregar_comprador_autorizado(otra.id, "Ajeno", e.owner.id)
    servicio_clientes.dar_de_baja_comprador_autorizado(empresa.id, juan.id, e.owner.id)
    for comprador_id in (juan.id, ajeno.id, 9999):
        with pytest.raises(CompradorInvalidoError, match="no es un comprador autorizado vigente"):
            _vender(e, cliente=empresa, comprador=CompradorVenta(comprador_autorizado_id=comprador_id))
    assert contar(e.ruta, "ventas") == 0


def test_comprador_excepcional_queda_registrado_y_auditado_sin_volverse_autorizado(e):
    _limite(e, 100 * PRECIO)
    empresa = _empresa(e)
    excepcional = CompradorVenta(nombre_excepcional="Pedro (chofer)", metodo_autorizacion="WHATSAPP",
                                 detalle_autorizacion="Mensaje de Marta 10:32")
    venta = _vender(e, cliente=empresa, comprador=excepcional, contado=PRECIO)
    registrado = repositorio_obligaciones.obtener_comprador_de_venta(venta.id)
    assert (registrado.nombre, registrado.excepcional, registrado.metodo_etiqueta) == ("Pedro (chofer)", True, "WhatsApp")
    assert contar(e.ruta, "compradores_autorizados") == 0
    assert contar(e.ruta, "auditoria", "accion = 'VENTA_COMPRADOR_EXCEPCIONAL'") == 1


@pytest.mark.parametrize(
    "datos",
    [
        {"nombre_excepcional": "Pedro", "metodo_autorizacion": "WHATSAPP"},
        {"nombre_excepcional": "Pedro", "detalle_autorizacion": "x"},
        {"metodo_autorizacion": "EMAIL", "detalle_autorizacion": "x"},
        {"nombre_excepcional": "Pedro", "metodo_autorizacion": "TELEPATIA", "detalle_autorizacion": "x"},
        {"comprador_autorizado_id": 1, "nombre_excepcional": "Pedro"},
    ],
)
def test_autorizacion_excepcional_incompleta_o_ambigua_se_rechaza(datos):
    with pytest.raises(DatosInvalidosError):
        CompradorVenta(**datos)


def test_comprador_en_persona_o_en_venta_de_contado_se_rechaza(e):
    _limite(e, 100 * PRECIO)
    excepcional = CompradorVenta(nombre_excepcional="X", metodo_autorizacion="OTRO", detalle_autorizacion="y")
    with pytest.raises(CompradorInvalidoError, match="Solo una empresa"):
        _vender(e, comprador=excepcional)
    with pytest.raises(CompradorInvalidoError, match="solo se registra en una venta a cuenta"):
        _vender(e, tipo="EFECTIVO", cliente=_empresa(e), comprador=excepcional)


# --- atomicidad ------------------------------------------------------------------------------------------------


def test_si_falla_el_cargo_no_queda_venta_stock_partida_comprador_ni_obligacion(e, monkeypatch):
    _limite(e, 100 * PRECIO)
    empresa = _empresa(e)
    antes = _foto(e)

    def fallar(*args, **kwargs):
        raise ErrorBaseDatos("falla simulada al registrar el cargo")

    monkeypatch.setattr(repositorio_clientes, "registrar_cargo_en_conexion", fallar)
    excepcional = CompradorVenta(nombre_excepcional="X", metodo_autorizacion="ORDEN", detalle_autorizacion="OC 12")
    with pytest.raises(ErrorBaseDatos):
        _vender(e, cliente=empresa, comprador=excepcional, contado=PRECIO)
    assert _foto(e) == antes


def test_una_venta_mixta_no_se_anula(e):
    _limite(e, 100 * PRECIO)
    venta = _vender(e, contado=PRECIO)
    with pytest.raises(VentaACuentaNoAnulableError):
        servicio_ventas.anular_venta(venta.id, "ERROR_CARGA", None, e.owner.id)


# --- el esquema como última defensa ----------------------------------------------------------------------------


def test_el_esquema_rechaza_obligaciones_y_saldos_fabricados(e):
    _limite(e, 100 * PRECIO)
    venta = _vender(e, contado=PRECIO)
    obligacion = repositorio_obligaciones.obtener_por_venta(venta.id)
    ataques = [
        ("UPDATE obligaciones_cliente SET saldo_pendiente_centavos = 0 WHERE id = ?", (obligacion.id,)),
        ("UPDATE obligaciones_cliente SET monto_original_centavos = 1 WHERE id = ?", (obligacion.id,)),
        ("DELETE FROM obligaciones_cliente WHERE id = ?", (obligacion.id,)),
        ("UPDATE ventas SET pago_contado_centavos = 0 WHERE id = ?", (venta.id,)),
        ("INSERT INTO obligaciones_cliente (cliente_id, venta_id, movimiento_cargo_id, monto_original_centavos,"
         " saldo_pendiente_centavos, fecha_origen) VALUES (?, ?, 999, 5, 5, 'x')", (e.cliente.id, venta.id)),
    ]
    for sql, parametros in ataques:
        with pytest.raises(sqlite3.IntegrityError):
            escribir(e.ruta, sql, parametros)
    assert violaciones_de_invariantes(e.ruta) == []


def test_el_esquema_rechaza_un_cargo_de_empresa_sin_comprador_y_un_contado_fuera_de_rango(e):
    empresa = _empresa(e)
    sesion = consultar(e.ruta, "SELECT id FROM sesiones_caja WHERE estado = 'ABIERTA'")[0]["id"]
    with pytest.raises(sqlite3.IntegrityError, match="menor que su total"):
        escribir(e.ruta, "INSERT INTO ventas (total_centavos, tipo_pago, sesion_caja_id, cliente_id, pago_contado_centavos)"
                         " VALUES (100, 'CUENTA_CORRIENTE', ?, ?, 100)", (sesion, empresa.id))
    escribir(e.ruta, "INSERT INTO ventas (id, total_centavos, tipo_pago, sesion_caja_id, cliente_id)"
                     " VALUES (77, 100, 'CUENTA_CORRIENTE', ?, ?)", (sesion, empresa.id))
    with pytest.raises(sqlite3.IntegrityError, match="quién compró"):
        escribir(e.ruta, "INSERT INTO movimientos_cuenta (cliente_id, tipo, monto_centavos, venta_id)"
                         " VALUES (?, 'CARGO', 100, 77)", (empresa.id,))
