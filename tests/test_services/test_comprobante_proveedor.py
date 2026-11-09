"""Comprobante del proveedor en una compra (migración 049): tipo + número + fecha opcionales, distintos del id
interno; sin duplicados activos por proveedor; editable (auditado) solo en compras activas; búsqueda por
referencia; compras históricas sin inferencia; reintento idempotente que compara también el comprobante."""

import sqlite3

import pytest

import db.conexion as modulo_conexion
from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from domain.compra import ItemCompra, validar_comprobante_proveedor
from excepciones import (
    ClaveIdempotenciaReutilizadaError,
    CompraYaAnuladaError,
    ComprobanteProveedorDuplicadoError,
    DatosInvalidosError,
    ErrorBaseDatos,
)
from services import servicio_compras, servicio_proveedores, servicio_stock
from tests.test_interfaces_web._asgi_cliente import solicitud
from tests.utilidades_clientes import usuario_logueado


class TestDominio:
    def test_vacio_es_sin_comprobante(self):
        assert not validar_comprobante_proveedor("", "", "").cargado
        assert validar_comprobante_proveedor(None, None, None).texto == "Sin comprobante"

    def test_normaliza_y_describe(self):
        c = validar_comprobante_proveedor("FACTURA", "  0001 - 00012345 ", "2026-11-01", "2026-11-03 10:00:00")
        assert (c.tipo, c.numero, c.fecha) == ("FACTURA", "0001 - 00012345", "2026-11-01")
        assert c.texto == "Factura 0001 - 00012345 (2026-11-01)"

    @pytest.mark.parametrize(
        "tipo, numero, fecha, fragmento",
        [
            ("FACTURA", "", "", "tipo y el número"),
            ("", "123", "", "tipo y el número"),
            ("", "", "2026-01-01", "requiere su tipo y número"),
            ("NOTA", "1", "", "no es válido"),
            ("REMITO", "x" * 41, "", "40 caracteres"),
            ("REMITO", "1", "2026-13-01", "no es válida"),
            ("REMITO", "1", "2026-11-04", "posterior al día de la compra"),
        ],
    )
    def test_rechazos(self, tipo, numero, fecha, fragmento):
        with pytest.raises(DatosInvalidosError, match=fragmento):
            validar_comprobante_proveedor(tipo, numero, fecha, "2026-11-03 09:00:00")


@pytest.fixture
def escenario(caja_abierta):
    owner, cookies = usuario_logueado("OWNER", "duenio")
    proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
    producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 200, stock_actual=5)
    return owner, cookies, proveedor, producto


def _comprar(owner, proveedor, producto, **extra):
    datos = {"origen_pago": "FUERA_DE_CAJA"}
    datos.update(extra)
    return servicio_compras.registrar_compra(proveedor.id, owner.id, [ItemCompra(producto.id, 2, 120)], **datos)


class TestServicio:
    def test_se_registra_con_comprobante_y_queda_en_la_auditoria_y_el_historial(self, escenario):
        owner, _, proveedor, producto = escenario
        compra = _comprar(owner, proveedor, producto, comprobante_tipo="FACTURA", comprobante_numero="0001-00000077", comprobante_fecha="2026-11-01")
        assert compra.comprobante.texto == "Factura 0001-00000077 (2026-11-01)"
        assert compra.id != 77  # el id interno no es el número del comprobante
        assert "comprobante Factura 0001-00000077" in repositorio_auditoria.listar(accion="COMPRA_REGISTRADA")[0].resumen
        resumen = servicio_compras.obtener_resumen_por_id(compra.id)
        assert resumen.comprobante_numero == "0001-00000077"
        pagina = servicio_compras.listar_pagina(referencia="00000077")
        assert [c.id for c in pagina.compras] == [compra.id]
        assert servicio_compras.listar_pagina(referencia="zzz").total == 0
        assert servicio_compras.listar_lineas_para_exportar(referencia="0001")[0].comprobante_numero == "0001-00000077"

    def test_sin_comprobante_sigue_funcionando_como_antes(self, escenario):
        owner, _, proveedor, producto = escenario
        compra = _comprar(owner, proveedor, producto)
        assert compra.comprobante_tipo is None and not compra.comprobante.cargado

    def test_el_mismo_comprobante_del_mismo_proveedor_se_rechaza_hasta_anular(self, escenario):
        owner, _, proveedor, producto = escenario
        primera = _comprar(owner, proveedor, producto, comprobante_tipo="FACTURA", comprobante_numero="A-1")
        with pytest.raises(ComprobanteProveedorDuplicadoError, match=f"compra #{primera.id}"):
            _comprar(owner, proveedor, producto, comprobante_tipo="FACTURA", comprobante_numero="A-1")
        # Otro proveedor u otro tipo con el mismo número no chocan.
        otro = servicio_proveedores.crear_proveedor("Otro SA")
        _comprar(owner, otro, producto, comprobante_tipo="FACTURA", comprobante_numero="A-1")
        _comprar(owner, proveedor, producto, comprobante_tipo="REMITO", comprobante_numero="A-1")
        servicio_compras.anular_compra(primera.id, "ERROR_CARGA", None, owner.id, reintegro_a_caja=None)
        repetida = _comprar(owner, proveedor, producto, comprobante_tipo="FACTURA", comprobante_numero="A-1")
        assert repetida.comprobante_numero == "A-1"
        # La anulada conserva su comprobante.
        assert servicio_compras.obtener_por_id(primera.id).comprobante_numero == "A-1"

    def test_el_reintento_idempotente_compara_el_comprobante(self, escenario):
        owner, _, proveedor, producto = escenario
        primera = _comprar(owner, proveedor, producto, clave_idempotencia="k1", comprobante_tipo="REMITO", comprobante_numero="R-9")
        igual = _comprar(owner, proveedor, producto, clave_idempotencia="k1", comprobante_tipo="REMITO", comprobante_numero="R-9")
        assert igual.id == primera.id
        with pytest.raises(ClaveIdempotenciaReutilizadaError):
            _comprar(owner, proveedor, producto, clave_idempotencia="k1", comprobante_tipo="REMITO", comprobante_numero="R-10")

    def test_actualizar_comprobante_audita_y_respeta_duplicados_y_anuladas(self, escenario):
        owner, _, proveedor, producto = escenario
        compra = _comprar(owner, proveedor, producto)
        assert servicio_compras.actualizar_comprobante_compra(compra.id, owner.id, "TICKET", "T-5", "") is True
        assert servicio_compras.actualizar_comprobante_compra(compra.id, owner.id, "TICKET", "T-5", "") is False
        resumen = repositorio_auditoria.listar(accion="COMPROBANTE_COMPRA_MODIFICADO")
        assert len(resumen) == 1 and "Sin comprobante → Ticket T-5" in resumen[0].resumen
        otra = _comprar(owner, proveedor, producto)
        with pytest.raises(ComprobanteProveedorDuplicadoError):
            servicio_compras.actualizar_comprobante_compra(otra.id, owner.id, "TICKET", "T-5", "")
        with pytest.raises(DatosInvalidosError):
            servicio_compras.actualizar_comprobante_compra(compra.id, owner.id, "TICKET", "", "")
        # Quitar el comprobante es válido (todo vacío).
        assert servicio_compras.actualizar_comprobante_compra(compra.id, owner.id, "", "", "") is True
        assert servicio_compras.obtener_por_id(compra.id).comprobante_tipo is None
        servicio_compras.anular_compra(otra.id, "ERROR_CARGA", None, owner.id, reintegro_a_caja=None)
        with pytest.raises(CompraYaAnuladaError):
            servicio_compras.actualizar_comprobante_compra(otra.id, owner.id, "TICKET", "T-6", "")

    def test_la_base_exige_tipo_y_numero_juntos_y_el_indice_unico(self, escenario):
        owner, _, proveedor, producto = escenario
        compra = _comprar(owner, proveedor, producto, comprobante_tipo="FACTURA", comprobante_numero="F-1")
        with pytest.raises(ErrorBaseDatos, match="tipo y número"):
            with obtener_conexion() as conexion:
                conexion.execute("UPDATE compras SET comprobante_tipo = NULL WHERE id = ?", (compra.id,))
        otra = _comprar(owner, proveedor, producto)
        with pytest.raises(ErrorBaseDatos):
            with obtener_conexion() as conexion:
                conexion.execute("UPDATE compras SET comprobante_tipo = 'FACTURA', comprobante_numero = 'F-1' WHERE id = ?", (otra.id,))


class TestWeb:
    def test_alta_detalle_listado_y_edicion_por_http(self, escenario):
        owner, cookies, proveedor, producto = escenario
        formulario = solicitud("GET", "/compras/nueva", cookies=cookies).texto
        assert 'name="comprobante_numero"' in formulario
        respuesta = solicitud(
            "POST", "/compras/nueva", cookies=cookies,
            formulario={
                "proveedor_id": str(proveedor.id), "producto_id": str(producto.id), "cantidad": "1", "costo_unitario": "1.00",
                "origen_pago": "FUERA_DE_CAJA", "clave_idempotencia": "k-web", "comprobante_tipo": "FACTURA",
                "comprobante_numero": "0003-00000100", "comprobante_fecha": "",
            },
        )
        assert respuesta.status == 303
        detalle = solicitud("GET", "/compras/1", cookies=cookies).texto
        assert 'id="compra-comprobante"' in detalle and "Factura 0003-00000100" in detalle and "número interno de esta compra en Twinx es #1" in detalle
        listado = solicitud("GET", "/compras?referencia=00000100", cookies=cookies).texto
        assert "Factura 0003-00000100" in listado
        assert 'data-compra="1"' in listado
        assert 'data-compra="1"' not in solicitud("GET", "/compras?referencia=nada", cookies=cookies).texto
        csv = solicitud("GET", "/compras/exportar?referencia=0003", cookies=cookies).texto
        assert "comprobante_numero" in csv and "0003-00000100" in csv

        assert "Comprobante actual" in solicitud("GET", "/compras/1/comprobante", cookies=cookies).texto
        editado = solicitud("POST", "/compras/1/comprobante", cookies=cookies, formulario={"comprobante_tipo": "REMITO", "comprobante_numero": "R-1", "comprobante_fecha": "2026-01-01"})
        assert editado.status == 303 and "success" in editado.header("location")
        assert "Remito R-1 (2026-01-01)" in solicitud("GET", "/compras/1", cookies=cookies).texto
        sin_cambios = solicitud("POST", "/compras/1/comprobante", cookies=cookies, formulario={"comprobante_tipo": "REMITO", "comprobante_numero": "R-1", "comprobante_fecha": "2026-01-01"})
        assert "warning" in sin_cambios.header("location")

    def test_duplicado_por_http_no_crea_la_compra(self, escenario):
        owner, cookies, proveedor, producto = escenario
        _comprar(owner, proveedor, producto, comprobante_tipo="FACTURA", comprobante_numero="DUP-1")
        respuesta = solicitud(
            "POST", "/compras/nueva", cookies=cookies,
            formulario={
                "proveedor_id": str(proveedor.id), "producto_id": str(producto.id), "cantidad": "1", "costo_unitario": "1.00",
                "origen_pago": "FUERA_DE_CAJA", "clave_idempotencia": "k-dup", "comprobante_tipo": "FACTURA",
                "comprobante_numero": "DUP-1", "comprobante_fecha": "",
            },
        )
        assert respuesta.status < 500
        assert servicio_compras.listar_pagina().total == 1


def test_la_migracion_049_deja_las_compras_historicas_sin_comprobante(tmp_path, monkeypatch):
    ruta = tmp_path / "048.db"
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
    con = sqlite3.connect(ruta)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(modulo_conexion._TABLA_MIGRACIONES)
    for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
        if migracion.name < "049_comprobante_proveedor_compras.sql":
            con.executescript(migracion.read_text(encoding="utf-8"))
            con.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (migracion.name,))
    con.execute("INSERT INTO usuarios (nombre_usuario, nombre_completo, password_hash, rol) VALUES ('a', 'A', 'h', 'OWNER')")
    con.execute("INSERT INTO proveedores (nombre) VALUES ('Distribuidora')")
    con.execute("INSERT INTO compras (proveedor_id, usuario_id, total_centavos, observaciones) VALUES (1, 1, 500, 'FC 123')")
    con.commit()
    con.close()

    modulo_conexion.inicializar_base_datos()
    modulo_conexion.inicializar_base_datos()

    con = sqlite3.connect(ruta)
    try:
        assert con.execute("SELECT comprobante_tipo, comprobante_numero, comprobante_fecha, observaciones FROM compras").fetchall() == [(None, None, None, "FC 123")]
        assert con.execute("SELECT COUNT(*) FROM schema_migraciones WHERE nombre_archivo = '049_comprobante_proveedor_compras.sql'").fetchone()[0] == 1
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        con.close()
