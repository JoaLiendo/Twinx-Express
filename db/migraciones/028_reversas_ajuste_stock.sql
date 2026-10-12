-- Migración 028: reversas explícitas y auditables de ajustes manuales de stock (V1.11-B).
--
-- Agrega:
--   * motivo `REVERSA` a `ajustes_stock.motivo`.
--   * `ajustes_stock.reversa_de_id` (nullable, autorreferencia a `ajustes_stock.id`): el ajuste
--     original que una REVERSA corrige. Un ajuste original nunca se borra ni se edita (es
--     histórico e inmutable, ver `services.servicio_stock.revertir_ajuste`): la corrección es
--     siempre un ajuste NUEVO, vinculado al original por esta columna.
--   * índice único parcial sobre `reversa_de_id`: a lo sumo una reversa por ajuste original, sin
--     depender solo de la validación de la interfaz.
--   * dos triggers que impiden, por acceso directo a la base, una reversa inconsistente: que no
--     sea motivo REVERSA, que no apunte a un producto distinto del original, que no aplique
--     exactamente `-delta` del original, que reviertan un RECUENTO (eso se corrige con un nuevo
--     inventario, no desde este flujo) o que reviertan otra reversa (una reversa no se revierte);
--     y que `reversa_de_id` no pueda modificarse después de creado.
--
-- RECONSTRUCCIÓN DE `ajustes_stock`
-- Ampliar el CHECK de `motivo` no se puede con ALTER TABLE, así que se reconstruye otra vez
-- (segunda vez sobre esta tabla, ver migración 027 para el detalle completo del porqué de cada
-- paso). Mismo guion exacto que 027, con las mismas dos particularidades que motivaron:
--   * `inventario_lineas.ajuste_id` (ON DELETE RESTRICT) se desarma antes del DROP y se restaura
--     después, con su guardia de inmutabilidad desactivado y reactivado alrededor;
--   * el swap final usa CREATE+DROP con nombre temporal, no `ALTER TABLE ... RENAME TO`
--     (ese RENAME revalida toda la base y rompe por `trg_inventarios_confirmar_con_ajustes`,
--     que referencia `ajustes_stock` desde otra tabla).
--
-- Orden: 0) guardias previos y snapshots; 1) desarmar inventario_lineas.ajuste_id; 2) reconstruir
-- ajustes_stock (con REVERSA y reversa_de_id); 3) restaurar inventario_lineas.ajuste_id y su
-- guardia; 4) verificación exhaustiva. El runner corre este script dentro de un único
-- `BEGIN IMMEDIATE`: cualquier fila insertada en `_fallas` aborta y revierte TODO.

DROP TABLE IF EXISTS temp._fallas;
DROP TABLE IF EXISTS temp._antes;
DROP TABLE IF EXISTS temp._ajustes_copia;
DROP TABLE IF EXISTS temp._lineas_ajuste_copia;
DROP TABLE IF EXISTS temp._objetos_ajustes_antes;
DROP TABLE IF EXISTS temp._objetos_lineas_antes;
DROP TABLE IF EXISTS temp._objeto_trigger_lineas;

CREATE TEMP TABLE _fallas (motivo TEXT NOT NULL);
CREATE TEMP TRIGGER _abortar_por_falla AFTER INSERT ON _fallas
BEGIN
    SELECT RAISE(ABORT, 'Migración 028: verificación fallida: ' || NEW.motivo);
END;

-- 0. Guardias previos -------------------------------------------------------------------

INSERT INTO _fallas SELECT 'la tabla ajustes_stock no tiene las columnas esperadas'
WHERE (SELECT group_concat(name, ',') FROM (SELECT name FROM pragma_table_info('ajustes_stock') ORDER BY cid))
      IS NOT 'id,producto_id,usuario_id,motivo,delta,stock_anterior,stock_resultante,observaciones,fecha,'
             || 'clave_idempotencia,inventario_id,costo_unitario_centavos';
INSERT INTO _fallas SELECT 'objeto inesperado sobre ajustes_stock: ' || name
FROM sqlite_master
WHERE tbl_name = 'ajustes_stock' AND type IN ('index', 'trigger')
  AND name NOT IN (
      'ajustes_stock', 'idx_ajustes_stock_producto_id', 'idx_ajustes_stock_clave_idempotencia',
      'idx_ajustes_stock_inventario_producto',
      'trg_ajustes_stock_inventario_valido', 'trg_ajustes_stock_inventario_inmutable'
  );
INSERT INTO _fallas SELECT 'vista inesperada que referencia ajustes_stock: ' || name
FROM sqlite_master WHERE type = 'view' AND sql LIKE '%ajustes_stock%';
INSERT INTO _fallas SELECT 'inventario_lineas.ajuste_id no es ON DELETE RESTRICT'
WHERE NOT EXISTS (SELECT 1 FROM pragma_foreign_key_list('inventario_lineas')
                  WHERE "table" = 'ajustes_stock' AND "from" = 'ajuste_id' AND on_delete = 'RESTRICT');
INSERT INTO _fallas SELECT 'foreign_key_check previo'
WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;
INSERT INTO _fallas SELECT 'integrity_check previo'
WHERE (SELECT integrity_check FROM pragma_integrity_check) <> 'ok';
INSERT INTO _fallas SELECT 'las claves foráneas no están activas'
WHERE (SELECT foreign_keys FROM pragma_foreign_keys) <> 1;
INSERT INTO _fallas SELECT 'ya existe algún objeto temporal de esta migración'
WHERE EXISTS (SELECT 1 FROM sqlite_master WHERE name IN ('ajustes_stock_nueva'));

-- 1. Snapshots ---------------------------------------------------------------------------

CREATE TEMP TABLE _antes AS
SELECT
    (SELECT COUNT(*) FROM ajustes_stock)                                   AS ajustes_n,
    (SELECT COALESCE(SUM(delta), 0) FROM ajustes_stock)                    AS ajustes_delta,
    (SELECT seq FROM sqlite_sequence WHERE name = 'ajustes_stock')         AS seq_ajustes,
    (SELECT COUNT(*) FROM inventario_lineas WHERE ajuste_id IS NOT NULL)   AS lineas_con_ajuste_n;

CREATE TEMP TABLE _ajustes_copia AS SELECT * FROM ajustes_stock;
CREATE TEMP TABLE _lineas_ajuste_copia AS
    SELECT id, ajuste_id FROM inventario_lineas WHERE ajuste_id IS NOT NULL;
CREATE TEMP TABLE _objetos_ajustes_antes AS
    SELECT name, sql FROM sqlite_master WHERE tbl_name = 'ajustes_stock' AND type IN ('index', 'trigger') AND sql IS NOT NULL;
CREATE TEMP TABLE _objetos_lineas_antes AS
    SELECT name, sql FROM sqlite_master WHERE tbl_name = 'inventario_lineas' AND type IN ('index', 'trigger');
CREATE TEMP TABLE _objeto_trigger_lineas AS
    SELECT sql FROM sqlite_master WHERE name = 'trg_inventario_lineas_modificar_solo_abierto';

INSERT INTO _fallas SELECT 'no se encontró trg_inventario_lineas_modificar_solo_abierto para recrear'
WHERE (SELECT COUNT(*) FROM _objeto_trigger_lineas) <> 1;

-- 2. Desarmar temporalmente la referencia de inventario_lineas hacia ajustes_stock -------

DROP TRIGGER trg_inventario_lineas_modificar_solo_abierto;

UPDATE inventario_lineas SET ajuste_id = NULL WHERE ajuste_id IS NOT NULL;

-- 3. Reconstrucción de ajustes_stock ------------------------------------------------------

CREATE TABLE ajustes_stock_nueva (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id             INTEGER NOT NULL REFERENCES productos(id) ON DELETE RESTRICT,
    usuario_id              INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    motivo                  TEXT NOT NULL CHECK (
                                motivo IN (
                                    'MERMA',
                                    'ROTURA',
                                    'VENCIMIENTO',
                                    'PERDIDA',
                                    'ROBO',
                                    'RECUENTO',
                                    'OTRO',
                                    'FALLA_FABRICA',
                                    'REVERSA'
                                )
                            ),
    delta                   INTEGER NOT NULL CHECK (delta != 0),
    stock_anterior          INTEGER NOT NULL CHECK (stock_anterior >= 0),
    stock_resultante        INTEGER NOT NULL CHECK (stock_resultante >= 0),
    observaciones           TEXT NULL,
    fecha                   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    clave_idempotencia      TEXT NULL,
    inventario_id           INTEGER NULL REFERENCES inventarios(id) ON DELETE RESTRICT,
    costo_unitario_centavos INTEGER NULL CHECK (costo_unitario_centavos IS NULL OR costo_unitario_centavos >= 0),
    reversa_de_id           INTEGER NULL REFERENCES ajustes_stock(id) ON DELETE RESTRICT
);

INSERT INTO ajustes_stock_nueva
    (id, producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones, fecha,
     clave_idempotencia, inventario_id, costo_unitario_centavos)
SELECT
     id, producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones, fecha,
     clave_idempotencia, inventario_id, costo_unitario_centavos
FROM ajustes_stock
ORDER BY id;

-- Ya sin ningún hijo con filas que la referencien (paso 2): el DELETE implícito del DROP no viola el RESTRICT.
DROP TABLE ajustes_stock;

CREATE TABLE ajustes_stock (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id             INTEGER NOT NULL REFERENCES productos(id) ON DELETE RESTRICT,
    usuario_id              INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    motivo                  TEXT NOT NULL CHECK (
                                motivo IN (
                                    'MERMA',
                                    'ROTURA',
                                    'VENCIMIENTO',
                                    'PERDIDA',
                                    'ROBO',
                                    'RECUENTO',
                                    'OTRO',
                                    'FALLA_FABRICA',
                                    'REVERSA'
                                )
                            ),
    delta                   INTEGER NOT NULL CHECK (delta != 0),
    stock_anterior          INTEGER NOT NULL CHECK (stock_anterior >= 0),
    stock_resultante        INTEGER NOT NULL CHECK (stock_resultante >= 0),
    observaciones           TEXT NULL,
    fecha                   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    clave_idempotencia      TEXT NULL,
    inventario_id           INTEGER NULL REFERENCES inventarios(id) ON DELETE RESTRICT,
    costo_unitario_centavos INTEGER NULL CHECK (costo_unitario_centavos IS NULL OR costo_unitario_centavos >= 0),
    reversa_de_id           INTEGER NULL REFERENCES ajustes_stock(id) ON DELETE RESTRICT
);

INSERT INTO ajustes_stock
    (id, producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones, fecha,
     clave_idempotencia, inventario_id, costo_unitario_centavos)
SELECT
     id, producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones, fecha,
     clave_idempotencia, inventario_id, costo_unitario_centavos
FROM ajustes_stock_nueva
ORDER BY id;

DROP TABLE ajustes_stock_nueva;

-- La secuencia AUTOINCREMENT no puede retroceder: se conserva la anterior si era mayor que la que
-- las dos copias con id explícito ya dejaron.
UPDATE sqlite_sequence SET seq = (SELECT seq_ajustes FROM _antes)
WHERE name = 'ajustes_stock'
  AND (SELECT seq_ajustes FROM _antes) IS NOT NULL
  AND seq < (SELECT seq_ajustes FROM _antes);
INSERT INTO sqlite_sequence (name, seq)
SELECT 'ajustes_stock', seq_ajustes FROM _antes
WHERE seq_ajustes IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'ajustes_stock');

CREATE INDEX idx_ajustes_stock_producto_id ON ajustes_stock(producto_id);
CREATE UNIQUE INDEX idx_ajustes_stock_clave_idempotencia ON ajustes_stock(clave_idempotencia);
CREATE UNIQUE INDEX idx_ajustes_stock_inventario_producto ON ajustes_stock(inventario_id, producto_id)
    WHERE inventario_id IS NOT NULL;
-- A lo sumo una reversa por ajuste original: no depende solo de la validación de la interfaz.
CREATE UNIQUE INDEX idx_ajustes_stock_reversa_de_id ON ajustes_stock(reversa_de_id)
    WHERE reversa_de_id IS NOT NULL;

CREATE TRIGGER trg_ajustes_stock_inventario_valido
BEFORE INSERT ON ajustes_stock
WHEN NEW.inventario_id IS NOT NULL
     AND (
         NEW.motivo <> 'RECUENTO'
         OR (SELECT estado FROM inventarios WHERE id = NEW.inventario_id) IS NOT 'ABIERTO'
         OR NOT EXISTS (
             SELECT 1 FROM inventario_lineas l
             WHERE l.inventario_id = NEW.inventario_id
               AND l.producto_id = NEW.producto_id
               AND l.cantidad_contada IS NOT NULL
               AND l.stock_esperado = NEW.stock_anterior
               AND NEW.delta = l.cantidad_contada - l.stock_esperado
         )
     )
BEGIN
    SELECT RAISE(ABORT, 'El ajuste no corresponde a una línea contada de un inventario abierto.');
END;

CREATE TRIGGER trg_ajustes_stock_inventario_inmutable
BEFORE UPDATE OF inventario_id ON ajustes_stock
BEGIN
    SELECT RAISE(ABORT, 'El inventario de un ajuste no se puede modificar.');
END;

-- Una REVERSA siempre tiene reversa_de_id, y solo una REVERSA puede tenerlo (consistencia
-- estructural; ver también domain.ajuste_stock.AjusteStock). El ajuste original debe existir (lo
-- garantiza la FK), ser del mismo producto, no ser RECUENTO (eso se corrige con un nuevo
-- inventario), no ser ya una reversa (una reversa no se revierte) y la reversa debe aplicar
-- exactamente -delta del original.
CREATE TRIGGER trg_ajustes_stock_reversa_valida
BEFORE INSERT ON ajustes_stock
WHEN (NEW.motivo = 'REVERSA') <> (NEW.reversa_de_id IS NOT NULL)
     OR (
         NEW.reversa_de_id IS NOT NULL
         AND (
             (SELECT producto_id FROM ajustes_stock WHERE id = NEW.reversa_de_id) <> NEW.producto_id
             OR (SELECT motivo FROM ajustes_stock WHERE id = NEW.reversa_de_id) = 'RECUENTO'
             OR (SELECT reversa_de_id FROM ajustes_stock WHERE id = NEW.reversa_de_id) IS NOT NULL
             OR (SELECT delta FROM ajustes_stock WHERE id = NEW.reversa_de_id) <> -NEW.delta
         )
     )
BEGIN
    SELECT RAISE(ABORT, 'La reversa no es válida para el ajuste original indicado.');
END;

CREATE TRIGGER trg_ajustes_stock_reversa_inmutable
BEFORE UPDATE OF reversa_de_id ON ajustes_stock
BEGIN
    SELECT RAISE(ABORT, 'El ajuste original de una reversa no se puede modificar.');
END;

-- 4. Restauración de inventario_lineas.ajuste_id y su guardia ----------------------------

UPDATE inventario_lineas
SET ajuste_id = (SELECT c.ajuste_id FROM _lineas_ajuste_copia c WHERE c.id = inventario_lineas.id)
WHERE id IN (SELECT id FROM _lineas_ajuste_copia);

CREATE TRIGGER trg_inventario_lineas_modificar_solo_abierto
BEFORE UPDATE ON inventario_lineas
WHEN (SELECT estado FROM inventarios WHERE id = OLD.inventario_id) IS NOT 'ABIERTO'
BEGIN
    SELECT RAISE(ABORT, 'Las líneas de un inventario cerrado no se pueden modificar.');
END;

-- 5. Verificación ---------------------------------------------------------------------------

INSERT INTO _fallas SELECT 'ajustes_stock: cantidad de filas'
WHERE (SELECT COUNT(*) FROM ajustes_stock) <> (SELECT ajustes_n FROM _antes);
INSERT INTO _fallas SELECT 'ajustes_stock: una fila difiere de la original (o falta)'
WHERE EXISTS (
    SELECT id, producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones,
           fecha, clave_idempotencia, inventario_id, costo_unitario_centavos FROM _ajustes_copia
    EXCEPT
    SELECT id, producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones,
           fecha, clave_idempotencia, inventario_id, costo_unitario_centavos FROM ajustes_stock
);
INSERT INTO _fallas SELECT 'ajustes_stock: hay una fila que no estaba en la original'
WHERE EXISTS (
    SELECT id, producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones,
           fecha, clave_idempotencia, inventario_id, costo_unitario_centavos FROM ajustes_stock
    EXCEPT
    SELECT id, producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones,
           fecha, clave_idempotencia, inventario_id, costo_unitario_centavos FROM _ajustes_copia
);
INSERT INTO _fallas SELECT 'ajustes_stock: alguna fila histórica quedó con reversa_de_id (debería ser NULL)'
WHERE EXISTS (SELECT 1 FROM ajustes_stock WHERE reversa_de_id IS NOT NULL);
INSERT INTO _fallas SELECT 'suma de delta de ajustes_stock'
WHERE (SELECT COALESCE(SUM(delta), 0) FROM ajustes_stock) <> (SELECT ajustes_delta FROM _antes);
INSERT INTO _fallas SELECT 'sqlite_sequence de ajustes_stock'
WHERE (SELECT seq FROM sqlite_sequence WHERE name = 'ajustes_stock') < (SELECT seq_ajustes FROM _antes);
INSERT INTO _fallas SELECT 'quedó una tabla ajustes_stock_nueva'
WHERE EXISTS (SELECT 1 FROM sqlite_master WHERE name = 'ajustes_stock_nueva');
INSERT INTO _fallas SELECT 'motivo inválido aceptado por el CHECK'
WHERE EXISTS (
    SELECT 1 FROM ajustes_stock
    WHERE motivo NOT IN ('MERMA', 'ROTURA', 'VENCIMIENTO', 'PERDIDA', 'ROBO', 'RECUENTO', 'OTRO',
                         'FALLA_FABRICA', 'REVERSA')
);
INSERT INTO _fallas SELECT 'faltan índices/triggers esperados de ajustes_stock'
WHERE (SELECT COUNT(*) FROM sqlite_master WHERE tbl_name = 'ajustes_stock' AND type IN ('index', 'trigger')
       AND name IN ('idx_ajustes_stock_producto_id', 'idx_ajustes_stock_clave_idempotencia',
                    'idx_ajustes_stock_inventario_producto', 'idx_ajustes_stock_reversa_de_id',
                    'trg_ajustes_stock_inventario_valido', 'trg_ajustes_stock_inventario_inmutable',
                    'trg_ajustes_stock_reversa_valida', 'trg_ajustes_stock_reversa_inmutable')) <> 8;
INSERT INTO _fallas SELECT 'los índices/triggers recreados de ajustes_stock no son idénticos a los originales: ' || a.name
FROM _objetos_ajustes_antes a
WHERE (SELECT sql FROM sqlite_master WHERE name = a.name AND tbl_name = 'ajustes_stock') IS NOT a.sql;

INSERT INTO _fallas SELECT 'inventario_lineas: cantidad de líneas con ajuste_id cambió'
WHERE (SELECT COUNT(*) FROM inventario_lineas WHERE ajuste_id IS NOT NULL) <> (SELECT lineas_con_ajuste_n FROM _antes);
INSERT INTO _fallas SELECT 'inventario_lineas: ajuste_id no se restauró igual (o falta)'
WHERE EXISTS (
    SELECT id, ajuste_id FROM _lineas_ajuste_copia
    EXCEPT
    SELECT id, ajuste_id FROM inventario_lineas WHERE ajuste_id IS NOT NULL
);
INSERT INTO _fallas SELECT 'trg_inventario_lineas_modificar_solo_abierto no quedó idéntico al original'
WHERE (SELECT sql FROM sqlite_master WHERE name = 'trg_inventario_lineas_modificar_solo_abierto')
      IS NOT (SELECT sql FROM _objeto_trigger_lineas);
INSERT INTO _fallas SELECT 'otros índices/triggers de inventario_lineas no quedaron idénticos: ' || a.name
FROM _objetos_lineas_antes a
WHERE (SELECT sql FROM sqlite_master WHERE name = a.name AND tbl_name = 'inventario_lineas') IS NOT a.sql;
INSERT INTO _fallas SELECT 'cambió la cantidad de índices/triggers de inventario_lineas'
WHERE (SELECT COUNT(*) FROM sqlite_master WHERE tbl_name = 'inventario_lineas' AND type IN ('index', 'trigger'))
      <> (SELECT COUNT(*) FROM _objetos_lineas_antes);

INSERT INTO _fallas SELECT 'foreign_key_check final' WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;
INSERT INTO _fallas SELECT 'integrity_check final'
WHERE (SELECT integrity_check FROM pragma_integrity_check) <> 'ok';

DROP TABLE temp._fallas;
DROP TABLE temp._antes;
DROP TABLE temp._ajustes_copia;
DROP TABLE temp._lineas_ajuste_copia;
DROP TABLE temp._objetos_ajustes_antes;
DROP TABLE temp._objetos_lineas_antes;
DROP TABLE temp._objeto_trigger_lineas;
