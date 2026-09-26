-- Migración 023: índices por fecha para los reportes por rango (V1.7-A).
--
-- Los reportes filtran `ventas` y `compras` con `fecha >= ? AND fecha < ?` (sin `date()` sobre la
-- columna): estos índices evitan recorrer la tabla completa. Migración aditiva: no modifica ni
-- transforma datos.

CREATE INDEX IF NOT EXISTS idx_ventas_fecha ON ventas(fecha);
CREATE INDEX IF NOT EXISTS idx_compras_fecha ON compras(fecha);
