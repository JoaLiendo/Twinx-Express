-- Migración 047: origen del pago de una compra al contado (V1.16 RC4).
--
--   * `compras.origen_pago`: cómo se pagó una compra CONTADO. 'CAJA_ACTUAL' = con efectivo de la caja abierta
--     (genera un EGRESO vinculado, en la misma transacción); 'FUERA_DE_CAJA' = transferencia, cuenta bancaria,
--     dinero personal, etc. (no toca la caja). NULL en toda compra a crédito y en las compras al contado
--     anteriores a esta migración: de esas no se sabe y no se infiere nada (ningún UPDATE histórico).
--   * `compras.caja_movimiento_id`: el EGRESO de caja de una compra 'CAJA_ACTUAL'.
--   * `compras.caja_movimiento_reintegro_id`: el INGRESO de caja que devolvió ese efectivo al anularla, si el
--     OWNER indicó que el efectivo volvió a la caja.
-- Invariantes por trigger: el origen solo existe en compras CONTADO nuevas, 'CAJA_ACTUAL' exige su egreso y
-- 'FUERA_DE_CAJA' lo prohíbe, un movimiento de caja respalda a lo sumo una compra, y nada de esto se edita
-- después (salvo registrar el reintegro una sola vez, al anular).

ALTER TABLE compras ADD COLUMN origen_pago TEXT NULL
    CHECK (origen_pago IS NULL OR origen_pago IN ('CAJA_ACTUAL', 'FUERA_DE_CAJA'));
ALTER TABLE compras ADD COLUMN caja_movimiento_id INTEGER NULL REFERENCES caja_movimientos(id) ON DELETE RESTRICT;
ALTER TABLE compras ADD COLUMN caja_movimiento_reintegro_id INTEGER NULL REFERENCES caja_movimientos(id) ON DELETE RESTRICT;

CREATE UNIQUE INDEX idx_compras_caja_movimiento ON compras(caja_movimiento_id) WHERE caja_movimiento_id IS NOT NULL;
CREATE UNIQUE INDEX idx_compras_caja_movimiento_reintegro
    ON compras(caja_movimiento_reintegro_id) WHERE caja_movimiento_reintegro_id IS NOT NULL;

CREATE TRIGGER trg_compras_origen_pago_coherente BEFORE INSERT ON compras
WHEN (NEW.condicion_pago = 'CREDITO' AND NEW.origen_pago IS NOT NULL)
     OR (NEW.origen_pago = 'CAJA_ACTUAL' AND NEW.caja_movimiento_id IS NULL)
     OR (NEW.origen_pago IS NOT 'CAJA_ACTUAL' AND NEW.caja_movimiento_id IS NOT NULL)
     OR NEW.caja_movimiento_reintegro_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Origen de pago de la compra incoherente.');
END;

CREATE TRIGGER trg_compras_egreso_valido BEFORE INSERT ON compras
WHEN NEW.caja_movimiento_id IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM caja_movimientos
                     WHERE id = NEW.caja_movimiento_id AND tipo = 'EGRESO' AND monto_centavos = NEW.total_centavos)
BEGIN
    SELECT RAISE(ABORT, 'El egreso de caja de la compra no corresponde.');
END;

CREATE TRIGGER trg_compras_origen_pago_inmutable BEFORE UPDATE OF origen_pago, caja_movimiento_id ON compras
WHEN NEW.origen_pago IS NOT OLD.origen_pago OR NEW.caja_movimiento_id IS NOT OLD.caja_movimiento_id
BEGIN
    SELECT RAISE(ABORT, 'El origen del pago de una compra no se modifica.');
END;

CREATE TRIGGER trg_compras_reintegro_unico BEFORE UPDATE OF caja_movimiento_reintegro_id ON compras
WHEN NEW.caja_movimiento_reintegro_id IS NOT OLD.caja_movimiento_reintegro_id
     AND NOT (OLD.caja_movimiento_reintegro_id IS NULL
              AND OLD.origen_pago = 'CAJA_ACTUAL'
              AND NEW.estado = 'ANULADA'
              AND EXISTS (SELECT 1 FROM caja_movimientos
                          WHERE id = NEW.caja_movimiento_reintegro_id AND tipo = 'INGRESO'
                            AND monto_centavos = OLD.total_centavos))
BEGIN
    SELECT RAISE(ABORT, 'El reintegro de caja de una compra se registra una sola vez, al anularla.');
END;
