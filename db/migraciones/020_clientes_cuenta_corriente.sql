-- Migración 020: clientes y cuenta corriente (V1.3).
--
-- Agrega:
--   * `clientes` (baja lógica) y `ventas.cliente_id`;
--   * `ventas.tipo_pago = 'CUENTA_CORRIENTE'` (exige cliente);
--   * `movimientos_cuenta`: libro append-only de CARGO (venta a cuenta) y COBRO
--     (pago en efectivo del cliente, respaldado por un INGRESO de caja);
--   * `caja_movimientos.origen` (MANUAL / COBRO_CUENTA).
--
-- RECONSTRUCCIÓN DE `ventas` (estrategia A)
-- Ampliar el CHECK de `tipo_pago` no se puede con ALTER TABLE, así que `ventas`
-- se reconstruye. Sin `PRAGMA foreign_keys` (no puede cambiarse dentro de la
-- transacción) y con las FK ACTIVAS:
--   1. guardias previos y snapshots (copias temporales de `ventas` y `detalle_venta`);
--   2. se crea `ventas_nueva` con todas las columnas actuales (mismo orden) + `cliente_id`;
--   3. se copian las filas conservando los `id`;
--   4. `DROP TABLE ventas`: con las FK activas SQLite borra implícitamente las
--      filas de `ventas` y el `ON DELETE CASCADE` vacía `detalle_venta` (se
--      verifica que quedó vacía: si las FK no estuvieran activas, se aborta);
--   5. `RENAME ventas_nueva -> ventas`;
--   6. se restaura `detalle_venta` desde su copia y `sqlite_sequence`;
--   7. se recrean los índices y triggers de `ventas` (idénticos a los de la
--      migración 019) y se crean los nuevos;
--   8. verificación fila por fila, de secuencias, de esquema, `foreign_key_check`
--      e `integrity_check`.
-- El runner corre este script dentro de un único `BEGIN IMMEDIATE`: cualquier
-- fila insertada en `_fallas` aborta y revierte TODO, y la migración no se registra.
--
-- Las ventas históricas quedan con `cliente_id = NULL`: nunca se infiere un cliente.
-- Los movimientos de caja históricos quedan con `origen = 'MANUAL'` (DEFAULT).

-- 0. Guardias previos ------------------------------------------------------------

DROP TABLE IF EXISTS temp._fallas;
DROP TABLE IF EXISTS temp._antes;
DROP TABLE IF EXISTS temp._ventas_copia;
DROP TABLE IF EXISTS temp._detalle_copia;
DROP TABLE IF EXISTS temp._objetos_ventas_antes;

CREATE TEMP TABLE _fallas (motivo TEXT NOT NULL);
CREATE TEMP TRIGGER _abortar_por_falla AFTER INSERT ON _fallas
BEGIN
    SELECT RAISE(ABORT, 'Migración 020: verificación fallida: ' || NEW.motivo);
END;

INSERT INTO _fallas SELECT 'la tabla ventas no tiene las columnas esperadas'
WHERE (SELECT group_concat(name, ',') FROM (SELECT name FROM pragma_table_info('ventas') ORDER BY cid))
      IS NOT 'id,fecha,total_centavos,tipo_pago,clave_idempotencia,contenido_hash,usuario_id,estado,'
             || 'motivo_anulacion,observaciones_anulacion,anulada_por_usuario_id,fecha_anulacion,sesion_caja_id';
INSERT INTO _fallas SELECT 'la tabla detalle_venta no tiene las columnas esperadas'
WHERE (SELECT group_concat(name, ',') FROM (SELECT name FROM pragma_table_info('detalle_venta') ORDER BY cid))
      IS NOT 'id,venta_id,producto_id,cantidad,precio_unitario_centavos,subtotal_centavos,costo_unitario_centavos';
INSERT INTO _fallas SELECT 'objeto inesperado que referencia ventas: ' || name
FROM sqlite_master
WHERE sql LIKE '%ventas%'
  AND name NOT IN ('ventas', 'detalle_venta', 'idx_ventas_clave_idempotencia', 'idx_ventas_sesion',
                   'trg_ventas_sesion_operable', 'trg_ventas_sesion_inmutable');
INSERT INTO _fallas SELECT 'falta un índice o trigger esperado de ventas'
WHERE (SELECT COUNT(*) FROM sqlite_master WHERE tbl_name = 'ventas' AND type IN ('index', 'trigger')
       AND name IN ('idx_ventas_clave_idempotencia', 'idx_ventas_sesion',
                    'trg_ventas_sesion_operable', 'trg_ventas_sesion_inmutable')) <> 4;
INSERT INTO _fallas SELECT 'detalle_venta no referencia a ventas con ON DELETE CASCADE'
WHERE NOT EXISTS (SELECT 1 FROM pragma_foreign_key_list('detalle_venta')
                  WHERE "table" = 'ventas' AND "from" = 'venta_id' AND on_delete = 'CASCADE');
INSERT INTO _fallas SELECT 'foreign_key_check previo'
WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;
INSERT INTO _fallas SELECT 'integrity_check previo'
WHERE (SELECT integrity_check FROM pragma_integrity_check) <> 'ok';
INSERT INTO _fallas SELECT 'las claves foráneas no están activas'
WHERE (SELECT foreign_keys FROM pragma_foreign_keys) <> 1;
INSERT INTO _fallas SELECT 'ya existe alguna tabla de la migración 020'
WHERE EXISTS (SELECT 1 FROM sqlite_master WHERE name IN ('clientes', 'movimientos_cuenta', 'ventas_nueva'));

-- 1. Snapshots --------------------------------------------------------------------

CREATE TEMP TABLE _antes AS
SELECT
    (SELECT COUNT(*) FROM ventas)                                       AS ventas_n,
    (SELECT COUNT(*) FROM detalle_venta)                                AS detalle_n,
    (SELECT COUNT(*) FROM caja_movimientos)                             AS mov_n,
    (SELECT COALESCE(SUM(total_centavos), 0) FROM ventas)               AS ventas_total,
    (SELECT COALESCE(SUM(subtotal_centavos), 0) FROM detalle_venta)     AS detalle_subtotal,
    (SELECT COALESCE(SUM(monto_centavos), 0) FROM caja_movimientos)     AS mov_monto,
    (SELECT seq FROM sqlite_sequence WHERE name = 'ventas')             AS seq_ventas,
    (SELECT seq FROM sqlite_sequence WHERE name = 'detalle_venta')      AS seq_detalle,
    (SELECT seq FROM sqlite_sequence WHERE name = 'caja_movimientos')   AS seq_mov;

CREATE TEMP TABLE _ventas_copia AS SELECT * FROM ventas;
CREATE TEMP TABLE _detalle_copia AS SELECT * FROM detalle_venta;
CREATE TEMP TABLE _objetos_ventas_antes AS
SELECT name, sql FROM sqlite_master
WHERE tbl_name = 'ventas' AND type IN ('index', 'trigger') AND sql IS NOT NULL;

-- 2. Tablas nuevas que `ventas` referencia -----------------------------------------

CREATE TABLE clientes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre         TEXT NOT NULL CHECK (length(trim(nombre)) > 0 AND length(nombre) <= 120),
    telefono       TEXT NULL CHECK (telefono IS NULL OR length(telefono) <= 30),
    email          TEXT NULL CHECK (email IS NULL OR length(email) <= 150),
    direccion      TEXT NULL CHECK (direccion IS NULL OR length(direccion) <= 200),
    observaciones  TEXT NULL CHECK (observaciones IS NULL OR length(observaciones) <= 500),
    activo         INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
    fecha_creacion TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX idx_clientes_activo ON clientes(activo);

-- 3. Reconstrucción de `ventas` -----------------------------------------------------

CREATE TABLE ventas_nueva (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha                   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    total_centavos          INTEGER NOT NULL CHECK (total_centavos >= 0),
    tipo_pago               TEXT NOT NULL CHECK (
                                tipo_pago IN ('EFECTIVO', 'TARJETA', 'TRANSFERENCIA', 'OTRO', 'CUENTA_CORRIENTE')
                            ),
    clave_idempotencia      TEXT NULL,
    contenido_hash          TEXT NULL,
    usuario_id              INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    estado                  TEXT NOT NULL DEFAULT 'ACTIVA' CHECK (estado IN ('ACTIVA', 'ANULADA')),
    motivo_anulacion        TEXT NULL,
    observaciones_anulacion TEXT NULL,
    anulada_por_usuario_id  INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fecha_anulacion         TEXT NULL,
    sesion_caja_id          INTEGER NULL REFERENCES sesiones_caja(id) ON DELETE RESTRICT,
    cliente_id              INTEGER NULL REFERENCES clientes(id) ON DELETE RESTRICT,
    -- Una venta a cuenta siempre tiene cliente.
    CHECK (tipo_pago <> 'CUENTA_CORRIENTE' OR cliente_id IS NOT NULL)
);

INSERT INTO ventas_nueva
    (id, fecha, total_centavos, tipo_pago, clave_idempotencia, contenido_hash, usuario_id, estado,
     motivo_anulacion, observaciones_anulacion, anulada_por_usuario_id, fecha_anulacion, sesion_caja_id)
SELECT
     id, fecha, total_centavos, tipo_pago, clave_idempotencia, contenido_hash, usuario_id, estado,
     motivo_anulacion, observaciones_anulacion, anulada_por_usuario_id, fecha_anulacion, sesion_caja_id
FROM ventas
ORDER BY id;

-- Con las FK activas, el DROP borra las filas de `ventas` y el CASCADE vacía `detalle_venta`.
DROP TABLE ventas;

INSERT INTO _fallas SELECT 'el DROP de ventas no vació detalle_venta (claves foráneas inactivas)'
WHERE EXISTS (SELECT 1 FROM detalle_venta);

ALTER TABLE ventas_nueva RENAME TO ventas;

INSERT INTO detalle_venta
    (id, venta_id, producto_id, cantidad, precio_unitario_centavos, subtotal_centavos, costo_unitario_centavos)
SELECT
     id, venta_id, producto_id, cantidad, precio_unitario_centavos, subtotal_centavos, costo_unitario_centavos
FROM _detalle_copia
ORDER BY id;

-- `sqlite_sequence`: el DROP quita la fila de `ventas` y las copias explícitas de `id`
-- solo la reconstruyen hasta el máximo `id` existente, no hasta el mayor `id` histórico.
UPDATE sqlite_sequence SET seq = (SELECT seq_ventas FROM _antes)
WHERE name = 'ventas' AND (SELECT seq_ventas FROM _antes) IS NOT NULL;
INSERT INTO sqlite_sequence (name, seq)
SELECT 'ventas', seq_ventas FROM _antes
WHERE seq_ventas IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'ventas');
-- Copiar cero filas a una tabla AUTOINCREMENT deja una fila (nombre, 0) que la original
-- no tenía (base sin ventas): se elimina para restaurar el estado exacto.
DELETE FROM sqlite_sequence WHERE name = 'ventas' AND (SELECT seq_ventas FROM _antes) IS NULL;
-- Lo mismo para `detalle_venta` (se vació y se restauró): su secuencia debe quedar idéntica.
UPDATE sqlite_sequence SET seq = (SELECT seq_detalle FROM _antes)
WHERE name = 'detalle_venta' AND (SELECT seq_detalle FROM _antes) IS NOT NULL;
DELETE FROM sqlite_sequence WHERE name = 'detalle_venta' AND (SELECT seq_detalle FROM _antes) IS NULL;

-- Índices y triggers existentes de `ventas` (idénticos a los de la migración 019).
CREATE UNIQUE INDEX idx_ventas_clave_idempotencia ON ventas(clave_idempotencia);
CREATE INDEX idx_ventas_sesion ON ventas(sesion_caja_id);

CREATE TRIGGER trg_ventas_sesion_operable BEFORE INSERT ON ventas
WHEN NEW.sesion_caja_id IS NULL
     OR NOT EXISTS (SELECT 1 FROM sesiones_caja WHERE id = NEW.sesion_caja_id AND estado = 'ABIERTA')
BEGIN
    SELECT RAISE(ABORT, 'La venta requiere una sesión de caja ABIERTA.');
END;

CREATE TRIGGER trg_ventas_sesion_inmutable BEFORE UPDATE OF sesion_caja_id ON ventas
WHEN OLD.sesion_caja_id IS NOT NEW.sesion_caja_id
BEGIN
    SELECT RAISE(ABORT, 'La sesión de caja de una venta no puede modificarse.');
END;

CREATE INDEX idx_ventas_cliente ON ventas(cliente_id);

-- 4. Verificación de la reconstrucción ---------------------------------------------

INSERT INTO _fallas SELECT 'ventas: cantidad de filas'
WHERE (SELECT COUNT(*) FROM ventas) <> (SELECT ventas_n FROM _antes);
INSERT INTO _fallas SELECT 'ventas: una fila difiere de la original (o falta)'
WHERE EXISTS (
    SELECT id, fecha, total_centavos, tipo_pago, clave_idempotencia, contenido_hash, usuario_id, estado,
           motivo_anulacion, observaciones_anulacion, anulada_por_usuario_id, fecha_anulacion, sesion_caja_id
    FROM _ventas_copia
    EXCEPT
    SELECT id, fecha, total_centavos, tipo_pago, clave_idempotencia, contenido_hash, usuario_id, estado,
           motivo_anulacion, observaciones_anulacion, anulada_por_usuario_id, fecha_anulacion, sesion_caja_id
    FROM ventas
);
INSERT INTO _fallas SELECT 'ventas: hay una fila que no estaba en la original'
WHERE EXISTS (
    SELECT id, fecha, total_centavos, tipo_pago, clave_idempotencia, contenido_hash, usuario_id, estado,
           motivo_anulacion, observaciones_anulacion, anulada_por_usuario_id, fecha_anulacion, sesion_caja_id
    FROM ventas
    EXCEPT
    SELECT id, fecha, total_centavos, tipo_pago, clave_idempotencia, contenido_hash, usuario_id, estado,
           motivo_anulacion, observaciones_anulacion, anulada_por_usuario_id, fecha_anulacion, sesion_caja_id
    FROM _ventas_copia
);
INSERT INTO _fallas SELECT 'ventas: se infirió un cliente para una venta histórica'
WHERE EXISTS (SELECT 1 FROM ventas WHERE cliente_id IS NOT NULL);
INSERT INTO _fallas SELECT 'detalle_venta: cantidad de filas'
WHERE (SELECT COUNT(*) FROM detalle_venta) <> (SELECT detalle_n FROM _antes);
INSERT INTO _fallas SELECT 'detalle_venta: una fila difiere de la original (o falta)'
WHERE EXISTS (
    SELECT id, venta_id, producto_id, cantidad, precio_unitario_centavos, subtotal_centavos,
           costo_unitario_centavos FROM _detalle_copia
    EXCEPT
    SELECT id, venta_id, producto_id, cantidad, precio_unitario_centavos, subtotal_centavos,
           costo_unitario_centavos FROM detalle_venta
);
INSERT INTO _fallas SELECT 'detalle_venta: hay una fila que no estaba en la original'
WHERE EXISTS (
    SELECT id, venta_id, producto_id, cantidad, precio_unitario_centavos, subtotal_centavos,
           costo_unitario_centavos FROM detalle_venta
    EXCEPT
    SELECT id, venta_id, producto_id, cantidad, precio_unitario_centavos, subtotal_centavos,
           costo_unitario_centavos FROM _detalle_copia
);
INSERT INTO _fallas SELECT 'suma de totales de ventas'
WHERE (SELECT COALESCE(SUM(total_centavos), 0) FROM ventas) <> (SELECT ventas_total FROM _antes);
INSERT INTO _fallas SELECT 'suma de subtotales de detalle_venta'
WHERE (SELECT COALESCE(SUM(subtotal_centavos), 0) FROM detalle_venta) <> (SELECT detalle_subtotal FROM _antes);
INSERT INTO _fallas SELECT 'sqlite_sequence de ventas'
WHERE (SELECT seq FROM sqlite_sequence WHERE name = 'ventas') IS NOT (SELECT seq_ventas FROM _antes);
INSERT INTO _fallas SELECT 'sqlite_sequence de detalle_venta'
WHERE (SELECT seq FROM sqlite_sequence WHERE name = 'detalle_venta') IS NOT (SELECT seq_detalle FROM _antes);
INSERT INTO _fallas SELECT 'sqlite_sequence de caja_movimientos'
WHERE (SELECT seq FROM sqlite_sequence WHERE name = 'caja_movimientos') IS NOT (SELECT seq_mov FROM _antes);
INSERT INTO _fallas SELECT 'quedó una fila de sqlite_sequence de ventas_nueva'
WHERE EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'ventas_nueva');
INSERT INTO _fallas SELECT 'quedó una tabla ventas_nueva'
WHERE EXISTS (SELECT 1 FROM sqlite_master WHERE name = 'ventas_nueva');
INSERT INTO _fallas SELECT 'los índices/triggers recreados de ventas no son idénticos a los originales: ' || a.name
FROM _objetos_ventas_antes a
WHERE (SELECT sql FROM sqlite_master WHERE name = a.name AND tbl_name = 'ventas') IS NOT a.sql;
INSERT INTO _fallas SELECT 'detalle_venta ya no referencia a ventas con ON DELETE CASCADE'
WHERE NOT EXISTS (SELECT 1 FROM pragma_foreign_key_list('detalle_venta')
                  WHERE "table" = 'ventas' AND "from" = 'venta_id' AND on_delete = 'CASCADE');
INSERT INTO _fallas SELECT 'ventas no tiene las columnas esperadas tras la reconstrucción'
WHERE (SELECT group_concat(name, ',') FROM (SELECT name FROM pragma_table_info('ventas') ORDER BY cid))
      IS NOT 'id,fecha,total_centavos,tipo_pago,clave_idempotencia,contenido_hash,usuario_id,estado,'
             || 'motivo_anulacion,observaciones_anulacion,anulada_por_usuario_id,fecha_anulacion,sesion_caja_id,'
             || 'cliente_id';
INSERT INTO _fallas SELECT 'foreign_key_check tras reconstruir ventas'
WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;
INSERT INTO _fallas SELECT 'integrity_check tras reconstruir ventas'
WHERE (SELECT integrity_check FROM pragma_integrity_check) <> 'ok';

-- 5. Movimientos de caja: origen -----------------------------------------------------
-- Las filas existentes quedan `MANUAL` por el DEFAULT (sin UPDATE).

ALTER TABLE caja_movimientos
    ADD COLUMN origen TEXT NOT NULL DEFAULT 'MANUAL' CHECK (origen IN ('MANUAL', 'COBRO_CUENTA'));

-- 6. Libro de cuenta corriente ---------------------------------------------------------

CREATE TABLE movimientos_cuenta (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha              TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    cliente_id         INTEGER NOT NULL REFERENCES clientes(id) ON DELETE RESTRICT,
    tipo               TEXT NOT NULL CHECK (tipo IN ('CARGO', 'COBRO')),
    monto_centavos     INTEGER NOT NULL CHECK (monto_centavos > 0),
    descripcion        TEXT NULL CHECK (descripcion IS NULL OR length(descripcion) <= 250),
    venta_id           INTEGER NULL REFERENCES ventas(id) ON DELETE RESTRICT,
    caja_movimiento_id INTEGER NULL REFERENCES caja_movimientos(id) ON DELETE RESTRICT,
    usuario_id         INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    clave_idempotencia TEXT NULL,
    contenido_hash     TEXT NULL,
    -- CARGO respalda una venta; COBRO respalda un ingreso de caja.
    CHECK (
        (tipo = 'CARGO' AND venta_id IS NOT NULL AND caja_movimiento_id IS NULL)
        OR (tipo = 'COBRO' AND caja_movimiento_id IS NOT NULL AND venta_id IS NULL)
    )
);

CREATE INDEX idx_movimientos_cuenta_cliente ON movimientos_cuenta(cliente_id, id);
CREATE UNIQUE INDEX idx_movimientos_cuenta_clave_idempotencia ON movimientos_cuenta(clave_idempotencia);
-- Una venta genera como máximo un CARGO y un ingreso de caja respalda como máximo un COBRO.
CREATE UNIQUE INDEX idx_movimientos_cuenta_un_cargo_por_venta
    ON movimientos_cuenta(venta_id) WHERE venta_id IS NOT NULL;
CREATE UNIQUE INDEX idx_movimientos_cuenta_un_cobro_por_ingreso
    ON movimientos_cuenta(caja_movimiento_id) WHERE caja_movimiento_id IS NOT NULL;

-- 7. Triggers ----------------------------------------------------------------------------

-- Ventas: cliente activo en toda venta nueva; cliente/tipo/total de una venta a cuenta
-- inmutables; una venta a cuenta no se anula (V1.3).
CREATE TRIGGER trg_ventas_cliente_activo BEFORE INSERT ON ventas
WHEN NEW.cliente_id IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM clientes WHERE id = NEW.cliente_id AND activo = 1)
BEGIN
    SELECT RAISE(ABORT, 'La venta requiere un cliente activo.');
END;

CREATE TRIGGER trg_ventas_cliente_inmutable BEFORE UPDATE OF cliente_id ON ventas
WHEN OLD.cliente_id IS NOT NEW.cliente_id
BEGIN
    SELECT RAISE(ABORT, 'El cliente de una venta no puede modificarse.');
END;

CREATE TRIGGER trg_ventas_cuenta_inmutable BEFORE UPDATE OF tipo_pago, total_centavos ON ventas
WHEN (OLD.tipo_pago = 'CUENTA_CORRIENTE' OR NEW.tipo_pago = 'CUENTA_CORRIENTE')
     AND (OLD.tipo_pago IS NOT NEW.tipo_pago OR OLD.total_centavos IS NOT NEW.total_centavos)
BEGIN
    SELECT RAISE(ABORT, 'El tipo de pago y el total de una venta a cuenta no pueden modificarse.');
END;

CREATE TRIGGER trg_ventas_cuenta_no_anulable BEFORE UPDATE OF estado ON ventas
WHEN OLD.tipo_pago = 'CUENTA_CORRIENTE' AND NEW.estado IS NOT OLD.estado
BEGIN
    SELECT RAISE(ABORT, 'Una venta a cuenta no puede anularse.');
END;

-- Caja: `origen` inmutable; un COBRO_CUENTA es siempre un INGRESO, con tipo y monto inmutables.
CREATE TRIGGER trg_caja_movimientos_origen_inmutable BEFORE UPDATE OF origen ON caja_movimientos
WHEN OLD.origen IS NOT NEW.origen
BEGIN
    SELECT RAISE(ABORT, 'El origen de un movimiento de caja no puede modificarse.');
END;

CREATE TRIGGER trg_caja_movimientos_cobro_tipo_inmutable BEFORE UPDATE OF tipo ON caja_movimientos
WHEN OLD.origen = 'COBRO_CUENTA' AND OLD.tipo IS NOT NEW.tipo
BEGIN
    SELECT RAISE(ABORT, 'El tipo de un movimiento de cobro de cuenta no puede modificarse.');
END;

CREATE TRIGGER trg_caja_movimientos_cobro_solo_ingreso BEFORE INSERT ON caja_movimientos
WHEN NEW.origen = 'COBRO_CUENTA' AND NEW.tipo <> 'INGRESO'
BEGIN
    SELECT RAISE(ABORT, 'Un movimiento de cobro de cuenta solo puede ser un INGRESO.');
END;

CREATE TRIGGER trg_caja_movimientos_cobro_monto_inmutable BEFORE UPDATE OF monto_centavos ON caja_movimientos
WHEN OLD.origen = 'COBRO_CUENTA' AND OLD.monto_centavos IS NOT NEW.monto_centavos
BEGIN
    SELECT RAISE(ABORT, 'El monto de un movimiento de cobro de cuenta no puede modificarse.');
END;

-- Clientes: no se desactiva un cliente con saldo pendiente.
CREATE TRIGGER trg_clientes_no_desactivar_con_saldo BEFORE UPDATE OF activo ON clientes
WHEN OLD.activo = 1 AND NEW.activo = 0
     AND (SELECT COALESCE(SUM(CASE tipo WHEN 'CARGO' THEN monto_centavos ELSE -monto_centavos END), 0)
          FROM movimientos_cuenta WHERE cliente_id = OLD.id) > 0
BEGIN
    SELECT RAISE(ABORT, 'No se puede desactivar un cliente con saldo pendiente.');
END;

-- Libro: cada CARGO respalda una venta a cuenta y cada COBRO un ingreso de caja de la
-- sesión abierta; el libro es append-only.
CREATE TRIGGER trg_movimientos_cuenta_cargo_valido BEFORE INSERT ON movimientos_cuenta
WHEN NEW.tipo = 'CARGO'
BEGIN
    SELECT RAISE(ABORT, 'El cargo requiere un cliente activo.')
    WHERE NOT EXISTS (SELECT 1 FROM clientes WHERE id = NEW.cliente_id AND activo = 1);
    SELECT RAISE(ABORT, 'El cargo debe respaldarse en una venta a cuenta activa del mismo cliente, de la sesión abierta y por el mismo total.')
    WHERE NOT EXISTS (
        SELECT 1 FROM ventas v JOIN sesiones_caja s ON s.id = v.sesion_caja_id
        WHERE v.id = NEW.venta_id AND v.tipo_pago = 'CUENTA_CORRIENTE' AND v.cliente_id = NEW.cliente_id
          AND v.total_centavos = NEW.monto_centavos AND v.estado = 'ACTIVA' AND s.estado = 'ABIERTA'
    );
END;

CREATE TRIGGER trg_movimientos_cuenta_cobro_valido BEFORE INSERT ON movimientos_cuenta
WHEN NEW.tipo = 'COBRO'
BEGIN
    -- El estado activo del cliente NO se exige: un cliente inactivo con deuda puede pagarla.
    -- Uno inactivo sin deuda queda igual rechazado por `monto <= saldo` (más abajo).
    SELECT RAISE(ABORT, 'El cobro requiere un cliente existente.')
    WHERE NOT EXISTS (SELECT 1 FROM clientes WHERE id = NEW.cliente_id);
    SELECT RAISE(ABORT, 'El cobro debe respaldarse en un ingreso de caja COBRO_CUENTA de la sesión abierta y por el mismo monto.')
    WHERE NOT EXISTS (
        SELECT 1 FROM caja_movimientos m JOIN sesiones_caja s ON s.id = m.sesion_caja_id
        WHERE m.id = NEW.caja_movimiento_id AND m.origen = 'COBRO_CUENTA' AND m.tipo = 'INGRESO'
          AND m.monto_centavos = NEW.monto_centavos AND s.estado = 'ABIERTA'
    );
    SELECT RAISE(ABORT, 'El cobro no puede superar el saldo del cliente.')
    WHERE NEW.monto_centavos > (
        SELECT COALESCE(SUM(CASE tipo WHEN 'CARGO' THEN monto_centavos ELSE -monto_centavos END), 0)
        FROM movimientos_cuenta WHERE cliente_id = NEW.cliente_id
    );
END;

CREATE TRIGGER trg_movimientos_cuenta_inmutable BEFORE UPDATE ON movimientos_cuenta
BEGIN
    SELECT RAISE(ABORT, 'Los movimientos de cuenta corriente no pueden modificarse.');
END;

CREATE TRIGGER trg_movimientos_cuenta_no_borrable BEFORE DELETE ON movimientos_cuenta
BEGIN
    SELECT RAISE(ABORT, 'Los movimientos de cuenta corriente no pueden borrarse.');
END;

-- 8. Verificación final -------------------------------------------------------------------

INSERT INTO _fallas SELECT 'triggers de la migración 020 incompletos'
WHERE (SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND name IN (
    'trg_ventas_sesion_operable', 'trg_ventas_sesion_inmutable',
    'trg_ventas_cliente_activo', 'trg_ventas_cliente_inmutable', 'trg_ventas_cuenta_inmutable',
    'trg_ventas_cuenta_no_anulable',
    'trg_caja_movimientos_origen_inmutable', 'trg_caja_movimientos_cobro_tipo_inmutable',
    'trg_caja_movimientos_cobro_solo_ingreso', 'trg_caja_movimientos_cobro_monto_inmutable',
    'trg_clientes_no_desactivar_con_saldo',
    'trg_movimientos_cuenta_cargo_valido', 'trg_movimientos_cuenta_cobro_valido',
    'trg_movimientos_cuenta_inmutable', 'trg_movimientos_cuenta_no_borrable')) <> 15;
INSERT INTO _fallas SELECT 'movimientos de caja históricos que no quedaron MANUAL'
WHERE EXISTS (SELECT 1 FROM caja_movimientos WHERE origen <> 'MANUAL');
INSERT INTO _fallas SELECT 'caja_movimientos: cantidad o suma de montos'
WHERE (SELECT COUNT(*) FROM caja_movimientos) <> (SELECT mov_n FROM _antes)
   OR (SELECT COALESCE(SUM(monto_centavos), 0) FROM caja_movimientos) <> (SELECT mov_monto FROM _antes);
INSERT INTO _fallas SELECT 'movimientos_cuenta.caja_movimiento_id no es ON DELETE RESTRICT'
WHERE NOT EXISTS (SELECT 1 FROM pragma_foreign_key_list('movimientos_cuenta')
                  WHERE "table" = 'caja_movimientos' AND "from" = 'caja_movimiento_id' AND on_delete = 'RESTRICT');
INSERT INTO _fallas SELECT 'foreign_key_check final'
WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;
INSERT INTO _fallas SELECT 'integrity_check final'
WHERE (SELECT integrity_check FROM pragma_integrity_check) <> 'ok';

DROP TRIGGER temp._abortar_por_falla;
DROP TABLE temp._fallas;
DROP TABLE temp._objetos_ventas_antes;
DROP TABLE temp._detalle_copia;
DROP TABLE temp._ventas_copia;
DROP TABLE temp._antes;
