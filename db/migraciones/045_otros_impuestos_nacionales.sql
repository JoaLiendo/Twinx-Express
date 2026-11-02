-- Migración 045: «Otros Impuestos Nacionales Indirectos» (V1.16-H).
--
-- El Régimen de Transparencia Fiscal al Consumidor (RG ARCA/AFIP 5614/2024) exige mostrar el IVA Contenido
-- y los Otros Impuestos Nacionales Indirectos. Twinx no calcula ninguno de estos últimos: en vez de suponer
-- cero, cada producto se clasifica explícitamente.
--
--   * `productos_otros_impuestos`: decisión del dueño por producto, NO_APLICA (confirmado sin esos
--     impuestos: aporta $ 0,00) o REQUIERE_TRATAMIENTO (alcanzado por un impuesto que Twinx no calcula:
--     bloquea la fiscalización). La ausencia de fila es SIN_CLASIFICAR: todo producto histórico queda así
--     y nadie recibe NO_APLICA automáticamente. Tabla aparte, como `productos_fiscal`.
--   * `comprobante_fiscal_lineas.otros_impuestos_nacionales`: la clasificación congelada en el snapshot
--     (solo puede ser NO_APLICA). Las líneas ya existentes quedan en NULL: un comprobante histórico no
--     registró ese dato y no se le inventa (la tabla de líneas es inmutable por trigger desde la 041).
--   * Toda línea de una factura nueva (rol PRINCIPAL) debe llegar con NO_APLICA: la base rechaza una factura
--     cuyos productos no están confirmados, aunque el código de la aplicación fallara. Las notas de crédito
--     copian las líneas de su factura (NULL si esa factura es anterior a esta migración).
--
-- Sin datos que migrar: se verifica que ningún comprobante, línea, venta, producto ni caja cambie.

DROP TABLE IF EXISTS temp._fallas;
DROP TABLE IF EXISTS temp._antes;
CREATE TEMP TABLE _fallas (motivo TEXT NOT NULL);
CREATE TEMP TRIGGER _abortar_por_falla AFTER INSERT ON _fallas
BEGIN
    SELECT RAISE(ABORT, 'Migración 045: verificación fallida: ' || NEW.motivo);
END;

INSERT INTO _fallas SELECT 'ya existe la clasificación de otros impuestos nacionales indirectos'
WHERE EXISTS (SELECT 1 FROM sqlite_master WHERE name = 'productos_otros_impuestos')
   OR EXISTS (SELECT 1 FROM pragma_table_info('comprobante_fiscal_lineas') WHERE name = 'otros_impuestos_nacionales');
INSERT INTO _fallas SELECT 'foreign_key_check previo'
WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;

CREATE TEMP TABLE _antes AS
SELECT (SELECT COUNT(*) FROM productos)                                     AS productos_n,
       (SELECT COUNT(*) FROM productos_fiscal)                              AS productos_fiscal_n,
       (SELECT COUNT(*) FROM ventas)                                        AS ventas_n,
       (SELECT COALESCE(SUM(total_centavos), 0) FROM ventas)                AS ventas_total,
       (SELECT COUNT(*) FROM caja_movimientos)                              AS caja_n,
       (SELECT COUNT(*) FROM comprobantes_fiscales)                         AS comprobantes_n,
       (SELECT group_concat(id || '|' || estado || '|' || COALESCE(codigo_autorizacion, '') || '|' || imp_total_centavos
                            || '|' || imp_trib_centavos, ';')
        FROM (SELECT * FROM comprobantes_fiscales ORDER BY id))             AS comprobantes_huella,
       (SELECT COUNT(*) FROM comprobante_fiscal_lineas)                     AS lineas_n,
       (SELECT COALESCE(SUM(subtotal_centavos), 0) FROM comprobante_fiscal_lineas) AS lineas_total;

CREATE TABLE productos_otros_impuestos (
    producto_id          INTEGER PRIMARY KEY REFERENCES productos(id) ON DELETE RESTRICT,
    clasificacion        TEXT NOT NULL CHECK (clasificacion IN ('NO_APLICA', 'REQUIERE_TRATAMIENTO')),
    usuario_id           INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fecha_actualizacion  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

ALTER TABLE comprobante_fiscal_lineas ADD COLUMN otros_impuestos_nacionales TEXT NULL
    CHECK (otros_impuestos_nacionales IS NULL OR otros_impuestos_nacionales = 'NO_APLICA');

CREATE TRIGGER trg_comprobante_fiscal_lineas_otros_impuestos BEFORE INSERT ON comprobante_fiscal_lineas
WHEN NEW.otros_impuestos_nacionales IS NULL
     AND EXISTS (SELECT 1 FROM comprobantes_fiscales WHERE id = NEW.comprobante_id AND rol = 'PRINCIPAL')
BEGIN
    SELECT RAISE(ABORT, 'Una factura nueva exige productos confirmados sin otros impuestos nacionales indirectos.');
END;

-- Verificación ---------------------------------------------------------------------------------------

INSERT INTO _fallas SELECT 'cambiaron productos, IVA de productos, ventas, caja, comprobantes o sus líneas'
WHERE (SELECT COUNT(*) FROM productos) <> (SELECT productos_n FROM _antes)
   OR (SELECT COUNT(*) FROM productos_fiscal) <> (SELECT productos_fiscal_n FROM _antes)
   OR (SELECT COUNT(*) FROM ventas) <> (SELECT ventas_n FROM _antes)
   OR (SELECT COALESCE(SUM(total_centavos), 0) FROM ventas) <> (SELECT ventas_total FROM _antes)
   OR (SELECT COUNT(*) FROM caja_movimientos) <> (SELECT caja_n FROM _antes)
   OR (SELECT COUNT(*) FROM comprobantes_fiscales) <> (SELECT comprobantes_n FROM _antes)
   OR (SELECT group_concat(id || '|' || estado || '|' || COALESCE(codigo_autorizacion, '') || '|' || imp_total_centavos
                           || '|' || imp_trib_centavos, ';')
       FROM (SELECT * FROM comprobantes_fiscales ORDER BY id)) IS NOT (SELECT comprobantes_huella FROM _antes)
   OR (SELECT COUNT(*) FROM comprobante_fiscal_lineas) <> (SELECT lineas_n FROM _antes)
   OR (SELECT COALESCE(SUM(subtotal_centavos), 0) FROM comprobante_fiscal_lineas) <> (SELECT lineas_total FROM _antes);
INSERT INTO _fallas SELECT 'se inventó una clasificación de otros impuestos nacionales indirectos'
WHERE EXISTS (SELECT 1 FROM productos_otros_impuestos)
   OR EXISTS (SELECT 1 FROM comprobante_fiscal_lineas WHERE otros_impuestos_nacionales IS NOT NULL);
INSERT INTO _fallas SELECT 'foreign_key_check final'
WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;
INSERT INTO _fallas SELECT 'integrity_check final'
WHERE (SELECT integrity_check FROM pragma_integrity_check) <> 'ok';

DROP TRIGGER temp._abortar_por_falla;
DROP TABLE temp._fallas;
DROP TABLE temp._antes;
