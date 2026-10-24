-- Migración 034: evaluaciones de proveedores (V1.13-B).
--
-- Cada evaluación es un hecho histórico: estrellas generales (1 a 5, decisión humana, nunca un promedio),
-- comentario obligatorio, criterios complementarios opcionales (1 a 5), usuario y fecha. Corregir una
-- evaluación es registrar otra: las filas no se modifican ni se borran (triggers). La calificación actual
-- de un proveedor NO se guarda: es la de su evaluación de mayor id (la fecha tiene resolución de un
-- segundo y puede empatar; el id es AUTOINCREMENT), así nunca puede desincronizarse.
--
-- `proveedor_id` ON DELETE RESTRICT: un proveedor evaluado tiene historia y se da de baja lógicamente
-- (ver `db.repositorios.proveedores.eliminar_proveedor_en_conexion`). `clave_idempotencia` protege del
-- doble envío del formulario (mismo patrón que la migración 014).
--
-- Aditiva: no toca ninguna tabla existente. Cualquier fila en `_fallas` revierte TODO.

DROP TABLE IF EXISTS temp._fallas;

CREATE TEMP TABLE _fallas (motivo TEXT NOT NULL);
CREATE TEMP TRIGGER _abortar_por_falla AFTER INSERT ON _fallas
BEGIN
    SELECT RAISE(ABORT, 'Migración 034: verificación fallida: ' || NEW.motivo);
END;

INSERT INTO _fallas SELECT 'ya existe la tabla evaluaciones_proveedor'
WHERE EXISTS (SELECT 1 FROM sqlite_master WHERE name = 'evaluaciones_proveedor');
INSERT INTO _fallas SELECT 'falta la migración 033'
WHERE NOT EXISTS (SELECT 1 FROM sqlite_master WHERE name = 'proveedor_categorias');
INSERT INTO _fallas SELECT 'las claves foráneas no están activas'
WHERE (SELECT foreign_keys FROM pragma_foreign_keys) <> 1;

CREATE TABLE evaluaciones_proveedor (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    proveedor_id        INTEGER NOT NULL REFERENCES proveedores(id) ON DELETE RESTRICT,
    estrellas           INTEGER NOT NULL CHECK (typeof(estrellas) = 'integer' AND estrellas BETWEEN 1 AND 5),
    comentario          TEXT NOT NULL CHECK (length(trim(comentario)) > 0 AND length(comentario) <= 500),
    precio              INTEGER NULL CHECK (precio IS NULL OR (typeof(precio) = 'integer' AND precio BETWEEN 1 AND 5)),
    calidad             INTEGER NULL CHECK (calidad IS NULL OR (typeof(calidad) = 'integer' AND calidad BETWEEN 1 AND 5)),
    entrega             INTEGER NULL CHECK (entrega IS NULL OR (typeof(entrega) = 'integer' AND entrega BETWEEN 1 AND 5)),
    cumplimiento        INTEGER NULL CHECK (cumplimiento IS NULL OR (typeof(cumplimiento) = 'integer' AND cumplimiento BETWEEN 1 AND 5)),
    condiciones_pago    INTEGER NULL CHECK (condiciones_pago IS NULL OR (typeof(condiciones_pago) = 'integer' AND condiciones_pago BETWEEN 1 AND 5)),
    atencion            INTEGER NULL CHECK (atencion IS NULL OR (typeof(atencion) = 'integer' AND atencion BETWEEN 1 AND 5)),
    usuario_id          INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fecha               TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    clave_idempotencia  TEXT NULL UNIQUE
);

CREATE INDEX idx_evaluaciones_proveedor_proveedor_id ON evaluaciones_proveedor(proveedor_id, id);

CREATE TRIGGER trg_evaluaciones_proveedor_inmutable
BEFORE UPDATE ON evaluaciones_proveedor
BEGIN
    SELECT RAISE(ABORT, 'Una evaluación de proveedor no se puede modificar: registrá una nueva.');
END;

CREATE TRIGGER trg_evaluaciones_proveedor_sin_borrado
BEFORE DELETE ON evaluaciones_proveedor
BEGIN
    SELECT RAISE(ABORT, 'Una evaluación de proveedor no se puede borrar.');
END;

INSERT INTO _fallas SELECT 'faltan índices/triggers esperados'
WHERE (SELECT COUNT(*) FROM sqlite_master
       WHERE name IN ('idx_evaluaciones_proveedor_proveedor_id', 'trg_evaluaciones_proveedor_inmutable',
                      'trg_evaluaciones_proveedor_sin_borrado')) <> 3;
INSERT INTO _fallas SELECT 'foreign_key_check final' WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;

DROP TABLE temp._fallas;
