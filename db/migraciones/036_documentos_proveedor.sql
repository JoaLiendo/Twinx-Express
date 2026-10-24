-- Migración 036: documentación fiscal declarada de proveedores (V1.13-D).
--
-- Una estructura chica y extensible en vez de una columna por documento: cada fila es un documento que
-- el OWNER declara haber recibido o consultado (constancia fiscal, Ingresos Brutos, CM05 u otro), con
-- su fecha, una referencia opcional (período, número) y una observación. Solo metadata: no se guardan
-- archivos, no se valida contra ningún servicio externo y nada deduce de acá una vigencia legal.
--
-- Historia: las filas no se modifican ni se borran (una corrección es un documento nuevo con su
-- observación). `proveedor_id` ON DELETE RESTRICT: un proveedor con documentación tiene historia.
--
-- Aditiva: no toca ninguna tabla existente. Cualquier fila en `_fallas` revierte TODO.

DROP TABLE IF EXISTS temp._fallas;

CREATE TEMP TABLE _fallas (motivo TEXT NOT NULL);
CREATE TEMP TRIGGER _abortar_por_falla AFTER INSERT ON _fallas
BEGIN
    SELECT RAISE(ABORT, 'Migración 036: verificación fallida: ' || NEW.motivo);
END;

INSERT INTO _fallas SELECT 'ya existe la tabla documentos_proveedor'
WHERE EXISTS (SELECT 1 FROM sqlite_master WHERE name = 'documentos_proveedor');
INSERT INTO _fallas SELECT 'falta la migración 035'
WHERE NOT EXISTS (SELECT 1 FROM sqlite_master WHERE name = 'reclamos_proveedor');
INSERT INTO _fallas SELECT 'las claves foráneas no están activas'
WHERE (SELECT foreign_keys FROM pragma_foreign_keys) <> 1;

CREATE TABLE documentos_proveedor (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    proveedor_id        INTEGER NOT NULL REFERENCES proveedores(id) ON DELETE RESTRICT,
    tipo                TEXT NOT NULL CHECK (tipo IN ('CONSTANCIA_FISCAL', 'INGRESOS_BRUTOS', 'CM05', 'OTRO_DOCUMENTO_FISCAL')),
    fecha_documento     TEXT NOT NULL CHECK (length(fecha_documento) = 10 AND date(fecha_documento) IS fecha_documento),
    referencia          TEXT NULL CHECK (referencia IS NULL OR (length(trim(referencia)) > 0 AND length(referencia) <= 100)),
    observacion         TEXT NULL CHECK (observacion IS NULL OR (length(trim(observacion)) > 0 AND length(observacion) <= 500)),
    usuario_id          INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fecha_registro      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    clave_idempotencia  TEXT NULL UNIQUE
);

CREATE INDEX idx_documentos_proveedor_proveedor_id ON documentos_proveedor(proveedor_id, fecha_documento);

CREATE TRIGGER trg_documentos_proveedor_inmutable
BEFORE UPDATE ON documentos_proveedor
BEGIN
    SELECT RAISE(ABORT, 'Un documento de proveedor no se puede modificar: registrá uno nuevo.');
END;

CREATE TRIGGER trg_documentos_proveedor_sin_borrado
BEFORE DELETE ON documentos_proveedor
BEGIN
    SELECT RAISE(ABORT, 'Un documento de proveedor no se puede borrar.');
END;

INSERT INTO _fallas SELECT 'faltan índices/triggers esperados'
WHERE (SELECT COUNT(*) FROM sqlite_master
       WHERE name IN ('idx_documentos_proveedor_proveedor_id', 'trg_documentos_proveedor_inmutable',
                      'trg_documentos_proveedor_sin_borrado')) <> 3;
INSERT INTO _fallas SELECT 'foreign_key_check final' WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;

DROP TABLE temp._fallas;
