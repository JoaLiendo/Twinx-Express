-- Migración 006: proveedores (Fase 4A).
-- Aditiva: no modifica ni elimina ninguna tabla existente. Es la base
-- sobre la que Fase 4B construirá `compras`/`detalle_compra`, con una
-- FK `proveedor_id REFERENCES proveedores(id) ON DELETE RESTRICT`
-- (ver `db.repositorios.proveedores.eliminar_proveedor`, escrito para
-- esa FK desde ahora aunque todavía no exista ninguna tabla que la use).

CREATE TABLE IF NOT EXISTS proveedores (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre            TEXT NOT NULL UNIQUE,
    contacto_nombre   TEXT NULL,
    telefono          TEXT NULL,
    email             TEXT NULL,
    direccion         TEXT NULL,
    notas             TEXT NULL,
    activo            INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
    fecha_creacion    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_proveedores_activo ON proveedores(activo);
