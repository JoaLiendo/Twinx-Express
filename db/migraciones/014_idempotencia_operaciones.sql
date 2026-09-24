-- V1.1 (C3): protección contra doble envío en las operaciones que mueven
-- dinero o stock sin haber tenido ninguna: ingreso/egreso de caja, ajuste
-- manual de stock y compra. Mismo criterio que la migración 008 (ventas):
-- el cliente genera una clave por formulario mostrado y la reenvía igual
-- en cualquier reintento (doble clic, doble submit, "atrás y reenviar");
-- el UNIQUE es la garantía real de que esa clave se usa una sola vez.
--
-- Columna NULL: los registros anteriores y los llamados sin clave (CLI,
-- servicios) no tienen ninguna. SQLite trata cada NULL de una columna
-- UNIQUE como distinto de cualquier otro, así que nunca chocan entre sí.

ALTER TABLE caja_movimientos ADD COLUMN clave_idempotencia TEXT NULL;
CREATE UNIQUE INDEX idx_caja_movimientos_clave_idempotencia ON caja_movimientos(clave_idempotencia);

ALTER TABLE ajustes_stock ADD COLUMN clave_idempotencia TEXT NULL;
CREATE UNIQUE INDEX idx_ajustes_stock_clave_idempotencia ON ajustes_stock(clave_idempotencia);

ALTER TABLE compras ADD COLUMN clave_idempotencia TEXT NULL;
CREATE UNIQUE INDEX idx_compras_clave_idempotencia ON compras(clave_idempotencia);
