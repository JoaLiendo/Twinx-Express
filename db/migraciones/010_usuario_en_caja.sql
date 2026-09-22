-- Migración 010: usuario que realizó cada movimiento de caja.
-- Aditiva: no modifica ni elimina ninguna tabla existente. Movimientos
-- anteriores a esta migración no tienen forma de saber quién los hizo
-- -- quedan NULL explícitamente, nunca se inventa un valor retroactivo
-- (mismo criterio que la migración 009 con ventas.usuario_id).

ALTER TABLE caja_movimientos
    ADD COLUMN usuario_id INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT;
