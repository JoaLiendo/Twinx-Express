"""Cierre operativo B1: DNI opcional de personas (migración 050) y reporte de ventas por cliente."""

import sqlite3
from datetime import date

import pytest

import db.conexion as modulo_conexion
from db.conexion import obtener_conexion
from domain.cliente import normalizar_dni
from domain.venta import ItemVenta
from excepciones import DatosInvalidosError, DniClienteDuplicadoError, ErrorBaseDatos
from services import servicio_clientes, servicio_cuenta_corriente, servicio_stock, servicio_ventas
from tests.test_interfaces_web._asgi_cliente import solicitud
from tests.utilidades_clientes import habilitar_credito_general, usuario_logueado

EMPRESA = {
    "tipo_cliente": "EMPRESA", "condicion_fiscal": "RESPONSABLE_INSCRIPTO", "razon_social": "Acme SA",
    "cuit": "30-71234567-1", "direccion": "Calle 1", "telefono": "11", "contacto": "Juan",
}


class TestDominio:
    @pytest.mark.parametrize("entrada, esperado", [("12.345.678", "12345678"), (" 1234567 ", "1234567"), ("", None), (None, None), ("12-345-678", "12345678")])
    def test_normaliza(self, entrada, esperado):
        assert normalizar_dni(entrada) == esperado

    @pytest.mark.parametrize("entrada", ["123456", "123456789", "12A45678", "abc", "20-12345678-6"])
    def test_rechaza(self, entrada):
        with pytest.raises(DatosInvalidosError, match="7 u 8 dígitos"):
            normalizar_dni(entrada)


@pytest.fixture
def owner(caja_abierta):
    usuario, cookies = usuario_logueado("OWNER", "duenio")
    habilitar_credito_general(usuario.id)
    return usuario, cookies


class TestServicio:
    def test_persona_con_dni_se_guarda_normalizado_y_se_busca(self, owner):
        usuario, _ = owner
        ana = servicio_clientes.crear_cliente("Ana", usuario.id, dni="12.345.678")
        assert ana.dni == "12345678" and ana.dni_formateado == "12.345.678"
        assert servicio_clientes.obtener_cliente(ana.id).dni == "12345678"
        assert [c.cliente.id for c in servicio_clientes.listar_clientes_con_saldo("45678")] == [ana.id]
        assert [c.cliente.id for c in servicio_clientes.buscar_clientes_activos_para_venta("1234")] == [ana.id]

    def test_sin_dni_sigue_siendo_valido_y_no_se_infiere(self, owner):
        usuario, _ = owner
        assert servicio_clientes.crear_cliente("Beto", usuario.id).dni is None
        assert servicio_clientes.crear_cliente("Carla", usuario.id, cuit="20-12345678-6").dni is None

    def test_dni_duplicado_se_rechaza_con_mensaje_claro(self, owner):
        usuario, _ = owner
        servicio_clientes.crear_cliente("Ana", usuario.id, dni="12345678")
        with pytest.raises(DniClienteDuplicadoError, match="12.345.678"):
            servicio_clientes.crear_cliente("Ana (otra vez)", usuario.id, dni="12.345.678")
        beto = servicio_clientes.crear_cliente("Beto", usuario.id)
        with pytest.raises(DniClienteDuplicadoError):
            servicio_clientes.editar_cliente(beto.id, "Beto", usuario.id, dni="12345678")

    def test_empresa_no_lleva_dni_ni_por_servicio_ni_por_sql(self, owner):
        usuario, _ = owner
        with pytest.raises(DatosInvalidosError, match="CUIT, no por DNI"):
            servicio_clientes.crear_cliente("Acme", usuario.id, dni="12345678", **EMPRESA)
        acme = servicio_clientes.crear_cliente("Acme", usuario.id, **EMPRESA)
        with pytest.raises(ErrorBaseDatos, match="CUIT, no por DNI"):
            with obtener_conexion() as conexion:
                conexion.execute("UPDATE clientes SET dni = '12345678' WHERE id = ?", (acme.id,))
        with pytest.raises(ErrorBaseDatos):
            with obtener_conexion() as conexion:
                conexion.execute("UPDATE clientes SET dni = '12A' WHERE id = ?", (acme.id,))

    def test_editar_quita_o_cambia_el_dni(self, owner):
        usuario, _ = owner
        ana = servicio_clientes.crear_cliente("Ana", usuario.id, dni="12345678")
        assert servicio_clientes.editar_cliente(ana.id, "Ana", usuario.id, dni="").dni is None
        assert servicio_clientes.editar_cliente(ana.id, "Ana", usuario.id, dni="7654321").dni == "7654321"


class TestWebDni:
    def test_alta_edicion_y_ficha_por_http(self, owner):
        usuario, cookies = owner
        assert 'name="dni"' in solicitud("GET", "/clientes/nuevo", cookies=cookies).texto
        respuesta = solicitud("POST", "/clientes/nuevo", cookies=cookies, formulario={"nombre": "Ana", "tipo_cliente": "PERSONA", "dni": "12.345.678"})
        assert respuesta.status == 303
        ficha = solicitud("GET", "/clientes/1", cookies=cookies).texto
        assert 'id="cliente-dni"' in ficha and "12.345.678" in ficha
        assert 'value="12345678"' in solicitud("GET", "/clientes/1/editar", cookies=cookies).texto
        duplicado = solicitud("POST", "/clientes/nuevo", cookies=cookies, formulario={"nombre": "Ana 2", "tipo_cliente": "PERSONA", "dni": "12345678"})
        assert duplicado.status < 500 and len(servicio_clientes.listar_clientes()) == 1
        invalido = solicitud("POST", "/clientes/nuevo", cookies=cookies, formulario={"nombre": "Ana 3", "tipo_cliente": "PERSONA", "dni": "12"})
        assert invalido.status < 500 and len(servicio_clientes.listar_clientes()) == 1
        assert "12.345.678" in solicitud("GET", "/clientes?q=12345678", cookies=cookies).texto or "Ana" in solicitud("GET", "/clientes?q=12345678", cookies=cookies).texto


class TestReporteClientes:
    def test_agrega_ventas_cobros_y_deuda_por_cliente(self, owner):
        usuario, cookies = owner
        producto = servicio_stock.registrar_producto("7790000000001", "Alfajor", 100, 250, stock_actual=100)
        ana = servicio_clientes.crear_cliente("Ana", usuario.id)
        beto = servicio_clientes.crear_cliente("Beto", usuario.id)
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 2)], "EFECTIVO", usuario_id=usuario.id, cliente_id=ana.id)
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 4)], "CUENTA_CORRIENTE", usuario_id=usuario.id, cliente_id=ana.id)
        anulada = servicio_ventas.registrar_venta([ItemVenta(producto.id, 9)], "EFECTIVO", usuario_id=usuario.id, cliente_id=ana.id)
        servicio_ventas.anular_venta(anulada.id, "ERROR_CARGA", None, usuario.id)
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO", usuario_id=usuario.id)  # anónima
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO", usuario_id=usuario.id, cliente_id=beto.id)
        servicio_cuenta_corriente.registrar_cobro(ana.id, 300, usuario.id)

        hoy = date.today().isoformat()
        reporte = servicio_reportes_clientes(hoy)
        assert [f.cliente_nombre for f in reporte.filas] == ["Ana", "Beto"]
        fila_ana = reporte.filas[0]
        assert fila_ana.cantidad_ventas == 2 and fila_ana.total_centavos == 1_500 and fila_ana.unidades == 6
        assert fila_ana.ticket_promedio_centavos == 750 and fila_ana.dias_con_compras == 1
        assert fila_ana.saldo_centavos == 700 and fila_ana.cantidad_cobros == 1 and fila_ana.cobrado_centavos == 300
        assert fila_ana.compras_cada_dias(reporte.dias_del_periodo) == 0.5
        assert reporte.filas[1].compras_cada_dias(1) is None
        assert reporte.total_centavos == 1_750 and reporte.total_deuda_centavos == 700

        html = solicitud("GET", f"/reportes/clientes?fecha_desde={hoy}&fecha_hasta={hoy}", cookies=cookies).texto
        assert 'id="clientes-cantidad">2' in html and "$17,50" in html and 'data-cliente="1"' in html
        assert "$7,00" in html
        csv = solicitud("GET", f"/reportes/clientes/exportar?fecha_desde={hoy}&fecha_hasta={hoy}", cookies=cookies)
        assert csv.status == 200 and "ticket_promedio" in csv.texto and "Ana" in csv.texto
        _, cajero = usuario_logueado("CASHIER", "cajera")
        assert solicitud("GET", "/reportes/clientes", cookies=cajero).status == 403
        assert solicitud("GET", "/reportes/clientes/exportar", cookies=cajero).status == 403

    def test_sin_ventas_identificadas_la_pantalla_no_falla(self, owner):
        _, cookies = owner
        assert "Sin ventas con cliente identificado" in solicitud("GET", "/reportes/clientes", cookies=cookies).texto


def servicio_reportes_clientes(hoy):
    from services import servicio_reportes

    return servicio_reportes.generar_reporte_clientes(hoy, hoy)


def test_la_migracion_050_deja_los_clientes_historicos_sin_dni(tmp_path, monkeypatch):
    ruta = tmp_path / "049.db"
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
    con = sqlite3.connect(ruta)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(modulo_conexion._TABLA_MIGRACIONES)
    for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
        if migracion.name < "050_dni_clientes.sql":
            con.executescript(migracion.read_text(encoding="utf-8"))
            con.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (migracion.name,))
    con.execute("INSERT INTO clientes (nombre, observaciones) VALUES ('Histórico', 'DNI 12345678')")
    con.execute("INSERT INTO clientes (nombre, tipo_cliente) VALUES ('Persona', 'PERSONA')")
    con.commit()
    con.close()

    modulo_conexion.inicializar_base_datos()
    modulo_conexion.inicializar_base_datos()

    con = sqlite3.connect(ruta)
    try:
        assert con.execute("SELECT dni FROM clientes ORDER BY id").fetchall() == [(None,), (None,)]
        assert con.execute("SELECT COUNT(*) FROM schema_migraciones WHERE nombre_archivo = '050_dni_clientes.sql'").fetchone()[0] == 1
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        con.close()
