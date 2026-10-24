-- Migración 035: reclamos e incidentes con proveedores (V1.13-C).
--
-- Información objetiva, separada de las evaluaciones (034): un reclamo NUNCA cambia estrellas.
--
-- `reclamos_proveedor`: el hecho reclamado. Sus datos de origen son inmutables (proveedor, tipo,
-- descripción, relaciones, cantidad, usuario, fecha). Solo `estado` cambia, y únicamente a través de un
-- `reclamos_proveedor_cambios` (trigger que lo aplica y rechaza cualquier otra escritura): el estado
-- actual es siempre el `estado_nuevo` de su último cambio, o PENDIENTE si no tiene ninguno.
-- Nada se borra.
--
-- Relaciones opcionales, solo cuando se conocen de verdad (el servicio no las inventa):
--   * `producto_id`; `compra_id` (de ESTE proveedor) y `detalle_compra_id` (una línea de esa compra, del
--     mismo producto);
--   * `ajuste_id`: un ajuste FALLA_FABRICA del mismo producto, a lo sumo un reclamo por ajuste.
-- La base garantiza esa coherencia (trigger) además del servicio.
--
-- Transiciones (CHECK sobre cada cambio): PENDIENTE -> RECONOCIDO | RECHAZADO | RESUELTO;
-- RECONOCIDO -> RESUELTO | RECHAZADO; RECHAZADO | RESUELTO -> PENDIENTE (reabrir). Nunca al mismo
-- estado. Cada cambio exige una observación y registra usuario y fecha.
--
-- FK nuevas y migraciones futuras: `reclamos_proveedor` referencia (ON DELETE RESTRICT) a `proveedores`,
-- `productos`, `compras`, `detalle_compra`, `ajustes_stock` y `usuarios`. Una migración futura que
-- RECONSTRUYA alguna de esas tablas (DROP + CREATE, como 024/025/027/028) debe tratar a
-- `reclamos_proveedor` como tabla hija: con FK activas, el DROP de la madre falla mientras haya reclamos
-- que la referencien (ver el procedimiento documentado en la migración 027 para `inventario_lineas`).
-- Además, `trg_reclamos_proveedor_relaciones_coherentes` referencia `compras`, `detalle_compra` y
-- `ajustes_stock`: igual que el trigger de inventarios que obligó a 027 a evitar `ALTER TABLE ... RENAME`,
-- ese RENAME revalidaría este trigger.
--
-- Aditiva: no toca ninguna tabla existente. Cualquier fila en `_fallas` revierte TODO.

DROP TABLE IF EXISTS temp._fallas;

CREATE TEMP TABLE _fallas (motivo TEXT NOT NULL);
CREATE TEMP TRIGGER _abortar_por_falla AFTER INSERT ON _fallas
BEGIN
    SELECT RAISE(ABORT, 'Migración 035: verificación fallida: ' || NEW.motivo);
END;

INSERT INTO _fallas SELECT 'ya existen las tablas de reclamos'
WHERE EXISTS (SELECT 1 FROM sqlite_master WHERE name IN ('reclamos_proveedor', 'reclamos_proveedor_cambios'));
INSERT INTO _fallas SELECT 'falta la migración 034'
WHERE NOT EXISTS (SELECT 1 FROM sqlite_master WHERE name = 'evaluaciones_proveedor');
INSERT INTO _fallas SELECT 'las claves foráneas no están activas'
WHERE (SELECT foreign_keys FROM pragma_foreign_keys) <> 1;

CREATE TABLE reclamos_proveedor (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    proveedor_id        INTEGER NOT NULL REFERENCES proveedores(id) ON DELETE RESTRICT,
    tipo                TEXT NOT NULL CHECK (tipo IN ('FALLA_FABRICA', 'ENTREGA', 'CALIDAD', 'FACTURACION', 'OTRO')),
    estado              TEXT NOT NULL DEFAULT 'PENDIENTE'
                            CHECK (estado IN ('PENDIENTE', 'RECONOCIDO', 'RECHAZADO', 'RESUELTO')),
    descripcion         TEXT NOT NULL CHECK (length(trim(descripcion)) > 0 AND length(descripcion) <= 500),
    producto_id         INTEGER NULL REFERENCES productos(id) ON DELETE RESTRICT,
    compra_id           INTEGER NULL REFERENCES compras(id) ON DELETE RESTRICT,
    detalle_compra_id   INTEGER NULL REFERENCES detalle_compra(id) ON DELETE RESTRICT,
    ajuste_id           INTEGER NULL REFERENCES ajustes_stock(id) ON DELETE RESTRICT,
    cantidad            INTEGER NULL CHECK (cantidad IS NULL OR (typeof(cantidad) = 'integer' AND cantidad > 0)),
    usuario_id          INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fecha               TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    clave_idempotencia  TEXT NULL UNIQUE,
    CHECK (detalle_compra_id IS NULL OR (compra_id IS NOT NULL AND producto_id IS NOT NULL)),
    CHECK (ajuste_id IS NULL OR (tipo = 'FALLA_FABRICA' AND producto_id IS NOT NULL))
);

CREATE INDEX idx_reclamos_proveedor_proveedor_id ON reclamos_proveedor(proveedor_id, estado);
CREATE UNIQUE INDEX idx_reclamos_proveedor_ajuste_id ON reclamos_proveedor(ajuste_id) WHERE ajuste_id IS NOT NULL;

CREATE TABLE reclamos_proveedor_cambios (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    reclamo_id          INTEGER NOT NULL REFERENCES reclamos_proveedor(id) ON DELETE RESTRICT,
    estado_anterior     TEXT NOT NULL,
    estado_nuevo        TEXT NOT NULL,
    observacion         TEXT NOT NULL CHECK (length(trim(observacion)) > 0 AND length(observacion) <= 500),
    usuario_id          INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fecha               TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    clave_idempotencia  TEXT NULL UNIQUE,
    CHECK (estado_anterior || '>' || estado_nuevo IN (
        'PENDIENTE>RECONOCIDO', 'PENDIENTE>RECHAZADO', 'PENDIENTE>RESUELTO',
        'RECONOCIDO>RESUELTO', 'RECONOCIDO>RECHAZADO',
        'RECHAZADO>PENDIENTE', 'RESUELTO>PENDIENTE'
    ))
);

CREATE INDEX idx_reclamos_proveedor_cambios_reclamo_id ON reclamos_proveedor_cambios(reclamo_id, id);

-- Un reclamo nace PENDIENTE y con relaciones coherentes entre sí.
CREATE TRIGGER trg_reclamos_proveedor_relaciones_coherentes
BEFORE INSERT ON reclamos_proveedor
WHEN NEW.estado <> 'PENDIENTE'
     OR (NEW.compra_id IS NOT NULL
         AND NOT EXISTS (SELECT 1 FROM compras c WHERE c.id = NEW.compra_id AND c.proveedor_id = NEW.proveedor_id))
     OR (NEW.detalle_compra_id IS NOT NULL
         AND NOT EXISTS (SELECT 1 FROM detalle_compra d
                         WHERE d.id = NEW.detalle_compra_id AND d.compra_id = NEW.compra_id
                           AND d.producto_id = NEW.producto_id))
     OR (NEW.ajuste_id IS NOT NULL
         AND NOT EXISTS (SELECT 1 FROM ajustes_stock a
                         WHERE a.id = NEW.ajuste_id AND a.producto_id = NEW.producto_id
                           AND a.motivo = 'FALLA_FABRICA' AND a.delta < 0))
BEGIN
    SELECT RAISE(ABORT, 'Reclamo inválido: nace pendiente y sus relaciones (compra, línea, ajuste) deben ser coherentes.');
END;

CREATE TRIGGER trg_reclamos_proveedor_origen_inmutable
BEFORE UPDATE OF id, proveedor_id, tipo, descripcion, producto_id, compra_id, detalle_compra_id, ajuste_id,
                 cantidad, usuario_id, fecha, clave_idempotencia ON reclamos_proveedor
BEGIN
    SELECT RAISE(ABORT, 'Los datos de un reclamo no se pueden modificar: registrá un cambio de estado.');
END;

CREATE TRIGGER trg_reclamos_proveedor_estado_segun_cambios
BEFORE UPDATE OF estado ON reclamos_proveedor
WHEN NEW.estado IS NOT (SELECT estado_nuevo FROM reclamos_proveedor_cambios
                        WHERE reclamo_id = NEW.id ORDER BY id DESC LIMIT 1)
BEGIN
    SELECT RAISE(ABORT, 'El estado de un reclamo solo cambia registrando un cambio de estado.');
END;

CREATE TRIGGER trg_reclamos_proveedor_sin_borrado
BEFORE DELETE ON reclamos_proveedor
BEGIN
    SELECT RAISE(ABORT, 'Un reclamo no se puede borrar.');
END;

-- El cambio parte del estado vigente (no de uno ya superado) y lo aplica.
CREATE TRIGGER trg_reclamos_proveedor_cambios_desde_estado_vigente
BEFORE INSERT ON reclamos_proveedor_cambios
WHEN NEW.estado_anterior IS NOT (SELECT estado FROM reclamos_proveedor WHERE id = NEW.reclamo_id)
BEGIN
    SELECT RAISE(ABORT, 'El reclamo ya no está en el estado desde el que se quiso cambiar.');
END;

CREATE TRIGGER trg_reclamos_proveedor_cambios_aplicar
AFTER INSERT ON reclamos_proveedor_cambios
BEGIN
    UPDATE reclamos_proveedor SET estado = NEW.estado_nuevo WHERE id = NEW.reclamo_id;
END;

CREATE TRIGGER trg_reclamos_proveedor_cambios_inmutable
BEFORE UPDATE ON reclamos_proveedor_cambios
BEGIN
    SELECT RAISE(ABORT, 'Un cambio de estado de reclamo no se puede modificar.');
END;

CREATE TRIGGER trg_reclamos_proveedor_cambios_sin_borrado
BEFORE DELETE ON reclamos_proveedor_cambios
BEGIN
    SELECT RAISE(ABORT, 'Un cambio de estado de reclamo no se puede borrar.');
END;

INSERT INTO _fallas SELECT 'faltan índices/triggers esperados'
WHERE (SELECT COUNT(*) FROM sqlite_master
       WHERE name IN ('idx_reclamos_proveedor_proveedor_id', 'idx_reclamos_proveedor_ajuste_id',
                      'idx_reclamos_proveedor_cambios_reclamo_id',
                      'trg_reclamos_proveedor_relaciones_coherentes', 'trg_reclamos_proveedor_origen_inmutable',
                      'trg_reclamos_proveedor_estado_segun_cambios', 'trg_reclamos_proveedor_sin_borrado',
                      'trg_reclamos_proveedor_cambios_desde_estado_vigente', 'trg_reclamos_proveedor_cambios_aplicar',
                      'trg_reclamos_proveedor_cambios_inmutable', 'trg_reclamos_proveedor_cambios_sin_borrado')) <> 11;
INSERT INTO _fallas SELECT 'foreign_key_check final' WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;

DROP TABLE temp._fallas;
