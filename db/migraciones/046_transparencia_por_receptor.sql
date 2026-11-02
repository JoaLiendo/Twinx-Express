-- Migración 046: la transparencia fiscal de la clase C depende del receptor (corrige el alcance de la 045).
--
-- La RG ARCA/AFIP 5614/2024 exige los «Otros Impuestos Nacionales Indirectos» «de tratarse de las
-- operaciones detalladas en el artículo 99 de la Ley 27.743» (operaciones con consumidores finales), no por
-- la letra del comprobante. La 045 exigía NO_APLICA en toda línea de factura (rol PRINCIPAL); desde acá, una
-- factura C cuyo receptor no es consumidor final (condicion_iva_receptor_id <> 5) admite líneas sin
-- clasificar (NULL). Clases A y B, y la C a consumidor final, conservan la regla de la 045.
--
-- Solo se recrea el trigger, con el mismo nombre. REQUIERE_TRATAMIENTO sigue sin poder persistirse en una
-- línea (CHECK de la 045): la aplicación lo bloquea con cualquier receptor. Sin datos que migrar: se verifica
-- que ningún comprobante ni línea cambie.

DROP TABLE IF EXISTS temp._fallas;
DROP TABLE IF EXISTS temp._antes;
CREATE TEMP TABLE _fallas (motivo TEXT NOT NULL);
CREATE TEMP TRIGGER _abortar_por_falla AFTER INSERT ON _fallas
BEGIN
    SELECT RAISE(ABORT, 'Migración 046: verificación fallida: ' || NEW.motivo);
END;

INSERT INTO _fallas SELECT 'falta el trigger de la migración 045'
WHERE NOT EXISTS (SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = 'trg_comprobante_fiscal_lineas_otros_impuestos');

CREATE TEMP TABLE _antes AS
SELECT (SELECT COUNT(*) FROM comprobantes_fiscales)                         AS comprobantes_n,
       (SELECT group_concat(id || '|' || estado || '|' || COALESCE(codigo_autorizacion, '') || '|' || imp_total_centavos, ';')
        FROM (SELECT * FROM comprobantes_fiscales ORDER BY id))             AS comprobantes_huella,
       (SELECT COUNT(*) FROM comprobante_fiscal_lineas)                     AS lineas_n,
       (SELECT group_concat(id || '|' || COALESCE(otros_impuestos_nacionales, ''), ';')
        FROM (SELECT * FROM comprobante_fiscal_lineas ORDER BY id))         AS lineas_huella;

DROP TRIGGER trg_comprobante_fiscal_lineas_otros_impuestos;

CREATE TRIGGER trg_comprobante_fiscal_lineas_otros_impuestos BEFORE INSERT ON comprobante_fiscal_lineas
WHEN NEW.otros_impuestos_nacionales IS NULL
     AND EXISTS (
         SELECT 1 FROM comprobantes_fiscales
         WHERE id = NEW.comprobante_id AND rol = 'PRINCIPAL' AND (clase <> 'C' OR condicion_iva_receptor_id = 5)
     )
BEGIN
    SELECT RAISE(ABORT, 'Una factura nueva exige productos confirmados sin otros impuestos nacionales indirectos.');
END;

-- Verificación ---------------------------------------------------------------------------------------

INSERT INTO _fallas SELECT 'cambiaron comprobantes o sus líneas'
WHERE (SELECT COUNT(*) FROM comprobantes_fiscales) <> (SELECT comprobantes_n FROM _antes)
   OR (SELECT group_concat(id || '|' || estado || '|' || COALESCE(codigo_autorizacion, '') || '|' || imp_total_centavos, ';')
       FROM (SELECT * FROM comprobantes_fiscales ORDER BY id)) IS NOT (SELECT comprobantes_huella FROM _antes)
   OR (SELECT COUNT(*) FROM comprobante_fiscal_lineas) <> (SELECT lineas_n FROM _antes)
   OR (SELECT group_concat(id || '|' || COALESCE(otros_impuestos_nacionales, ''), ';')
       FROM (SELECT * FROM comprobante_fiscal_lineas ORDER BY id)) IS NOT (SELECT lineas_huella FROM _antes);
INSERT INTO _fallas SELECT 'integrity_check final'
WHERE (SELECT integrity_check FROM pragma_integrity_check) <> 'ok';

DROP TRIGGER temp._abortar_por_falla;
DROP TABLE temp._fallas;
DROP TABLE temp._antes;
