-- Migración inicial: esquema base de la aplicación.
-- Se ejecuta automáticamente mediante db.conexion.inicializar_base_datos().
-- Es idempotente (CREATE ... IF NOT EXISTS): puede correrse en cada arranque.
--
-- Todas las columnas de dinero se guardan en CENTAVOS (INTEGER), nunca como
-- REAL/float: evita errores de precisión binaria en cálculos financieros.
-- Ver domain/dinero.py para la conversión a/desde texto decimal ("150.50").

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS productos (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo_barras        TEXT NOT NULL UNIQUE,
    nombre               TEXT NOT NULL,
    precio_costo_centavos  INTEGER NOT NULL CHECK (precio_costo_centavos >= 0),
    precio_venta_centavos  INTEGER NOT NULL CHECK (precio_venta_centavos >= 0),
    stock_actual         INTEGER NOT NULL DEFAULT 0 CHECK (stock_actual >= 0),
    stock_minimo         INTEGER NOT NULL DEFAULT 0 CHECK (stock_minimo >= 0),
    fecha_actualizacion  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS ventas (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha          TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    total_centavos INTEGER NOT NULL CHECK (total_centavos >= 0),
    tipo_pago      TEXT NOT NULL CHECK (tipo_pago IN ('EFECTIVO', 'TARJETA', 'TRANSFERENCIA', 'OTRO'))
);

CREATE TABLE IF NOT EXISTS detalle_venta (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    venta_id                 INTEGER NOT NULL REFERENCES ventas(id) ON DELETE CASCADE,
    producto_id              INTEGER NOT NULL REFERENCES productos(id) ON DELETE RESTRICT,
    cantidad                 INTEGER NOT NULL CHECK (cantidad > 0),
    precio_unitario_centavos INTEGER NOT NULL CHECK (precio_unitario_centavos >= 0),
    subtotal_centavos        INTEGER NOT NULL CHECK (subtotal_centavos >= 0)
);

CREATE TABLE IF NOT EXISTS caja_movimientos (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha          TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    tipo           TEXT NOT NULL CHECK (tipo IN ('APERTURA', 'CIERRE', 'INGRESO', 'EGRESO')),
    monto_centavos INTEGER NOT NULL CHECK (monto_centavos >= 0),
    descripcion    TEXT
);

-- Índices de apoyo para las búsquedas más frecuentes (lector de código de
-- barras y reportes de ventas por producto).
CREATE INDEX IF NOT EXISTS idx_productos_codigo_barras ON productos(codigo_barras);
CREATE INDEX IF NOT EXISTS idx_detalle_venta_venta_id ON detalle_venta(venta_id);
CREATE INDEX IF NOT EXISTS idx_detalle_venta_producto_id ON detalle_venta(producto_id);
