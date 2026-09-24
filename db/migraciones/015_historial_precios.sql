-- V1.2 (Fase 1): historial de cambios de precio de un producto.
--
-- Una fila por campo que realmente cambió (`VENTA` = precio de venta,
-- `COSTO` = precio de costo): nunca se registra un cambio cuyo valor nuevo es
-- igual al anterior. No reemplaza al precio congelado de cada venta
-- (`detalle_venta`): las ventas históricas no dependen de esta tabla.
--
-- `usuario_id` es NULL cuando el cambio no tiene un usuario autenticado
-- (ej. el CLI): nunca se inventa uno. No se rellenan cambios anteriores a esta
-- migración: no hay dato real de cuáles fueron.
--
-- `origen` distingue por qué camino cambió el precio: edición manual del
-- producto, ingreso de mercadería (costo), actualización masiva o importación.

CREATE TABLE IF NOT EXISTS historial_precios (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id             INTEGER NOT NULL REFERENCES productos(id) ON DELETE RESTRICT,
    usuario_id              INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fecha                   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    campo                   TEXT NOT NULL CHECK (campo IN ('VENTA', 'COSTO')),
    precio_anterior_centavos INTEGER NOT NULL CHECK (precio_anterior_centavos >= 0),
    precio_nuevo_centavos   INTEGER NOT NULL CHECK (precio_nuevo_centavos >= 0),
    origen                  TEXT NOT NULL CHECK (origen IN ('EDICION', 'COMPRA', 'MASIVA', 'IMPORTACION')),
    CHECK (precio_anterior_centavos != precio_nuevo_centavos)
);

CREATE INDEX IF NOT EXISTS idx_historial_precios_producto_id ON historial_precios(producto_id);
