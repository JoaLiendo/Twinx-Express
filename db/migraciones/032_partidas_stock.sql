-- Migración 032: partidas de stock y su libro de movimientos (V1.12-A). Diseño: docs/V1.12_PARTIDAS.md.
--
-- `partidas_stock`: una existencia concreta de un producto (lo que entró junto, con un vencimiento y un
-- costo propios si se conocen). `movimientos_partida`: cada cambio de su cantidad disponible, con una
-- única causa persistida (línea de venta, ajuste o línea de compra).
--
-- Invariantes que garantiza la base:
--   * `cantidad_disponible` solo cambia por un movimiento (trigger que la recalcula al insertar uno y
--     rechaza cualquier otra escritura) y queda siempre entre 0 y `cantidad_original` (CHECK);
--   * movimientos inmutables (no se editan ni se borran);
--   * datos de origen inmutables (producto, origen, cantidad original, causa, vencimiento, costo, fecha).
-- La conciliación `productos.stock_actual = SUM(cantidad_disponible)` la verifican los servicios dentro de
-- la misma transacción de cada operación que mueve stock (ver `services.servicio_partidas`).
--
-- Existencias anteriores: una partida `HEREDADA` por producto con stock > 0 (activo o no), por exactamente
-- su stock actual, sin vencimiento, costo ni fecha de ingreso: no se inventa ningún dato. Productos sin
-- stock no tienen partida. Se verifica abajo; cualquier falla revierte TODO (el runner usa un único
-- `BEGIN IMMEDIATE`).

DROP TABLE IF EXISTS temp._fallas;
DROP TABLE IF EXISTS temp._antes;

CREATE TEMP TABLE _fallas (motivo TEXT NOT NULL);
CREATE TEMP TRIGGER _abortar_por_falla AFTER INSERT ON _fallas
BEGIN
    SELECT RAISE(ABORT, 'Migración 032: verificación fallida: ' || NEW.motivo);
END;

INSERT INTO _fallas SELECT 'ya existen tablas de partidas'
WHERE EXISTS (SELECT 1 FROM sqlite_master WHERE name IN ('partidas_stock', 'movimientos_partida'));
INSERT INTO _fallas SELECT 'hay productos con stock negativo'
WHERE EXISTS (SELECT 1 FROM productos WHERE stock_actual < 0);
INSERT INTO _fallas SELECT 'foreign_key_check previo'
WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;
INSERT INTO _fallas SELECT 'integrity_check previo'
WHERE (SELECT integrity_check FROM pragma_integrity_check) <> 'ok';
INSERT INTO _fallas SELECT 'las claves foráneas no están activas'
WHERE (SELECT foreign_keys FROM pragma_foreign_keys) <> 1;

CREATE TEMP TABLE _antes AS
SELECT
    (SELECT COUNT(*) FROM productos)                                AS productos_n,
    (SELECT COALESCE(SUM(stock_actual), 0) FROM productos)          AS stock_total,
    (SELECT COUNT(*) FROM productos WHERE stock_actual > 0)         AS productos_con_stock,
    (SELECT COALESCE(SUM(version_stock), 0) FROM productos)         AS versiones;

CREATE TABLE partidas_stock (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id              INTEGER NOT NULL REFERENCES productos(id) ON DELETE RESTRICT,
    -- ANULACION_VENTA: unidades que volvieron al anular una venta anterior a V1.12 (sin partida conocida).
    origen                   TEXT NOT NULL CHECK (
                                 origen IN ('HEREDADA', 'STOCK_INICIAL', 'COMPRA', 'RECUENTO', 'AJUSTE', 'ANULACION_VENTA')
                             ),
    detalle_compra_id        INTEGER NULL REFERENCES detalle_compra(id) ON DELETE RESTRICT,
    ajuste_id                INTEGER NULL REFERENCES ajustes_stock(id) ON DELETE RESTRICT,
    detalle_venta_id         INTEGER NULL REFERENCES detalle_venta(id) ON DELETE RESTRICT,
    cantidad_original        INTEGER NOT NULL CHECK (cantidad_original > 0),
    cantidad_disponible      INTEGER NOT NULL CHECK (cantidad_disponible >= 0 AND cantidad_disponible <= cantidad_original),
    fecha_vencimiento        TEXT NULL CHECK (
                                 fecha_vencimiento IS NULL
                                 OR (length(fecha_vencimiento) = 10 AND date(fecha_vencimiento) IS fecha_vencimiento)
                             ),
    costo_unitario_centavos  INTEGER NULL CHECK (costo_unitario_centavos IS NULL OR costo_unitario_centavos >= 0),
    fecha_ingreso            TEXT NULL,
    -- Solo la existencia heredada no tiene fecha de ingreso conocida, ni ningún otro dato.
    CHECK ((origen = 'HEREDADA') = (fecha_ingreso IS NULL)),
    CHECK (origen <> 'HEREDADA' OR (fecha_vencimiento IS NULL AND costo_unitario_centavos IS NULL)),
    -- Exactamente una causa de origen, salvo la heredada (ninguna), y la que corresponde a su origen.
    CHECK ((detalle_compra_id IS NOT NULL) + (ajuste_id IS NOT NULL) + (detalle_venta_id IS NOT NULL)
           = (origen <> 'HEREDADA')),
    CHECK (origen <> 'COMPRA' OR detalle_compra_id IS NOT NULL),
    CHECK (origen NOT IN ('STOCK_INICIAL', 'RECUENTO', 'AJUSTE') OR ajuste_id IS NOT NULL),
    CHECK (origen <> 'ANULACION_VENTA' OR (detalle_venta_id IS NOT NULL AND fecha_vencimiento IS NULL))
);

CREATE INDEX idx_partidas_stock_producto_id ON partidas_stock(producto_id);
CREATE INDEX idx_partidas_stock_detalle_compra_id ON partidas_stock(detalle_compra_id) WHERE detalle_compra_id IS NOT NULL;
CREATE INDEX idx_partidas_stock_ajuste_id ON partidas_stock(ajuste_id) WHERE ajuste_id IS NOT NULL;
CREATE INDEX idx_partidas_stock_detalle_venta_id ON partidas_stock(detalle_venta_id) WHERE detalle_venta_id IS NOT NULL;
CREATE UNIQUE INDEX idx_partidas_stock_una_heredada_por_producto ON partidas_stock(producto_id) WHERE origen = 'HEREDADA';

CREATE TABLE movimientos_partida (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    partida_id        INTEGER NOT NULL REFERENCES partidas_stock(id) ON DELETE RESTRICT,
    delta             INTEGER NOT NULL CHECK (delta <> 0),
    detalle_venta_id  INTEGER NULL REFERENCES detalle_venta(id) ON DELETE RESTRICT,
    ajuste_id         INTEGER NULL REFERENCES ajustes_stock(id) ON DELETE RESTRICT,
    detalle_compra_id INTEGER NULL REFERENCES detalle_compra(id) ON DELETE RESTRICT,
    -- Cómo se eligió la partida: FEFO (regla, la partida exacta no se conocía), ELEGIDA (se conocía: la
    -- eligió el usuario o la determina la causa, ej. anular la compra que la originó),
    -- UNICA (era la única con disponible) o REVERSION (deshace exactamente un movimiento anterior).
    criterio          TEXT NOT NULL CHECK (criterio IN ('FEFO', 'ELEGIDA', 'UNICA', 'REVERSION')),
    fecha             TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK ((detalle_venta_id IS NOT NULL) + (ajuste_id IS NOT NULL) + (detalle_compra_id IS NOT NULL) = 1)
);

CREATE INDEX idx_movimientos_partida_partida_id ON movimientos_partida(partida_id);
CREATE INDEX idx_movimientos_partida_detalle_venta_id ON movimientos_partida(detalle_venta_id) WHERE detalle_venta_id IS NOT NULL;
CREATE INDEX idx_movimientos_partida_ajuste_id ON movimientos_partida(ajuste_id) WHERE ajuste_id IS NOT NULL;
CREATE INDEX idx_movimientos_partida_detalle_compra_id ON movimientos_partida(detalle_compra_id) WHERE detalle_compra_id IS NOT NULL;

-- El movimiento es la única forma de cambiar el disponible: lo aplica este trigger (el CHECK de la
-- tabla rechaza consumir de más o devolver por encima de lo que entró).
CREATE TRIGGER trg_movimientos_partida_aplicar
AFTER INSERT ON movimientos_partida
BEGIN
    UPDATE partidas_stock SET cantidad_disponible = cantidad_disponible + NEW.delta WHERE id = NEW.partida_id;
END;

CREATE TRIGGER trg_partidas_stock_disponible_segun_movimientos
AFTER UPDATE OF cantidad_disponible ON partidas_stock
WHEN NEW.cantidad_disponible <> NEW.cantidad_original
     + (SELECT COALESCE(SUM(delta), 0) FROM movimientos_partida WHERE partida_id = NEW.id)
BEGIN
    SELECT RAISE(ABORT, 'El disponible de una partida solo cambia mediante un movimiento.');
END;

CREATE TRIGGER trg_partidas_stock_alta_completa
BEFORE INSERT ON partidas_stock
WHEN NEW.cantidad_disponible <> NEW.cantidad_original
BEGIN
    SELECT RAISE(ABORT, 'Una partida nueva nace con todo su disponible.');
END;

CREATE TRIGGER trg_partidas_stock_origen_inmutable
BEFORE UPDATE OF id, producto_id, origen, detalle_compra_id, ajuste_id, detalle_venta_id, cantidad_original,
                 fecha_vencimiento, costo_unitario_centavos, fecha_ingreso ON partidas_stock
BEGIN
    SELECT RAISE(ABORT, 'Los datos de origen de una partida no se pueden modificar.');
END;

CREATE TRIGGER trg_movimientos_partida_inmutable
BEFORE UPDATE ON movimientos_partida
BEGIN
    SELECT RAISE(ABORT, 'Un movimiento de partida no se puede modificar.');
END;

CREATE TRIGGER trg_movimientos_partida_sin_borrado
BEFORE DELETE ON movimientos_partida
BEGIN
    SELECT RAISE(ABORT, 'Un movimiento de partida no se puede borrar.');
END;

CREATE TRIGGER trg_partidas_stock_sin_borrado
BEFORE DELETE ON partidas_stock
BEGIN
    SELECT RAISE(ABORT, 'Una partida no se puede borrar.');
END;

-- Existencia heredada: exactamente el stock actual de cada producto, sin ningún dato inventado.
INSERT INTO partidas_stock (producto_id, origen, cantidad_original, cantidad_disponible)
SELECT id, 'HEREDADA', stock_actual, stock_actual FROM productos WHERE stock_actual > 0 ORDER BY id;

-- Verificación ---------------------------------------------------------------------------------

INSERT INTO _fallas SELECT 'productos: cantidad, stock o versión cambiaron'
WHERE (SELECT COUNT(*) FROM productos) <> (SELECT productos_n FROM _antes)
   OR (SELECT COALESCE(SUM(stock_actual), 0) FROM productos) <> (SELECT stock_total FROM _antes)
   OR (SELECT COALESCE(SUM(version_stock), 0) FROM productos) <> (SELECT versiones FROM _antes);
INSERT INTO _fallas SELECT 'una partida heredada por producto con stock'
WHERE (SELECT COUNT(*) FROM partidas_stock) <> (SELECT productos_con_stock FROM _antes);
INSERT INTO _fallas SELECT 'stock no conciliado con partidas: producto ' || p.id
FROM productos p
WHERE p.stock_actual <> (SELECT COALESCE(SUM(cantidad_disponible), 0) FROM partidas_stock WHERE producto_id = p.id);
INSERT INTO _fallas SELECT 'se inventó un dato en una partida heredada'
WHERE EXISTS (SELECT 1 FROM partidas_stock WHERE fecha_vencimiento IS NOT NULL OR costo_unitario_centavos IS NOT NULL
              OR fecha_ingreso IS NOT NULL OR origen <> 'HEREDADA');
INSERT INTO _fallas SELECT 'foreign_key_check final' WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;
INSERT INTO _fallas SELECT 'integrity_check final'
WHERE (SELECT integrity_check FROM pragma_integrity_check) <> 'ok';

DROP TABLE temp._fallas;
DROP TABLE temp._antes;
