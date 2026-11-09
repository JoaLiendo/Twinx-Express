-- Migración 048: gastos operativos del comercio (cierre operativo, bloque A).
--
-- Un gasto es un egreso del negocio que NO es mercadería (alquiler, luz, agua, Internet, servicios,
-- salarios, mantenimiento, transporte, comisiones, impuestos y tasas, otros). La mercadería no va acá:
-- su costo se reconoce al vender (margen bruto) y registrarla también como gasto la contaría dos veces.
--
--   * `importe_centavos` en centavos enteros, siempre > 0 (la dirección es siempre "sale dinero").
--   * `medio_pago`: 'EFECTIVO_CAJA' = con efectivo de la caja abierta; genera un EGRESO de caja en la misma
--     transacción y lo referencia en `caja_movimiento_id` (un egreso respalda a lo sumo un gasto). Los demás
--     medios ('EFECTIVO' fuera de la caja, 'TRANSFERENCIA', 'TARJETA', 'DEBITO_AUTOMATICO', 'OTRO') no tocan
--     la caja.
--   * `descripcion` es obligatoria para la categoría 'OTRO' (un "otro" sin explicación no sirve para nada).
--   * Un gasto con historia económica no desaparece: no se borra (trigger) y lo económico (fecha, importe,
--     medio de pago, vínculo con la caja, autor, clave) es inmutable; se corrige anulándolo (estado 'ANULADO',
--     con motivo, fecha y usuario) y cargando otro. Lo descriptivo (categoría, descripción, referencia,
--     responsable, observaciones) se edita mientras está VIGENTE, auditado por el servicio.
--   * `caja_movimiento_reintegro_id`: el INGRESO que devolvió el efectivo a la caja al anular un gasto
--     'EFECTIVO_CAJA', si el OWNER indicó que volvió. Se registra una sola vez y solo al anular.
--   * `clave_idempotencia`: un reenvío del formulario no crea dos gastos (ni dos egresos de caja).
-- Nada histórico se toca: la tabla nace vacía.

CREATE TABLE gastos (
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha                         TEXT NOT NULL CHECK (length(fecha) = 10 AND fecha = date(fecha)),
    categoria                     TEXT NOT NULL CHECK (categoria IN (
                                      'ALQUILER', 'ELECTRICIDAD', 'AGUA', 'INTERNET', 'SERVICIOS', 'SALARIOS',
                                      'MANTENIMIENTO', 'TRANSPORTE', 'COMISIONES', 'IMPUESTOS_Y_TASAS', 'OTRO')),
    descripcion                   TEXT NULL,
    importe_centavos              INTEGER NOT NULL CHECK (importe_centavos > 0),
    medio_pago                    TEXT NOT NULL CHECK (medio_pago IN (
                                      'EFECTIVO_CAJA', 'EFECTIVO', 'TRANSFERENCIA', 'TARJETA', 'DEBITO_AUTOMATICO', 'OTRO')),
    referencia                    TEXT NULL,
    responsable                   TEXT NULL,
    observaciones                 TEXT NULL,
    usuario_id                    INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    creado_en                     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    modificado_en                 TEXT NULL,
    estado                        TEXT NOT NULL DEFAULT 'VIGENTE' CHECK (estado IN ('VIGENTE', 'ANULADO')),
    anulado_en                    TEXT NULL,
    anulado_por_usuario_id        INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    motivo_anulacion              TEXT NULL,
    caja_movimiento_id            INTEGER NULL REFERENCES caja_movimientos(id) ON DELETE RESTRICT,
    caja_movimiento_reintegro_id  INTEGER NULL REFERENCES caja_movimientos(id) ON DELETE RESTRICT,
    clave_idempotencia            TEXT NULL,
    CHECK (categoria <> 'OTRO' OR (descripcion IS NOT NULL AND length(trim(descripcion)) > 0)),
    CHECK ((estado = 'VIGENTE' AND anulado_en IS NULL AND anulado_por_usuario_id IS NULL AND motivo_anulacion IS NULL)
        OR (estado = 'ANULADO' AND anulado_en IS NOT NULL AND anulado_por_usuario_id IS NOT NULL
            AND motivo_anulacion IS NOT NULL AND length(trim(motivo_anulacion)) > 0)),
    CHECK ((medio_pago = 'EFECTIVO_CAJA' AND caja_movimiento_id IS NOT NULL)
        OR (medio_pago <> 'EFECTIVO_CAJA' AND caja_movimiento_id IS NULL AND caja_movimiento_reintegro_id IS NULL))
);

CREATE INDEX idx_gastos_fecha ON gastos(fecha);
CREATE INDEX idx_gastos_categoria ON gastos(categoria);
CREATE UNIQUE INDEX idx_gastos_clave_idempotencia ON gastos(clave_idempotencia) WHERE clave_idempotencia IS NOT NULL;
CREATE UNIQUE INDEX idx_gastos_caja_movimiento ON gastos(caja_movimiento_id) WHERE caja_movimiento_id IS NOT NULL;
CREATE UNIQUE INDEX idx_gastos_caja_movimiento_reintegro
    ON gastos(caja_movimiento_reintegro_id) WHERE caja_movimiento_reintegro_id IS NOT NULL;

-- Nace vigente y sin reintegro; el egreso de caja (si lo hay) es un EGRESO por el mismo importe.
CREATE TRIGGER trg_gastos_alta_coherente BEFORE INSERT ON gastos
WHEN NEW.estado <> 'VIGENTE' OR NEW.caja_movimiento_reintegro_id IS NOT NULL OR NEW.modificado_en IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Un gasto nace vigente, sin reintegro ni modificaciones.');
END;

CREATE TRIGGER trg_gastos_egreso_valido BEFORE INSERT ON gastos
WHEN NEW.caja_movimiento_id IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM caja_movimientos
                     WHERE id = NEW.caja_movimiento_id AND tipo = 'EGRESO' AND monto_centavos = NEW.importe_centavos)
BEGIN
    SELECT RAISE(ABORT, 'El egreso de caja del gasto no corresponde.');
END;

CREATE TRIGGER trg_gastos_economico_inmutable BEFORE UPDATE OF
    fecha, importe_centavos, medio_pago, caja_movimiento_id, usuario_id, creado_en, clave_idempotencia ON gastos
WHEN NEW.fecha IS NOT OLD.fecha OR NEW.importe_centavos IS NOT OLD.importe_centavos
     OR NEW.medio_pago IS NOT OLD.medio_pago OR NEW.caja_movimiento_id IS NOT OLD.caja_movimiento_id
     OR NEW.usuario_id IS NOT OLD.usuario_id OR NEW.creado_en IS NOT OLD.creado_en
     OR NEW.clave_idempotencia IS NOT OLD.clave_idempotencia
BEGIN
    SELECT RAISE(ABORT, 'Lo económico de un gasto no se modifica: anulalo y registrá otro.');
END;

CREATE TRIGGER trg_gastos_anulado_inmutable BEFORE UPDATE ON gastos
WHEN OLD.estado = 'ANULADO'
     AND NOT (NEW.estado = 'ANULADO'
              AND NEW.categoria IS OLD.categoria AND NEW.descripcion IS OLD.descripcion
              AND NEW.referencia IS OLD.referencia AND NEW.responsable IS OLD.responsable
              AND NEW.observaciones IS OLD.observaciones AND NEW.modificado_en IS OLD.modificado_en
              AND NEW.anulado_en IS OLD.anulado_en AND NEW.anulado_por_usuario_id IS OLD.anulado_por_usuario_id
              AND NEW.motivo_anulacion IS OLD.motivo_anulacion
              AND NEW.caja_movimiento_reintegro_id IS OLD.caja_movimiento_reintegro_id)
BEGIN
    SELECT RAISE(ABORT, 'Un gasto anulado no se modifica.');
END;

-- La anulación es VIGENTE → ANULADO en un solo paso, con sus tres datos; nunca al revés.
CREATE TRIGGER trg_gastos_anulacion_completa BEFORE UPDATE OF estado, anulado_en, anulado_por_usuario_id, motivo_anulacion ON gastos
WHEN OLD.estado = 'VIGENTE'
     AND (NEW.estado IS NOT OLD.estado OR NEW.anulado_en IS NOT OLD.anulado_en
          OR NEW.anulado_por_usuario_id IS NOT OLD.anulado_por_usuario_id OR NEW.motivo_anulacion IS NOT OLD.motivo_anulacion)
     AND NOT (NEW.estado = 'ANULADO' AND NEW.anulado_en IS NOT NULL AND NEW.anulado_por_usuario_id IS NOT NULL
              AND NEW.motivo_anulacion IS NOT NULL AND length(trim(NEW.motivo_anulacion)) > 0)
BEGIN
    SELECT RAISE(ABORT, 'La anulación de un gasto exige fecha, usuario y motivo.');
END;

-- El reintegro a caja se registra una sola vez, al anular un gasto pagado con la caja, y es un INGRESO
-- por el mismo importe.
CREATE TRIGGER trg_gastos_reintegro_unico BEFORE UPDATE OF caja_movimiento_reintegro_id ON gastos
WHEN NEW.caja_movimiento_reintegro_id IS NOT OLD.caja_movimiento_reintegro_id
     AND NOT (OLD.caja_movimiento_reintegro_id IS NULL
              AND OLD.medio_pago = 'EFECTIVO_CAJA'
              AND NEW.estado = 'ANULADO'
              AND EXISTS (SELECT 1 FROM caja_movimientos
                          WHERE id = NEW.caja_movimiento_reintegro_id AND tipo = 'INGRESO'
                            AND monto_centavos = OLD.importe_centavos))
BEGIN
    SELECT RAISE(ABORT, 'El reintegro de caja de un gasto se registra una sola vez, al anularlo.');
END;

CREATE TRIGGER trg_gastos_sin_borrado BEFORE DELETE ON gastos
BEGIN
    SELECT RAISE(ABORT, 'Un gasto no se borra: se anula.');
END;
