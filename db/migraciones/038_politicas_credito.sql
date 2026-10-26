-- Migración 038: políticas de crédito por capas (V1.14-B).
--
-- Una fila por capa configurada: GENERAL, PERSONA, EMPRESA (por tipo de cliente) o CLIENTE
-- (excepción individual, con `cliente_id`). Precedencia GENERAL -> TIPO -> INDIVIDUAL: la capa
-- más específica que exista gana (ver `domain.credito.resolver_politica`).
--
-- Representación explícita, sin ambigüedad entre NULL y cero:
--   * capa SIN FILA o modo 'HEREDAR' -> no configurada: hereda de la capa anterior (y si no hay
--                                       GENERAL, el cliente NO tiene crédito: nunca se inventa
--                                       crédito ilimitado). GENERAL no admite 'HEREDAR';
--   * modo 'SIN_CREDITO'              -> crédito deshabilitado (`limite_centavos` siempre NULL);
--   * modo 'LIMITE', limite 0         -> crédito habilitado con límite cero (nada disponible);
--   * modo 'LIMITE', limite N > 0     -> crédito habilitado hasta N centavos de deuda.
-- No existe "ilimitado". Las filas no se borran (volver a heredar es el modo 'HEREDAR') y `version`
-- crece en cada cambio: el servicio rechaza una edición pensada sobre una versión que ya no es la
-- vigente (dos OWNER editando a la vez). La historia de cambios queda en la auditoría.
--
-- Instalaciones existentes: la tabla nace VACÍA. Hasta que el OWNER configure una política, ningún
-- cliente tiene crédito (las deudas existentes se conservan intactas y se pueden seguir cobrando).

DROP TABLE IF EXISTS temp._fallas;
CREATE TEMP TABLE _fallas (motivo TEXT NOT NULL);
CREATE TEMP TRIGGER _abortar_por_falla AFTER INSERT ON _fallas
BEGIN
    SELECT RAISE(ABORT, 'Migración 038: verificación fallida: ' || NEW.motivo);
END;

INSERT INTO _fallas SELECT 'ya existe la tabla politicas_credito'
WHERE EXISTS (SELECT 1 FROM sqlite_master WHERE name = 'politicas_credito');

CREATE TABLE politicas_credito (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ambito              TEXT NOT NULL CHECK (ambito IN ('GENERAL', 'PERSONA', 'EMPRESA', 'CLIENTE')),
    cliente_id          INTEGER NULL REFERENCES clientes(id) ON DELETE RESTRICT,
    modo                TEXT NOT NULL CHECK (modo IN ('HEREDAR', 'SIN_CREDITO', 'LIMITE')),
    limite_centavos     INTEGER NULL,
    version             INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    usuario_id          INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fecha_actualizacion TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK ((ambito = 'CLIENTE') = (cliente_id IS NOT NULL)),
    CHECK (ambito <> 'GENERAL' OR modo <> 'HEREDAR'),
    CHECK ((modo IN ('HEREDAR', 'SIN_CREDITO') AND limite_centavos IS NULL)
           OR (modo = 'LIMITE' AND limite_centavos IS NOT NULL AND limite_centavos >= 0
               AND limite_centavos <= 100000000000))
);

CREATE UNIQUE INDEX idx_politicas_credito_capa ON politicas_credito(ambito, COALESCE(cliente_id, 0));

-- El ámbito y el cliente de una fila no cambian; la versión solo avanza de a uno.
CREATE TRIGGER trg_politicas_credito_capa_inmutable BEFORE UPDATE ON politicas_credito
WHEN NEW.ambito IS NOT OLD.ambito OR NEW.cliente_id IS NOT OLD.cliente_id OR NEW.version <> OLD.version + 1
BEGIN
    SELECT RAISE(ABORT, 'Una política de crédito solo cambia su modo y límite, avanzando su versión.');
END;

CREATE TRIGGER trg_politicas_credito_no_borrable BEFORE DELETE ON politicas_credito
BEGIN
    SELECT RAISE(ABORT, 'Las políticas de crédito no se borran: se vuelven a HEREDAR.');
END;

INSERT INTO _fallas SELECT 'foreign_key_check final'
WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;
INSERT INTO _fallas SELECT 'integrity_check final'
WHERE (SELECT integrity_check FROM pragma_integrity_check) <> 'ok';

DROP TRIGGER temp._abortar_por_falla;
DROP TABLE temp._fallas;
