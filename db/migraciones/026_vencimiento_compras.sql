-- Migración 026: fecha de vencimiento de las compras (V1.10-A).
--
-- Agrega `compras.fecha_vencimiento TEXT NULL`, un dato operativo informativo: no modifica ni el
-- total de la compra, ni su cargo en `movimientos_proveedor`, ni ningún saldo. Reglas (en el
-- esquema, como última defensa; las reglas comerciales de alta y edición llegan en V1.10-C):
--   * CONTADO => siempre NULL (una compra al contado no genera deuda que pueda vencer).
--   * CREDITO => NULL (todavía sin vencimiento informado) o una fecha calendario real
--     `AAAA-MM-DD` que no sea anterior al día de la compra. No hay tope superior: no se inventan
--     reglas comerciales (plazos máximos) que el negocio no definió.
--
-- CERO INFERENCIAS: toda compra existente queda con `fecha_vencimiento = NULL`, incluso las
-- CREDITO. No hay evidencia en el esquema anterior de cuál era su plazo; no se estima ninguno
-- (ni 7, 15 o 30 días, ni la fecha de un pago, ni nada derivado del proveedor).
--
-- NO REQUIERE RECONSTRUIR `compras`: SQLite admite `ALTER TABLE ... ADD COLUMN` con un CHECK que
-- referencia otras columnas de la misma tabla (comprobado en la versión del entorno) y valida las
-- filas previas, que al ser todas NULL cumplen. Reconstruir la tabla (con sus hijos
-- `detalle_compra` y `movimientos_proveedor`, ambos con FK) sería un riesgo sin ningún beneficio.
--
-- DETALLE DEL CHECK: en SQL una condición que da NULL NO viola un CHECK. `date()` devuelve NULL
-- para un texto que no es fecha (ej. '2099-13-01'), así que `date(x) = x` daría NULL y dejaría
-- pasar el valor inválido. Por eso se usa `IS` (que nunca devuelve NULL) en las comparaciones que
-- pueden involucrar un NULL. `date()` además normaliza los días fuera de mes (ej. '2099-02-30'
-- pasa a '2099-03-02'), que `date(x) IS x` rechaza por ser distinto del original.
--
-- Sin índice sobre `fecha_vencimiento`: ninguna consulta SQL de V1.10 filtra por esa columna (la
-- clasificación por vencimiento se calcula en dominio, ver auditoría de diseño de V1.10).
--
-- Mismo patrón de verificación exhaustiva que las migraciones 020/024/025: guardias previos,
-- snapshots, cambio y comparación fila por fila, con cualquier fila insertada en `_fallas`
-- abortando toda la migración (el runner la corre dentro de un único `BEGIN IMMEDIATE`).

DROP TABLE IF EXISTS temp._fallas;
DROP TABLE IF EXISTS temp._compras_copia;
DROP TABLE IF EXISTS temp._proveedor_copia;
DROP TABLE IF EXISTS temp._caja_copia;
DROP TABLE IF EXISTS temp._sesiones_copia;
DROP TABLE IF EXISTS temp._cuenta_copia;
DROP TABLE IF EXISTS temp._saldos_antes;
DROP TABLE IF EXISTS temp._esquema_antes;
DROP TABLE IF EXISTS temp._antes;

CREATE TEMP TABLE _fallas (motivo TEXT NOT NULL);
CREATE TEMP TRIGGER _abortar_por_falla AFTER INSERT ON _fallas
BEGIN
    SELECT RAISE(ABORT, 'Migración 026: verificación fallida: ' || NEW.motivo);
END;

-- 0. Guardias previos ----------------------------------------------------------------

INSERT INTO _fallas SELECT 'la tabla compras no tiene las columnas esperadas'
WHERE (SELECT group_concat(name, ',') FROM (SELECT name FROM pragma_table_info('compras') ORDER BY cid))
      IS NOT 'id,proveedor_id,usuario_id,fecha,observaciones,total_centavos,clave_idempotencia,estado,'
             || 'motivo_anulacion,observaciones_anulacion,anulada_por_usuario_id,fecha_anulacion,'
             || 'costo_trazable,condicion_pago';
INSERT INTO _fallas SELECT 'compras ya tiene la columna fecha_vencimiento'
WHERE EXISTS (SELECT 1 FROM pragma_table_info('compras') WHERE name = 'fecha_vencimiento');
INSERT INTO _fallas SELECT 'foreign_key_check previo'
WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;
INSERT INTO _fallas SELECT 'integrity_check previo'
WHERE (SELECT integrity_check FROM pragma_integrity_check) <> 'ok';
INSERT INTO _fallas SELECT 'las claves foráneas no están activas'
WHERE (SELECT foreign_keys FROM pragma_foreign_keys) <> 1;

-- 1. Snapshots ------------------------------------------------------------------------
-- Las tablas que esta migración NO debe tocar (libro de proveedor, caja, cuenta corriente de
-- clientes) se copian enteras para compararlas fila por fila al final.

CREATE TEMP TABLE _antes AS
SELECT
    (SELECT COUNT(*) FROM compras)                                        AS compras_n,
    (SELECT seq FROM sqlite_sequence WHERE name = 'compras')              AS seq_compras,
    (SELECT COUNT(*) FROM movimientos_proveedor)                          AS proveedor_n,
    (SELECT seq FROM sqlite_sequence WHERE name = 'movimientos_proveedor') AS seq_proveedor;

CREATE TEMP TABLE _compras_copia AS
SELECT id, proveedor_id, usuario_id, fecha, observaciones, total_centavos, clave_idempotencia, estado,
       motivo_anulacion, observaciones_anulacion, anulada_por_usuario_id, fecha_anulacion, costo_trazable,
       condicion_pago
FROM compras;
CREATE TEMP TABLE _proveedor_copia AS SELECT * FROM movimientos_proveedor;
CREATE TEMP TABLE _caja_copia AS SELECT * FROM caja_movimientos;
CREATE TEMP TABLE _sesiones_copia AS SELECT * FROM sesiones_caja;
CREATE TEMP TABLE _cuenta_copia AS SELECT * FROM movimientos_cuenta;
CREATE TEMP TABLE _saldos_antes AS
SELECT proveedor_id,
       SUM(CASE tipo WHEN 'CARGO_COMPRA' THEN monto_centavos ELSE -monto_centavos END) AS saldo
FROM movimientos_proveedor GROUP BY proveedor_id;
-- El resto del esquema (todo objeto que no sea la propia `compras`) debe quedar idéntico.
CREATE TEMP TABLE _esquema_antes AS
SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name <> 'compras';

-- 2. compras.fecha_vencimiento ---------------------------------------------------------
-- Aditiva: sin ningún UPDATE explícito, las filas existentes quedan NULL.

ALTER TABLE compras ADD COLUMN fecha_vencimiento TEXT NULL CHECK (
    fecha_vencimiento IS NULL
    OR (
        typeof(fecha_vencimiento) = 'text'
        AND condicion_pago = 'CREDITO'
        AND fecha_vencimiento GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
        AND date(fecha_vencimiento) IS fecha_vencimiento
        AND (fecha_vencimiento >= date(fecha)) IS 1
    )
);

-- 3. Verificación -----------------------------------------------------------------------

INSERT INTO _fallas SELECT 'la columna fecha_vencimiento no quedó como TEXT NULL sin default'
WHERE NOT EXISTS (
    SELECT 1 FROM pragma_table_info('compras')
    WHERE name = 'fecha_vencimiento' AND type = 'TEXT' AND "notnull" = 0 AND dflt_value IS NULL AND pk = 0
);
INSERT INTO _fallas SELECT 'alguna compra existente quedó con fecha_vencimiento (cero inferencias)'
WHERE EXISTS (SELECT 1 FROM compras WHERE fecha_vencimiento IS NOT NULL);

INSERT INTO _fallas SELECT 'compras: cantidad de filas'
WHERE (SELECT COUNT(*) FROM compras) <> (SELECT compras_n FROM _antes);
INSERT INTO _fallas SELECT 'compras: una fila difiere de la original (o falta)'
WHERE EXISTS (
    SELECT * FROM _compras_copia
    EXCEPT
    SELECT id, proveedor_id, usuario_id, fecha, observaciones, total_centavos, clave_idempotencia, estado,
           motivo_anulacion, observaciones_anulacion, anulada_por_usuario_id, fecha_anulacion, costo_trazable,
           condicion_pago
    FROM compras
);
INSERT INTO _fallas SELECT 'compras: hay una fila que no estaba en la original'
WHERE EXISTS (
    SELECT id, proveedor_id, usuario_id, fecha, observaciones, total_centavos, clave_idempotencia, estado,
           motivo_anulacion, observaciones_anulacion, anulada_por_usuario_id, fecha_anulacion, costo_trazable,
           condicion_pago
    FROM compras
    EXCEPT
    SELECT * FROM _compras_copia
);
INSERT INTO _fallas SELECT 'sqlite_sequence de compras'
WHERE (SELECT seq FROM sqlite_sequence WHERE name = 'compras') IS NOT (SELECT seq_compras FROM _antes);

INSERT INTO _fallas SELECT 'movimientos_proveedor: cantidad de filas'
WHERE (SELECT COUNT(*) FROM movimientos_proveedor) <> (SELECT proveedor_n FROM _antes);
INSERT INTO _fallas SELECT 'movimientos_proveedor: una fila difiere de la original (o falta)'
WHERE EXISTS (SELECT * FROM _proveedor_copia EXCEPT SELECT * FROM movimientos_proveedor);
INSERT INTO _fallas SELECT 'movimientos_proveedor: hay una fila que no estaba en la original'
WHERE EXISTS (SELECT * FROM movimientos_proveedor EXCEPT SELECT * FROM _proveedor_copia);
INSERT INTO _fallas SELECT 'sqlite_sequence de movimientos_proveedor'
WHERE (SELECT seq FROM sqlite_sequence WHERE name = 'movimientos_proveedor') IS NOT (SELECT seq_proveedor FROM _antes);
INSERT INTO _fallas SELECT 'el saldo de algún proveedor cambió'
WHERE EXISTS (
    SELECT proveedor_id,
           SUM(CASE tipo WHEN 'CARGO_COMPRA' THEN monto_centavos ELSE -monto_centavos END)
    FROM movimientos_proveedor GROUP BY proveedor_id
    EXCEPT
    SELECT proveedor_id, saldo FROM _saldos_antes
)
OR EXISTS (
    SELECT proveedor_id, saldo FROM _saldos_antes
    EXCEPT
    SELECT proveedor_id,
           SUM(CASE tipo WHEN 'CARGO_COMPRA' THEN monto_centavos ELSE -monto_centavos END)
    FROM movimientos_proveedor GROUP BY proveedor_id
);

INSERT INTO _fallas SELECT 'caja_movimientos cambió'
WHERE EXISTS (SELECT * FROM _caja_copia EXCEPT SELECT * FROM caja_movimientos)
   OR EXISTS (SELECT * FROM caja_movimientos EXCEPT SELECT * FROM _caja_copia);
INSERT INTO _fallas SELECT 'sesiones_caja cambió'
WHERE EXISTS (SELECT * FROM _sesiones_copia EXCEPT SELECT * FROM sesiones_caja)
   OR EXISTS (SELECT * FROM sesiones_caja EXCEPT SELECT * FROM _sesiones_copia);
INSERT INTO _fallas SELECT 'movimientos_cuenta cambió'
WHERE EXISTS (SELECT * FROM _cuenta_copia EXCEPT SELECT * FROM movimientos_cuenta)
   OR EXISTS (SELECT * FROM movimientos_cuenta EXCEPT SELECT * FROM _cuenta_copia);

INSERT INTO _fallas SELECT 'el esquema fuera de compras cambió (tablas, índices o triggers)'
WHERE EXISTS (SELECT * FROM _esquema_antes EXCEPT SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name <> 'compras')
   OR EXISTS (SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name <> 'compras' EXCEPT SELECT * FROM _esquema_antes);

INSERT INTO _fallas SELECT 'foreign_key_check final' WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;
INSERT INTO _fallas SELECT 'integrity_check final'
WHERE (SELECT integrity_check FROM pragma_integrity_check) <> 'ok';

DROP TRIGGER temp._abortar_por_falla;
DROP TABLE temp._fallas;
DROP TABLE temp._esquema_antes;
DROP TABLE temp._saldos_antes;
DROP TABLE temp._cuenta_copia;
DROP TABLE temp._sesiones_copia;
DROP TABLE temp._caja_copia;
DROP TABLE temp._proveedor_copia;
DROP TABLE temp._compras_copia;
DROP TABLE temp._antes;
