-- Migración 051: pedidos de clientes (cierre operativo, bloque D).
--
-- Un pedido es lo que un cliente encarga para retirar o recibir después: productos, cantidades, notas y,
-- si corresponde, el cliente (o un contacto suelto). NO es una compra a proveedor ni una sugerencia de
-- reposición, y NO mueve dinero ni stock: no reserva ni descuenta existencias, no cobra, no factura. El
-- stock y el precio se validan de nuevo al concretar la venta en el POS, que es el único camino para
-- convertirlo (`pedidos.venta_id`, uno por venta y una venta por pedido).
--
-- Estados: PENDIENTE → CONVERTIDO (con `venta_id`) o PENDIENTE → CANCELADO (con motivo, fecha y usuario).
-- Un pedido terminal no cambia más; ninguno se borra. `precio_unitario_centavos` de cada línea es el
-- precio vigente cuando se tomó el pedido (informativo): el precio que rige es el del momento de la venta.

CREATE TABLE pedidos (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha_creacion            TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    usuario_id                INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    cliente_id                INTEGER NULL REFERENCES clientes(id) ON DELETE RESTRICT,
    contacto_nombre           TEXT NULL,
    contacto_telefono         TEXT NULL,
    notas                     TEXT NULL,
    fecha_entrega             TEXT NULL CHECK (fecha_entrega IS NULL OR (length(fecha_entrega) = 10 AND fecha_entrega = date(fecha_entrega))),
    estado                    TEXT NOT NULL DEFAULT 'PENDIENTE' CHECK (estado IN ('PENDIENTE', 'CONVERTIDO', 'CANCELADO')),
    venta_id                  INTEGER NULL REFERENCES ventas(id) ON DELETE RESTRICT,
    convertido_en             TEXT NULL,
    cancelado_en              TEXT NULL,
    cancelado_por_usuario_id  INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    motivo_cancelacion        TEXT NULL,
    clave_idempotencia        TEXT NULL,
    CHECK (cliente_id IS NOT NULL OR (contacto_nombre IS NOT NULL AND length(trim(contacto_nombre)) > 0)),
    CHECK ((estado = 'CONVERTIDO') = (venta_id IS NOT NULL)),
    CHECK ((estado = 'CONVERTIDO') = (convertido_en IS NOT NULL)),
    CHECK ((estado = 'CANCELADO') = (cancelado_en IS NOT NULL AND cancelado_por_usuario_id IS NOT NULL
                                     AND motivo_cancelacion IS NOT NULL AND length(trim(motivo_cancelacion)) > 0))
);

CREATE TABLE pedido_lineas (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    pedido_id                 INTEGER NOT NULL REFERENCES pedidos(id) ON DELETE RESTRICT,
    producto_id               INTEGER NOT NULL REFERENCES productos(id) ON DELETE RESTRICT,
    cantidad                  INTEGER NOT NULL CHECK (cantidad > 0),
    precio_unitario_centavos  INTEGER NOT NULL CHECK (precio_unitario_centavos >= 0),
    UNIQUE (pedido_id, producto_id)
);

CREATE INDEX idx_pedidos_estado_fecha ON pedidos(estado, fecha_creacion);
CREATE INDEX idx_pedidos_cliente ON pedidos(cliente_id);
CREATE UNIQUE INDEX idx_pedidos_venta ON pedidos(venta_id) WHERE venta_id IS NOT NULL;
CREATE UNIQUE INDEX idx_pedidos_clave_idempotencia ON pedidos(clave_idempotencia) WHERE clave_idempotencia IS NOT NULL;
CREATE INDEX idx_pedido_lineas_pedido ON pedido_lineas(pedido_id);

CREATE TRIGGER trg_pedidos_nace_pendiente BEFORE INSERT ON pedidos
WHEN NEW.estado <> 'PENDIENTE'
BEGIN
    SELECT RAISE(ABORT, 'Un pedido nace PENDIENTE.');
END;

CREATE TRIGGER trg_pedidos_terminal_inmutable BEFORE UPDATE ON pedidos
WHEN OLD.estado <> 'PENDIENTE'
BEGIN
    SELECT RAISE(ABORT, 'Un pedido convertido o cancelado no se modifica.');
END;

-- Lo que se pidió no se reescribe después de tomado el pedido: se cancela y se toma otro.
CREATE TRIGGER trg_pedidos_datos_inmutables BEFORE UPDATE OF usuario_id, fecha_creacion, cliente_id, clave_idempotencia ON pedidos
WHEN NEW.usuario_id IS NOT OLD.usuario_id OR NEW.fecha_creacion IS NOT OLD.fecha_creacion
     OR NEW.cliente_id IS NOT OLD.cliente_id OR NEW.clave_idempotencia IS NOT OLD.clave_idempotencia
BEGIN
    SELECT RAISE(ABORT, 'El origen de un pedido no se modifica.');
END;

CREATE TRIGGER trg_pedidos_sin_borrado BEFORE DELETE ON pedidos
BEGIN
    SELECT RAISE(ABORT, 'Un pedido no se borra: se cancela.');
END;

CREATE TRIGGER trg_pedido_lineas_inmutables BEFORE UPDATE ON pedido_lineas
BEGIN
    SELECT RAISE(ABORT, 'Las líneas de un pedido no se modifican.');
END;

CREATE TRIGGER trg_pedido_lineas_sin_borrado BEFORE DELETE ON pedido_lineas
BEGIN
    SELECT RAISE(ABORT, 'Las líneas de un pedido no se borran.');
END;

-- Solo se agregan líneas a un pedido pendiente (en la misma transacción del alta).
CREATE TRIGGER trg_pedido_lineas_solo_pendiente BEFORE INSERT ON pedido_lineas
WHEN NOT EXISTS (SELECT 1 FROM pedidos WHERE id = NEW.pedido_id AND estado = 'PENDIENTE')
BEGIN
    SELECT RAISE(ABORT, 'Solo un pedido pendiente admite líneas.');
END;

-- La venta que convierte un pedido tiene que existir y estar activa.
CREATE TRIGGER trg_pedidos_venta_activa BEFORE UPDATE OF venta_id ON pedidos
WHEN NEW.venta_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM ventas WHERE id = NEW.venta_id AND estado = 'ACTIVA')
BEGIN
    SELECT RAISE(ABORT, 'La venta que convierte el pedido no existe o está anulada.');
END;
