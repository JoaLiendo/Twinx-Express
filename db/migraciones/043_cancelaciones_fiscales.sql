-- Migración 043: cancelación fiscal de una venta con nota de crédito (V1.16-F).
--
-- No existe una transacción distribuida SQLite + ARCA. El orden seguro es:
--   1. registrar la intención (esta tabla, PENDIENTE) junto con la nota de crédito PENDIENTE;
--   2. autorizar la NC en ARCA;
--   3. recién con la NC autorizada, aplicar el efecto comercial (stock, partidas, venta ANULADA) y
--      marcar la cancelación APLICADA, todo en UNA transacción local.
-- Un fallo de red en el paso 2 deja la NC INCIERTA y la cancelación PENDIENTE: nada local cambió. Cuando la
-- conciliación confirma la NC, el paso 3 se aplica una sola vez (la transición PENDIENTE -> APLICADA es
-- única). Una NC rechazada deja la cancelación RECHAZADA y la venta intacta. SIN_APLICAR: la NC quedó
-- autorizada pero el efecto comercial ya no puede aplicarse (p. ej. la caja de la venta se cerró mientras
-- la NC estaba incierta): queda a la vista del dueño, nunca se aplica en silencio sobre un cierre.

CREATE TABLE cancelaciones_fiscales (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    venta_id                 INTEGER NOT NULL REFERENCES ventas(id) ON DELETE RESTRICT,
    comprobante_original_id  INTEGER NOT NULL REFERENCES comprobantes_fiscales(id) ON DELETE RESTRICT,
    nota_credito_id          INTEGER NOT NULL UNIQUE REFERENCES comprobantes_fiscales(id) ON DELETE RESTRICT,
    motivo                   TEXT NOT NULL CHECK (motivo IN ('ERROR_CARGA', 'ARREPENTIMIENTO_CLIENTE', 'PRODUCTO_INCORRECTO', 'OTRO')),
    observaciones            TEXT NULL CHECK (observaciones IS NULL OR (length(trim(observaciones)) > 0 AND length(observaciones) <= 500)),
    estado                   TEXT NOT NULL DEFAULT 'PENDIENTE' CHECK (estado IN ('PENDIENTE', 'APLICADA', 'RECHAZADA', 'SIN_APLICAR')),
    detalle                  TEXT NULL CHECK (detalle IS NULL OR length(detalle) <= 300),
    usuario_id               INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fecha_creacion           TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    fecha_actualizacion      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK (motivo <> 'OTRO' OR observaciones IS NOT NULL)
);

-- Una sola cancelación viva por venta (doble NC imposible aunque lleguen dos pedidos a la vez).
CREATE UNIQUE INDEX idx_cancelaciones_fiscales_venta_viva
    ON cancelaciones_fiscales(venta_id) WHERE estado IN ('PENDIENTE', 'APLICADA', 'SIN_APLICAR');

CREATE TRIGGER trg_cancelaciones_fiscales_alta BEFORE INSERT ON cancelaciones_fiscales
BEGIN
    SELECT RAISE(ABORT, 'Una cancelación nace pendiente.') WHERE NEW.estado <> 'PENDIENTE';
    SELECT RAISE(ABORT, 'La cancelación referencia la nota de crédito de la factura de esa venta.')
    WHERE NOT EXISTS (SELECT 1 FROM comprobantes_fiscales n
                      WHERE n.id = NEW.nota_credito_id AND n.rol = 'NOTA_CREDITO'
                        AND n.comprobante_asociado_id = NEW.comprobante_original_id AND n.venta_id = NEW.venta_id);
END;

CREATE TRIGGER trg_cancelaciones_fiscales_transicion BEFORE UPDATE ON cancelaciones_fiscales
WHEN NEW.id IS NOT OLD.id OR NEW.venta_id IS NOT OLD.venta_id OR NEW.comprobante_original_id IS NOT OLD.comprobante_original_id
     OR NEW.nota_credito_id IS NOT OLD.nota_credito_id OR NEW.motivo IS NOT OLD.motivo
     OR NEW.observaciones IS NOT OLD.observaciones OR NEW.usuario_id IS NOT OLD.usuario_id
     OR NEW.fecha_creacion IS NOT OLD.fecha_creacion
     OR (NEW.estado IS NOT OLD.estado AND (OLD.estado || '>' || NEW.estado) NOT IN (
            'PENDIENTE>APLICADA', 'PENDIENTE>RECHAZADA', 'PENDIENTE>SIN_APLICAR', 'SIN_APLICAR>APLICADA'))
BEGIN
    SELECT RAISE(ABORT, 'Transición de cancelación fiscal no permitida.');
END;

-- APLICADA exige la NC autorizada y la venta ya anulada en la misma transacción.
CREATE TRIGGER trg_cancelaciones_fiscales_aplicada BEFORE UPDATE OF estado ON cancelaciones_fiscales
WHEN NEW.estado = 'APLICADA' AND OLD.estado <> 'APLICADA'
     AND (NOT EXISTS (SELECT 1 FROM comprobantes_fiscales WHERE id = NEW.nota_credito_id
                      AND estado IN ('AUTORIZADO', 'CAEA_PENDIENTE_INFORMAR', 'CAEA_INFORMADO', 'CAEA_INFORME_RECHAZADO'))
          OR NOT EXISTS (SELECT 1 FROM ventas WHERE id = NEW.venta_id AND estado = 'ANULADA'))
BEGIN
    SELECT RAISE(ABORT, 'Una cancelación solo se aplica con la nota de crédito autorizada y la venta anulada.');
END;

CREATE TRIGGER trg_cancelaciones_fiscales_no_borrables BEFORE DELETE ON cancelaciones_fiscales
BEGIN
    SELECT RAISE(ABORT, 'Las cancelaciones fiscales no se borran.');
END;
