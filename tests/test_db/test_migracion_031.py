"""Migración 031 (V1.11-E): índice por fecha de `ajustes_stock`. El reporte de pérdidas filtra por rango
de fecha y deja de recorrer la tabla completa."""

import db.conexion as modulo_conexion
from db.conexion import obtener_conexion


def test_el_rango_de_fechas_de_ajustes_usa_el_indice(base_datos_temporal):
    with obtener_conexion() as conexion:
        plan = " ".join(
            fila["detail"]
            for fila in conexion.execute(
                "EXPLAIN QUERY PLAN SELECT id FROM ajustes_stock a WHERE a.fecha >= ? AND a.fecha < ?",
                ("2026-01-01", "2026-02-01"),
            ).fetchall()
        )
        aplicadas = [f["nombre_archivo"] for f in conexion.execute("SELECT nombre_archivo FROM schema_migraciones")]
    assert "idx_ajustes_stock_fecha" in plan
    assert "031_indice_fecha_ajustes_stock.sql" in aplicadas
    assert len(list(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql"))) == len(aplicadas)
