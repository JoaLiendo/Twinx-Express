-- Migración 002: baja lógica de productos.
-- Un producto con ventas asociadas no puede eliminarse físicamente
-- (FK ON DELETE RESTRICT en detalle_venta): en ese caso se desactiva
-- (`activo = 0`) en lugar de borrarse, para conservar el historial de
-- ventas que lo referencia. Los productos sin ventas asociadas sí se
-- eliminan físicamente (ver db.repositorios.productos.eliminar_producto).

ALTER TABLE productos ADD COLUMN activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1));

CREATE INDEX IF NOT EXISTS idx_productos_activo ON productos(activo);
