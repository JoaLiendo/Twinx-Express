-- Migración 039: venta mixta, comprador por venta, obligaciones e imputaciones (V1.14-C/D).
--
-- La cuenta corriente sigue siendo el libro `movimientos_cuenta` (migración 020): CARGO por la
-- porción financiada de una venta, COBRO por cada pago del cliente. Esta migración le agrega la
-- causalidad que el libro no tenía:
--
--   * `ventas.pago_contado_centavos`: lo que una venta a cuenta (`CUENTA_CORRIENTE`) cobró en el
--     momento, en efectivo. 0 = venta 100% a crédito (todas las históricas). El CARGO es siempre
--     `total - pago_contado` (> 0): una venta mixta financia el resto. Una venta con otro medio no
--     lleva pago_contado (su total ya es contado).
--   * `compradores_venta`: quién compró a nombre de una EMPRESA en una venta a cuenta, un comprador
--     autorizado vigente o una autorización excepcional (nombre + método + detalle + usuario). Las
--     ventas históricas no tienen comprador: nunca se inventa.
--   * `obligaciones_cliente`: una por CARGO (la crea un trigger al insertar el CARGO, así no puede
--     existir una venta financiada sin su obligación), con monto original y saldo pendiente.
--   * `imputaciones_cobro`: qué parte de cada COBRO pagó qué obligación. Persistidas y
--     append-only: nunca se recalculan desde el saldo total. El saldo pendiente de una obligación
--     es siempre `monto_original - SUM(imputaciones)` (lo exige un trigger).
--
-- MIGRACIÓN HISTÓRICA (causal, sin inventar):
--   * cada CARGO histórico tiene su `venta_id`: se convierte en su obligación con la misma fecha y
--     monto (causalidad real, no un saldo agregado);
--   * los COBRO históricos se registraron contra el saldo total, sin imputación. Se reconstruyen
--     con la regla por defecto (FIFO: el cobro paga las obligaciones más antiguas primero, en el
--     orden del libro) y quedan marcadas `HEREDADA_FIFO` para distinguirlas de una imputación
--     registrada en el momento. La reconstrucción es determinista y exacta: como el libro nunca
--     admitió un cobro mayor al saldo, cada cobro cubre solo obligaciones anteriores a él.
--   * el saldo de cada cliente NO cambia: se verifica `SUM(saldos pendientes) == saldo del libro`.

DROP TABLE IF EXISTS temp._fallas;
DROP TABLE IF EXISTS temp._antes;
CREATE TEMP TABLE _fallas (motivo TEXT NOT NULL);
CREATE TEMP TRIGGER _abortar_por_falla AFTER INSERT ON _fallas
BEGIN
    SELECT RAISE(ABORT, 'Migración 039: verificación fallida: ' || NEW.motivo);
END;

INSERT INTO _fallas SELECT 'ya existe alguna tabla de la migración 039'
WHERE EXISTS (SELECT 1 FROM sqlite_master
              WHERE name IN ('obligaciones_cliente', 'imputaciones_cobro', 'compradores_venta'));
INSERT INTO _fallas SELECT 'la tabla ventas ya tiene pago_contado_centavos'
WHERE EXISTS (SELECT 1 FROM pragma_table_info('ventas') WHERE name = 'pago_contado_centavos');
INSERT INTO _fallas SELECT 'hay un CARGO histórico sin venta a cuenta que lo respalde'
WHERE EXISTS (SELECT 1 FROM movimientos_cuenta m LEFT JOIN ventas v ON v.id = m.venta_id
              WHERE m.tipo = 'CARGO' AND (v.id IS NULL OR v.tipo_pago <> 'CUENTA_CORRIENTE'
                                          OR v.cliente_id IS NOT m.cliente_id OR v.total_centavos <> m.monto_centavos));
INSERT INTO _fallas SELECT 'hay un cliente con saldo negativo en el libro'
WHERE EXISTS (SELECT 1 FROM movimientos_cuenta GROUP BY cliente_id
              HAVING SUM(CASE tipo WHEN 'CARGO' THEN monto_centavos ELSE -monto_centavos END) < 0);
INSERT INTO _fallas SELECT 'foreign_key_check previo'
WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;

CREATE TEMP TABLE _antes AS
SELECT (SELECT COUNT(*) FROM ventas)                                   AS ventas_n,
       (SELECT COALESCE(SUM(total_centavos), 0) FROM ventas)           AS ventas_total,
       (SELECT COUNT(*) FROM movimientos_cuenta)                       AS libro_n,
       (SELECT COUNT(*) FROM caja_movimientos)                         AS caja_n,
       (SELECT COALESCE(SUM(monto_centavos), 0) FROM caja_movimientos) AS caja_total;

-- 1. Venta mixta -----------------------------------------------------------------------------

ALTER TABLE ventas ADD COLUMN pago_contado_centavos INTEGER NOT NULL DEFAULT 0 CHECK (pago_contado_centavos >= 0);

CREATE TRIGGER trg_ventas_contado_valido BEFORE INSERT ON ventas
WHEN (NEW.tipo_pago <> 'CUENTA_CORRIENTE' AND NEW.pago_contado_centavos <> 0)
     OR (NEW.tipo_pago = 'CUENTA_CORRIENTE' AND NEW.pago_contado_centavos >= NEW.total_centavos)
BEGIN
    SELECT RAISE(ABORT, 'El pago al contado solo existe en una venta a cuenta y debe ser menor que su total.');
END;

CREATE TRIGGER trg_ventas_contado_inmutable BEFORE UPDATE OF pago_contado_centavos ON ventas
WHEN OLD.pago_contado_centavos IS NOT NEW.pago_contado_centavos
BEGIN
    SELECT RAISE(ABORT, 'El pago al contado de una venta no puede modificarse.');
END;

-- 2. Comprador de una venta a cuenta de una empresa ------------------------------------------------

CREATE TABLE compradores_venta (
    venta_id                INTEGER PRIMARY KEY REFERENCES ventas(id) ON DELETE RESTRICT,
    cliente_id              INTEGER NOT NULL REFERENCES clientes(id) ON DELETE RESTRICT,
    comprador_autorizado_id INTEGER NULL REFERENCES compradores_autorizados(id) ON DELETE RESTRICT,
    nombre_excepcional      TEXT NULL CHECK (nombre_excepcional IS NULL
                                             OR (length(trim(nombre_excepcional)) > 0 AND length(nombre_excepcional) <= 120)),
    metodo_autorizacion     TEXT NULL CHECK (metodo_autorizacion IS NULL
                                             OR metodo_autorizacion IN ('WHATSAPP', 'EMAIL', 'ORDEN', 'LLAMADA', 'OTRO')),
    detalle_autorizacion    TEXT NULL CHECK (detalle_autorizacion IS NULL
                                             OR (length(trim(detalle_autorizacion)) > 0 AND length(detalle_autorizacion) <= 250)),
    usuario_id              INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fecha                   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    -- O un autorizado habitual, o una autorización excepcional completa: nunca ambos ni ninguno.
    CHECK ((comprador_autorizado_id IS NOT NULL AND nombre_excepcional IS NULL AND metodo_autorizacion IS NULL
            AND detalle_autorizacion IS NULL)
           OR (comprador_autorizado_id IS NULL AND nombre_excepcional IS NOT NULL AND metodo_autorizacion IS NOT NULL
               AND detalle_autorizacion IS NOT NULL))
);

CREATE INDEX idx_compradores_venta_autorizado ON compradores_venta(comprador_autorizado_id);

CREATE TRIGGER trg_compradores_venta_valido BEFORE INSERT ON compradores_venta
BEGIN
    SELECT RAISE(ABORT, 'El comprador corresponde a una venta a cuenta de esa misma empresa.')
    WHERE NOT EXISTS (SELECT 1 FROM ventas v JOIN clientes c ON c.id = v.cliente_id
                      WHERE v.id = NEW.venta_id AND v.cliente_id = NEW.cliente_id
                        AND v.tipo_pago = 'CUENTA_CORRIENTE' AND c.tipo_cliente = 'EMPRESA');
    SELECT RAISE(ABORT, 'El comprador autorizado no está vigente para esta empresa.')
    WHERE NEW.comprador_autorizado_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM compradores_autorizados
                      WHERE id = NEW.comprador_autorizado_id AND cliente_id = NEW.cliente_id AND activo = 1);
END;

CREATE TRIGGER trg_compradores_venta_inmutable BEFORE UPDATE ON compradores_venta
BEGIN
    SELECT RAISE(ABORT, 'El comprador registrado en una venta no puede modificarse.');
END;

CREATE TRIGGER trg_compradores_venta_no_borrable BEFORE DELETE ON compradores_venta
BEGIN
    SELECT RAISE(ABORT, 'El comprador registrado en una venta no puede borrarse.');
END;

-- 3. Obligaciones ----------------------------------------------------------------------------

CREATE TABLE obligaciones_cliente (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id               INTEGER NOT NULL REFERENCES clientes(id) ON DELETE RESTRICT,
    venta_id                 INTEGER NOT NULL UNIQUE REFERENCES ventas(id) ON DELETE RESTRICT,
    movimiento_cargo_id      INTEGER NOT NULL UNIQUE REFERENCES movimientos_cuenta(id) ON DELETE RESTRICT,
    monto_original_centavos  INTEGER NOT NULL CHECK (monto_original_centavos > 0),
    saldo_pendiente_centavos INTEGER NOT NULL
        CHECK (saldo_pendiente_centavos >= 0 AND saldo_pendiente_centavos <= monto_original_centavos),
    fecha_origen             TEXT NOT NULL
);

-- Las pendientes de un cliente, de la más antigua a la más nueva (FIFO y antigüedad).
CREATE INDEX idx_obligaciones_cliente_pendientes
    ON obligaciones_cliente(cliente_id, movimiento_cargo_id) WHERE saldo_pendiente_centavos > 0;

-- Una obligación solo nace como espejo exacto de su CARGO (saldo completo).
CREATE TRIGGER trg_obligaciones_cliente_espejo_del_cargo BEFORE INSERT ON obligaciones_cliente
WHEN NOT EXISTS (SELECT 1 FROM movimientos_cuenta m
                 WHERE m.id = NEW.movimiento_cargo_id AND m.tipo = 'CARGO' AND m.cliente_id = NEW.cliente_id
                   AND m.venta_id = NEW.venta_id AND m.monto_centavos = NEW.monto_original_centavos
                   AND m.fecha = NEW.fecha_origen)
     OR NEW.saldo_pendiente_centavos <> NEW.monto_original_centavos
BEGIN
    SELECT RAISE(ABORT, 'Una obligación solo nace de su CARGO, por el monto completo.');
END;

-- Solo cambia el saldo, y solo para reflejar exactamente sus imputaciones.
CREATE TRIGGER trg_obligaciones_cliente_saldo_imputado BEFORE UPDATE ON obligaciones_cliente
WHEN NEW.id IS NOT OLD.id OR NEW.cliente_id IS NOT OLD.cliente_id OR NEW.venta_id IS NOT OLD.venta_id
     OR NEW.movimiento_cargo_id IS NOT OLD.movimiento_cargo_id
     OR NEW.monto_original_centavos IS NOT OLD.monto_original_centavos OR NEW.fecha_origen IS NOT OLD.fecha_origen
     OR NEW.saldo_pendiente_centavos <> OLD.monto_original_centavos
        - (SELECT COALESCE(SUM(monto_centavos), 0) FROM imputaciones_cobro WHERE obligacion_id = OLD.id)
BEGIN
    SELECT RAISE(ABORT, 'El saldo de una obligación solo refleja sus imputaciones.');
END;

CREATE TRIGGER trg_obligaciones_cliente_no_borrable BEFORE DELETE ON obligaciones_cliente
BEGIN
    SELECT RAISE(ABORT, 'Las obligaciones de clientes no se borran.');
END;

-- 4. Imputaciones ----------------------------------------------------------------------------

CREATE TABLE imputaciones_cobro (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    movimiento_cobro_id INTEGER NOT NULL REFERENCES movimientos_cuenta(id) ON DELETE RESTRICT,
    obligacion_id       INTEGER NOT NULL REFERENCES obligaciones_cliente(id) ON DELETE RESTRICT,
    monto_centavos      INTEGER NOT NULL CHECK (monto_centavos > 0),
    tipo                TEXT NOT NULL CHECK (tipo IN ('FIFO', 'MANUAL', 'HEREDADA_FIFO')),
    observacion         TEXT NULL CHECK (observacion IS NULL
                                         OR (length(trim(observacion)) > 0 AND length(observacion) <= 250)),
    usuario_id          INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fecha               TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (movimiento_cobro_id, obligacion_id),
    -- Una imputación manual es una excepción: siempre explicada y con el OWNER que la hizo.
    CHECK (tipo <> 'MANUAL' OR (observacion IS NOT NULL AND usuario_id IS NOT NULL))
);

CREATE INDEX idx_imputaciones_cobro_obligacion ON imputaciones_cobro(obligacion_id);

CREATE TRIGGER trg_imputaciones_cobro_valida BEFORE INSERT ON imputaciones_cobro
BEGIN
    SELECT RAISE(ABORT, 'La imputación debe aplicar un COBRO a una obligación anterior del mismo cliente.')
    WHERE NOT EXISTS (SELECT 1 FROM movimientos_cuenta m JOIN obligaciones_cliente o ON o.id = NEW.obligacion_id
                      WHERE m.id = NEW.movimiento_cobro_id AND m.tipo = 'COBRO' AND m.cliente_id = o.cliente_id
                        AND o.movimiento_cargo_id < m.id);
    SELECT RAISE(ABORT, 'La imputación supera el saldo pendiente de la obligación.')
    WHERE NEW.monto_centavos > (SELECT saldo_pendiente_centavos FROM obligaciones_cliente WHERE id = NEW.obligacion_id);
    SELECT RAISE(ABORT, 'Las imputaciones superan el monto del cobro.')
    WHERE NEW.monto_centavos
          + (SELECT COALESCE(SUM(monto_centavos), 0) FROM imputaciones_cobro WHERE movimiento_cobro_id = NEW.movimiento_cobro_id)
          > (SELECT monto_centavos FROM movimientos_cuenta WHERE id = NEW.movimiento_cobro_id);
END;

CREATE TRIGGER trg_imputaciones_cobro_descuenta_saldo AFTER INSERT ON imputaciones_cobro
BEGIN
    UPDATE obligaciones_cliente SET saldo_pendiente_centavos = saldo_pendiente_centavos - NEW.monto_centavos
    WHERE id = NEW.obligacion_id;
END;

CREATE TRIGGER trg_imputaciones_cobro_inmutable BEFORE UPDATE ON imputaciones_cobro
BEGIN
    SELECT RAISE(ABORT, 'Las imputaciones de cobros no pueden modificarse.');
END;

CREATE TRIGGER trg_imputaciones_cobro_no_borrable BEFORE DELETE ON imputaciones_cobro
BEGIN
    SELECT RAISE(ABORT, 'Las imputaciones de cobros no pueden borrarse.');
END;

-- 5. Libro: el CARGO es la porción financiada y nace con su obligación ---------------------------

DROP TRIGGER trg_movimientos_cuenta_cargo_valido;

CREATE TRIGGER trg_movimientos_cuenta_cargo_valido BEFORE INSERT ON movimientos_cuenta
WHEN NEW.tipo = 'CARGO'
BEGIN
    SELECT RAISE(ABORT, 'El cargo requiere un cliente activo.')
    WHERE NOT EXISTS (SELECT 1 FROM clientes WHERE id = NEW.cliente_id AND activo = 1);
    SELECT RAISE(ABORT, 'El cargo debe respaldarse en una venta a cuenta activa del mismo cliente, de la sesión abierta y por la porción financiada.')
    WHERE NOT EXISTS (
        SELECT 1 FROM ventas v JOIN sesiones_caja s ON s.id = v.sesion_caja_id
        WHERE v.id = NEW.venta_id AND v.tipo_pago = 'CUENTA_CORRIENTE' AND v.cliente_id = NEW.cliente_id
          AND v.total_centavos - v.pago_contado_centavos = NEW.monto_centavos
          AND v.estado = 'ACTIVA' AND s.estado = 'ABIERTA'
    );
    SELECT RAISE(ABORT, 'Una compra a cuenta de una empresa requiere registrar quién compró.')
    WHERE EXISTS (SELECT 1 FROM clientes WHERE id = NEW.cliente_id AND tipo_cliente = 'EMPRESA')
      AND NOT EXISTS (SELECT 1 FROM compradores_venta WHERE venta_id = NEW.venta_id);
END;

CREATE TRIGGER trg_movimientos_cuenta_cargo_crea_obligacion AFTER INSERT ON movimientos_cuenta
WHEN NEW.tipo = 'CARGO'
BEGIN
    INSERT INTO obligaciones_cliente
        (cliente_id, venta_id, movimiento_cargo_id, monto_original_centavos, saldo_pendiente_centavos, fecha_origen)
    VALUES (NEW.cliente_id, NEW.venta_id, NEW.id, NEW.monto_centavos, NEW.monto_centavos, NEW.fecha);
END;

-- 6. Migración causal de la historia -------------------------------------------------------------

INSERT INTO obligaciones_cliente
    (cliente_id, venta_id, movimiento_cargo_id, monto_original_centavos, saldo_pendiente_centavos, fecha_origen)
SELECT cliente_id, venta_id, id, monto_centavos, monto_centavos, fecha
FROM movimientos_cuenta WHERE tipo = 'CARGO' ORDER BY id;

-- FIFO reconstruido: el k-ésimo peso cobrado a un cliente pagó su k-ésimo peso cargado (en el orden
-- del libro). Cada par (cobro, obligación) recibe la intersección de sus tramos acumulados.
WITH cargos AS (
    SELECT o.id AS obligacion_id, o.cliente_id, o.monto_original_centavos AS monto,
           SUM(o.monto_original_centavos) OVER (PARTITION BY o.cliente_id ORDER BY o.movimiento_cargo_id) AS fin
    FROM obligaciones_cliente o
),
cobros AS (
    SELECT m.id AS cobro_id, m.cliente_id, m.monto_centavos AS monto, m.fecha, m.usuario_id,
           SUM(m.monto_centavos) OVER (PARTITION BY m.cliente_id ORDER BY m.id) AS fin
    FROM movimientos_cuenta m WHERE m.tipo = 'COBRO'
),
cruces AS (
    SELECT p.cobro_id, c.obligacion_id, p.usuario_id, p.fecha,
           MIN(c.fin, p.fin) - MAX(c.fin - c.monto, p.fin - p.monto) AS monto
    FROM cargos c JOIN cobros p ON p.cliente_id = c.cliente_id
)
INSERT INTO imputaciones_cobro (movimiento_cobro_id, obligacion_id, monto_centavos, tipo, usuario_id, fecha)
SELECT cobro_id, obligacion_id, monto, 'HEREDADA_FIFO', usuario_id, fecha
FROM cruces WHERE monto > 0
ORDER BY cobro_id, obligacion_id;

-- 7. Verificación -------------------------------------------------------------------------------

INSERT INTO _fallas SELECT 'no todo CARGO tiene exactamente su obligación'
WHERE (SELECT COUNT(*) FROM obligaciones_cliente) <> (SELECT COUNT(*) FROM movimientos_cuenta WHERE tipo = 'CARGO');
INSERT INTO _fallas SELECT 'el saldo de algún cliente cambió: ' || l.cliente_id
FROM (SELECT cliente_id, SUM(CASE tipo WHEN 'CARGO' THEN monto_centavos ELSE -monto_centavos END) AS saldo
      FROM movimientos_cuenta GROUP BY cliente_id) l
WHERE l.saldo <> (SELECT COALESCE(SUM(saldo_pendiente_centavos), 0) FROM obligaciones_cliente o WHERE o.cliente_id = l.cliente_id);
INSERT INTO _fallas SELECT 'un cobro histórico no quedó imputado completo: ' || m.id
FROM movimientos_cuenta m
WHERE m.tipo = 'COBRO'
  AND m.monto_centavos <> (SELECT COALESCE(SUM(monto_centavos), 0) FROM imputaciones_cobro WHERE movimiento_cobro_id = m.id);
INSERT INTO _fallas SELECT 'una obligación no cumple monto = imputado + saldo'
WHERE EXISTS (SELECT 1 FROM obligaciones_cliente o
              WHERE o.monto_original_centavos <> o.saldo_pendiente_centavos
                    + (SELECT COALESCE(SUM(monto_centavos), 0) FROM imputaciones_cobro WHERE obligacion_id = o.id));
INSERT INTO _fallas SELECT 'cambiaron ventas, libro o caja'
WHERE (SELECT COUNT(*) FROM ventas) <> (SELECT ventas_n FROM _antes)
   OR (SELECT COALESCE(SUM(total_centavos), 0) FROM ventas) <> (SELECT ventas_total FROM _antes)
   OR (SELECT COUNT(*) FROM movimientos_cuenta) <> (SELECT libro_n FROM _antes)
   OR (SELECT COUNT(*) FROM caja_movimientos) <> (SELECT caja_n FROM _antes)
   OR (SELECT COALESCE(SUM(monto_centavos), 0) FROM caja_movimientos) <> (SELECT caja_total FROM _antes);
INSERT INTO _fallas SELECT 'una venta histórica quedó con pago al contado o comprador inventado'
WHERE EXISTS (SELECT 1 FROM ventas WHERE pago_contado_centavos <> 0) OR EXISTS (SELECT 1 FROM compradores_venta);
INSERT INTO _fallas SELECT 'foreign_key_check final'
WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;
INSERT INTO _fallas SELECT 'integrity_check final'
WHERE (SELECT integrity_check FROM pragma_integrity_check) <> 'ok';

DROP TRIGGER temp._abortar_por_falla;
DROP TABLE temp._fallas;
DROP TABLE temp._antes;
