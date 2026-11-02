"""Migración 045 (V1.16-H): otros impuestos nacionales indirectos.

Upgrade desde una base RC1 (001-044) con un producto histórico y una factura B ya autorizada: ningún
producto recibe NO_APLICA, el comprobante autorizado no cambia (ni su documento: sigue sin la línea de
otros impuestos, que nadie confirmó al emitirlo) y sus notas de crédito pueden emitirse igual. Nunca toca
`data/kiosco.db`.
"""

import sqlite3

import pytest

import db.conexion as modulo_conexion
from db.conexion import obtener_conexion
from db.repositorios import fiscal as repositorio_fiscal
from domain.fiscal.documento import armar
from domain.fiscal.otros_impuestos import SIN_CLASIFICAR
from excepciones import ErrorBaseDatos
from tests import utilidades_fiscales as uf

NOMBRE_045 = "045_otros_impuestos_nacionales.sql"
_EMISOR = '{"cuit": "20111111112", "razon_social": "Kiosco", "condicion_iva": "RESPONSABLE_INSCRIPTO", "domicilio": "Calle 1", "ingresos_brutos": null, "inicio_actividades": null, "punto_venta": 3}'
_RECEPTOR = '{"condicion_iva": "CONSUMIDOR_FINAL", "condicion_iva_receptor_id": 5, "doc_tipo": 99, "doc_nro": "0", "nombre": "Consumidor final", "domicilio": null}'


@pytest.fixture
def base_rc1(tmp_path, monkeypatch):
    ruta = tmp_path / "rc1.db"
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
    con = sqlite3.connect(ruta)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(modulo_conexion._TABLA_MIGRACIONES)
    for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
        if migracion.name < NOMBRE_045:
            con.executescript(migracion.read_text(encoding="utf-8"))
            con.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (migracion.name,))
    con.execute("INSERT INTO usuarios (id, nombre_usuario, nombre_completo, password_hash, rol) VALUES (1, 'd', 'Dueño', 'h', 'OWNER')")
    con.execute(
        "INSERT INTO productos (id, codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos, stock_actual)"
        " VALUES (1, '779', 'Alfajor', 100, 12100, 10)"
    )
    con.execute("INSERT INTO productos_fiscal (producto_id, tratamiento_iva, alicuota_iva_id) VALUES (1, 'GRAVADO', 5)")
    con.execute(
        "INSERT INTO sesiones_caja (id, estado, origen, fecha_apertura, usuario_apertura_id, fondo_centavos)"
        " VALUES (1, 'ABIERTA', 'NORMAL', '2026-10-01 08:00:00', 1, 0)"
    )
    con.execute("INSERT INTO ventas (id, total_centavos, tipo_pago, usuario_id, sesion_caja_id) VALUES (1, 12100, 'EFECTIVO', 1, 1)")
    con.execute(
        "INSERT INTO detalle_venta (venta_id, producto_id, cantidad, precio_unitario_centavos, subtotal_centavos)"
        " VALUES (1, 1, 1, 12100, 12100)"
    )
    con.execute(
        "INSERT INTO comprobantes_fiscales (id, venta_id, rol, ambiente, modalidad, cuit_emisor, punto_venta, tipo_comprobante,"
        " clase, concepto, moneda, cotizacion, doc_tipo, doc_nro, condicion_iva_receptor_id, imp_total_centavos,"
        " imp_tot_conc_centavos, imp_neto_centavos, imp_op_ex_centavos, imp_trib_centavos, imp_iva_centavos,"
        " snapshot_emisor_json, snapshot_receptor_json, contenido_hash, estado)"
        " VALUES (1, 1, 'PRINCIPAL', 'HOMOLOGACION', 'CAE', '20111111112', 3, 6, 'B', 1, 'PES', '1', 99, '0', 5,"
        " 12100, 0, 10000, 0, 0, 2100, ?, ?, ?, 'PENDIENTE')",
        (_EMISOR, _RECEPTOR, "a" * 64),
    )
    con.execute(
        "INSERT INTO comprobante_fiscal_lineas (comprobante_id, orden, producto_id, codigo, descripcion, cantidad,"
        " precio_unitario_centavos, subtotal_centavos, tratamiento_iva, alicuota_iva_id)"
        " VALUES (1, 1, 1, '779', 'Alfajor', 1, 12100, 12100, 'GRAVADO', 5)"
    )
    con.execute("INSERT INTO comprobante_fiscal_alicuotas VALUES (1, 5, 10000, 2100)")
    con.execute("UPDATE comprobantes_fiscales SET estado = 'ENVIANDO', numero = 1, fecha_comprobante = '2026-10-01' WHERE id = 1")
    con.execute(
        "UPDATE comprobantes_fiscales SET estado = 'AUTORIZADO', resultado = 'A', codigo_autorizacion = '71234567890123',"
        " vencimiento_autorizacion = '2026-10-11' WHERE id = 1"
    )
    con.commit()
    con.close()
    return ruta


def _huella(ruta) -> tuple:
    con = sqlite3.connect(ruta)
    try:
        return tuple(
            con.execute(consulta).fetchall()
            for consulta in (
                "SELECT * FROM productos ORDER BY id",
                "SELECT * FROM productos_fiscal ORDER BY producto_id",
                "SELECT * FROM ventas ORDER BY id",
                "SELECT * FROM comprobantes_fiscales ORDER BY id",
                "SELECT id, comprobante_id, orden, producto_id, codigo, descripcion, cantidad, precio_unitario_centavos,"
                " subtotal_centavos, tratamiento_iva, alicuota_iva_id FROM comprobante_fiscal_lineas ORDER BY id",
                "SELECT * FROM comprobante_fiscal_alicuotas ORDER BY comprobante_id",
            )
        )
    finally:
        con.close()


class TestUpgradeDesdeRC1:
    def test_historicos_identicos_y_nada_inventado(self, base_rc1):
        antes = _huella(base_rc1)
        modulo_conexion.inicializar_base_datos()
        assert _huella(base_rc1) == antes
        con = sqlite3.connect(base_rc1)
        try:
            assert con.execute("SELECT COUNT(*) FROM productos_otros_impuestos").fetchone()[0] == 0
            assert con.execute("SELECT otros_impuestos_nacionales FROM comprobante_fiscal_lineas").fetchall() == [(None,)]
            assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        finally:
            con.close()

    def test_producto_historico_queda_sin_clasificar(self, base_rc1):
        """Invariante 1: nunca NO_APLICA automáticamente."""
        modulo_conexion.inicializar_base_datos()
        assert repositorio_fiscal.obtener_otros_impuestos(1) == SIN_CLASIFICAR
        with obtener_conexion() as conexion:
            venta = repositorio_fiscal.venta_para_fiscalizar_en_conexion(conexion, 1)
        assert [linea.otros_impuestos for linea in venta.lineas] == [SIN_CLASIFICAR]

    def test_documento_autorizado_historico_no_cambia(self, base_rc1):
        modulo_conexion.inicializar_base_datos()
        documento = armar(repositorio_fiscal.obtener_comprobante(1))
        assert documento.transparencia == [("IVA Contenido", "$ 21,00")]

    def test_nota_de_credito_de_una_factura_historica(self, base_rc1):
        modulo_conexion.inicializar_base_datos()
        original = repositorio_fiscal.obtener_comprobante(1)
        nueva = repositorio_fiscal.NuevoComprobante(
            venta_id=1, rol="NOTA_CREDITO", comprobante_asociado_id=1, ambiente="HOMOLOGACION", modalidad="CAE",
            caea_id=None, tipo_comprobante=8, clase="B", concepto=1, moneda="PES", cotizacion="1",
            emisor=original.emisor, receptor=original.receptor, importes=original.importes, lineas=original.lineas,
            contenido_hash="b" * 64, usuario_id=1, estado="PENDIENTE",
        )
        with obtener_conexion() as conexion:
            nota = repositorio_fiscal.insertar_comprobante_en_conexion(conexion, nueva)
        assert [linea.otros_impuestos for linea in repositorio_fiscal.obtener_comprobante(nota).lineas] == [SIN_CLASIFICAR]

    def test_idempotente(self, base_rc1):
        modulo_conexion.inicializar_base_datos()
        modulo_conexion.inicializar_base_datos()
        con = sqlite3.connect(base_rc1)
        try:
            assert con.execute("SELECT COUNT(*) FROM schema_migraciones WHERE nombre_archivo = ?", (NOMBRE_045,)).fetchone()[0] == 1
        finally:
            con.close()


def test_la_linea_de_un_snapshot_no_se_reescribe(caja_abierta):
    venta = uf.vender([(uf.producto(12100, uf.IVA_21), 1)])
    comprobante = uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))
    with pytest.raises(ErrorBaseDatos, match="no pueden modificarse"):
        with obtener_conexion() as conexion:
            conexion.execute(
                "UPDATE comprobante_fiscal_lineas SET otros_impuestos_nacionales = NULL WHERE comprobante_id = ?", (comprobante,)
            )
