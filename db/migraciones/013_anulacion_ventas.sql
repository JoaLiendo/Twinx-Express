-- Migración 013: anulación de ventas.
-- Aditiva: no modifica ni elimina ninguna tabla existente. Ventas
-- anteriores a esta migración quedan con `estado = 'ACTIVA'` (el valor
-- por defecto, su estado implícito real: nunca fueron anuladas) y con
-- el resto de las columnas nuevas en NULL, nunca un valor inventado --
-- mismo criterio que las migraciones 008/009/010/011.
--
-- Sin tabla separada de anulaciones: a diferencia de `ajustes_stock`
-- (migración 012, relación 1:N -- un mismo producto puede tener muchos
-- ajustes), una venta se anula como máximo una vez, así que es
-- información 1:1 por venta -- mismo criterio que `diferencia_centavos`
-- en `caja_movimientos` (migración 011).
--
-- `anulada_por_usuario_id` es NULL-able por la misma razón que
-- `ventas.usuario_id` (migración 009): la columna no puede exigir NOT
-- NULL a nivel de esquema sin romper la compatibilidad con ventas
-- viejas, aunque en la práctica `services.servicio_ventas.anular_venta`
-- siempre la completa (es un flujo exclusivamente web, autenticado).

ALTER TABLE ventas
    ADD COLUMN estado TEXT NOT NULL DEFAULT 'ACTIVA' CHECK (estado IN ('ACTIVA', 'ANULADA'));

ALTER TABLE ventas ADD COLUMN motivo_anulacion TEXT NULL;
ALTER TABLE ventas ADD COLUMN observaciones_anulacion TEXT NULL;

ALTER TABLE ventas
    ADD COLUMN anulada_por_usuario_id INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT;

ALTER TABLE ventas ADD COLUMN fecha_anulacion TEXT NULL;
