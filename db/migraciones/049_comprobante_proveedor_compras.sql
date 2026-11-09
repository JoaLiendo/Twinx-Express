-- Migración 049: identificación del comprobante que entrega el proveedor en una compra (cierre operativo, B2).
--
--   * `compras.comprobante_tipo`: FACTURA / REMITO / TICKET / OTRO, o NULL si no se cargó.
--   * `compras.comprobante_numero`: el número tal como figura en el papel (texto: "0001-00012345", "R-77"...).
--     NUNCA se confunde con el id interno de la compra, que sigue siendo `compras.id`.
--   * `compras.comprobante_fecha`: la fecha del comprobante del proveedor (puede diferir de `compras.fecha`,
--     que es cuando se cargó en Twinx).
-- Los tres son opcionales y editables mientras la compra está ACTIVA (lo audita el servicio). Las compras
-- históricas quedan en NULL: no se infiere nada. Un mismo proveedor no puede tener dos compras ACTIVAS con el
-- mismo tipo y número de comprobante (índice único parcial: al anular una, su número vuelve a estar libre).

ALTER TABLE compras ADD COLUMN comprobante_tipo TEXT NULL
    CHECK (comprobante_tipo IS NULL OR comprobante_tipo IN ('FACTURA', 'REMITO', 'TICKET', 'OTRO'));
ALTER TABLE compras ADD COLUMN comprobante_numero TEXT NULL
    CHECK (comprobante_numero IS NULL OR length(trim(comprobante_numero)) > 0);
ALTER TABLE compras ADD COLUMN comprobante_fecha TEXT NULL
    CHECK (comprobante_fecha IS NULL OR (length(comprobante_fecha) = 10 AND comprobante_fecha = date(comprobante_fecha)));

CREATE UNIQUE INDEX idx_compras_comprobante_proveedor
    ON compras(proveedor_id, comprobante_tipo, comprobante_numero)
    WHERE comprobante_numero IS NOT NULL AND estado = 'ACTIVA';
CREATE INDEX idx_compras_comprobante_numero ON compras(comprobante_numero) WHERE comprobante_numero IS NOT NULL;

-- Un número sin tipo (o al revés) no identifica nada: van juntos o no van.
CREATE TRIGGER trg_compras_comprobante_completo_insert BEFORE INSERT ON compras
WHEN (NEW.comprobante_tipo IS NULL) <> (NEW.comprobante_numero IS NULL)
     OR (NEW.comprobante_fecha IS NOT NULL AND NEW.comprobante_numero IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'El comprobante del proveedor requiere tipo y número.');
END;

CREATE TRIGGER trg_compras_comprobante_completo_update BEFORE UPDATE OF comprobante_tipo, comprobante_numero, comprobante_fecha ON compras
WHEN (NEW.comprobante_tipo IS NULL) <> (NEW.comprobante_numero IS NULL)
     OR (NEW.comprobante_fecha IS NOT NULL AND NEW.comprobante_numero IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'El comprobante del proveedor requiere tipo y número.');
END;

-- Una compra anulada conserva el comprobante con el que se cargó.
CREATE TRIGGER trg_compras_comprobante_anulada_inmutable BEFORE UPDATE OF comprobante_tipo, comprobante_numero, comprobante_fecha ON compras
WHEN OLD.estado = 'ANULADA'
     AND (NEW.comprobante_tipo IS NOT OLD.comprobante_tipo OR NEW.comprobante_numero IS NOT OLD.comprobante_numero
          OR NEW.comprobante_fecha IS NOT OLD.comprobante_fecha)
BEGIN
    SELECT RAISE(ABORT, 'El comprobante de una compra anulada no se modifica.');
END;
