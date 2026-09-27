-- Migración 025: infraestructura de cuentas a pagar a proveedores (V1.9-A).
--
-- Agrega:
--   * `caja_movimientos.origen`: nuevo valor 'PAGO_PROVEEDOR' (egreso respaldado por un pago a
--     proveedor), sin reutilizar 'MANUAL' -- mismo criterio que 'COBRO_CUENTA' (migración 020):
--     un origen propio permite reportes y arqueo exactos, en vez de depender de texto libre en
--     `descripcion`.
--   * `compras.condicion_pago` ('CONTADO' / 'CREDITO'). Compras históricas quedan 'CONTADO' por el
--     DEFAULT: no hay ninguna evidencia de deuda retroactiva en el esquema anterior a esta
--     migración, así que no se infiere ninguna. Sin `fecha_vencimiento` (fuera de alcance de
--     V1.9.0, ver auditoría).
--   * `movimientos_proveedor`: libro append-only de CARGO_COMPRA / PAGO / REVERSA_COMPRA, mismo
--     patrón que `movimientos_cuenta` (migración 020).
--
-- Este checkpoint (V1.9-A) es solo infraestructura: no genera ningún `CARGO_COMPRA` ni `PAGO`
-- todavía (eso es V1.9-B/V1.9-C) -- `movimientos_proveedor` queda vacío tras esta migración, y se
-- verifica explícitamente más abajo.
--
-- RECONSTRUCCIÓN DE `caja_movimientos`
-- Ampliar el CHECK de `origen` no se puede con ALTER TABLE. A diferencia de la reconstrucción de
-- `ventas` en la migración 020 (cuyo único hijo, `detalle_venta`, es ON DELETE CASCADE y se vacía
-- solo), `caja_movimientos` tiene un hijo con ON DELETE RESTRICT: `movimientos_cuenta.caja_movimiento_id`.
-- Con las FK activas (no se pueden desactivar dentro de esta transacción: es un no-op en SQLite si
-- ya hay una transacción abierta, y el runner ya abrió una con `BEGIN IMMEDIATE` antes de correr
-- este script), un `DROP TABLE caja_movimientos` con filas de `movimientos_cuenta` todavía
-- apuntando a ella falla de inmediato con "FOREIGN KEY constraint failed" (comprobado
-- empíricamente en una base de prueba): el RESTRICT bloquea el DELETE implícito del DROP antes de
-- llegar a evaluar ningún trigger. En cambio, un `DROP TABLE` sobre la propia tabla que tiene el
-- trigger de no-borrado NO dispara ese trigger (comprobado empíricamente: el DELETE implícito de
-- un DROP no dispara triggers definidos sobre la tabla que se está borrando). Por eso el orden acá
-- es:
--   1. snapshot y DROP de `movimientos_cuenta` (nada más la referencia, y su propio
--      `trg_movimientos_cuenta_no_borrable` no bloquea el DROP de sí misma);
--   2. recreación INMEDIATA de `movimientos_cuenta` vacía (sin filas, sin índices ni triggers
--      todavía) -- necesaria porque `ALTER TABLE ... RENAME TO` reescribe y por lo tanto
--      revalida toda la definición de cualquier trigger de la base que mencione la tabla
--      renombrada, y `trg_clientes_no_desactivar_con_saldo` (sobre `clientes`, migración 020)
--      consulta `movimientos_cuenta`: con la tabla ausente, ese `RENAME` falla con "no such
--      table: movimientos_cuenta" aunque el rename en sí no toque ni `clientes` ni
--      `movimientos_cuenta` (comprobado empíricamente). Vacía, tampoco bloquea el `DROP` de
--      `caja_movimientos` de abajo: el RESTRICT solo actúa sobre filas que efectivamente
--      referencian, y todavía no hay ninguna;
--   3. reconstrucción de `caja_movimientos` (ya sin ningún hijo con filas que la referencien);
--   4. restauración fila por fila de `movimientos_cuenta` (mismos ids: las referencias a
--      `caja_movimientos` siguen siendo válidas porque esa tabla conservó sus ids) y creación de
--      sus índices/triggers, recién ahora que ya tiene datos y no hace falta ningún otro `RENAME`.
-- Mismo patrón de verificación exhaustiva que las migraciones 020/024: guardias previos,
-- snapshots, reconstrucción, restauración y comparación fila por fila, con cualquier fila
-- insertada en `_fallas` abortando toda la migración (el runner la corre dentro de un único
-- `BEGIN IMMEDIATE`).

DROP TABLE IF EXISTS temp._fallas;
DROP TABLE IF EXISTS temp._antes;
DROP TABLE IF EXISTS temp._caja_copia;
DROP TABLE IF EXISTS temp._cuenta_copia;
DROP TABLE IF EXISTS temp._objetos_caja_antes;
DROP TABLE IF EXISTS temp._objetos_cuenta_antes;

CREATE TEMP TABLE _fallas (motivo TEXT NOT NULL);
CREATE TEMP TRIGGER _abortar_por_falla AFTER INSERT ON _fallas
BEGIN
    SELECT RAISE(ABORT, 'Migración 025: verificación fallida: ' || NEW.motivo);
END;

-- 0. Guardias previos ----------------------------------------------------------------

INSERT INTO _fallas SELECT 'la tabla caja_movimientos no tiene las columnas esperadas'
WHERE (SELECT group_concat(name, ',') FROM (SELECT name FROM pragma_table_info('caja_movimientos') ORDER BY cid))
      IS NOT 'id,fecha,tipo,monto_centavos,descripcion,usuario_id,diferencia_centavos,clave_idempotencia,'
             || 'sesion_caja_id,origen';
INSERT INTO _fallas SELECT 'la tabla movimientos_cuenta no tiene las columnas esperadas'
WHERE (SELECT group_concat(name, ',') FROM (SELECT name FROM pragma_table_info('movimientos_cuenta') ORDER BY cid))
      IS NOT 'id,fecha,cliente_id,tipo,monto_centavos,descripcion,venta_id,caja_movimiento_id,usuario_id,'
             || 'clave_idempotencia,contenido_hash';
-- Se restringe a `tbl_name` (el objeto propio de una de las dos tablas), no al texto de su SQL:
-- `trg_clientes_no_desactivar_con_saldo` (definido sobre `clientes`, migración 020) consulta
-- `movimientos_cuenta` legítimamente y no debe confundirse con un objeto huérfano de esta migración.
INSERT INTO _fallas SELECT 'objeto inesperado sobre caja_movimientos o movimientos_cuenta: ' || name
FROM sqlite_master
WHERE tbl_name IN ('caja_movimientos', 'movimientos_cuenta')
  AND type IN ('index', 'trigger')
  AND name NOT IN (
      'caja_movimientos', 'movimientos_cuenta',
      'idx_caja_movimientos_clave_idempotencia', 'idx_caja_movimientos_sesion',
      'idx_caja_movimientos_una_apertura_por_sesion',
      'trg_caja_movimientos_sesion_operable', 'trg_caja_movimientos_sesion_inmutable',
      'trg_caja_movimientos_origen_inmutable', 'trg_caja_movimientos_cobro_tipo_inmutable',
      'trg_caja_movimientos_cobro_solo_ingreso', 'trg_caja_movimientos_cobro_monto_inmutable',
      'idx_movimientos_cuenta_cliente', 'idx_movimientos_cuenta_clave_idempotencia',
      'idx_movimientos_cuenta_un_cargo_por_venta', 'idx_movimientos_cuenta_un_cobro_por_ingreso',
      'trg_movimientos_cuenta_cargo_valido', 'trg_movimientos_cuenta_cobro_valido',
      'trg_movimientos_cuenta_inmutable', 'trg_movimientos_cuenta_no_borrable'
  );
-- Las vistas no tienen un `tbl_name` propio útil para el filtro de arriba (el de una vista es su
-- propio nombre): se detectan por texto, igual que hacía la migración 020 para `ventas`.
INSERT INTO _fallas SELECT 'vista inesperada que referencia caja_movimientos o movimientos_cuenta: ' || name
FROM sqlite_master
WHERE type = 'view' AND (sql LIKE '%caja_movimientos%' OR sql LIKE '%movimientos_cuenta%');
INSERT INTO _fallas SELECT 'foreign_key_check previo'
WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;
INSERT INTO _fallas SELECT 'integrity_check previo'
WHERE (SELECT integrity_check FROM pragma_integrity_check) <> 'ok';
INSERT INTO _fallas SELECT 'las claves foráneas no están activas'
WHERE (SELECT foreign_keys FROM pragma_foreign_keys) <> 1;
INSERT INTO _fallas SELECT 'ya existe alguna tabla de la migración 025'
WHERE EXISTS (
    SELECT 1 FROM sqlite_master
    WHERE name IN ('movimientos_proveedor', 'caja_movimientos_nueva')
);

-- 1. Snapshots ------------------------------------------------------------------------

CREATE TEMP TABLE _antes AS
SELECT
    (SELECT COUNT(*) FROM caja_movimientos)                             AS caja_n,
    (SELECT COALESCE(SUM(monto_centavos), 0) FROM caja_movimientos)     AS caja_monto,
    (SELECT seq FROM sqlite_sequence WHERE name = 'caja_movimientos')   AS seq_caja,
    (SELECT COUNT(*) FROM movimientos_cuenta)                           AS cuenta_n,
    (SELECT COALESCE(SUM(monto_centavos), 0) FROM movimientos_cuenta)   AS cuenta_monto,
    (SELECT seq FROM sqlite_sequence WHERE name = 'movimientos_cuenta') AS seq_cuenta,
    (SELECT COUNT(*) FROM compras)                                      AS compras_n;

CREATE TEMP TABLE _caja_copia AS SELECT * FROM caja_movimientos;
CREATE TEMP TABLE _cuenta_copia AS SELECT * FROM movimientos_cuenta;
CREATE TEMP TABLE _objetos_caja_antes AS
SELECT name, sql FROM sqlite_master WHERE tbl_name = 'caja_movimientos' AND type IN ('index', 'trigger') AND sql IS NOT NULL;
CREATE TEMP TABLE _objetos_cuenta_antes AS
SELECT name, sql FROM sqlite_master WHERE tbl_name = 'movimientos_cuenta' AND type IN ('index', 'trigger') AND sql IS NOT NULL;

-- 2. DROP de movimientos_cuenta (hijo RESTRICT de caja_movimientos) y recreación vacía --------
-- Un DROP no dispara los triggers de la propia tabla que se borra: `trg_movimientos_cuenta_no_borrable`
-- no lo bloquea (comprobado empíricamente). Nada más referencia a `movimientos_cuenta` (guardia previa).
-- Se recrea vacía de inmediato (sin índices ni triggers: se agregan en el paso 4, ya con datos)
-- por el motivo explicado arriba -- el `ALTER ... RENAME` del paso 3 la necesita presente.

DROP TABLE movimientos_cuenta;

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
    CHECK (
        (tipo = 'CARGO' AND venta_id IS NOT NULL AND caja_movimiento_id IS NULL)
        OR (tipo = 'COBRO' AND caja_movimiento_id IS NOT NULL AND venta_id IS NULL)
    )
);

-- 3. Reconstrucción de caja_movimientos ------------------------------------------------

CREATE TABLE caja_movimientos_nueva (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha               TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    tipo                TEXT NOT NULL CHECK (tipo IN ('APERTURA', 'CIERRE', 'INGRESO', 'EGRESO')),
    monto_centavos      INTEGER NOT NULL CHECK (monto_centavos >= 0),
    descripcion         TEXT,
    usuario_id          INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    diferencia_centavos INTEGER NULL,
    clave_idempotencia  TEXT NULL,
    sesion_caja_id      INTEGER NULL REFERENCES sesiones_caja(id) ON DELETE RESTRICT,
    origen              TEXT NOT NULL DEFAULT 'MANUAL' CHECK (origen IN ('MANUAL', 'COBRO_CUENTA', 'PAGO_PROVEEDOR'))
);

INSERT INTO caja_movimientos_nueva
    (id, fecha, tipo, monto_centavos, descripcion, usuario_id, diferencia_centavos, clave_idempotencia,
     sesion_caja_id, origen)
SELECT
     id, fecha, tipo, monto_centavos, descripcion, usuario_id, diferencia_centavos, clave_idempotencia,
     sesion_caja_id, origen
FROM caja_movimientos
ORDER BY id;

DROP TABLE caja_movimientos;
ALTER TABLE caja_movimientos_nueva RENAME TO caja_movimientos;

UPDATE sqlite_sequence SET seq = (SELECT seq_caja FROM _antes)
WHERE name = 'caja_movimientos' AND (SELECT seq_caja FROM _antes) IS NOT NULL;
INSERT INTO sqlite_sequence (name, seq)
SELECT 'caja_movimientos', seq_caja FROM _antes
WHERE seq_caja IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'caja_movimientos');
DELETE FROM sqlite_sequence WHERE name = 'caja_movimientos' AND (SELECT seq_caja FROM _antes) IS NULL;

-- Índices y triggers existentes de caja_movimientos, idénticos a los de la migración 020.
CREATE UNIQUE INDEX idx_caja_movimientos_clave_idempotencia ON caja_movimientos(clave_idempotencia);
CREATE INDEX idx_caja_movimientos_sesion ON caja_movimientos(sesion_caja_id);
CREATE UNIQUE INDEX idx_caja_movimientos_una_apertura_por_sesion
    ON caja_movimientos(sesion_caja_id) WHERE tipo = 'APERTURA';

CREATE TRIGGER trg_caja_movimientos_sesion_operable BEFORE INSERT ON caja_movimientos
WHEN NEW.sesion_caja_id IS NULL
     OR NOT EXISTS (SELECT 1 FROM sesiones_caja WHERE id = NEW.sesion_caja_id AND estado = 'ABIERTA')
BEGIN
    SELECT RAISE(ABORT, 'El movimiento de caja requiere una sesión de caja ABIERTA.');
END;

CREATE TRIGGER trg_caja_movimientos_sesion_inmutable BEFORE UPDATE OF sesion_caja_id ON caja_movimientos
WHEN OLD.sesion_caja_id IS NOT NEW.sesion_caja_id
BEGIN
    SELECT RAISE(ABORT, 'La sesión de caja de un movimiento no puede modificarse.');
END;

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

-- Nuevos (V1.9-A), simétricos a los de COBRO_CUENTA: un PAGO_PROVEEDOR es siempre un EGRESO, con
-- tipo y monto inmutables (`trg_caja_movimientos_origen_inmutable`, ya genérico, cubre el origen
-- para cualquier valor, incluido este).
CREATE TRIGGER trg_caja_movimientos_pago_proveedor_solo_egreso BEFORE INSERT ON caja_movimientos
WHEN NEW.origen = 'PAGO_PROVEEDOR' AND NEW.tipo <> 'EGRESO'
BEGIN
    SELECT RAISE(ABORT, 'Un movimiento de pago a proveedor solo puede ser un EGRESO.');
END;

CREATE TRIGGER trg_caja_movimientos_pago_proveedor_tipo_inmutable BEFORE UPDATE OF tipo ON caja_movimientos
WHEN OLD.origen = 'PAGO_PROVEEDOR' AND OLD.tipo IS NOT NEW.tipo
BEGIN
    SELECT RAISE(ABORT, 'El tipo de un movimiento de pago a proveedor no puede modificarse.');
END;

CREATE TRIGGER trg_caja_movimientos_pago_proveedor_monto_inmutable BEFORE UPDATE OF monto_centavos ON caja_movimientos
WHEN OLD.origen = 'PAGO_PROVEEDOR' AND OLD.monto_centavos IS NOT NEW.monto_centavos
BEGIN
    SELECT RAISE(ABORT, 'El monto de un movimiento de pago a proveedor no puede modificarse.');
END;

-- 4. Verificación de la reconstrucción de caja_movimientos ----------------------------

INSERT INTO _fallas SELECT 'caja_movimientos: cantidad de filas'
WHERE (SELECT COUNT(*) FROM caja_movimientos) <> (SELECT caja_n FROM _antes);
INSERT INTO _fallas SELECT 'caja_movimientos: una fila difiere de la original (o falta)'
WHERE EXISTS (
    SELECT id, fecha, tipo, monto_centavos, descripcion, usuario_id, diferencia_centavos,
           clave_idempotencia, sesion_caja_id, origen FROM _caja_copia
    EXCEPT
    SELECT id, fecha, tipo, monto_centavos, descripcion, usuario_id, diferencia_centavos,
           clave_idempotencia, sesion_caja_id, origen FROM caja_movimientos
);
INSERT INTO _fallas SELECT 'caja_movimientos: hay una fila que no estaba en la original'
WHERE EXISTS (
    SELECT id, fecha, tipo, monto_centavos, descripcion, usuario_id, diferencia_centavos,
           clave_idempotencia, sesion_caja_id, origen FROM caja_movimientos
    EXCEPT
    SELECT id, fecha, tipo, monto_centavos, descripcion, usuario_id, diferencia_centavos,
           clave_idempotencia, sesion_caja_id, origen FROM _caja_copia
);
INSERT INTO _fallas SELECT 'suma de montos de caja_movimientos'
WHERE (SELECT COALESCE(SUM(monto_centavos), 0) FROM caja_movimientos) <> (SELECT caja_monto FROM _antes);
INSERT INTO _fallas SELECT 'sqlite_sequence de caja_movimientos'
WHERE (SELECT seq FROM sqlite_sequence WHERE name = 'caja_movimientos') IS NOT (SELECT seq_caja FROM _antes);
INSERT INTO _fallas SELECT 'quedó una tabla caja_movimientos_nueva'
WHERE EXISTS (SELECT 1 FROM sqlite_master WHERE name = 'caja_movimientos_nueva');
INSERT INTO _fallas SELECT 'los índices/triggers previos de caja_movimientos no son idénticos a los originales: ' || a.name
FROM _objetos_caja_antes a
WHERE (SELECT sql FROM sqlite_master WHERE name = a.name AND tbl_name = 'caja_movimientos') IS NOT a.sql;
INSERT INTO _fallas SELECT 'origen inválido aceptado por el CHECK'
WHERE EXISTS (SELECT 1 FROM caja_movimientos WHERE origen NOT IN ('MANUAL', 'COBRO_CUENTA', 'PAGO_PROVEEDOR'));

-- 5. Restauración de movimientos_cuenta (ya recreada vacía en el paso 2) y sus índices/triggers --

INSERT INTO movimientos_cuenta
    (id, fecha, cliente_id, tipo, monto_centavos, descripcion, venta_id, caja_movimiento_id, usuario_id,
     clave_idempotencia, contenido_hash)
SELECT
     id, fecha, cliente_id, tipo, monto_centavos, descripcion, venta_id, caja_movimiento_id, usuario_id,
     clave_idempotencia, contenido_hash
FROM _cuenta_copia
ORDER BY id;

UPDATE sqlite_sequence SET seq = (SELECT seq_cuenta FROM _antes)
WHERE name = 'movimientos_cuenta' AND (SELECT seq_cuenta FROM _antes) IS NOT NULL;
INSERT INTO sqlite_sequence (name, seq)
SELECT 'movimientos_cuenta', seq_cuenta FROM _antes
WHERE seq_cuenta IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'movimientos_cuenta');
DELETE FROM sqlite_sequence WHERE name = 'movimientos_cuenta' AND (SELECT seq_cuenta FROM _antes) IS NULL;

CREATE INDEX idx_movimientos_cuenta_cliente ON movimientos_cuenta(cliente_id, id);
CREATE UNIQUE INDEX idx_movimientos_cuenta_clave_idempotencia ON movimientos_cuenta(clave_idempotencia);
CREATE UNIQUE INDEX idx_movimientos_cuenta_un_cargo_por_venta
    ON movimientos_cuenta(venta_id) WHERE venta_id IS NOT NULL;
CREATE UNIQUE INDEX idx_movimientos_cuenta_un_cobro_por_ingreso
    ON movimientos_cuenta(caja_movimiento_id) WHERE caja_movimiento_id IS NOT NULL;

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

-- 6. Verificación de movimientos_cuenta -------------------------------------------------

INSERT INTO _fallas SELECT 'movimientos_cuenta: cantidad de filas'
WHERE (SELECT COUNT(*) FROM movimientos_cuenta) <> (SELECT cuenta_n FROM _antes);
INSERT INTO _fallas SELECT 'movimientos_cuenta: una fila difiere de la original (o falta)'
WHERE EXISTS (
    SELECT id, fecha, cliente_id, tipo, monto_centavos, descripcion, venta_id, caja_movimiento_id,
           usuario_id, clave_idempotencia, contenido_hash FROM _cuenta_copia
    EXCEPT
    SELECT id, fecha, cliente_id, tipo, monto_centavos, descripcion, venta_id, caja_movimiento_id,
           usuario_id, clave_idempotencia, contenido_hash FROM movimientos_cuenta
);
INSERT INTO _fallas SELECT 'movimientos_cuenta: hay una fila que no estaba en la original'
WHERE EXISTS (
    SELECT id, fecha, cliente_id, tipo, monto_centavos, descripcion, venta_id, caja_movimiento_id,
           usuario_id, clave_idempotencia, contenido_hash FROM movimientos_cuenta
    EXCEPT
    SELECT id, fecha, cliente_id, tipo, monto_centavos, descripcion, venta_id, caja_movimiento_id,
           usuario_id, clave_idempotencia, contenido_hash FROM _cuenta_copia
);
INSERT INTO _fallas SELECT 'suma de montos de movimientos_cuenta'
WHERE (SELECT COALESCE(SUM(monto_centavos), 0) FROM movimientos_cuenta) <> (SELECT cuenta_monto FROM _antes);
INSERT INTO _fallas SELECT 'sqlite_sequence de movimientos_cuenta'
WHERE (SELECT seq FROM sqlite_sequence WHERE name = 'movimientos_cuenta') IS NOT (SELECT seq_cuenta FROM _antes);
INSERT INTO _fallas SELECT 'movimientos_cuenta.caja_movimiento_id no es ON DELETE RESTRICT'
WHERE NOT EXISTS (SELECT 1 FROM pragma_foreign_key_list('movimientos_cuenta')
                  WHERE "table" = 'caja_movimientos' AND "from" = 'caja_movimiento_id' AND on_delete = 'RESTRICT');
INSERT INTO _fallas SELECT 'los índices/triggers recreados de movimientos_cuenta no son idénticos a los originales: ' || a.name
FROM _objetos_cuenta_antes a
WHERE (SELECT sql FROM sqlite_master WHERE name = a.name AND tbl_name = 'movimientos_cuenta') IS NOT a.sql;

-- 7. compras.condicion_pago --------------------------------------------------------------
-- Aditiva: compras históricas quedan CONTADO por el DEFAULT, sin ningún UPDATE explícito y sin
-- generar ningún movimiento en `movimientos_proveedor` (esa tabla queda vacía, ver verificación final).

ALTER TABLE compras
    ADD COLUMN condicion_pago TEXT NOT NULL DEFAULT 'CONTADO' CHECK (condicion_pago IN ('CONTADO', 'CREDITO'));

-- 8. Libro de proveedor: movimientos_proveedor --------------------------------------------

CREATE TABLE movimientos_proveedor (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha              TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    proveedor_id       INTEGER NOT NULL REFERENCES proveedores(id) ON DELETE RESTRICT,
    tipo               TEXT NOT NULL CHECK (tipo IN ('CARGO_COMPRA', 'PAGO', 'REVERSA_COMPRA')),
    monto_centavos     INTEGER NOT NULL CHECK (monto_centavos > 0),
    compra_id          INTEGER NULL REFERENCES compras(id) ON DELETE RESTRICT,
    caja_movimiento_id INTEGER NULL REFERENCES caja_movimientos(id) ON DELETE RESTRICT,
    medio_pago         TEXT NULL CHECK (medio_pago IS NULL OR medio_pago IN ('EFECTIVO', 'TRANSFERENCIA')),
    observacion        TEXT NULL CHECK (observacion IS NULL OR length(observacion) <= 250),
    usuario_id         INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    clave_idempotencia TEXT NULL,
    contenido_hash     TEXT NULL,
    -- CARGO_COMPRA y REVERSA_COMPRA respaldan una compra (nunca caja ni medio de pago); un PAGO
    -- nunca referencia una compra puntual (no se imputa a compras concretas, ver auditoría V1.9)
    -- y respalda caja solo si es en efectivo.
    CHECK (
        (tipo IN ('CARGO_COMPRA', 'REVERSA_COMPRA')
            AND compra_id IS NOT NULL AND caja_movimiento_id IS NULL AND medio_pago IS NULL)
        OR (tipo = 'PAGO' AND compra_id IS NULL
            AND ((medio_pago = 'EFECTIVO' AND caja_movimiento_id IS NOT NULL)
                 OR (medio_pago = 'TRANSFERENCIA' AND caja_movimiento_id IS NULL)))
    )
);

CREATE INDEX idx_movimientos_proveedor_proveedor ON movimientos_proveedor(proveedor_id, id);
CREATE UNIQUE INDEX idx_movimientos_proveedor_clave_idempotencia
    ON movimientos_proveedor(clave_idempotencia) WHERE clave_idempotencia IS NOT NULL;
CREATE UNIQUE INDEX idx_movimientos_proveedor_un_cargo_por_compra
    ON movimientos_proveedor(compra_id) WHERE tipo = 'CARGO_COMPRA';
CREATE UNIQUE INDEX idx_movimientos_proveedor_una_reversa_por_compra
    ON movimientos_proveedor(compra_id) WHERE tipo = 'REVERSA_COMPRA';
CREATE UNIQUE INDEX idx_movimientos_proveedor_un_pago_por_egreso
    ON movimientos_proveedor(caja_movimiento_id) WHERE caja_movimiento_id IS NOT NULL;

-- 9. Triggers de movimientos_proveedor ------------------------------------------------------
-- Mismo criterio que movimientos_cuenta: libro append-only (no UPDATE, no DELETE) y cada
-- movimiento se valida en INSERT contra su respaldo real, no solo contra la forma que ya exige
-- el CHECK de arriba.

CREATE TRIGGER trg_movimientos_proveedor_cargo_valido BEFORE INSERT ON movimientos_proveedor
WHEN NEW.tipo = 'CARGO_COMPRA'
BEGIN
    SELECT RAISE(ABORT, 'El cargo debe respaldarse en una compra a crédito activa del mismo proveedor y por su mismo total.')
    WHERE NOT EXISTS (
        SELECT 1 FROM compras c
        WHERE c.id = NEW.compra_id AND c.proveedor_id = NEW.proveedor_id
          AND c.condicion_pago = 'CREDITO' AND c.estado = 'ACTIVA' AND c.total_centavos = NEW.monto_centavos
    );
END;

CREATE TRIGGER trg_movimientos_proveedor_reversa_valida BEFORE INSERT ON movimientos_proveedor
WHEN NEW.tipo = 'REVERSA_COMPRA'
BEGIN
    SELECT RAISE(ABORT, 'La reversa debe respaldarse en una compra existente del mismo proveedor y por su mismo total.')
    WHERE NOT EXISTS (
        SELECT 1 FROM compras c
        WHERE c.id = NEW.compra_id AND c.proveedor_id = NEW.proveedor_id AND c.total_centavos = NEW.monto_centavos
    );
    SELECT RAISE(ABORT, 'La reversa requiere un cargo previo de la misma compra, proveedor y monto.')
    WHERE NOT EXISTS (
        SELECT 1 FROM movimientos_proveedor cargo
        WHERE cargo.compra_id = NEW.compra_id AND cargo.tipo = 'CARGO_COMPRA'
          AND cargo.proveedor_id = NEW.proveedor_id AND cargo.monto_centavos = NEW.monto_centavos
    );
END;

CREATE TRIGGER trg_movimientos_proveedor_pago_efectivo_valido BEFORE INSERT ON movimientos_proveedor
WHEN NEW.tipo = 'PAGO' AND NEW.medio_pago = 'EFECTIVO'
BEGIN
    SELECT RAISE(ABORT, 'El pago en efectivo debe respaldarse en un egreso de caja PAGO_PROVEEDOR de la sesión abierta y por el mismo monto.')
    WHERE NOT EXISTS (
        SELECT 1 FROM caja_movimientos m JOIN sesiones_caja s ON s.id = m.sesion_caja_id
        WHERE m.id = NEW.caja_movimiento_id AND m.origen = 'PAGO_PROVEEDOR' AND m.tipo = 'EGRESO'
          AND m.monto_centavos = NEW.monto_centavos AND s.estado = 'ABIERTA'
    );
END;

CREATE TRIGGER trg_movimientos_proveedor_inmutable BEFORE UPDATE ON movimientos_proveedor
BEGIN
    SELECT RAISE(ABORT, 'Los movimientos de cuenta de proveedor no pueden modificarse.');
END;

CREATE TRIGGER trg_movimientos_proveedor_no_borrable BEFORE DELETE ON movimientos_proveedor
BEGIN
    SELECT RAISE(ABORT, 'Los movimientos de cuenta de proveedor no pueden borrarse.');
END;

-- 10. Verificación final --------------------------------------------------------------------

INSERT INTO _fallas SELECT 'movimientos_proveedor no quedó vacío tras la migración (deuda retroactiva)'
WHERE EXISTS (SELECT 1 FROM movimientos_proveedor);
INSERT INTO _fallas SELECT 'alguna compra histórica no quedó CONTADO'
WHERE EXISTS (SELECT 1 FROM compras WHERE condicion_pago <> 'CONTADO');
INSERT INTO _fallas SELECT 'compras: cantidad de filas cambió'
WHERE (SELECT COUNT(*) FROM compras) <> (SELECT compras_n FROM _antes);
INSERT INTO _fallas SELECT 'triggers/índices de la migración 025 incompletos'
WHERE (SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND name IN (
    'trg_caja_movimientos_pago_proveedor_solo_egreso', 'trg_caja_movimientos_pago_proveedor_tipo_inmutable',
    'trg_caja_movimientos_pago_proveedor_monto_inmutable',
    'trg_movimientos_proveedor_cargo_valido', 'trg_movimientos_proveedor_reversa_valida',
    'trg_movimientos_proveedor_pago_efectivo_valido', 'trg_movimientos_proveedor_inmutable',
    'trg_movimientos_proveedor_no_borrable')) <> 8;
INSERT INTO _fallas SELECT 'foreign_key_check final' WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;
INSERT INTO _fallas SELECT 'integrity_check final'
WHERE (SELECT integrity_check FROM pragma_integrity_check) <> 'ok';

DROP TRIGGER temp._abortar_por_falla;
DROP TABLE temp._fallas;
DROP TABLE temp._objetos_cuenta_antes;
DROP TABLE temp._objetos_caja_antes;
DROP TABLE temp._cuenta_copia;
DROP TABLE temp._caja_copia;
DROP TABLE temp._antes;
