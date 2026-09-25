-- Migración 022: inventario físico (V1.4).
-- Aditiva: agrega columnas nullable/con default y tablas nuevas; no reconstruye ninguna tabla.
--
-- 1) `productos.version_stock`: contador que un trigger incrementa en cada cambio REAL de
--    `stock_actual`. El inventario guarda la versión que vio al contar y al confirmar exige la
--    misma: así detecta cualquier movimiento posterior (venta, compra, ajuste, anulación), incluso
--    el caso ABA en que el stock vuelve al mismo número (venta -2 y compra +2). El valor absoluto
--    no importa, solo que crezca: los productos existentes parten en 0. El trigger no se dispara
--    por cambios de precio, nombre, imagen o `activo` (`UPDATE OF stock_actual` + `WHEN` valor
--    distinto), y su propio UPDATE no lista `stock_actual`, así que no se re-dispara.
--    IMPORTANTE: `version_stock` no forma parte de `_COLUMNAS` ni de los `UPDATE ... RETURNING` de
--    `db.repositorios.productos` (RETURNING no refleja los efectos de triggers AFTER): se lee con
--    consultas explícitas dentro de la transacción (ver `db.repositorios.inventarios`).
--
-- 2) `inventarios` / `inventario_lineas`: un inventario ABIERTO a la vez, sin reapertura
--    (ABIERTO -> CONFIRMADO | CANCELADO). Las líneas se fijan al crearlo; el conteo completa
--    esperado + versión + contado + costo + quién y cuándo, todo junto o nada. La diferencia no se
--    guarda: es `cantidad_contada - stock_esperado`.
--
-- 3) `ajustes_stock.inventario_id`: cada ajuste RECUENTO de un inventario queda explicado por su
--    origen, con a lo sumo uno por (inventario, producto).
--
-- Los triggers impiden dejar estados imposibles por acceso directo a la base: inventario cerrado
-- inmutable, líneas solo modificables mientras el inventario está ABIERTO, ajuste de inventario
-- que no coincide con su línea, y cierre CONFIRMADO con diferencias sin ajuste.

ALTER TABLE productos ADD COLUMN version_stock INTEGER NOT NULL DEFAULT 0 CHECK (version_stock >= 0);

CREATE TRIGGER trg_productos_version_stock
AFTER UPDATE OF stock_actual ON productos
WHEN NEW.stock_actual IS NOT OLD.stock_actual
BEGIN
    UPDATE productos SET version_stock = version_stock + 1 WHERE id = NEW.id;
END;

CREATE TABLE inventarios (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    estado              TEXT NOT NULL DEFAULT 'ABIERTO' CHECK (estado IN ('ABIERTO', 'CONFIRMADO', 'CANCELADO')),
    usuario_id          INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fecha_inicio        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    fecha_cierre        TEXT NULL,
    usuario_cierre_id   INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    observaciones       TEXT NULL,
    clave_idempotencia  TEXT NULL,
    CHECK (
        (estado = 'ABIERTO' AND fecha_cierre IS NULL AND usuario_cierre_id IS NULL)
        OR (estado <> 'ABIERTO' AND fecha_cierre IS NOT NULL AND usuario_cierre_id IS NOT NULL)
    )
);

CREATE UNIQUE INDEX idx_inventarios_un_solo_abierto ON inventarios(estado) WHERE estado = 'ABIERTO';
CREATE UNIQUE INDEX idx_inventarios_clave_idempotencia ON inventarios(clave_idempotencia)
    WHERE clave_idempotencia IS NOT NULL;

CREATE TABLE inventario_lineas (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    inventario_id            INTEGER NOT NULL REFERENCES inventarios(id) ON DELETE RESTRICT,
    producto_id              INTEGER NOT NULL REFERENCES productos(id) ON DELETE RESTRICT,
    stock_esperado           INTEGER NULL CHECK (stock_esperado IS NULL OR stock_esperado >= 0),
    version_esperada         INTEGER NULL CHECK (version_esperada IS NULL OR version_esperada >= 0),
    cantidad_contada         INTEGER NULL CHECK (cantidad_contada IS NULL OR cantidad_contada >= 0),
    costo_unitario_centavos  INTEGER NULL CHECK (costo_unitario_centavos IS NULL OR costo_unitario_centavos >= 0),
    usuario_conteo_id        INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fecha_conteo             TEXT NULL,
    ajuste_id                INTEGER NULL REFERENCES ajustes_stock(id) ON DELETE RESTRICT,
    UNIQUE (inventario_id, producto_id),
    CHECK (
        (stock_esperado IS NULL) = (version_esperada IS NULL)
        AND (stock_esperado IS NULL) = (cantidad_contada IS NULL)
        AND (stock_esperado IS NULL) = (costo_unitario_centavos IS NULL)
        AND (stock_esperado IS NULL) = (usuario_conteo_id IS NULL)
        AND (stock_esperado IS NULL) = (fecha_conteo IS NULL)
    ),
    CHECK (ajuste_id IS NULL OR cantidad_contada IS NOT NULL)
);

CREATE INDEX idx_inventario_lineas_producto_id ON inventario_lineas(producto_id);

ALTER TABLE ajustes_stock ADD COLUMN inventario_id INTEGER NULL REFERENCES inventarios(id) ON DELETE RESTRICT;

CREATE UNIQUE INDEX idx_ajustes_stock_inventario_producto ON ajustes_stock(inventario_id, producto_id)
    WHERE inventario_id IS NOT NULL;

-- --- inventarios ------------------------------------------------------------------------------------

CREATE TRIGGER trg_inventarios_cerrado_inmutable
BEFORE UPDATE ON inventarios
WHEN OLD.estado <> 'ABIERTO'
BEGIN
    SELECT RAISE(ABORT, 'Un inventario cerrado no se puede modificar.');
END;

CREATE TRIGGER trg_inventarios_datos_de_inicio_inmutables
BEFORE UPDATE OF usuario_id, fecha_inicio ON inventarios
BEGIN
    SELECT RAISE(ABORT, 'El usuario y la fecha de inicio de un inventario no se pueden modificar.');
END;

CREATE TRIGGER trg_inventarios_no_borrable
BEFORE DELETE ON inventarios
BEGIN
    SELECT RAISE(ABORT, 'Un inventario no se puede borrar.');
END;

CREATE TRIGGER trg_inventarios_confirmar_con_conteos
BEFORE UPDATE OF estado ON inventarios
WHEN OLD.estado = 'ABIERTO' AND NEW.estado = 'CONFIRMADO'
     AND NOT EXISTS (
         SELECT 1 FROM inventario_lineas WHERE inventario_id = OLD.id AND cantidad_contada IS NOT NULL
     )
BEGIN
    SELECT RAISE(ABORT, 'No se puede confirmar un inventario sin ninguna línea contada.');
END;

CREATE TRIGGER trg_inventarios_confirmar_con_ajustes
BEFORE UPDATE OF estado ON inventarios
WHEN OLD.estado = 'ABIERTO' AND NEW.estado = 'CONFIRMADO'
     AND EXISTS (
         SELECT 1
         FROM inventario_lineas l
         LEFT JOIN ajustes_stock a ON a.id = l.ajuste_id
         WHERE l.inventario_id = OLD.id
           AND l.cantidad_contada IS NOT NULL
           AND (
                (l.cantidad_contada <> l.stock_esperado AND
                    (a.id IS NULL OR a.inventario_id IS NOT l.inventario_id OR a.producto_id <> l.producto_id
                     OR a.delta <> l.cantidad_contada - l.stock_esperado))
                OR (l.cantidad_contada = l.stock_esperado AND l.ajuste_id IS NOT NULL)
           )
     )
BEGIN
    SELECT RAISE(ABORT, 'No se puede confirmar un inventario con diferencias sin su ajuste.');
END;

-- --- líneas --------------------------------------------------------------------------------------------

CREATE TRIGGER trg_inventario_lineas_alta_solo_abierto
BEFORE INSERT ON inventario_lineas
WHEN (SELECT estado FROM inventarios WHERE id = NEW.inventario_id) IS NOT 'ABIERTO'
BEGIN
    SELECT RAISE(ABORT, 'Solo se pueden agregar líneas a un inventario abierto.');
END;

CREATE TRIGGER trg_inventario_lineas_alta_sin_conteo
BEFORE INSERT ON inventario_lineas
WHEN NEW.cantidad_contada IS NOT NULL OR NEW.ajuste_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Una línea nueva de inventario no puede venir contada ni con ajuste.');
END;

CREATE TRIGGER trg_inventario_lineas_modificar_solo_abierto
BEFORE UPDATE ON inventario_lineas
WHEN (SELECT estado FROM inventarios WHERE id = OLD.inventario_id) IS NOT 'ABIERTO'
BEGIN
    SELECT RAISE(ABORT, 'Las líneas de un inventario cerrado no se pueden modificar.');
END;

CREATE TRIGGER trg_inventario_lineas_identidad_inmutable
BEFORE UPDATE OF inventario_id, producto_id ON inventario_lineas
BEGIN
    SELECT RAISE(ABORT, 'El inventario y el producto de una línea no se pueden modificar.');
END;

CREATE TRIGGER trg_inventario_lineas_no_borrables
BEFORE DELETE ON inventario_lineas
BEGIN
    SELECT RAISE(ABORT, 'Las líneas de un inventario no se pueden borrar.');
END;

-- --- ajustes de inventario --------------------------------------------------------------------------------

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
