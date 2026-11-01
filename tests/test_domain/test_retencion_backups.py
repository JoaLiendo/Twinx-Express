"""V1.15-C: política de retención (función pura, calendario simulado)."""

from datetime import datetime, timedelta

from domain.backup import TIPO_AUTOMATICO, TIPO_CIERRE_CAJA, TIPO_MANUAL, TIPO_PRE_UPDATE, TIPO_SEGURIDAD
from domain.retencion_backups import BackupClasificado, PoliticaRetencion, backups_a_conservar


def _b(fecha: datetime, tipo: str = TIPO_CIERRE_CAJA, sufijo: str = "") -> BackupClasificado:
    return BackupClasificado(f"{tipo}_{fecha:%Y%m%d_%H%M%S}{sufijo}.zip", fecha, tipo)


def _diarios(desde: datetime, dias: int, tipo: str = TIPO_CIERRE_CAJA) -> list[BackupClasificado]:
    return [_b(desde + timedelta(days=i), tipo) for i in range(dias)]


SOLO_DIARIOS = PoliticaRetencion(diarios=14, semanales=0, mensuales=0)


def test_conserva_los_ultimos_14_dias_con_backups():
    backups = _diarios(datetime(2026, 3, 1, 22), 20)

    conservados = backups_a_conservar(backups, SOLO_DIARIOS)

    assert conservados == {b.nombre for b in backups[-14:]}


def test_dias_sin_operar_no_consumen_retencion():
    """Un mes cerrado no borra nada: cuentan días con backups, no días de calendario."""
    backups = _diarios(datetime(2026, 1, 1, 22), 10) + _diarios(datetime(2026, 3, 1, 22), 4)

    assert backups_a_conservar(backups, SOLO_DIARIOS) == {b.nombre for b in backups}


def test_varios_backups_el_mismo_dia_conserva_el_ultimo_del_dia():
    dia = datetime(2026, 5, 10)
    backups = [_b(dia.replace(hour=h)) for h in (9, 14, 21)]

    assert backups_a_conservar(backups, SOLO_DIARIOS) == {backups[-1].nombre}


def test_semanales_cruzando_el_anio_usan_semana_iso():
    # 2026-12-28 (lunes) y 2027-01-03 (domingo) son la misma semana ISO 2026-W53.
    mismo_semana = [_b(datetime(2026, 12, 28, 20)), _b(datetime(2027, 1, 3, 20))]
    politica = PoliticaRetencion(diarios=0, semanales=8, mensuales=0)

    assert backups_a_conservar(mismo_semana, politica) == {mismo_semana[1].nombre}


def test_conserva_8_semanales():
    backups = [_b(datetime(2026, 1, 5, 20) + timedelta(weeks=i)) for i in range(12)]
    politica = PoliticaRetencion(diarios=0, semanales=8, mensuales=0)

    assert backups_a_conservar(backups, politica) == {b.nombre for b in backups[-8:]}


def test_conserva_12_mensuales():
    backups = [_b(datetime(2025 + (m // 12), m % 12 + 1, 15, 20)) for m in range(18)]
    politica = PoliticaRetencion(diarios=0, semanales=0, mensuales=12)

    assert backups_a_conservar(backups, politica) == {b.nombre for b in backups[-12:]}


def test_politica_completa_con_un_anio_de_cierres_diarios():
    backups = _diarios(datetime(2025, 1, 1, 21), 400)

    conservados = backups_a_conservar(backups)

    # 14 diarios + semanales/mensuales que caen fuera de esos días: nunca más que 14+8+12.
    assert len(conservados) <= 14 + 8 + 12
    assert {b.nombre for b in backups[-14:]} <= conservados
    meses = {(b.fecha.year, b.fecha.month) for b in backups if b.nombre in conservados}
    assert len(meses) == 12
    assert backups[0].nombre not in conservados


def test_categorias_superpuestas_no_duplican_ni_pierden():
    """El último backup es a la vez diario, semanal y mensual: se conserva una vez."""
    backups = _diarios(datetime(2026, 6, 1, 21), 3)

    assert backups_a_conservar(backups) == {b.nombre for b in backups}


def test_pre_update_tiene_familia_propia_y_no_se_borra_como_diario():
    regulares = _diarios(datetime(2026, 1, 1, 21), 30)
    pre_updates = [_b(datetime(2026, 1, d, 8), TIPO_PRE_UPDATE) for d in (2, 3, 4, 5, 6)]

    conservados = backups_a_conservar(regulares + pre_updates, SOLO_DIARIOS)

    assert {b.nombre for b in pre_updates[-3:]} <= conservados
    assert not {b.nombre for b in pre_updates[:2]} & conservados


def test_pre_update_protegido_se_conserva_aunque_exceda_el_limite():
    pre_updates = [_b(datetime(2026, 1, d, 8), TIPO_PRE_UPDATE) for d in range(1, 8)]

    conservados = backups_a_conservar(pre_updates, protegidos=frozenset({pre_updates[0].nombre}))

    assert pre_updates[0].nombre in conservados
    assert len(conservados) == 4


def test_los_manuales_recientes_siempre_se_conservan():
    dia = datetime(2026, 4, 1)
    manuales = [_b(dia.replace(hour=8 + i), TIPO_MANUAL) for i in range(12)]

    conservados = backups_a_conservar(manuales, SOLO_DIARIOS)

    assert {b.nombre for b in manuales[-10:]} <= conservados
    assert manuales[0].nombre not in conservados


def test_seguridad_conserva_los_3_ultimos():
    seguridad = [_b(datetime(2026, 2, d, 10), TIPO_SEGURIDAD) for d in range(1, 6)]

    assert backups_a_conservar(seguridad) == {b.nombre for b in seguridad[-3:]}


def test_automaticos_y_cierres_comparten_el_esquema_regular():
    backups = [_b(datetime(2026, 7, 1, 9), TIPO_AUTOMATICO), _b(datetime(2026, 7, 1, 22), TIPO_CIERRE_CAJA)]

    assert backups_a_conservar(backups, SOLO_DIARIOS) == {backups[1].nombre}


def test_es_determinista_ante_fechas_iguales():
    fecha = datetime(2026, 8, 1, 12)
    backups = [_b(fecha, sufijo=s) for s in ("_a", "_b", "_c")]

    resultados = {frozenset(backups_a_conservar(list(orden), SOLO_DIARIOS)) for orden in (backups, backups[::-1])}

    assert resultados == {frozenset({backups[2].nombre})}
