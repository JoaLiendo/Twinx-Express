-- V1.2 (Fase 3): registro de auditoría de operaciones administrativas.
--
-- Una fila por operación importante (alta/edición/baja de productos, cambios de
-- precio masivos, ajustes de stock, compras, anulaciones, movimientos de caja,
-- administración de usuarios, importaciones y configuración): quién, cuándo,
-- qué acción, sobre qué entidad y un resumen corto. NO guarda contraseñas,
-- hashes, cookies, claves de idempotencia ni datos sensibles: `resumen` es un
-- texto breve armado por el servicio, nunca un volcado de datos.
--
-- No reemplaza las columnas de trazabilidad específicas (`usuario_id` en
-- ventas, caja, compras, ajustes y anulaciones), que siguen siendo la fuente
-- de cada operación; esto es la vista transversal.
--
-- `usuario_id` NULL: operación sin usuario autenticado (nunca se inventa uno).

CREATE TABLE IF NOT EXISTS auditoria (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    usuario_id  INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    accion      TEXT NOT NULL,
    entidad     TEXT NOT NULL,
    entidad_id  INTEGER NULL,
    resumen     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_auditoria_fecha ON auditoria(fecha);
CREATE INDEX IF NOT EXISTS idx_auditoria_accion ON auditoria(accion);
CREATE INDEX IF NOT EXISTS idx_auditoria_usuario_id ON auditoria(usuario_id);
