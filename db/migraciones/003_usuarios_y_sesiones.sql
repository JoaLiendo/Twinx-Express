-- Migración 003: usuarios y sesiones (Fase 2B).
-- Aditiva: no modifica ninguna tabla existente ni sus datos. Prepara la
-- persistencia para autenticación; el login, el hashing/verificación de
-- contraseñas y la autorización por rol se implementan en fases
-- posteriores (ver auditoría de Fase 2, fases 2C en adelante).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS usuarios (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre_usuario   TEXT NOT NULL UNIQUE,
    nombre_completo  TEXT NOT NULL,
    password_hash    TEXT NOT NULL,
    rol              TEXT NOT NULL CHECK (rol IN ('OWNER', 'CASHIER')),
    activo           INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
    fecha_creacion   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_usuarios_activo ON usuarios(activo);

-- `token` es la clave primaria: ya es un identificador único generado
-- por la aplicación (no hace falta un id autoincremental adicional).
CREATE TABLE IF NOT EXISTS sesiones (
    token            TEXT PRIMARY KEY,
    usuario_id       INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    fecha_creacion   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    fecha_expiracion TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sesiones_usuario_id ON sesiones(usuario_id);
