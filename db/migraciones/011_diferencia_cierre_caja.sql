-- Migración 011: diferencia de caja (faltante/sobrante) en el cierre.
-- Aditiva: no modifica ni elimina ninguna tabla existente. Cierres
-- anteriores a esta migración no tienen forma de saber cuál era el
-- efectivo esperado en ese momento -- quedan NULL explícitamente, nunca
-- se inventa una diferencia retroactiva (mismo criterio que las
-- migraciones 009/010).
--
-- `monto_centavos` (ya existente) sigue siendo el efectivo contado del
-- cierre: no hace falta una columna aparte para eso. El efectivo
-- esperado tampoco se persiste por separado -- es reconstruible como
-- `monto_centavos - diferencia_centavos` para quien lo necesite, así
-- que guardarlo también sería duplicar información.

ALTER TABLE caja_movimientos
    ADD COLUMN diferencia_centavos INTEGER NULL;
