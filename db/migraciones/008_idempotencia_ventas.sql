-- Migración 008: idempotencia de ventas (Fase 5A).
-- Aditiva: no modifica ni elimina ninguna tabla existente. Las ventas
-- ya registradas quedan con clave_idempotencia/contenido_hash = NULL,
-- su estado implícito actual (ninguna venta anterior a esta fase tenía
-- clave), ahora explícito -- mismo criterio que toda migración previa
-- de este proyecto.
--
-- SQLite trata cada NULL de una columna UNIQUE como distinto de
-- cualquier otro NULL, así que múltiples ventas sin clave (las
-- registradas antes de esta fase, o las que en el futuro llame el CLI
-- sin pasar una) nunca chocan entre sí ni con las que sí la tengan.

ALTER TABLE ventas ADD COLUMN clave_idempotencia TEXT NULL;
ALTER TABLE ventas ADD COLUMN contenido_hash TEXT NULL;

CREATE UNIQUE INDEX idx_ventas_clave_idempotencia ON ventas(clave_idempotencia);
