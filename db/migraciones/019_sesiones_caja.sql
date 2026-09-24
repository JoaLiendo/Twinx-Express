-- Migración 019: sesiones de caja explícitas (V1.3).
--
-- Hasta la V1.2 la "sesión" de caja se deducía de `caja_movimientos` (último
-- APERTURA -> CIERRE) y las ventas se ligaban por fecha. Desde ahora la
-- sesión es una fila de `sesiones_caja` y `ventas`/`caja_movimientos` la
-- referencian con `sesion_caja_id`.
--
-- ADITIVA respecto de las tablas comerciales: solo `ALTER TABLE ... ADD
-- COLUMN` y `UPDATE` de la columna nueva. Nunca reconstruye, copia, borra ni
-- inserta filas en `ventas`, `detalle_venta` ni `caja_movimientos` (por eso no
-- hay riesgo de que el `ON DELETE CASCADE` de `detalle_venta` borre nada, y no
-- hace falta tocar `PRAGMA foreign_keys`, que no puede cambiarse dentro de la
-- transacción). El runner la ejecuta dentro de un único `BEGIN IMMEDIATE`: si
-- cualquier verificación falla se revierte todo y la migración no se registra.
--
-- ORDEN (importa):
--   1. tablas, columnas e índices
--   2. invariantes previas
--   3. cálculo de la reconstrucción (solo tablas temporales)
--   4. sesiones históricas en su estado final + sesión LEGADO si hay huérfanos
--   5. backfill de movimientos y de ventas
--   6. verificaciones
--   7. triggers (DESPUÉS del backfill: durante el backfill no existe ninguno)
--   8. verificación final
--
-- RECONSTRUCCIÓN HISTÓRICA
--   * Cada movimiento APERTURA genera una sesión origen RECONSTRUIDA, estado
--     CERRADA. Nunca se genera una sesión ABIERTA: una migración de datos
--     históricos no deja una caja actual abierta; la V1.3 arranca con una
--     apertura explícita del usuario.
--   * El fin de la sesión es el primer movimiento posterior (por `id`, el orden
--     real de los eventos) que sea CIERRE o APERTURA. Con CIERRE se conservan
--     fecha, contado y diferencia (NULL si el cierre es anterior a la 011).
--     Con otra APERTURA, o sin fin (última apertura), el cierre no fue
--     registrado: fecha_cierre, contado y diferencia quedan NULL.
--   * Movimientos: por orden de `id`. Los que caen fuera de toda sesión (antes
--     de la primera apertura, entre un cierre y la siguiente apertura, un
--     CIERRE sin apertura) van a la sesión LEGADO.
--   * Ventas: solo se dispone del timestamp de la venta ORIGINAL (nunca el de
--     anulación). Para una venta en T:
--       1) sesión con apertura < T <= límite (límite = fecha del fin de la
--          sesión; sin límite si no tiene fin); si hay varias, la de mayor id;
--       2) si no hay ninguna, sesión con apertura = T y T <= límite (mayor id);
--       3) si no hay ninguna (o la fecha no es válida), sesión LEGADO.
--     Caso de frontera (CIERRE de A, APERTURA de B y venta en el mismo segundo):
--     la regla 1 la asigna a A. Limitación conocida: la asignación de ventas
--     depende solo del timestamp; si el reloj retrocedió puede diferir de la
--     real, siempre de forma determinista y sin perder ninguna venta.
--   * Sesión LEGADO: una sola, solo si quedan ventas o movimientos huérfanos,
--     siempre CERRADA y con fondo/contado/diferencia/usuarios/fecha_cierre en
--     NULL. Es un contenedor de datos históricos sin sesión reconstruible, no
--     una sesión real: nunca admite operaciones nuevas.

-- 1. Tablas, columnas e índices ------------------------------------------------

CREATE TABLE sesiones_caja (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    estado              TEXT NOT NULL CHECK (estado IN ('ABIERTA', 'CERRADA')),
    origen              TEXT NOT NULL CHECK (origen IN ('NORMAL', 'RECONSTRUIDA', 'LEGADO')),
    fecha_apertura      TEXT NOT NULL,
    fecha_cierre        TEXT NULL,
    usuario_apertura_id INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    usuario_cierre_id   INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fondo_centavos      INTEGER NULL CHECK (fondo_centavos IS NULL OR fondo_centavos >= 0),
    contado_centavos    INTEGER NULL CHECK (contado_centavos IS NULL OR contado_centavos >= 0),
    diferencia_centavos INTEGER NULL,
    -- Solo LEGADO carece de fondo.
    CHECK ((origen = 'LEGADO') = (fondo_centavos IS NULL)),
    -- Solo una sesión NORMAL puede estar ABIERTA, y sin datos de cierre.
    CHECK (
        estado = 'CERRADA'
        OR (origen = 'NORMAL' AND fecha_cierre IS NULL AND contado_centavos IS NULL
            AND diferencia_centavos IS NULL AND usuario_cierre_id IS NULL)
    ),
    -- LEGADO: contenedor cerrado, sin datos de caja.
    CHECK (
        origen <> 'LEGADO'
        OR (estado = 'CERRADA' AND fecha_cierre IS NULL AND contado_centavos IS NULL
            AND diferencia_centavos IS NULL AND usuario_apertura_id IS NULL AND usuario_cierre_id IS NULL)
    ),
    -- Una sesión NORMAL cerrada siempre tiene fecha de cierre y monto contado.
    CHECK (
        NOT (origen = 'NORMAL' AND estado = 'CERRADA')
        OR (fecha_cierre IS NOT NULL AND contado_centavos IS NOT NULL)
    )
);

-- Como máximo una sesión abierta, y como máximo una sesión LEGADO.
CREATE UNIQUE INDEX idx_sesiones_caja_una_abierta ON sesiones_caja(estado) WHERE estado = 'ABIERTA';
CREATE UNIQUE INDEX idx_sesiones_caja_un_legado ON sesiones_caja(origen) WHERE origen = 'LEGADO';

ALTER TABLE caja_movimientos
    ADD COLUMN sesion_caja_id INTEGER NULL REFERENCES sesiones_caja(id) ON DELETE RESTRICT;
ALTER TABLE ventas
    ADD COLUMN sesion_caja_id INTEGER NULL REFERENCES sesiones_caja(id) ON DELETE RESTRICT;

CREATE INDEX idx_caja_movimientos_sesion ON caja_movimientos(sesion_caja_id);
CREATE INDEX idx_ventas_sesion ON ventas(sesion_caja_id);
-- Una única APERTURA por sesión.
CREATE UNIQUE INDEX idx_caja_movimientos_una_apertura_por_sesion
    ON caja_movimientos(sesion_caja_id) WHERE tipo = 'APERTURA';

-- 2. Invariantes previas -------------------------------------------------------

DROP TABLE IF EXISTS temp._antes;
DROP TABLE IF EXISTS temp._fallas;
DROP TABLE IF EXISTS temp._sesion_tmp;
DROP TABLE IF EXISTS temp._mov_sesion;
DROP TABLE IF EXISTS temp._venta_sesion;

CREATE TEMP TABLE _antes AS
SELECT
    (SELECT COUNT(*) FROM ventas)                              AS ventas_n,
    (SELECT COUNT(*) FROM detalle_venta)                       AS detalle_n,
    (SELECT COUNT(*) FROM caja_movimientos)                    AS mov_n,
    (SELECT COALESCE(SUM(total_centavos), 0) FROM ventas)      AS ventas_total,
    (SELECT COALESCE(SUM(monto_centavos), 0) FROM caja_movimientos) AS mov_monto,
    (SELECT COUNT(*) FROM caja_movimientos WHERE tipo = 'APERTURA') AS aperturas_n,
    (SELECT seq FROM sqlite_sequence WHERE name = 'ventas')            AS seq_ventas,
    (SELECT seq FROM sqlite_sequence WHERE name = 'detalle_venta')     AS seq_detalle,
    (SELECT seq FROM sqlite_sequence WHERE name = 'caja_movimientos')  AS seq_mov;

-- Una fila insertada acá aborta la migración con el motivo (ver más abajo).
CREATE TEMP TABLE _fallas (motivo TEXT NOT NULL);
CREATE TEMP TRIGGER _abortar_por_falla AFTER INSERT ON _fallas
BEGIN
    SELECT RAISE(ABORT, 'Migración 019: verificación fallida: ' || NEW.motivo);
END;

-- 3. Reconstrucción (solo tablas temporales) -----------------------------------

CREATE TEMP TABLE _sesion_tmp AS
SELECT
    ROW_NUMBER() OVER (ORDER BY a.id) AS sesion_id,
    a.id                              AS apertura_id,
    a.fecha                           AS fecha_apertura,
    a.monto_centavos                  AS fondo,
    a.usuario_id                      AS usuario_apertura_id,
    f.id                              AS fin_id,
    f.tipo                            AS fin_tipo,
    f.fecha                           AS fin_fecha,
    f.monto_centavos                  AS fin_monto,
    f.diferencia_centavos             AS fin_diferencia,
    f.usuario_id                      AS fin_usuario
FROM caja_movimientos a
LEFT JOIN caja_movimientos f
       ON f.id = (SELECT MIN(m.id) FROM caja_movimientos m
                  WHERE m.id > a.id AND m.tipo IN ('CIERRE', 'APERTURA'))
WHERE a.tipo = 'APERTURA';

-- Movimientos: por orden de id. Sin sesión (NULL) => LEGADO.
CREATE TEMP TABLE _mov_sesion AS
SELECT
    m.id AS mov_id,
    CASE
        WHEN m.tipo = 'APERTURA' THEN
            (SELECT s.sesion_id FROM _sesion_tmp s WHERE s.apertura_id = m.id)
        ELSE
            (SELECT s.sesion_id FROM _sesion_tmp s
             WHERE s.apertura_id < m.id
               AND (s.fin_id IS NULL
                    OR m.id < s.fin_id
                    OR (m.id = s.fin_id AND m.tipo = 'CIERRE'))
             ORDER BY s.apertura_id DESC LIMIT 1)
    END AS sesion_id
FROM caja_movimientos m;

-- Ventas: por timestamp de la venta original. Sin sesión (NULL) => LEGADO.
CREATE TEMP TABLE _venta_sesion AS
SELECT
    v.id AS venta_id,
    CASE WHEN datetime(v.fecha) IS NULL THEN NULL ELSE COALESCE(
        (SELECT s.sesion_id FROM _sesion_tmp s
         WHERE datetime(s.fecha_apertura) < datetime(v.fecha)
           AND (s.fin_fecha IS NULL OR datetime(v.fecha) <= datetime(s.fin_fecha))
         ORDER BY s.apertura_id DESC LIMIT 1),
        (SELECT s.sesion_id FROM _sesion_tmp s
         WHERE datetime(s.fecha_apertura) = datetime(v.fecha)
           AND (s.fin_fecha IS NULL OR datetime(v.fecha) <= datetime(s.fin_fecha))
         ORDER BY s.apertura_id DESC LIMIT 1)
    ) END AS sesion_id
FROM ventas v;

-- 4. Sesiones históricas en su estado final ------------------------------------

INSERT INTO sesiones_caja
    (id, estado, origen, fecha_apertura, fecha_cierre, usuario_apertura_id,
     usuario_cierre_id, fondo_centavos, contado_centavos, diferencia_centavos)
SELECT
    sesion_id,
    'CERRADA',
    'RECONSTRUIDA',
    fecha_apertura,
    CASE WHEN fin_tipo = 'CIERRE' THEN fin_fecha END,
    usuario_apertura_id,
    CASE WHEN fin_tipo = 'CIERRE' THEN fin_usuario END,
    fondo,
    CASE WHEN fin_tipo = 'CIERRE' THEN fin_monto END,
    CASE WHEN fin_tipo = 'CIERRE' THEN fin_diferencia END
FROM _sesion_tmp
ORDER BY sesion_id;

-- Sesión LEGADO: solo si hay movimientos o ventas sin sesión reconstruible.
INSERT INTO sesiones_caja (estado, origen, fecha_apertura)
SELECT 'CERRADA', 'LEGADO', COALESCE(MIN(datetime(fecha)), MIN(fecha))
FROM (
    SELECT m.fecha AS fecha FROM caja_movimientos m
      JOIN _mov_sesion ms ON ms.mov_id = m.id WHERE ms.sesion_id IS NULL
    UNION ALL
    SELECT v.fecha AS fecha FROM ventas v
      JOIN _venta_sesion vs ON vs.venta_id = v.id WHERE vs.sesion_id IS NULL
)
HAVING COUNT(*) > 0;

-- 5. Backfill ------------------------------------------------------------------
-- Aún no existe ningún trigger sobre `sesion_caja_id`: esta es la única vez que
-- la columna pasa de NULL a una sesión histórica (CERRADA, RECONSTRUIDA o LEGADO).

UPDATE caja_movimientos
SET sesion_caja_id = COALESCE(
    (SELECT ms.sesion_id FROM _mov_sesion ms WHERE ms.mov_id = caja_movimientos.id),
    (SELECT id FROM sesiones_caja WHERE origen = 'LEGADO')
);

UPDATE ventas
SET sesion_caja_id = COALESCE(
    (SELECT vs.sesion_id FROM _venta_sesion vs WHERE vs.venta_id = ventas.id),
    (SELECT id FROM sesiones_caja WHERE origen = 'LEGADO')
);

-- 6. Verificaciones ------------------------------------------------------------

INSERT INTO _fallas SELECT 'cantidad de ventas'
WHERE (SELECT COUNT(*) FROM ventas) <> (SELECT ventas_n FROM _antes);
INSERT INTO _fallas SELECT 'cantidad de detalle_venta'
WHERE (SELECT COUNT(*) FROM detalle_venta) <> (SELECT detalle_n FROM _antes);
INSERT INTO _fallas SELECT 'cantidad de caja_movimientos'
WHERE (SELECT COUNT(*) FROM caja_movimientos) <> (SELECT mov_n FROM _antes);
INSERT INTO _fallas SELECT 'suma de totales de ventas'
WHERE (SELECT COALESCE(SUM(total_centavos), 0) FROM ventas) <> (SELECT ventas_total FROM _antes);
INSERT INTO _fallas SELECT 'suma de montos de caja_movimientos'
WHERE (SELECT COALESCE(SUM(monto_centavos), 0) FROM caja_movimientos) <> (SELECT mov_monto FROM _antes);
INSERT INTO _fallas SELECT 'sqlite_sequence de ventas'
WHERE (SELECT seq FROM sqlite_sequence WHERE name = 'ventas') IS NOT (SELECT seq_ventas FROM _antes);
INSERT INTO _fallas SELECT 'sqlite_sequence de detalle_venta'
WHERE (SELECT seq FROM sqlite_sequence WHERE name = 'detalle_venta') IS NOT (SELECT seq_detalle FROM _antes);
INSERT INTO _fallas SELECT 'sqlite_sequence de caja_movimientos'
WHERE (SELECT seq FROM sqlite_sequence WHERE name = 'caja_movimientos') IS NOT (SELECT seq_mov FROM _antes);
INSERT INTO _fallas SELECT 'venta sin sesión'
WHERE EXISTS (SELECT 1 FROM ventas WHERE sesion_caja_id IS NULL);
INSERT INTO _fallas SELECT 'movimiento sin sesión'
WHERE EXISTS (SELECT 1 FROM caja_movimientos WHERE sesion_caja_id IS NULL);
INSERT INTO _fallas SELECT 'detalle_venta huérfano'
WHERE EXISTS (SELECT 1 FROM detalle_venta d WHERE NOT EXISTS (SELECT 1 FROM ventas v WHERE v.id = d.venta_id));
INSERT INTO _fallas SELECT 'la migración dejó una sesión ABIERTA'
WHERE EXISTS (SELECT 1 FROM sesiones_caja WHERE estado = 'ABIERTA');
INSERT INTO _fallas SELECT 'cada APERTURA debe estar en su propia sesión'
WHERE (SELECT COUNT(DISTINCT sesion_caja_id) FROM caja_movimientos WHERE tipo = 'APERTURA')
      <> (SELECT aperturas_n FROM _antes);
INSERT INTO _fallas SELECT 'una sesión reconstruida sin exactamente una APERTURA'
WHERE EXISTS (
    SELECT 1 FROM sesiones_caja s
    WHERE s.origen = 'RECONSTRUIDA'
      AND (SELECT COUNT(*) FROM caja_movimientos m WHERE m.sesion_caja_id = s.id AND m.tipo = 'APERTURA') <> 1
);
INSERT INTO _fallas SELECT 'APERTURA en la sesión LEGADO'
WHERE EXISTS (
    SELECT 1 FROM caja_movimientos m JOIN sesiones_caja s ON s.id = m.sesion_caja_id
    WHERE s.origen = 'LEGADO' AND m.tipo = 'APERTURA'
);
INSERT INTO _fallas SELECT 'sesión LEGADO sin contenido'
WHERE EXISTS (
    SELECT 1 FROM sesiones_caja s
    WHERE s.origen = 'LEGADO'
      AND NOT EXISTS (SELECT 1 FROM ventas v WHERE v.sesion_caja_id = s.id)
      AND NOT EXISTS (SELECT 1 FROM caja_movimientos m WHERE m.sesion_caja_id = s.id)
);
INSERT INTO _fallas SELECT 'foreign_key_check'
WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;
INSERT INTO _fallas SELECT 'integrity_check'
WHERE (SELECT integrity_check FROM pragma_integrity_check) <> 'ok';

-- 7. Triggers (después del backfill) -------------------------------------------

-- Operación normal: solo se opera sobre una sesión ABIERTA (que solo puede ser
-- NORMAL). Una sesión RECONSTRUIDA (cerrada) o LEGADO nunca es operable.
CREATE TRIGGER trg_ventas_sesion_operable BEFORE INSERT ON ventas
WHEN NEW.sesion_caja_id IS NULL
     OR NOT EXISTS (SELECT 1 FROM sesiones_caja WHERE id = NEW.sesion_caja_id AND estado = 'ABIERTA')
BEGIN
    SELECT RAISE(ABORT, 'La venta requiere una sesión de caja ABIERTA.');
END;

CREATE TRIGGER trg_caja_movimientos_sesion_operable BEFORE INSERT ON caja_movimientos
WHEN NEW.sesion_caja_id IS NULL
     OR NOT EXISTS (SELECT 1 FROM sesiones_caja WHERE id = NEW.sesion_caja_id AND estado = 'ABIERTA')
BEGIN
    SELECT RAISE(ABORT, 'El movimiento de caja requiere una sesión de caja ABIERTA.');
END;

-- Una vez asignada, la sesión de una venta o de un movimiento es inmutable.
CREATE TRIGGER trg_ventas_sesion_inmutable BEFORE UPDATE OF sesion_caja_id ON ventas
WHEN OLD.sesion_caja_id IS NOT NEW.sesion_caja_id
BEGIN
    SELECT RAISE(ABORT, 'La sesión de caja de una venta no puede modificarse.');
END;

CREATE TRIGGER trg_caja_movimientos_sesion_inmutable BEFORE UPDATE OF sesion_caja_id ON caja_movimientos
WHEN OLD.sesion_caja_id IS NOT NEW.sesion_caja_id
BEGIN
    SELECT RAISE(ABORT, 'La sesión de caja de un movimiento no puede modificarse.');
END;

-- Solo esta migración crea sesiones RECONSTRUIDA o LEGADO.
CREATE TRIGGER trg_sesiones_caja_solo_normal BEFORE INSERT ON sesiones_caja
WHEN NEW.origen <> 'NORMAL'
BEGIN
    SELECT RAISE(ABORT, 'Solo se pueden crear sesiones de caja NORMAL.');
END;

-- Una sesión cerrada no se reabre ni se modifica; el origen y los datos de
-- apertura nunca cambian.
CREATE TRIGGER trg_sesiones_caja_cerrada_inmutable BEFORE UPDATE ON sesiones_caja
WHEN OLD.estado = 'CERRADA'
BEGIN
    SELECT RAISE(ABORT, 'Una sesión de caja cerrada no puede modificarse.');
END;

CREATE TRIGGER trg_sesiones_caja_datos_de_apertura_inmutables BEFORE UPDATE ON sesiones_caja
WHEN NEW.origen IS NOT OLD.origen
     OR NEW.fecha_apertura IS NOT OLD.fecha_apertura
     OR NEW.fondo_centavos IS NOT OLD.fondo_centavos
     OR NEW.usuario_apertura_id IS NOT OLD.usuario_apertura_id
BEGIN
    SELECT RAISE(ABORT, 'El origen y los datos de apertura de una sesión de caja no pueden modificarse.');
END;

-- 8. Verificación final --------------------------------------------------------

INSERT INTO _fallas SELECT 'triggers de sesiones de caja incompletos'
WHERE (SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND name IN (
    'trg_ventas_sesion_operable', 'trg_caja_movimientos_sesion_operable',
    'trg_ventas_sesion_inmutable', 'trg_caja_movimientos_sesion_inmutable',
    'trg_sesiones_caja_solo_normal', 'trg_sesiones_caja_cerrada_inmutable',
    'trg_sesiones_caja_datos_de_apertura_inmutables')) <> 7;
INSERT INTO _fallas SELECT 'venta o movimiento sin sesión tras crear los triggers'
WHERE EXISTS (SELECT 1 FROM ventas WHERE sesion_caja_id IS NULL)
   OR EXISTS (SELECT 1 FROM caja_movimientos WHERE sesion_caja_id IS NULL);
INSERT INTO _fallas SELECT 'conteos finales'
WHERE (SELECT COUNT(*) FROM ventas) <> (SELECT ventas_n FROM _antes)
   OR (SELECT COUNT(*) FROM detalle_venta) <> (SELECT detalle_n FROM _antes)
   OR (SELECT COUNT(*) FROM caja_movimientos) <> (SELECT mov_n FROM _antes);
INSERT INTO _fallas SELECT 'foreign_key_check final'
WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;
INSERT INTO _fallas SELECT 'sesión ABIERTA al terminar'
WHERE EXISTS (SELECT 1 FROM sesiones_caja WHERE estado = 'ABIERTA');

DROP TRIGGER temp._abortar_por_falla;
DROP TABLE temp._fallas;
DROP TABLE temp._venta_sesion;
DROP TABLE temp._mov_sesion;
DROP TABLE temp._sesion_tmp;
DROP TABLE temp._antes;
