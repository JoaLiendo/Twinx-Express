"""Gastos operativos (migración 048): alta con o sin efectivo de la caja (egreso atómico e idempotente),
corrección de lo descriptivo, anulación con reintegro opcional, invariantes de la base y consultas."""

import sqlite3

import pytest

import db.conexion as modulo_conexion
from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import usuarios as repositorio_usuarios
from db.repositorios.gastos import FiltrosGastos
from domain.usuario import Usuario
from excepciones import (
    CajaCerradaError,
    ClaveIdempotenciaReutilizadaError,
    DatosInvalidosError,
    ErrorBaseDatos,
    GastoNoEncontradoError,
    GastoYaAnuladoError,
)
from services import servicio_caja, servicio_gastos


@pytest.fixture
def owner(caja_abierta):
    return repositorio_usuarios.crear_usuario(
        Usuario(nombre_usuario="ana", nombre_completo="Ana", password_hash="hash-de-prueba", rol="OWNER")
    )


def _registrar(owner, **extra):
    datos = {
        "usuario_id": owner.id,
        "fecha": "2026-11-05",
        "categoria": "ELECTRICIDAD",
        "descripcion": "Factura de luz octubre",
        "importe_centavos": 125_000,
        "medio_pago": "TRANSFERENCIA",
    }
    datos.update(extra)
    return servicio_gastos.registrar_gasto(**datos)


def _esperado_en_caja() -> int:
    return servicio_caja.calcular_arqueo_de_sesion().efectivo_estimado_centavos


def _consultar(sql: str, *parametros):
    with obtener_conexion() as conexion:
        return conexion.execute(sql, parametros).fetchall()


class TestAlta:
    def test_fuera_de_la_caja_no_toca_la_caja_y_queda_auditado(self, owner):
        esperado = _esperado_en_caja()
        gasto = _registrar(owner, referencia="FC 0001-00012345", responsable="Ana", observaciones="  pagada por home banking ")

        assert gasto.vigente and gasto.caja_movimiento_id is None
        assert gasto.referencia == "FC 0001-00012345" and gasto.observaciones == "pagada por home banking"
        assert gasto.usuario_nombre == "Ana"
        assert _esperado_en_caja() == esperado
        entradas = repositorio_auditoria.listar(accion="GASTO_REGISTRADO")
        assert len(entradas) == 1 and "Electricidad" in entradas[0].resumen and "1.250,00" in entradas[0].resumen
        assert repositorio_auditoria.listar(accion="CAJA_EGRESO") == []

    def test_con_efectivo_de_la_caja_crea_un_egreso_vinculado_y_baja_el_esperado(self, owner):
        esperado = _esperado_en_caja()
        gasto = _registrar(owner, medio_pago="EFECTIVO_CAJA", categoria="TRANSPORTE", descripcion="Flete")

        assert gasto.caja_movimiento_id is not None
        assert _esperado_en_caja() == esperado - 125_000
        movimientos = [m for m in servicio_caja.listar_movimientos_de_sesion_actual() if m.id == gasto.caja_movimiento_id]
        assert movimientos[0].tipo == "EGRESO" and movimientos[0].monto_centavos == 125_000
        assert movimientos[0].descripcion == "Gasto operativo: Transporte – Flete"
        # Una sola entrada de auditoría por la operación (el egreso no se audita aparte como CAJA_EGRESO).
        assert repositorio_auditoria.listar(accion="CAJA_EGRESO") == []
        assert f"egreso de caja #{gasto.caja_movimiento_id}" in repositorio_auditoria.listar(accion="GASTO_REGISTRADO")[0].resumen

    @pytest.mark.sin_caja_abierta
    def test_con_la_caja_cerrada_no_se_puede_pagar_con_su_efectivo_y_no_queda_nada(self, owner):
        with pytest.raises(CajaCerradaError):
            _registrar(owner, medio_pago="EFECTIVO_CAJA")
        assert _consultar("SELECT COUNT(*) FROM gastos")[0][0] == 0
        assert _consultar("SELECT COUNT(*) FROM caja_movimientos WHERE tipo = 'EGRESO'")[0][0] == 0

    @pytest.mark.sin_caja_abierta
    def test_con_la_caja_cerrada_se_puede_registrar_con_otro_medio(self, owner):
        gasto = _registrar(owner, medio_pago="EFECTIVO")
        assert gasto.vigente and gasto.caja_movimiento_id is None

    def test_un_fallo_despues_del_egreso_revierte_gasto_y_egreso(self, owner, monkeypatch):
        from db.repositorios import gastos as repositorio_gastos

        def explota(*args, **kwargs):
            raise ErrorBaseDatos("falla simulada")

        monkeypatch.setattr(repositorio_gastos, "registrar_en_conexion", explota)
        esperado = _esperado_en_caja()
        with pytest.raises(ErrorBaseDatos):
            _registrar(owner, medio_pago="EFECTIVO_CAJA")
        assert _esperado_en_caja() == esperado
        assert _consultar("SELECT COUNT(*) FROM caja_movimientos WHERE tipo = 'EGRESO'")[0][0] == 0

    def test_doble_envio_no_duplica_ni_el_gasto_ni_el_egreso(self, owner):
        servicio_caja.registrar_ingreso(500_000, "Fondo de prueba", usuario_id=owner.id)
        primero = _registrar(owner, medio_pago="EFECTIVO_CAJA", clave_idempotencia="clave-1")
        servicio_caja.cerrar_caja(_esperado_en_caja(), usuario_id=owner.id)
        segundo = _registrar(owner, medio_pago="EFECTIVO_CAJA", clave_idempotencia="clave-1")

        assert segundo.id == primero.id
        assert _consultar("SELECT COUNT(*) FROM gastos")[0][0] == 1
        assert _consultar("SELECT COUNT(*) FROM caja_movimientos WHERE tipo = 'EGRESO'")[0][0] == 1

    def test_la_misma_clave_con_otros_datos_se_rechaza(self, owner):
        _registrar(owner, clave_idempotencia="clave-2")
        with pytest.raises(ClaveIdempotenciaReutilizadaError):
            _registrar(owner, clave_idempotencia="clave-2", importe_centavos=999)

    @pytest.mark.parametrize(
        "extra, fragmento",
        [
            ({"importe_centavos": 0}, "mayor que cero"),
            ({"importe_centavos": -5}, "mayor que cero"),
            ({"categoria": "MERCADERIA"}, "categoría"),
            ({"categoria": "OTRO", "descripcion": "  "}, "Otros"),
            ({"medio_pago": "CHEQUE"}, "cómo se pagó"),
            ({"fecha": "2026-13-01"}, "fecha"),
            ({"fecha": ""}, "fecha"),
            ({"fecha": "2099-01-01"}, "futura"),
            ({"descripcion": "x" * 201}, "200"),
        ],
    )
    def test_datos_invalidos_se_rechazan_sin_escribir(self, owner, extra, fragmento):
        with pytest.raises(DatosInvalidosError, match=fragmento):
            _registrar(owner, **extra)
        assert _consultar("SELECT COUNT(*) FROM gastos")[0][0] == 0

    def test_otro_con_descripcion_se_acepta_y_la_fecha_de_hoy_tambien(self, owner):
        from datetime import date

        gasto = _registrar(owner, categoria="OTRO", descripcion="Matafuegos recarga", fecha=date.today().isoformat())
        assert gasto.categoria_etiqueta.startswith("Otros")


class TestModificacion:
    def test_corrige_lo_descriptivo_y_audita_las_diferencias(self, owner):
        gasto = _registrar(owner)
        modificado = servicio_gastos.modificar_gasto(
            gasto.id, usuario_id=owner.id, categoria="SERVICIOS", descripcion="Luz y gas octubre",
            referencia="REF-9", responsable=None, observaciones=None,
        )
        assert modificado.categoria == "SERVICIOS" and modificado.referencia == "REF-9"
        assert modificado.modificado_en is not None
        assert modificado.importe_centavos == 125_000
        resumen = repositorio_auditoria.listar(accion="GASTO_MODIFICADO")[0].resumen
        assert "categoría: ELECTRICIDAD → SERVICIOS" in resumen and "referencia: — → REF-9" in resumen

    def test_sin_cambios_no_audita_nada(self, owner):
        gasto = _registrar(owner)
        servicio_gastos.modificar_gasto(
            gasto.id, usuario_id=owner.id, categoria=gasto.categoria, descripcion=gasto.descripcion,
            referencia=None, responsable=None, observaciones=None,
        )
        assert repositorio_auditoria.listar(accion="GASTO_MODIFICADO") == []

    def test_un_gasto_anulado_o_inexistente_no_se_modifica(self, owner):
        gasto = _registrar(owner)
        servicio_gastos.anular_gasto(gasto.id, usuario_id=owner.id, motivo="Cargado dos veces")
        with pytest.raises(GastoYaAnuladoError):
            servicio_gastos.modificar_gasto(
                gasto.id, usuario_id=owner.id, categoria="AGUA", descripcion=None, referencia=None, responsable=None, observaciones=None
            )
        with pytest.raises(GastoNoEncontradoError):
            servicio_gastos.modificar_gasto(
                999, usuario_id=owner.id, categoria="AGUA", descripcion=None, referencia=None, responsable=None, observaciones=None
            )

    def test_lo_economico_es_inmutable_en_la_base(self, owner):
        gasto = _registrar(owner)
        for columna, valor in (("importe_centavos", 1), ("fecha", "2026-01-01"), ("medio_pago", "TARJETA")):
            with pytest.raises(ErrorBaseDatos, match="Lo económico"):
                with obtener_conexion() as conexion:
                    conexion.execute(f"UPDATE gastos SET {columna} = ? WHERE id = ?", (valor, gasto.id))
        with pytest.raises(ErrorBaseDatos, match="no se borra"):
            with obtener_conexion() as conexion:
                conexion.execute("DELETE FROM gastos WHERE id = ?", (gasto.id,))


class TestAnulacion:
    def test_anular_exige_motivo(self, owner):
        gasto = _registrar(owner)
        with pytest.raises(DatosInvalidosError, match="motivo"):
            servicio_gastos.anular_gasto(gasto.id, usuario_id=owner.id, motivo="  ")
        assert servicio_gastos.obtener_gasto(gasto.id).vigente

    def test_anular_un_gasto_fuera_de_la_caja_no_toca_la_caja(self, owner):
        gasto = _registrar(owner)
        esperado = _esperado_en_caja()
        anulado = servicio_gastos.anular_gasto(gasto.id, usuario_id=owner.id, motivo="Cargado dos veces")
        assert anulado.estado == "ANULADO" and anulado.motivo_anulacion == "Cargado dos veces"
        assert anulado.anulado_por_nombre == "Ana" and anulado.anulado_en is not None
        assert _esperado_en_caja() == esperado
        assert "anulado: Cargado dos veces" in repositorio_auditoria.listar(accion="GASTO_ANULADO")[0].resumen

    def test_anular_uno_pagado_con_la_caja_exige_decir_si_vuelve_el_efectivo(self, owner):
        gasto = _registrar(owner, medio_pago="EFECTIVO_CAJA")
        with pytest.raises(DatosInvalidosError, match="vuelve a la caja"):
            servicio_gastos.anular_gasto(gasto.id, usuario_id=owner.id, motivo="Error")
        assert servicio_gastos.obtener_gasto(gasto.id).vigente

    def test_reintegro_de_uno_fuera_de_la_caja_se_rechaza(self, owner):
        gasto = _registrar(owner)
        with pytest.raises(DatosInvalidosError, match="nada que reintegrar"):
            servicio_gastos.anular_gasto(gasto.id, usuario_id=owner.id, motivo="Error", reintegro_a_caja=True)

    def test_anular_con_reintegro_devuelve_el_efectivo_con_un_ingreso_vinculado(self, owner):
        gasto = _registrar(owner, medio_pago="EFECTIVO_CAJA")
        esperado = _esperado_en_caja()
        anulado = servicio_gastos.anular_gasto(gasto.id, usuario_id=owner.id, motivo="Error de carga", reintegro_a_caja=True)
        assert anulado.caja_movimiento_reintegro_id is not None and anulado.anulado_con_reintegro
        assert _esperado_en_caja() == esperado + 125_000
        # El egreso original sigue en la caja; se agregó un ingreso por el mismo importe.
        assert _consultar("SELECT COUNT(*) FROM caja_movimientos WHERE tipo = 'EGRESO'")[0][0] == 1
        assert _consultar("SELECT monto_centavos FROM caja_movimientos WHERE id = ?", anulado.caja_movimiento_reintegro_id)[0][0] == 125_000
        assert f"reintegro a caja: ingreso #{anulado.caja_movimiento_reintegro_id}" in repositorio_auditoria.listar(accion="GASTO_ANULADO")[0].resumen
        assert repositorio_auditoria.listar(accion="CAJA_INGRESO") == []

    def test_anular_sin_reintegro_deja_la_caja_como_esta(self, owner):
        gasto = _registrar(owner, medio_pago="EFECTIVO_CAJA")
        esperado = _esperado_en_caja()
        anulado = servicio_gastos.anular_gasto(gasto.id, usuario_id=owner.id, motivo="Ya pagado", reintegro_a_caja=False)
        assert anulado.caja_movimiento_reintegro_id is None
        assert _esperado_en_caja() == esperado
        assert "sin reintegro a caja" in repositorio_auditoria.listar(accion="GASTO_ANULADO")[0].resumen

    def test_reintegro_con_la_caja_cerrada_se_rechaza_sin_anular(self, owner):
        servicio_caja.registrar_ingreso(500_000, "Fondo de prueba", usuario_id=owner.id)
        gasto = _registrar(owner, medio_pago="EFECTIVO_CAJA")
        servicio_caja.cerrar_caja(_esperado_en_caja(), usuario_id=owner.id)
        with pytest.raises(CajaCerradaError):
            servicio_gastos.anular_gasto(gasto.id, usuario_id=owner.id, motivo="Error", reintegro_a_caja=True)
        assert servicio_gastos.obtener_gasto(gasto.id).vigente
        # Sin reintegro sí se puede anular con la caja cerrada.
        assert servicio_gastos.anular_gasto(gasto.id, usuario_id=owner.id, motivo="Error", reintegro_a_caja=False).estado == "ANULADO"

    def test_no_se_anula_dos_veces(self, owner):
        gasto = _registrar(owner)
        servicio_gastos.anular_gasto(gasto.id, usuario_id=owner.id, motivo="Error")
        with pytest.raises(GastoYaAnuladoError):
            servicio_gastos.anular_gasto(gasto.id, usuario_id=owner.id, motivo="Error")
        assert len(repositorio_auditoria.listar(accion="GASTO_ANULADO")) == 1

    def test_la_base_impide_degradar_una_anulacion_o_un_segundo_reintegro(self, owner):
        gasto = _registrar(owner, medio_pago="EFECTIVO_CAJA")
        anulado = servicio_gastos.anular_gasto(gasto.id, usuario_id=owner.id, motivo="Error", reintegro_a_caja=True)
        with pytest.raises(ErrorBaseDatos, match="anulado no se modifica"):
            with obtener_conexion() as conexion:
                conexion.execute("UPDATE gastos SET estado = 'VIGENTE', anulado_en = NULL, anulado_por_usuario_id = NULL, motivo_anulacion = NULL WHERE id = ?", (gasto.id,))
        with pytest.raises(ErrorBaseDatos):
            with obtener_conexion() as conexion:
                conexion.execute("UPDATE gastos SET caja_movimiento_reintegro_id = ? WHERE id = ?", (anulado.caja_movimiento_id, gasto.id))
        with pytest.raises(ErrorBaseDatos, match="exige fecha, usuario y motivo"):
            with obtener_conexion() as conexion:
                conexion.execute("UPDATE gastos SET estado = 'ANULADO' WHERE id = ?", (_registrar(owner).id,))

    def test_un_egreso_respalda_a_lo_sumo_un_gasto(self, owner):
        gasto = _registrar(owner, medio_pago="EFECTIVO_CAJA")
        with pytest.raises(ErrorBaseDatos):
            with obtener_conexion() as conexion:
                conexion.execute(
                    "INSERT INTO gastos (fecha, categoria, importe_centavos, medio_pago, usuario_id, caja_movimiento_id)"
                    " VALUES ('2026-11-05', 'AGUA', 125000, 'EFECTIVO_CAJA', ?, ?)",
                    (owner.id, gasto.caja_movimiento_id),
                )


class TestConsultas:
    def test_listado_filtra_y_pagina_y_el_total_solo_suma_vigentes(self, owner):
        a = _registrar(owner, fecha="2026-11-01", categoria="ALQUILER", importe_centavos=500_000, descripcion="Alquiler nov")
        b = _registrar(owner, fecha="2026-11-03", categoria="AGUA", importe_centavos=20_000, descripcion="Aysa")
        c = _registrar(owner, fecha="2026-10-20", categoria="AGUA", importe_centavos=30_000, descripcion="Aysa sept", responsable="Juan")
        servicio_gastos.anular_gasto(b.id, usuario_id=owner.id, motivo="Duplicado")

        todo = servicio_gastos.listar_gastos(servicio_gastos.filtros_del_listado(None, None, None, None, None, None))
        assert [g.id for g in todo.gastos] == [b.id, a.id, c.id]
        assert todo.cantidad == 3 and todo.total_vigente_centavos == 530_000 and todo.total_paginas == 1

        agua = servicio_gastos.listar_gastos(servicio_gastos.filtros_del_listado(None, None, "AGUA", None, "VIGENTE", None))
        assert [g.id for g in agua.gastos] == [c.id]
        noviembre = servicio_gastos.listar_gastos(servicio_gastos.filtros_del_listado("2026-11-01", "2026-11-30", None, None, None, None))
        assert {g.id for g in noviembre.gastos} == {a.id, b.id}
        por_texto = servicio_gastos.listar_gastos(servicio_gastos.filtros_del_listado(None, None, None, None, None, "juan"))
        assert [g.id for g in por_texto.gastos] == [c.id]

    def test_filtros_desconocidos_se_rechazan(self, owner):
        with pytest.raises(DatosInvalidosError):
            servicio_gastos.filtros_del_listado(None, None, "MERCADERIA", None, None, None)
        with pytest.raises(DatosInvalidosError):
            servicio_gastos.filtros_del_listado("2026-02-30", None, None, None, None, None)

    def test_resumen_del_periodo_por_categoria_excluye_anulados(self, owner):
        _registrar(owner, fecha="2026-11-01", categoria="ALQUILER", importe_centavos=500_000)
        _registrar(owner, fecha="2026-11-02", categoria="AGUA", importe_centavos=20_000)
        anulado = _registrar(owner, fecha="2026-11-02", categoria="AGUA", importe_centavos=70_000)
        servicio_gastos.anular_gasto(anulado.id, usuario_id=owner.id, motivo="Duplicado")
        _registrar(owner, fecha="2026-10-15", categoria="AGUA", importe_centavos=1_000)

        resumen = servicio_gastos.resumir_periodo("2026-11-01", "2026-11-30")
        assert resumen.total_centavos == 520_000
        assert [(t.categoria, t.cantidad, t.importe_centavos) for t in resumen.por_categoria] == [
            ("ALQUILER", 1, 500_000),
            ("AGUA", 1, 20_000),
        ]


def test_la_migracion_048_sobre_una_base_047_queda_registrada_una_vez_y_sin_tocar_nada(tmp_path, monkeypatch):
    ruta = tmp_path / "047.db"
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
    con = sqlite3.connect(ruta)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(modulo_conexion._TABLA_MIGRACIONES)
    for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
        if migracion.name < "048_gastos_operativos.sql":
            con.executescript(migracion.read_text(encoding="utf-8"))
            con.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (migracion.name,))
    con.execute("INSERT INTO usuarios (nombre_usuario, nombre_completo, password_hash, rol) VALUES ('a', 'A', 'h', 'OWNER')")
    con.commit()
    tablas_antes = {f[0] for f in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    con.close()

    modulo_conexion.inicializar_base_datos()
    modulo_conexion.inicializar_base_datos()

    con = sqlite3.connect(ruta)
    try:
        tablas = {f[0] for f in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        # Se aplican también las migraciones posteriores (051 crea `pedidos` y `pedido_lineas`): lo que importa
        # es que no se pierda ninguna tabla previa y que `gastos` nazca vacía.
        assert tablas_antes <= tablas and "gastos" in tablas
        assert con.execute("SELECT COUNT(*) FROM gastos").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM schema_migraciones WHERE nombre_archivo = '048_gastos_operativos.sql'").fetchone()[0] == 1
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        con.close()
