-- Migración 024: anulación de compras y trazabilidad del costo (V1.7-B).
--
-- Agrega:
--   * `compras`: `estado` (ACTIVA/ANULADA), motivo, observaciones, usuario y fecha de anulación, y
--     `costo_trazable` (1 solo para compras registradas con esta trazabilidad);
--   * `detalle_compra.historial_precio_id`: el evento de `historial_precios` que produjo el cambio de
--     costo de esa línea (NULL si no cambió el costo);
--   * `historial_precios.origen = 'ANULACION_COMPRA'`.
--
-- Compras históricas: quedan `ACTIVA`, `costo_trazable = 0` y sin `historial_precio_id`. Nunca se infiere
-- qué evento de precio produjo una compra vieja (ni por fecha, ni por valor, ni por origen): al anularlas
-- solo se revierte el stock y el costo se conserva.
--
-- RECONSTRUCCIÓN DE `historial_precios`
-- Ampliar el CHECK de `origen` no se puede con ALTER TABLE. La tabla no es referenciada por ninguna otra
-- (se verifica), así que se reconstruye con las FK activas: copia de resguardo, tabla nueva, copia fila por
-- fila conservando los `id`, DROP, RENAME, índices y verificación. El runner corre el script dentro de un
-- único `BEGIN IMMEDIATE`: cualquier fila insertada en `_fallas` aborta y revierte TODO.

DROP TABLE IF EXISTS temp._fallas;
DROP TABLE IF EXISTS temp._historial_copia;
DROP TABLE IF EXISTS temp._historial_secuencia;

CREATE TEMP TABLE _fallas (motivo TEXT NOT NULL);
CREATE TEMP TRIGGER _abortar_por_falla AFTER INSERT ON _fallas
BEGIN
    SELECT RAISE(ABORT, 'Migración 024: verificación fallida: ' || NEW.motivo);
END;

INSERT INTO _fallas SELECT 'la tabla historial_precios no tiene las columnas esperadas'
WHERE (SELECT group_concat(name, ',') FROM (SELECT name FROM pragma_table_info('historial_precios') ORDER BY cid))
      IS NOT 'id,producto_id,usuario_id,fecha,campo,precio_anterior_centavos,precio_nuevo_centavos,origen,lote_id';
INSERT INTO _fallas SELECT 'objeto inesperado que referencia historial_precios: ' || name
FROM sqlite_master
WHERE sql LIKE '%historial_precios%'
  AND name NOT IN ('historial_precios', 'idx_historial_precios_producto_id', 'idx_historial_precios_lote_id');

CREATE TEMP TABLE _historial_copia AS SELECT * FROM historial_precios;
CREATE TEMP TABLE _historial_secuencia AS SELECT seq FROM sqlite_sequence WHERE name = 'historial_precios';

CREATE TABLE historial_precios_nueva (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id              INTEGER NOT NULL REFERENCES productos(id) ON DELETE RESTRICT,
    usuario_id               INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fecha                    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    campo                    TEXT NOT NULL CHECK (campo IN ('VENTA', 'COSTO')),
    precio_anterior_centavos INTEGER NOT NULL CHECK (precio_anterior_centavos >= 0),
    precio_nuevo_centavos    INTEGER NOT NULL CHECK (precio_nuevo_centavos >= 0),
    origen                   TEXT NOT NULL CHECK (
                                 origen IN ('EDICION', 'COMPRA', 'MASIVA', 'IMPORTACION', 'ANULACION_COMPRA')
                             ),
    lote_id                  INTEGER NULL REFERENCES lotes_precios(id) ON DELETE RESTRICT,
    CHECK (precio_anterior_centavos != precio_nuevo_centavos)
);

INSERT INTO historial_precios_nueva
    (id, producto_id, usuario_id, fecha, campo, precio_anterior_centavos, precio_nuevo_centavos, origen, lote_id)
SELECT id, producto_id, usuario_id, fecha, campo, precio_anterior_centavos, precio_nuevo_centavos, origen, lote_id
FROM historial_precios ORDER BY id;

DROP TABLE historial_precios;
ALTER TABLE historial_precios_nueva RENAME TO historial_precios;

CREATE INDEX idx_historial_precios_producto_id ON historial_precios(producto_id);
CREATE INDEX idx_historial_precios_lote_id ON historial_precios(lote_id);

-- La secuencia AUTOINCREMENT no puede retroceder: se conserva la anterior si era mayor.
UPDATE sqlite_sequence
SET seq = (SELECT seq FROM _historial_secuencia)
WHERE name = 'historial_precios'
  AND EXISTS (SELECT 1 FROM _historial_secuencia)
  AND seq < (SELECT seq FROM _historial_secuencia);

INSERT INTO _fallas SELECT 'historial_precios: distinta cantidad de filas'
WHERE (SELECT COUNT(*) FROM historial_precios) != (SELECT COUNT(*) FROM _historial_copia);
INSERT INTO _fallas SELECT 'historial_precios: fila alterada (id ' || c.id || ')'
FROM _historial_copia c
LEFT JOIN historial_precios h ON h.id = c.id
WHERE h.id IS NULL
   OR h.producto_id IS NOT c.producto_id OR h.usuario_id IS NOT c.usuario_id OR h.fecha IS NOT c.fecha
   OR h.campo IS NOT c.campo OR h.precio_anterior_centavos IS NOT c.precio_anterior_centavos
   OR h.precio_nuevo_centavos IS NOT c.precio_nuevo_centavos OR h.origen IS NOT c.origen
   OR h.lote_id IS NOT c.lote_id;
INSERT INTO _fallas SELECT 'historial_precios: faltan índices'
WHERE (SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' AND tbl_name = 'historial_precios'
       AND name IN ('idx_historial_precios_producto_id', 'idx_historial_precios_lote_id')) != 2;

-- Compras: estado y datos de la anulación ------------------------------------------------------

ALTER TABLE compras ADD COLUMN estado TEXT NOT NULL DEFAULT 'ACTIVA' CHECK (estado IN ('ACTIVA', 'ANULADA'));
ALTER TABLE compras ADD COLUMN motivo_anulacion TEXT NULL CHECK (
    motivo_anulacion IS NULL
    OR motivo_anulacion IN ('ERROR_CARGA', 'MERCADERIA_NO_RECIBIDA', 'DEVOLUCION_A_PROVEEDOR', 'OTRO')
);
ALTER TABLE compras ADD COLUMN observaciones_anulacion TEXT NULL;
ALTER TABLE compras ADD COLUMN anulada_por_usuario_id INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT;
ALTER TABLE compras ADD COLUMN fecha_anulacion TEXT NULL;
ALTER TABLE compras ADD COLUMN costo_trazable INTEGER NOT NULL DEFAULT 0 CHECK (costo_trazable IN (0, 1));

ALTER TABLE detalle_compra ADD COLUMN historial_precio_id INTEGER NULL REFERENCES historial_precios(id) ON DELETE RESTRICT;

INSERT INTO _fallas SELECT 'foreign_key_check con violaciones' WHERE EXISTS (SELECT 1 FROM pragma_foreign_key_check);
INSERT INTO _fallas SELECT 'integrity_check no devolvió ok'
WHERE (SELECT integrity_check FROM pragma_integrity_check LIMIT 1) IS NOT 'ok';

DROP TABLE temp._fallas;
DROP TABLE temp._historial_copia;
DROP TABLE temp._historial_secuencia;
