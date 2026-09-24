-- V1.2 (Fase 2): actualización masiva de precios.
--
-- Un lote agrupa todos los cambios de precio de una misma operación masiva:
-- quién la hizo, con qué criterio (tipo/dirección/valor/redondeo/alcance) y
-- cuántos productos tocó. Cada producto cambiado queda además en
-- `historial_precios` con su `lote_id`.
--
-- `valor`: puntos básicos (1 % = 100) si `tipo = 'PORCENTAJE'`, centavos si
-- `tipo = 'MONTO'` -- siempre entero, sin floats.
-- `clave_idempotencia`: mismo criterio que la migración 014 (doble envío):
-- el UNIQUE garantiza que una confirmación se aplica una sola vez.

CREATE TABLE IF NOT EXISTS lotes_precios (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha               TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    usuario_id          INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    tipo                TEXT NOT NULL CHECK (tipo IN ('PORCENTAJE', 'MONTO')),
    direccion           TEXT NOT NULL CHECK (direccion IN ('AUMENTAR', 'DISMINUIR')),
    valor               INTEGER NOT NULL CHECK (valor > 0),
    redondeo            TEXT NOT NULL CHECK (redondeo IN ('NINGUNO', 'ENTERO')),
    alcance             TEXT NOT NULL,
    cantidad_productos  INTEGER NOT NULL CHECK (cantidad_productos > 0),
    clave_idempotencia  TEXT NULL
);

CREATE UNIQUE INDEX idx_lotes_precios_clave_idempotencia ON lotes_precios(clave_idempotencia);

ALTER TABLE historial_precios ADD COLUMN lote_id INTEGER NULL REFERENCES lotes_precios(id) ON DELETE RESTRICT;
CREATE INDEX idx_historial_precios_lote_id ON historial_precios(lote_id);
