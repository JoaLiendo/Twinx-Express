-- Migración 007: compras / ingreso de mercadería (Fase 4B).
-- Aditiva: no modifica ni elimina ninguna tabla existente.
--
-- `proveedor_id` y `producto_id` usan ON DELETE RESTRICT: una compra ya
-- registrada nunca puede quedar huérfana de su proveedor o de alguno de
-- sus productos (ver auditoría de Fase 4A: esta es la FK que activa la
-- baja lógica de `db.repositorios.proveedores.eliminar_proveedor`, hasta
-- ahora sin ejercitar). `compra_id` usa ON DELETE CASCADE porque el
-- detalle no tiene sentido sin su cabecera -- aunque en esta fase las
-- compras son inmutables y nunca se borran (ver services.servicio_compras).
--
-- Los montos se guardan en CENTAVOS (INTEGER), igual que el resto del
-- proyecto (ver domain/dinero.py).

CREATE TABLE IF NOT EXISTS compras (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    proveedor_id      INTEGER NOT NULL REFERENCES proveedores(id) ON DELETE RESTRICT,
    usuario_id        INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fecha             TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    observaciones     TEXT NULL,
    total_centavos    INTEGER NOT NULL CHECK (total_centavos >= 0)
);

CREATE TABLE IF NOT EXISTS detalle_compra (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    compra_id                INTEGER NOT NULL REFERENCES compras(id) ON DELETE CASCADE,
    producto_id              INTEGER NOT NULL REFERENCES productos(id) ON DELETE RESTRICT,
    cantidad                 INTEGER NOT NULL CHECK (cantidad > 0),
    costo_unitario_centavos  INTEGER NOT NULL CHECK (costo_unitario_centavos >= 0),
    subtotal_centavos        INTEGER NOT NULL CHECK (subtotal_centavos >= 0)
);

CREATE INDEX IF NOT EXISTS idx_compras_proveedor_id ON compras(proveedor_id);
CREATE INDEX IF NOT EXISTS idx_detalle_compra_compra_id ON detalle_compra(compra_id);
CREATE INDEX IF NOT EXISTS idx_detalle_compra_producto_id ON detalle_compra(producto_id);
