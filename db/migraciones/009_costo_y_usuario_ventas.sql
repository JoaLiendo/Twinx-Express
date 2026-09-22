-- Migración 009: histórico de costo y usuario en ventas.
-- Aditiva: no modifica ni elimina ninguna tabla existente. Ventas
-- anteriores a esta migración no tienen forma de saber quién las cobró
-- ni cuál era el costo del producto en ese momento -- quedan NULL
-- explícitamente, nunca se inventa un valor retroactivo (mismo criterio
-- que la migración 008 con clave_idempotencia/contenido_hash).

ALTER TABLE ventas
    ADD COLUMN usuario_id INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT;

ALTER TABLE detalle_venta
    ADD COLUMN costo_unitario_centavos INTEGER NULL
        CHECK (costo_unitario_centavos IS NULL OR costo_unitario_centavos >= 0);
