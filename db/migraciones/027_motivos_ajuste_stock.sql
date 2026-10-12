-- Migración 027: nuevos motivos manuales de ajuste de stock y snapshot de costo (V1.11-A).
--
-- Agrega:
--   * motivo `FALLA_FABRICA` a `ajustes_stock.motivo` (junto con `VENCIMIENTO`, `ROTURA`, `ROBO`,
--     ya existentes desde la migración 012, estos cuatro son los únicos motivos manuales vigentes
--     desde esta versión -- ver `domain.ajuste_stock.MOTIVOS_MANUALES_PERMITIDOS`). `MERMA`,
--     `PERDIDA` y `OTRO` quedan como motivos legacy: el CHECK los sigue aceptando para no romper
--     filas históricas, pero `services.servicio_stock.ajustar_stock` ya no los admite para
--     ajustes nuevos (la restricción es de aplicación, no de esquema).
--   * `ajustes_stock.costo_unitario_centavos` (nullable): snapshot del costo vigente del producto
--     al momento del ajuste, igual criterio que `inventario_lineas.costo_unitario_centavos`
--     (migración 022). Filas históricas quedan NULL -- no se inventa ningún costo retroactivo.
--
-- RECONSTRUCCIÓN DE `ajustes_stock`
-- Ampliar el CHECK de `motivo` no se puede con ALTER TABLE, así que se reconstruye. A diferencia de
-- `ventas`/`detalle_venta` (migración 020, hijo ON DELETE CASCADE que se vacía solo) y como
-- `caja_movimientos`/`movimientos_cuenta` (migración 025, hijo ON DELETE RESTRICT), `ajustes_stock`
-- tiene un hijo ON DELETE RESTRICT: `inventario_lineas.ajuste_id`. Con las FK activas (no se pueden
-- desactivar dentro de esta transacción), un `DROP TABLE ajustes_stock` con alguna fila de
-- `inventario_lineas.ajuste_id` todavía apuntando a ella falla con "FOREIGN KEY constraint failed".
--
-- A diferencia de 025 (que resolvía esto recreando temporalmente VACÍA la tabla hija completa,
-- porque esa era además una reconstrucción de la tabla hija), acá alcanza con desarmar solo la
-- referencia: ninguna otra columna de `inventario_lineas` cambia. Pero `inventario_lineas` tiene un
-- guardia (`trg_inventario_lineas_modificar_solo_abierto`) que bloquea CUALQUIER UPDATE sobre una
-- línea de un inventario que ya no está ABIERTO -- y las líneas con `ajuste_id` no NULL son
-- justamente las de inventarios ya CONFIRMADOS. Por eso el guardia se desactiva (DROP TRIGGER),
-- se pone `ajuste_id = NULL` donde corresponda, se reconstruye `ajustes_stock`, se restaura
-- `ajuste_id` (mismos ids: `ajustes_stock` conserva los suyos) y se recrea el guardia idéntico.
--
-- Además: a diferencia de 020/024/025 (que usan `ALTER TABLE ... RENAME TO` para el swap final),
-- acá se evita el RENAME y se usa CREATE+DROP con un nombre temporal. Motivo (comprobado
-- empíricamente en una base de prueba): `ALTER TABLE ... RENAME TO` revalida toda la base en busca
-- de triggers/vistas que referencien CUALQUIER tabla, y `trg_inventarios_confirmar_con_ajustes`
-- (sobre `inventarios`, migración 022) referencia `ajustes_stock` -- con la tabla momentáneamente
-- ausente entre el DROP y el RENAME, ese trigger (ajeno a la tabla que se está renombrando) rompe
-- el RENAME con "no such table: ajustes_stock". Un `CREATE TABLE ajustes_stock` directo (sin
-- RENAME) no dispara esa revalidación y permite recrearla con su nombre final sin ese problema.
--
-- Orden:
--   1. guardias previos y snapshots (ajustes_stock, inventario_lineas.ajuste_id, objetos de ambas);
--   2. DROP del guardia de inmutabilidad de `inventario_lineas` y `ajuste_id = NULL` donde corresponda;
--   3. reconstrucción de `ajustes_stock` (tabla temporal, copia, DROP, CREATE final, copia de vuelta,
--      secuencia, índices, triggers propios);
--   4. restauración de `inventario_lineas.ajuste_id` y recreación del guardia;
--   5. verificación exhaustiva fila por fila, de secuencia, de objetos, `foreign_key_check` e
--      `integrity_check`.
-- El runner corre este script dentro de un único `BEGIN IMMEDIATE`: cualquier fila insertada en
-- `_fallas` aborta y revierte TODO, y la migración no se registra.

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
    SELECT RAISE(ABORT, 'Migración 027: verificación fallida: ' || NEW.motivo);
END;

-- 0. Guardias previos -------------------------------------------------------------------

INSERT INTO _fallas SELECT 'la tabla ajustes_stock no tiene las columnas esperadas'
WHERE (SELECT group_concat(name, ',') FROM (SELECT name FROM pragma_table_info('ajustes_stock') ORDER BY cid))
      IS NOT 'id,producto_id,usuario_id,motivo,delta,stock_anterior,stock_resultante,observaciones,fecha,'
             || 'clave_idempotencia,inventario_id';
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
-- El guardia bloquea cualquier UPDATE sobre una línea de un inventario no ABIERTO (las líneas con
-- ajuste_id ya son de inventarios CONFIRMADOS): se quita, se desreferencia y se recrea más abajo,
-- idéntico, recién cuando ya no hace falta otro UPDATE sobre estas líneas.

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
                                    'FALLA_FABRICA'
                                )
                            ),
    delta                   INTEGER NOT NULL CHECK (delta != 0),
    stock_anterior          INTEGER NOT NULL CHECK (stock_anterior >= 0),
    stock_resultante        INTEGER NOT NULL CHECK (stock_resultante >= 0),
    observaciones           TEXT NULL,
    fecha                   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    clave_idempotencia      TEXT NULL,
    inventario_id           INTEGER NULL REFERENCES inventarios(id) ON DELETE RESTRICT,
    costo_unitario_centavos INTEGER NULL CHECK (costo_unitario_centavos IS NULL OR costo_unitario_centavos >= 0)
);

INSERT INTO ajustes_stock_nueva
    (id, producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones, fecha,
     clave_idempotencia, inventario_id)
SELECT
     id, producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones, fecha,
     clave_idempotencia, inventario_id
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
                                    'FALLA_FABRICA'
                                )
                            ),
    delta                   INTEGER NOT NULL CHECK (delta != 0),
    stock_anterior          INTEGER NOT NULL CHECK (stock_anterior >= 0),
    stock_resultante        INTEGER NOT NULL CHECK (stock_resultante >= 0),
    observaciones           TEXT NULL,
    fecha                   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    clave_idempotencia      TEXT NULL,
    inventario_id           INTEGER NULL REFERENCES inventarios(id) ON DELETE RESTRICT,
    costo_unitario_centavos INTEGER NULL CHECK (costo_unitario_centavos IS NULL OR costo_unitario_centavos >= 0)
);

INSERT INTO ajustes_stock
    (id, producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones, fecha,
     clave_idempotencia, inventario_id)
SELECT
     id, producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones, fecha,
     clave_idempotencia, inventario_id
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

-- 4. Restauración de inventario_lineas.ajuste_id y su guardia ----------------------------
-- Mismos ids en ajustes_stock (copia explícita arriba): las referencias siguen siendo válidas.

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
           fecha, clave_idempotencia, inventario_id FROM _ajustes_copia
    EXCEPT
    SELECT id, producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones,
           fecha, clave_idempotencia, inventario_id FROM ajustes_stock
);
INSERT INTO _fallas SELECT 'ajustes_stock: hay una fila que no estaba en la original'
WHERE EXISTS (
    SELECT id, producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones,
           fecha, clave_idempotencia, inventario_id FROM ajustes_stock
    EXCEPT
    SELECT id, producto_id, usuario_id, motivo, delta, stock_anterior, stock_resultante, observaciones,
           fecha, clave_idempotencia, inventario_id FROM _ajustes_copia
);
INSERT INTO _fallas SELECT 'ajustes_stock: alguna fila histórica no quedó con costo_unitario_centavos NULL'
WHERE EXISTS (SELECT 1 FROM ajustes_stock WHERE costo_unitario_centavos IS NOT NULL);
INSERT INTO _fallas SELECT 'suma de delta de ajustes_stock'
WHERE (SELECT COALESCE(SUM(delta), 0) FROM ajustes_stock) <> (SELECT ajustes_delta FROM _antes);
INSERT INTO _fallas SELECT 'sqlite_sequence de ajustes_stock'
WHERE (SELECT seq FROM sqlite_sequence WHERE name = 'ajustes_stock') < (SELECT seq_ajustes FROM _antes);
INSERT INTO _fallas SELECT 'quedó una tabla ajustes_stock_nueva'
WHERE EXISTS (SELECT 1 FROM sqlite_master WHERE name = 'ajustes_stock_nueva');
INSERT INTO _fallas SELECT 'motivo inválido aceptado por el CHECK'
WHERE EXISTS (
    SELECT 1 FROM ajustes_stock
    WHERE motivo NOT IN ('MERMA', 'ROTURA', 'VENCIMIENTO', 'PERDIDA', 'ROBO', 'RECUENTO', 'OTRO', 'FALLA_FABRICA')
);
INSERT INTO _fallas SELECT 'faltan índices/triggers esperados de ajustes_stock'
WHERE (SELECT COUNT(*) FROM sqlite_master WHERE tbl_name = 'ajustes_stock' AND type IN ('index', 'trigger')
       AND name IN ('idx_ajustes_stock_producto_id', 'idx_ajustes_stock_clave_idempotencia',
                    'idx_ajustes_stock_inventario_producto', 'trg_ajustes_stock_inventario_valido',
                    'trg_ajustes_stock_inventario_inmutable')) <> 5;
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
