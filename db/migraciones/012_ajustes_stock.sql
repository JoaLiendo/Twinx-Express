-- Migración 012: ajustes manuales de stock (merma, rotura, vencimiento,
-- pérdida, robo, recuento). Tabla nueva (no aditiva sobre `productos`):
-- un ajuste es un evento histórico, no un atributo del producto -- guardar
-- solo el último dejaría sin registro los anteriores.
--
-- `usuario_id` es NOT NULL (a diferencia de ventas/caja): este bloque es
-- exclusivamente web, sin ninguna ruta CLI sin autenticar, mismo criterio
-- que ya usa `compras.usuario_id`.
--
-- `ON DELETE RESTRICT` en ambas FK: ni un producto ni un usuario se borran
-- físicamente nunca (solo se desactivan), así que un ajuste siempre puede
-- resolver a quién y a qué producto corresponde.

CREATE TABLE IF NOT EXISTS ajustes_stock (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id       INTEGER NOT NULL REFERENCES productos(id) ON DELETE RESTRICT,
    usuario_id        INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    motivo            TEXT NOT NULL CHECK (
                          motivo IN (
                              'MERMA',
                              'ROTURA',
                              'VENCIMIENTO',
                              'PERDIDA',
                              'ROBO',
                              'RECUENTO',
                              'OTRO'
                          )
                      ),
    delta             INTEGER NOT NULL CHECK (delta != 0),
    stock_anterior    INTEGER NOT NULL CHECK (stock_anterior >= 0),
    stock_resultante  INTEGER NOT NULL CHECK (stock_resultante >= 0),
    observaciones     TEXT NULL,
    fecha             TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_ajustes_stock_producto_id ON ajustes_stock(producto_id);
