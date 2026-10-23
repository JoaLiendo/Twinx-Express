-- Migración 031: índice por fecha de `ajustes_stock` (V1.11-E).
--
-- El reporte de pérdidas (V1.11-D) filtra por `fecha` en un rango y, desde V1.11-E, el dashboard del
-- OWNER lo consulta en cada carga: sin índice es un recorrido completo de la tabla, que solo crece.
-- Mismo criterio que `idx_ventas_fecha`/`idx_compras_fecha` (migración 023). Solo agrega un índice:
-- no toca ningún dato.

CREATE INDEX idx_ajustes_stock_fecha ON ajustes_stock(fecha);
