"""Relación producto-proveedor (V1.4): alta/baja de vínculo, principal, vínculo automático en la
compra, proveedores inactivos, auditoría, permisos, integridad y reposición con proveedor."""

import threading

import pytest

from db.repositorios import auditoria as repositorio_auditoria
from domain.compra import ItemCompra
from excepciones import (
    ClaveIdempotenciaReutilizadaError,
    DatosInvalidosError,
    PermisoDenegadoError,
    ProductoNoEncontradoError,
    ProveedorNoEncontradoError,
    VinculoProveedorExistenteError,
    VinculoProveedorNoEncontradoError,
)
from services import servicio_compras, servicio_proveedores, servicio_stock
from tests.utilidades_clientes import consultar, contar, crear_owner, crear_producto, crear_usuario, escribir


@pytest.fixture
def e(base_datos_temporal):
    owner = crear_owner()
    return type(
        "Escenario",
        (),
        {
            "ruta": base_datos_temporal,
            "owner": owner,
            "cajera": crear_usuario("cajera", "CASHIER"),
            "prov_a": servicio_proveedores.crear_proveedor("Proveedor A", usuario_id=owner.id),
            "prov_b": servicio_proveedores.crear_proveedor("Proveedor B", usuario_id=owner.id),
            "p1": crear_producto("7790000000001"),
            "p2": crear_producto("7790000000002"),
        },
    )


def _comprar(e, proveedor, producto, costo=100, cantidad=2, clave=None):
    return servicio_compras.registrar_compra(
        proveedor.id, e.owner.id, [ItemCompra(producto.id, cantidad, costo)], clave_idempotencia=clave
    )


def _vinculos(e, producto):
    return {
        f["proveedor_id"]: bool(f["es_principal"])
        for f in consultar(e.ruta, "SELECT * FROM producto_proveedor WHERE producto_id = ?", (producto.id,))
    }


def _acciones(prefijo=""):
    return [x.accion for x in repositorio_auditoria.listar() if x.accion.startswith(prefijo)]


# --- vínculo explícito --------------------------------------------------------------------------------


def test_vincular_crea_el_vinculo_con_codigo_y_el_primero_queda_principal(e):
    vinculo = servicio_proveedores.vincular_producto(e.prov_a.id, e.p1.id, e.owner.id, "  COD-9 ")

    assert (vinculo.codigo_proveedor, vinculo.es_principal) == ("COD-9", True)
    assert _vinculos(e, e.p1) == {e.prov_a.id: True}


def test_el_segundo_proveedor_vinculado_no_es_principal(e):
    servicio_proveedores.vincular_producto(e.prov_a.id, e.p1.id, e.owner.id)
    segundo = servicio_proveedores.vincular_producto(e.prov_b.id, e.p1.id, e.owner.id)

    assert segundo.es_principal is False
    assert _vinculos(e, e.p1) == {e.prov_a.id: True, e.prov_b.id: False}


def test_vincular_dos_veces_el_mismo_par_falla(e):
    servicio_proveedores.vincular_producto(e.prov_a.id, e.p1.id, e.owner.id)

    with pytest.raises(VinculoProveedorExistenteError):
        servicio_proveedores.vincular_producto(e.prov_a.id, e.p1.id, e.owner.id)
    assert contar(e.ruta, "producto_proveedor") == 1


def test_codigo_del_proveedor_demasiado_largo_se_rechaza(e):
    with pytest.raises(DatosInvalidosError):
        servicio_proveedores.vincular_producto(e.prov_a.id, e.p1.id, e.owner.id, "x" * 61)
    assert contar(e.ruta, "producto_proveedor") == 0


def test_no_se_vincula_un_proveedor_ni_un_producto_inactivo_o_inexistente(e):
    servicio_proveedores.eliminar_proveedor(e.prov_b.id)  # sin compras: se borra físicamente
    with pytest.raises(ProveedorNoEncontradoError):
        servicio_proveedores.vincular_producto(e.prov_b.id, e.p1.id, e.owner.id)
    with pytest.raises(ProductoNoEncontradoError):
        servicio_proveedores.vincular_producto(e.prov_a.id, 9999, e.owner.id)
    servicio_stock.eliminar_producto(e.p2.id)
    with pytest.raises(ProductoNoEncontradoError):
        servicio_proveedores.vincular_producto(e.prov_a.id, e.p2.id, e.owner.id)


def test_quitar_vinculo_no_promueve_a_otro_principal(e):
    servicio_proveedores.vincular_producto(e.prov_a.id, e.p1.id, e.owner.id)
    servicio_proveedores.vincular_producto(e.prov_b.id, e.p1.id, e.owner.id)

    servicio_proveedores.quitar_vinculo(e.prov_a.id, e.p1.id, e.owner.id)

    assert _vinculos(e, e.p1) == {e.prov_b.id: False}


def test_quitar_un_vinculo_inexistente_falla(e):
    with pytest.raises(VinculoProveedorNoEncontradoError):
        servicio_proveedores.quitar_vinculo(e.prov_a.id, e.p1.id, e.owner.id)


# --- principal ------------------------------------------------------------------------------------------


def test_cambio_explicito_de_principal_deja_un_unico_principal(e):
    servicio_proveedores.vincular_producto(e.prov_a.id, e.p1.id, e.owner.id)
    servicio_proveedores.vincular_producto(e.prov_b.id, e.p1.id, e.owner.id)

    resultado = servicio_proveedores.establecer_principal(e.prov_b.id, e.p1.id, e.owner.id)

    assert resultado.es_principal is True
    assert _vinculos(e, e.p1) == {e.prov_a.id: False, e.prov_b.id: True}


def test_marcar_principal_al_que_ya_lo_es_no_cambia_ni_audita(e):
    servicio_proveedores.vincular_producto(e.prov_a.id, e.p1.id, e.owner.id)

    servicio_proveedores.establecer_principal(e.prov_a.id, e.p1.id, e.owner.id)

    assert _acciones("PRODUCTO_PROVEEDOR_PRINCIPAL") == []


def test_principal_de_un_vinculo_inexistente_falla(e):
    with pytest.raises(VinculoProveedorNoEncontradoError):
        servicio_proveedores.establecer_principal(e.prov_a.id, e.p1.id, e.owner.id)


# --- permisos -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("operacion", ["vincular", "quitar", "principal"])
def test_solo_un_owner_gestiona_vinculos_a_nivel_de_servicio(e, operacion):
    servicio_proveedores.vincular_producto(e.prov_a.id, e.p1.id, e.owner.id)
    funciones = {
        "vincular": lambda usuario: servicio_proveedores.vincular_producto(e.prov_b.id, e.p1.id, usuario),
        "quitar": lambda usuario: servicio_proveedores.quitar_vinculo(e.prov_a.id, e.p1.id, usuario),
        "principal": lambda usuario: servicio_proveedores.establecer_principal(e.prov_a.id, e.p1.id, usuario),
    }

    with pytest.raises(PermisoDenegadoError):
        funciones[operacion](e.cajera.id)
    with pytest.raises(PermisoDenegadoError):
        funciones[operacion](9999)
    assert _vinculos(e, e.p1) == {e.prov_a.id: True}


# --- compra ---------------------------------------------------------------------------------------------


def test_la_compra_crea_el_vinculo_inexistente_y_el_primero_queda_principal(e):
    _comprar(e, e.prov_a, e.p1)

    assert _vinculos(e, e.p1) == {e.prov_a.id: True}


def test_la_compra_a_otro_proveedor_no_cambia_el_principal_existente(e):
    _comprar(e, e.prov_a, e.p1)
    _comprar(e, e.prov_b, e.p1)

    assert _vinculos(e, e.p1) == {e.prov_a.id: True, e.prov_b.id: False}


def test_una_compra_con_un_vinculo_existente_no_lo_duplica_ni_lo_cambia(e):
    servicio_proveedores.vincular_producto(e.prov_a.id, e.p1.id, e.owner.id, "COD-1")
    _comprar(e, e.prov_a, e.p1)

    filas = consultar(e.ruta, "SELECT codigo_proveedor, es_principal FROM producto_proveedor")
    assert [tuple(f) for f in filas] == [("COD-1", 1)]


def test_una_compra_a_un_proveedor_sin_principal_vinculado_a_mano_no_lo_altera(e):
    servicio_proveedores.vincular_producto(e.prov_a.id, e.p1.id, e.owner.id)
    servicio_proveedores.vincular_producto(e.prov_b.id, e.p1.id, e.owner.id)
    servicio_proveedores.quitar_vinculo(e.prov_a.id, e.p1.id, e.owner.id)  # queda B sin principal

    _comprar(e, e.prov_b, e.p1)  # el vínculo B ya existe: no se toca

    assert _vinculos(e, e.p1) == {e.prov_b.id: False}


def test_un_producto_sin_principal_toma_como_principal_a_un_proveedor_nuevo_de_una_compra(e):
    servicio_proveedores.vincular_producto(e.prov_a.id, e.p1.id, e.owner.id)
    servicio_proveedores.quitar_vinculo(e.prov_a.id, e.p1.id, e.owner.id)

    _comprar(e, e.prov_b, e.p1)

    assert _vinculos(e, e.p1) == {e.prov_b.id: True}


def test_compra_con_varios_productos_vincula_cada_uno(e):
    servicio_compras.registrar_compra(
        e.prov_a.id, e.owner.id, [ItemCompra(e.p1.id, 1, 10), ItemCompra(e.p2.id, 1, 20)]
    )

    assert _vinculos(e, e.p1) == {e.prov_a.id: True}
    assert _vinculos(e, e.p2) == {e.prov_a.id: True}


def test_compra_idempotente_no_duplica_vinculos_ni_stock(e):
    _comprar(e, e.prov_a, e.p1, clave="clave-1")
    _comprar(e, e.prov_a, e.p1, clave="clave-1")

    assert contar(e.ruta, "producto_proveedor") == 1
    assert contar(e.ruta, "compras") == 1
    assert servicio_stock.obtener_por_id(e.p1.id).stock_actual == 100 + 2
    with pytest.raises(ClaveIdempotenciaReutilizadaError):
        _comprar(e, e.prov_b, e.p1, clave="clave-1")
    assert _vinculos(e, e.p1) == {e.prov_a.id: True}


def test_una_compra_fallida_no_deja_vinculos(e):
    with pytest.raises(ProductoNoEncontradoError):
        servicio_compras.registrar_compra(
            e.prov_a.id, e.owner.id, [ItemCompra(e.p1.id, 1, 10), ItemCompra(9999, 1, 10)]
        )

    assert contar(e.ruta, "producto_proveedor") == 0


def test_compras_concurrentes_a_distintos_proveedores_dejan_un_solo_principal(e):
    errores = []

    def comprar(proveedor):
        try:
            _comprar(e, proveedor, e.p1)
        except Exception as error:  # noqa: BLE001 - se reporta en el hilo principal
            errores.append(error)

    hilos = [threading.Thread(target=comprar, args=(p,)) for p in (e.prov_a, e.prov_b, e.prov_a, e.prov_b)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    assert errores == []
    vinculos = _vinculos(e, e.p1)
    assert set(vinculos) == {e.prov_a.id, e.prov_b.id} and list(vinculos.values()).count(True) == 1


# --- proveedor inactivo e integridad ----------------------------------------------------------------------


def test_dar_de_baja_a_un_proveedor_vinculado_lo_desactiva_y_conserva_el_vinculo(e):
    servicio_proveedores.vincular_producto(e.prov_a.id, e.p1.id, e.owner.id)

    baja_logica = servicio_proveedores.eliminar_proveedor(e.prov_a.id, usuario_id=e.owner.id)

    assert baja_logica is True
    assert _vinculos(e, e.p1) == {e.prov_a.id: True}
    assert servicio_proveedores.obtener_ficha(e.prov_a.id).proveedor.activo is False


def test_un_proveedor_inactivo_no_se_puede_vincular_pero_su_vinculo_previo_se_puede_quitar_o_reactivar(e):
    servicio_proveedores.vincular_producto(e.prov_a.id, e.p1.id, e.owner.id)
    servicio_proveedores.eliminar_proveedor(e.prov_a.id)

    with pytest.raises(ProveedorNoEncontradoError):
        servicio_proveedores.vincular_producto(e.prov_a.id, e.p2.id, e.owner.id)
    servicio_proveedores.reactivar_proveedor(e.prov_a.id, usuario_id=e.owner.id)
    servicio_proveedores.vincular_producto(e.prov_a.id, e.p2.id, e.owner.id)
    assert _vinculos(e, e.p2) == {e.prov_a.id: True}


def test_un_producto_vinculado_pasa_a_baja_logica_y_lo_informa_como_motivo(e):
    servicio_proveedores.vincular_producto(e.prov_a.id, e.p1.id, e.owner.id)

    baja_logica = servicio_stock.eliminar_producto(e.p1.id, usuario_id=e.owner.id)

    assert baja_logica is True
    assert "proveedores vinculados" in servicio_stock.motivos_de_conservacion(e.p1.id)
    assert contar(e.ruta, "productos", f"id = {e.p1.id} AND activo = 0") == 1


def test_no_se_puede_borrar_a_mano_un_producto_ni_un_proveedor_vinculado(e):
    servicio_proveedores.vincular_producto(e.prov_a.id, e.p1.id, e.owner.id)

    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        escribir(e.ruta, "DELETE FROM productos WHERE id = ?", (e.p1.id,))
    with pytest.raises(sqlite3.IntegrityError):
        escribir(e.ruta, "DELETE FROM proveedores WHERE id = ?", (e.prov_a.id,))


# --- auditoría ---------------------------------------------------------------------------------------------


def test_auditoria_de_proveedores_y_vinculos(e):
    servicio_proveedores.actualizar_proveedor(e.prov_a.id, "Proveedor A2", usuario_id=e.owner.id)
    servicio_proveedores.vincular_producto(e.prov_a.id, e.p1.id, e.owner.id)
    servicio_proveedores.vincular_producto(e.prov_b.id, e.p1.id, e.owner.id)
    servicio_proveedores.establecer_principal(e.prov_b.id, e.p1.id, e.owner.id)
    servicio_proveedores.quitar_vinculo(e.prov_a.id, e.p1.id, e.owner.id)
    servicio_proveedores.eliminar_proveedor(e.prov_b.id, usuario_id=e.owner.id)
    servicio_proveedores.reactivar_proveedor(e.prov_b.id, usuario_id=e.owner.id)

    assert sorted(_acciones("PROVEEDOR") + _acciones("PRODUCTO_PROVEEDOR")) == sorted(
        [
            "PROVEEDOR_CREADO",
            "PROVEEDOR_CREADO",
            "PROVEEDOR_EDITADO",
            "PRODUCTO_PROVEEDOR_VINCULADO",
            "PRODUCTO_PROVEEDOR_VINCULADO",
            "PRODUCTO_PROVEEDOR_PRINCIPAL",
            "PRODUCTO_PROVEEDOR_QUITADO",
            "PROVEEDOR_BAJA",
            "PROVEEDOR_REACTIVADO",
        ]
    )


def test_el_vinculo_automatico_de_una_compra_no_genera_auditoria_propia(e):
    _comprar(e, e.prov_a, e.p1)

    assert _acciones("PRODUCTO_PROVEEDOR") == []
    assert _acciones("COMPRA_REGISTRADA") == ["COMPRA_REGISTRADA"]


def test_si_la_auditoria_falla_el_vinculo_se_revierte(e, monkeypatch):
    def fallar(*_args, **_kwargs):
        raise RuntimeError("falla simulada")

    with monkeypatch.context() as parche:
        parche.setattr(repositorio_auditoria, "registrar_en_conexion", fallar)
        with pytest.raises(RuntimeError):
            servicio_proveedores.vincular_producto(e.prov_a.id, e.p1.id, e.owner.id)

    assert contar(e.ruta, "producto_proveedor") == 0


# --- búsqueda y ficha -------------------------------------------------------------------------------------


def test_busqueda_por_nombre_contacto_telefono_y_email_con_comodines_literales(e):
    servicio_proveedores.crear_proveedor("100% Lácteos", contacto_nombre="Marta", telefono="4444-1", email="a@x.com")
    servicio_proveedores.crear_proveedor("Snack_Bar")

    ids = lambda texto, inactivos=False: [  # noqa: E731
        p.nombre for p in servicio_proveedores.buscar_proveedores(texto, inactivos)
    ]

    assert ids("lácteos") == ["100% Lácteos"]
    assert ids("MARTA") == ["100% Lácteos"]
    assert ids("4444") == ["100% Lácteos"]
    assert ids("a@x") == ["100% Lácteos"]
    assert ids("%") == ["100% Lácteos"] and ids("_") == ["Snack_Bar"]
    assert ids("zzz") == []
    assert ids("") == ["100% Lácteos", "Proveedor A", "Proveedor B", "Snack_Bar"]


def test_la_busqueda_excluye_inactivos_salvo_que_se_pidan(e):
    inactivo = servicio_proveedores.crear_proveedor("Viejo Mayorista")
    _comprar(e, inactivo, e.p1)
    servicio_proveedores.eliminar_proveedor(inactivo.id)

    assert servicio_proveedores.buscar_proveedores("viejo") == []
    assert [p.nombre for p in servicio_proveedores.buscar_proveedores("viejo", incluir_inactivos=True)] == [
        "Viejo Mayorista"
    ]


def test_ficha_con_productos_e_historial_de_compras_mas_reciente_primero(e):
    servicio_proveedores.vincular_producto(e.prov_a.id, e.p2.id, e.owner.id)
    primera = _comprar(e, e.prov_a, e.p1)
    segunda = _comprar(e, e.prov_a, e.p1)
    _comprar(e, e.prov_b, e.p1)

    ficha = servicio_proveedores.obtener_ficha(e.prov_a.id)

    assert [c.id for c in ficha.compras] == [segunda.id, primera.id]
    assert [(i.producto_nombre, i.vinculo.es_principal) for i in ficha.productos] == [
        ("Producto 7790000000001", True),
        ("Producto 7790000000002", True),
    ]


def test_ficha_de_un_proveedor_inexistente_falla(e):
    with pytest.raises(ProveedorNoEncontradoError):
        servicio_proveedores.obtener_ficha(9999)


# --- reposición ----------------------------------------------------------------------------------------------


def _con_stock_minimo(e, producto, minimo, stock):
    escribir(e.ruta, "UPDATE productos SET stock_minimo = ?, stock_actual = ? WHERE id = ?", (minimo, stock, producto.id))


def test_reposicion_muestra_el_proveedor_principal_y_su_ultimo_costo(e):
    _comprar(e, e.prov_a, e.p1, costo=100)
    _comprar(e, e.prov_a, e.p1, costo=130)  # último costo con A
    _comprar(e, e.prov_b, e.p1, costo=999)  # costo vigente ahora: 999, pero el principal es A
    _con_stock_minimo(e, e.p1, minimo=20, stock=5)

    (sugerencia,) = servicio_stock.listar_reposicion()

    assert (sugerencia.proveedor_id, sugerencia.proveedor_nombre) == (e.prov_a.id, "Proveedor A")
    assert sugerencia.costo_unitario_centavos == 130 and sugerencia.costo_es_del_proveedor is True
    assert sugerencia.costo_estimado_centavos == 15 * 130


def test_el_ultimo_costo_se_busca_por_detalle_compra_id_maximo(e):
    _comprar(e, e.prov_a, e.p1, costo=100)
    _comprar(e, e.prov_a, e.p1, costo=80)
    # Fecha de la primera compra adelantada: no debe influir, manda el id.
    escribir(e.ruta, "UPDATE compras SET fecha = '2099-01-01 00:00:00' WHERE id = 1")
    _con_stock_minimo(e, e.p1, minimo=10, stock=0)

    assert servicio_stock.listar_reposicion()[0].costo_unitario_centavos == 80


def test_reposicion_usa_el_costo_vigente_si_no_hay_principal_o_nunca_se_le_compro(e):
    servicio_proveedores.vincular_producto(e.prov_a.id, e.p1.id, e.owner.id)  # principal sin compras
    _con_stock_minimo(e, e.p1, minimo=10, stock=0)
    _con_stock_minimo(e, e.p2, minimo=10, stock=0)

    sugerencias = {s.producto_id: s for s in servicio_stock.listar_reposicion()}

    assert sugerencias[e.p1.id].proveedor_nombre == "Proveedor A"
    assert sugerencias[e.p1.id].costo_es_del_proveedor is False
    assert sugerencias[e.p1.id].costo_unitario_centavos == 10  # costo vigente del producto
    assert sugerencias[e.p2.id].proveedor_nombre is None
    assert sugerencias[e.p2.id].costo_unitario_centavos == 10


def test_reposicion_marca_al_proveedor_principal_inactivo_y_no_crea_compras(e):
    _comprar(e, e.prov_a, e.p1, costo=50)
    servicio_proveedores.eliminar_proveedor(e.prov_a.id)
    _con_stock_minimo(e, e.p1, minimo=200, stock=1)
    compras_antes = contar(e.ruta, "compras")

    (sugerencia,) = servicio_stock.listar_reposicion()

    assert sugerencia.proveedor_activo is False and sugerencia.costo_unitario_centavos == 50
    assert contar(e.ruta, "compras") == compras_antes
