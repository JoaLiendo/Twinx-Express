-- Migración 029: stock inicial trazable (V1.11-C).
--
-- Desde esta versión, un producto creado con stock > 0 registra ese stock como un movimiento
-- propio: un ajuste de motivo interno `STOCK_INICIAL` (anterior 0, delta = resultante = stock de
-- alta). Se modela en `ajustes_stock` y no en una tabla aparte porque es exactamente su forma
-- (delta, stock anterior/resultante, fecha, usuario, snapshot de costo), el kardex ya lo lee y la
-- futura partida de V1.12 podrá referenciar su origen con la misma FK que un RECUENTO positivo.
--
-- Cambios sobre `ajustes_stock`:
--   * motivo `STOCK_INICIAL` en el CHECK (origen interno: no es manual, ni pérdida, ni RECUENTO);
--   * `usuario_id` pasa a admitir NULL SOLO para `STOCK_INICIAL` (CHECK de tabla): un alta sin
--     usuario real (CLI, importación sin sesión) no inventa responsable. El resto de los motivos
--     sigue exigiéndolo igual que antes;
--   * a lo sumo un `STOCK_INICIAL` por producto (índice único parcial);
--   * `trg_ajustes_stock_stock_inicial_valido`: anterior 0, delta > 0, resultante = stock actual
--     del producto, sin inventario ni reversa, y que sea el primer ajuste del producto;
--   * `trg_ajustes_stock_reversa_valida` también rechaza revertir un `STOCK_INICIAL` (un stock de
--     alta mal cargado se corrige con un inventario físico, igual que un RECUENTO).
--
-- Productos históricos: NO se infiere ningún STOCK_INICIAL (ni fecha, ni responsable, ni costo):
-- su stock de alta sigue dentro del saldo inicial reconstruido del kardex. Se verifica abajo.
--
-- Reconstrucción: mismo guion que 027/028 (ver 027 para el detalle de cada paso): desarmar
-- `inventario_lineas.ajuste_id` (hijo ON DELETE RESTRICT) con su guardia, DROP + CREATE directo
-- en vez de `ALTER TABLE ... RENAME TO`, restaurar y verificar todo. Diferencia: desde 028 la tabla
-- se autorreferencia (`reversa_de_id`), así que también se desarma esa referencia y los datos se
-- restauran desde la copia temporal (ver paso 3). El runner corre el script en un único
-- `BEGIN IMMEDIATE`: cualquier fila en `_fallas` revierte TODO.

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
    SELECT RAISE(ABORT, 'Migración 029: verificación fallida: ' || NEW.motivo);
END;

-- 0. Guardias previos -------------------------------------------------------------------

INSERT INTO _fallas SELECT 'la tabla ajustes_stock no tiene las columnas esperadas'
WHERE (SELECT group_concat(name, ',') FROM (SELECT name FROM pragma_table_info('ajustes_stock') ORDER BY cid))
      IS NOT 'id,producto_id,usuario_id,motivo,delta,stock_anterior,stock_resultante,observaciones,fecha,'
             || 'clave_idempotencia,inventario_id,costo_unitario_centavos,reversa_de_id';
INSERT INTO _fallas SELECT 'objeto inesperado sobre ajustes_stock: ' || name
FROM sqlite_master
WHERE tbl_name = 'ajustes_stock' AND type IN ('index', 'trigger')
  AND name NOT IN (
      'ajustes_stock', 'idx_ajustes_stock_producto_id', 'idx_ajustes_stock_clave_idempotencia',
      'idx_ajustes_stock_inventario_producto', 'idx_ajustes_stock_reversa_de_id',
      'trg_ajustes_stock_inventario_valido', 'trg_ajustes_stock_inventario_inmutable',
      'trg_ajustes_stock_reversa_valida', 'trg_ajustes_stock_reversa_inmutable'
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

-- 1. Snapshots ---------------------------------------------------------------------------

CREATE TEMP TABLE _antes AS
SELECT
    (SELECT COUNT(*) FROM ajustes_stock)                                   AS ajustes_n,
    (SELECT COALESCE(SUM(delta), 0) FROM ajustes_stock)                    AS ajustes_delta,
    (SELECT seq FROM sqlite_sequence WHERE name = 'ajustes_stock')         AS seq_ajustes,
    (SELECT COUNT(*) FROM inventario_lineas WHERE ajuste_id IS NOT NULL)   AS lineas_con_ajuste_n,
    (SELECT COUNT(*) FROM productos)                                       AS productos_n,
    (SELECT COALESCE(SUM(stock_actual), 0) FROM productos)                 AS productos_stock;

CREATE TEMP TABLE _ajustes_copia AS SELECT * FROM ajustes_stock;
CREATE TEMP TABLE _lineas_ajuste_copia AS
    SELECT id, ajuste_id FROM inventario_lineas WHERE ajuste_id IS NOT NULL;
-- Todos los objetos propios de ajustes_stock salvo la reversa válida, que esta migración cambia a propósito.
CREATE TEMP TABLE _objetos_ajustes_antes AS
    SELECT name, sql FROM sqlite_master
    WHERE tbl_name = 'ajustes_stock' AND type IN ('index', 'trigger') AND sql IS NOT NULL
      AND name <> 'trg_ajustes_stock_reversa_valida';
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

-- `reversa_de_id` es una autorreferencia ON DELETE RESTRICT: con reversas históricas, el DELETE
-- implícito del DROP fallaría al borrar un original antes que su reversa (comprobado empíricamente).
-- Se desarma igual que `inventario_lineas.ajuste_id` (su guardia de inmutabilidad se va con la tabla)
-- y se restaura desde `_ajustes_copia`. A diferencia de 027/028 no hay tabla intermedia
-- `ajustes_stock_nueva`: con FK hacia la tabla vieja, sus propias filas bloquearían el DROP.
DROP TRIGGER trg_ajustes_stock_reversa_inmutable;
UPDATE ajustes_stock SET reversa_de_id = NULL WHERE reversa_de_id IS NOT NULL;

DROP TABLE ajustes_stock;

CREATE TABLE ajustes_stock (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id             INTEGER NOT NULL REFERENCES productos(id) ON DELETE RESTRICT,
    usuario_id              INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
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
                                    'REVERSA',
                                    'STOCK_INICIAL'
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
    reversa_de_id           INTEGER NULL REFERENCES ajustes_stock(id) ON DELETE RESTRICT,
    CHECK (usuario_id IS NOT NULL OR motivo = 'STOCK_INICIAL')
);

INSERT INTO ajustes_stock
    (id, producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones, fecha,
     clave_idempotencia, inventario_id, costo_unitario_centavos, reversa_de_id)
SELECT
     id, producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones, fecha,
     clave_idempotencia, inventario_id, costo_unitario_centavos, reversa_de_id
FROM _ajustes_copia
ORDER BY id;

-- La secuencia AUTOINCREMENT no puede retroceder.
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
CREATE UNIQUE INDEX idx_ajustes_stock_un_stock_inicial_por_producto ON ajustes_stock(producto_id)
    WHERE motivo = 'STOCK_INICIAL';

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

-- Igual que en 028, más: un STOCK_INICIAL tampoco se revierte.
CREATE TRIGGER trg_ajustes_stock_reversa_valida
BEFORE INSERT ON ajustes_stock
WHEN (NEW.motivo = 'REVERSA') <> (NEW.reversa_de_id IS NOT NULL)
     OR (
         NEW.reversa_de_id IS NOT NULL
         AND (
             (SELECT producto_id FROM ajustes_stock WHERE id = NEW.reversa_de_id) <> NEW.producto_id
             OR (SELECT motivo FROM ajustes_stock WHERE id = NEW.reversa_de_id) IN ('RECUENTO', 'STOCK_INICIAL')
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

-- El stock de alta es el primer movimiento del producto: parte de 0, suma, y deja exactamente el
-- stock que el producto tiene en ese momento (se inserta en la misma transacción que el alta).
CREATE TRIGGER trg_ajustes_stock_stock_inicial_valido
BEFORE INSERT ON ajustes_stock
WHEN NEW.motivo = 'STOCK_INICIAL'
     AND (
         NEW.stock_anterior <> 0
         OR NEW.delta <= 0
         OR NEW.inventario_id IS NOT NULL
         OR NEW.reversa_de_id IS NOT NULL
         OR (SELECT stock_actual FROM productos WHERE id = NEW.producto_id) IS NOT NEW.stock_resultante
         OR EXISTS (SELECT 1 FROM ajustes_stock WHERE producto_id = NEW.producto_id)
     )
BEGIN
    SELECT RAISE(ABORT, 'El stock inicial no es válido para este producto.');
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
           fecha, clave_idempotencia, inventario_id, costo_unitario_centavos, reversa_de_id FROM _ajustes_copia
    EXCEPT
    SELECT id, producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones,
           fecha, clave_idempotencia, inventario_id, costo_unitario_centavos, reversa_de_id FROM ajustes_stock
);
INSERT INTO _fallas SELECT 'ajustes_stock: hay una fila que no estaba en la original'
WHERE EXISTS (
    SELECT id, producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones,
           fecha, clave_idempotencia, inventario_id, costo_unitario_centavos, reversa_de_id FROM ajustes_stock
    EXCEPT
    SELECT id, producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones,
           fecha, clave_idempotencia, inventario_id, costo_unitario_centavos, reversa_de_id FROM _ajustes_copia
);
INSERT INTO _fallas SELECT 'se infirió un STOCK_INICIAL para un producto histórico'
WHERE EXISTS (SELECT 1 FROM ajustes_stock WHERE motivo = 'STOCK_INICIAL');
INSERT INTO _fallas SELECT 'un ajuste histórico quedó sin usuario'
WHERE EXISTS (SELECT 1 FROM ajustes_stock WHERE usuario_id IS NULL);
INSERT INTO _fallas SELECT 'productos: cantidad o stock cambió'
WHERE (SELECT COUNT(*) FROM productos) <> (SELECT productos_n FROM _antes)
   OR (SELECT COALESCE(SUM(stock_actual), 0) FROM productos) <> (SELECT productos_stock FROM _antes);
INSERT INTO _fallas SELECT 'suma de delta de ajustes_stock'
WHERE (SELECT COALESCE(SUM(delta), 0) FROM ajustes_stock) <> (SELECT ajustes_delta FROM _antes);
INSERT INTO _fallas SELECT 'sqlite_sequence de ajustes_stock'
WHERE (SELECT seq FROM sqlite_sequence WHERE name = 'ajustes_stock') < (SELECT seq_ajustes FROM _antes);
INSERT INTO _fallas SELECT 'faltan índices/triggers esperados de ajustes_stock'
WHERE (SELECT COUNT(*) FROM sqlite_master WHERE tbl_name = 'ajustes_stock' AND type IN ('index', 'trigger')
       AND name IN ('idx_ajustes_stock_producto_id', 'idx_ajustes_stock_clave_idempotencia',
                    'idx_ajustes_stock_inventario_producto', 'idx_ajustes_stock_reversa_de_id',
                    'idx_ajustes_stock_un_stock_inicial_por_producto',
                    'trg_ajustes_stock_inventario_valido', 'trg_ajustes_stock_inventario_inmutable',
                    'trg_ajustes_stock_reversa_valida', 'trg_ajustes_stock_reversa_inmutable',
                    'trg_ajustes_stock_stock_inicial_valido')) <> 10;
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
