"""Migración 041 (V1.16-A): modelo fiscal.

Upgrade desde una base V1.15 (001-040) con datos: nada histórico cambia y nada se inventa (ningún
producto recibe IVA, ninguna venta recibe comprobante). Después, las invariantes que exige la base:
máquina de estados, inmutabilidad, unicidad de numeración, una factura principal viva por venta, una
incertidumbre por secuencia, notas asociadas válidas, venta fiscalizada no anulable y producción
bloqueada sin habilitación del técnico. Nunca toca `data/kiosco.db`.
"""

import sqlite3

import pytest

import db.conexion as modulo_conexion
from db.conexion import obtener_conexion
from excepciones import ErrorBaseDatos
from tests import utilidades_fiscales as uf

NOMBRE_041 = "041_modelo_fiscal.sql"


@pytest.fixture
def base_v115(tmp_path, monkeypatch):
    ruta = tmp_path / "v115.db"
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
    con = sqlite3.connect(ruta)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(modulo_conexion._TABLA_MIGRACIONES)
    for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
        if migracion.name < NOMBRE_041:
            con.executescript(migracion.read_text(encoding="utf-8"))
            con.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (migracion.name,))
    con.execute("INSERT INTO usuarios (id, nombre_usuario, nombre_completo, password_hash, rol) VALUES (1, 'd', 'Dueño', 'h', 'OWNER')")
    con.execute("INSERT INTO clientes (id, nombre) VALUES (1, 'Histórico sin clasificar')")
    con.execute(
        "INSERT INTO productos (id, codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos, stock_actual)"
        " VALUES (1, '779', 'Alfajor', 100, 12100, 10)"
    )
    con.execute(
        "INSERT INTO sesiones_caja (id, estado, origen, fecha_apertura, usuario_apertura_id, fondo_centavos)"
        " VALUES (1, 'ABIERTA', 'NORMAL', '2026-10-01 08:00:00', 1, 0)"
    )
    con.execute("INSERT INTO ventas (id, total_centavos, tipo_pago, usuario_id, sesion_caja_id) VALUES (1, 24200, 'EFECTIVO', 1, 1)")
    con.execute(
        "INSERT INTO detalle_venta (venta_id, producto_id, cantidad, precio_unitario_centavos, subtotal_centavos)"
        " VALUES (1, 1, 2, 12100, 24200)"
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
                "SELECT * FROM ventas ORDER BY id",
                "SELECT * FROM detalle_venta ORDER BY id",
                "SELECT * FROM productos ORDER BY id",
                "SELECT * FROM clientes ORDER BY id",
                "SELECT * FROM caja_movimientos ORDER BY id",
            )
        )
    finally:
        con.close()


class TestUpgradeDesdeV115:
    def test_datos_historicos_identicos_y_nada_inventado(self, base_v115):
        antes = _huella(base_v115)
        modulo_conexion.inicializar_base_datos()
        assert _huella(base_v115) == antes
        con = sqlite3.connect(base_v115)
        try:
            assert con.execute("SELECT COUNT(*) FROM productos_fiscal").fetchone()[0] == 0
            assert con.execute("SELECT COUNT(*) FROM comprobantes_fiscales").fetchone()[0] == 0
            assert con.execute("SELECT COUNT(*) FROM perfil_fiscal").fetchone()[0] == 0
            assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        finally:
            con.close()

    def test_idempotente(self, base_v115):
        modulo_conexion.inicializar_base_datos()
        modulo_conexion.inicializar_base_datos()
        con = sqlite3.connect(base_v115)
        try:
            assert con.execute("SELECT COUNT(*) FROM schema_migraciones WHERE nombre_archivo = ?", (NOMBRE_041,)).fetchone()[0] == 1
        finally:
            con.close()


@pytest.fixture
def venta(caja_abierta):
    p = uf.producto(12100, uf.IVA_21)
    return uf.vender([(p, 1)])


def _falla(funcion, *args, **kwargs) -> str:
    with pytest.raises(ErrorBaseDatos) as error:
        funcion(*args, **kwargs)
    return str(error.value)


class TestEstados:
    def test_nace_pendiente(self, venta):
        assert "nace pendiente" in _falla(uf.insertar, uf.nuevo_comprobante_para_venta(venta.id, estado="AUTORIZADO"))

    def test_camino_valido_hasta_autorizado(self, venta):
        cid = uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))
        uf.autorizar(cid, 1)

    @pytest.mark.parametrize(
        "origen, destino",
        [("PENDIENTE", "AUTORIZADO"), ("PENDIENTE", "INCIERTO"), ("PENDIENTE", "RECHAZADO")],
    )
    def test_transiciones_prohibidas_desde_pendiente(self, venta, origen, destino):
        cid = uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))
        campos = {"numero": 1, "fecha_comprobante": "2026-11-01"} if destino == "INCIERTO" else {}
        if destino == "AUTORIZADO":
            campos = {"numero": 1, "fecha_comprobante": "2026-11-01", "codigo_autorizacion": "71234567890123",
                      "vencimiento_autorizacion": "2026-11-11"}
        assert "no permitida" in _falla(uf.transicionar, cid, destino, **campos)

    def test_autorizado_es_terminal(self, venta):
        cid = uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))
        uf.autorizar(cid, 1)
        for destino in ("RECHAZADO", "INCIERTO", "PENDIENTE", "ENVIANDO"):
            assert "no permitida" in _falla(uf.transicionar, cid, destino)

    def test_autorizado_exige_cae_de_14_digitos(self, venta):
        cid = uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))
        uf.transicionar(cid, "ENVIANDO", numero=1, fecha_comprobante="2026-11-01")
        for cae in (None, "", "123", "7123456789012X"):
            _falla(uf.transicionar, cid, "AUTORIZADO", codigo_autorizacion=cae, vencimiento_autorizacion="2026-11-11")

    def test_rechazado_no_tiene_cae(self, venta):
        cid = uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))
        uf.transicionar(cid, "ENVIANDO", numero=1, fecha_comprobante="2026-11-01")
        _falla(uf.transicionar, cid, "RECHAZADO", resultado="R", codigo_autorizacion="71234567890123")
        uf.transicionar(cid, "RECHAZADO", resultado="R")

    def test_enviando_exige_numero(self, venta):
        cid = uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))
        _falla(uf.transicionar, cid, "ENVIANDO")

    def test_incierto_vuelve_a_pendiente_liberando_numero(self, venta):
        cid = uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))
        uf.transicionar(cid, "ENVIANDO", numero=1, fecha_comprobante="2026-11-01")
        uf.transicionar(cid, "INCIERTO")
        _falla(uf.transicionar, cid, "PENDIENTE")  # sin liberar el número
        uf.transicionar(cid, "PENDIENTE", numero=None, fecha_comprobante=None)


class TestInmutabilidad:
    def test_autorizado_no_cambia_numero_cae_ni_fecha(self, venta):
        cid = uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))
        uf.autorizar(cid, 7)
        with pytest.raises(ErrorBaseDatos):
            with obtener_conexion() as conexion:
                conexion.execute("UPDATE comprobantes_fiscales SET numero = 8 WHERE id = ?", (cid,))
        with pytest.raises(ErrorBaseDatos):
            with obtener_conexion() as conexion:
                conexion.execute("UPDATE comprobantes_fiscales SET codigo_autorizacion = NULL WHERE id = ?", (cid,))

    def test_snapshot_no_cambia_ni_pendiente(self, venta):
        cid = uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))
        for sentencia in (
            "UPDATE comprobantes_fiscales SET imp_total_centavos = 1 WHERE id = ?",
            "UPDATE comprobantes_fiscales SET snapshot_receptor_json = '{}' WHERE id = ?",
            "UPDATE comprobantes_fiscales SET doc_nro = '1' WHERE id = ?",
        ):
            with pytest.raises(ErrorBaseDatos):
                with obtener_conexion() as conexion:
                    conexion.execute(sentencia, (cid,))

    def test_no_se_borra_nada(self, venta):
        cid = uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))
        for sentencia in (
            "DELETE FROM comprobantes_fiscales WHERE id = ?",
            "DELETE FROM comprobante_fiscal_lineas WHERE comprobante_id = ?",
            "DELETE FROM comprobante_fiscal_alicuotas WHERE comprobante_id = ?",
            "UPDATE comprobante_fiscal_lineas SET cantidad = 9 WHERE comprobante_id = ?",
        ):
            with pytest.raises(ErrorBaseDatos):
                with obtener_conexion() as conexion:
                    conexion.execute(sentencia, (cid,))

    def test_cambiar_el_producto_no_cambia_el_comprobante(self, venta):
        cid = uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))
        uf.autorizar(cid, 1)
        with obtener_conexion() as conexion:
            conexion.execute("UPDATE productos SET nombre = 'Renombrado', precio_venta_centavos = 1")
        comprobante = uf.repositorio_fiscal.obtener_comprobante(cid)
        assert comprobante.lineas[0].descripcion != "Renombrado"
        assert comprobante.lineas[0].precio_unitario_centavos == 12100


class TestUnicidad:
    def test_un_solo_principal_vivo_por_venta(self, venta):
        uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))
        assert "UNIQUE" in _falla(uf.insertar, uf.nuevo_comprobante_para_venta(venta.id))

    def test_rechazado_libera_la_venta(self, venta):
        cid = uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))
        uf.transicionar(cid, "ENVIANDO", numero=1, fecha_comprobante="2026-11-01")
        uf.transicionar(cid, "RECHAZADO", resultado="R")
        uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))

    def test_numero_unico_por_secuencia(self, caja_abierta):
        p = uf.producto(1000, uf.IVA_21)
        a, b = uf.vender([(p, 1)]), uf.vender([(p, 2)])
        ca = uf.insertar(uf.nuevo_comprobante_para_venta(a.id))
        cb = uf.insertar(uf.nuevo_comprobante_para_venta(b.id))
        uf.autorizar(ca, 5)
        uf.transicionar(cb, "ENVIANDO", numero=6, fecha_comprobante="2026-11-01")
        uf.transicionar(cb, "RECHAZADO", resultado="R")
        cc = uf.insertar(uf.nuevo_comprobante_para_venta(b.id))
        _falla(uf.transicionar, cc, "ENVIANDO", numero=5, fecha_comprobante="2026-11-01")
        uf.transicionar(cc, "ENVIANDO", numero=6, fecha_comprobante="2026-11-01")  # el 6 rechazado no se consumió

    def test_una_incertidumbre_bloquea_la_secuencia(self, caja_abierta):
        p = uf.producto(1000, uf.IVA_21)
        a, b = uf.vender([(p, 1)]), uf.vender([(p, 2)])
        ca = uf.insertar(uf.nuevo_comprobante_para_venta(a.id))
        cb = uf.insertar(uf.nuevo_comprobante_para_venta(b.id))
        uf.transicionar(ca, "ENVIANDO", numero=1, fecha_comprobante="2026-11-01")
        uf.transicionar(ca, "INCIERTO")
        assert "UNIQUE" in _falla(uf.transicionar, cb, "ENVIANDO", numero=2, fecha_comprobante="2026-11-01")


class TestNotasAsociadas:
    def test_nc_requiere_principal_autorizado(self, venta):
        principal = uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))
        nc = uf.nuevo_comprobante_para_venta(venta.id, rol="NOTA_CREDITO", comprobante_asociado_id=principal)
        assert "nota referencia" in _falla(uf.insertar, nc)
        uf.autorizar(principal, 1)
        uf.insertar(uf.nuevo_comprobante_para_venta(venta.id, rol="NOTA_CREDITO", comprobante_asociado_id=principal))

    def test_nc_duplicada_rechazada(self, venta):
        principal = uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))
        uf.autorizar(principal, 1)
        uf.insertar(uf.nuevo_comprobante_para_venta(venta.id, rol="NOTA_CREDITO", comprobante_asociado_id=principal))
        assert "UNIQUE" in _falla(
            uf.insertar, uf.nuevo_comprobante_para_venta(venta.id, rol="NOTA_CREDITO", comprobante_asociado_id=principal)
        )

    def test_nc_de_otra_clase_rechazada(self, venta):
        principal = uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))
        uf.autorizar(principal, 1)
        nc = uf.nuevo_comprobante_para_venta(venta.id, clase="A", rol="NOTA_CREDITO", comprobante_asociado_id=principal)
        _falla(uf.insertar, nc)


class TestVentaFiscalizada:
    def test_no_se_anula_por_sql(self, venta):
        cid = uf.insertar(uf.nuevo_comprobante_para_venta(venta.id))
        uf.autorizar(cid, 1)
        with pytest.raises(ErrorBaseDatos, match="nota de crédito"):
            with obtener_conexion() as conexion:
                conexion.execute("UPDATE ventas SET estado = 'ANULADA' WHERE id = ?", (venta.id,))

    def test_venta_anulada_no_se_fiscaliza(self, venta):
        with obtener_conexion() as conexion:
            conexion.execute("UPDATE ventas SET estado = 'ANULADA' WHERE id = ?", (venta.id,))
        assert "venta activa" in _falla(uf.insertar, uf.nuevo_comprobante_para_venta(venta.id))


class TestProduccion:
    def test_perfil_no_pasa_a_produccion_sin_habilitacion(self, base_datos_temporal):
        uf.guardar_perfil_validado()
        with pytest.raises(ErrorBaseDatos, match="no habilitada"):
            with obtener_conexion() as conexion:
                uf.repositorio_fiscal.cambiar_ambiente_en_conexion(conexion, "PRODUCCION")
        assert uf.repositorio_fiscal.obtener_perfil().ambiente == "HOMOLOGACION"

    def test_habilitacion_de_otro_cuit_no_alcanza(self, base_datos_temporal):
        uf.guardar_perfil_validado()
        with obtener_conexion() as conexion:
            uf.repositorio_fiscal.registrar_habilitacion_produccion_en_conexion(conexion, "30712345671", "a" * 64, "otro")
        with pytest.raises(ErrorBaseDatos):
            with obtener_conexion() as conexion:
                uf.repositorio_fiscal.cambiar_ambiente_en_conexion(conexion, "PRODUCCION")

    def test_con_habilitacion_cambia_e_invalida_puntos_de_venta(self, base_datos_temporal):
        uf.guardar_perfil_validado()
        with obtener_conexion() as conexion:
            uf.repositorio_fiscal.registrar_habilitacion_produccion_en_conexion(conexion, uf.CUIT_EMISOR, "a" * 64, "técnico")
            uf.repositorio_fiscal.cambiar_ambiente_en_conexion(conexion, "PRODUCCION")
        actual = uf.repositorio_fiscal.obtener_perfil()
        assert actual.ambiente == "PRODUCCION"
        assert not actual.punto_venta_cae_listo

    def test_habilitaciones_inmutables(self, base_datos_temporal):
        with obtener_conexion() as conexion:
            uf.repositorio_fiscal.registrar_habilitacion_produccion_en_conexion(conexion, uf.CUIT_EMISOR, "a" * 64, "técnico")
        for sentencia in ("UPDATE habilitaciones_produccion_fiscal SET cuit = cuit", "DELETE FROM habilitaciones_produccion_fiscal"):
            with pytest.raises(ErrorBaseDatos):
                with obtener_conexion() as conexion:
                    conexion.execute(sentencia)


class TestIntentos:
    def test_se_cierra_una_sola_vez(self, base_datos_temporal):
        with obtener_conexion() as conexion:
            correlacion = uf.repositorio_fiscal.abrir_intento_en_conexion(conexion, None, "FEDummy", "HOMOLOGACION")
            uf.repositorio_fiscal.cerrar_intento_en_conexion(conexion, correlacion, "A", 10, None)
        with pytest.raises(ErrorBaseDatos):
            with obtener_conexion() as conexion:
                uf.repositorio_fiscal.cerrar_intento_en_conexion(conexion, correlacion, "R", 10, None)
        with pytest.raises(ErrorBaseDatos):
            with obtener_conexion() as conexion:
                conexion.execute("DELETE FROM intentos_fiscales")
