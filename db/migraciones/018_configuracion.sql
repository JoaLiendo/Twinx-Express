-- V1.2 (Fase 5): configuración clave/valor de la instalación.
--
-- Hoy solo guarda los datos comerciales que se imprimen en el ticket (nombre,
-- dirección, teléfono y leyenda de pie). Es una tabla genérica clave/valor a
-- propósito mínimo: las claves válidas las define `domain.comercio`, no la base.
-- Sin datos sembrados: una instalación sin configurar usa los valores por defecto
-- del dominio (el ticket sigue mostrando "Kiosco" como antes).

CREATE TABLE IF NOT EXISTS configuracion (
    clave                TEXT PRIMARY KEY,
    valor                TEXT NOT NULL,
    fecha_actualizacion  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
