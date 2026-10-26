-- Migración 040: cierres, strikes, veto y autorizaciones temporales de crédito (V1.14-E).
--
-- El ESTADO financiero (NORMAL / REGULAR / DEMORADO / CRÍTICA) no se guarda: se deriva siempre de la
-- antigüedad de la obligación pendiente más antigua (ver `domain.credito`). Lo que sí se persiste es la
-- HISTORIA, que no puede reconstruirse desde el estado actual:
--
--   * `episodios_credito`: cada entrada en CRÍTICA abre un episodio (= cierre del crédito) con su fecha
--     de inicio teórica (origen de la obligación + 60 días). Como máximo uno abierto por cliente. El
--     episodio guarda a lo sumo UN strike (`fecha_strike`) y su reapertura explícita por el OWNER.
--     Pagar no reabre nada: la reapertura es una acción aparte, con observación.
--   * 3 strikes = veto permanente del crédito: el esquema impide reabrir el episodio y rechaza todo
--     CARGO nuevo de ese cliente, aunque alguien configure un límite o edite la base a mano. Comprar al
--     contado sigue permitido.
--   * `autorizaciones_credito`: permiso temporal de un OWNER para vender a cuenta a un cliente DEMORADO,
--     con compromiso y fecha límite. El esquema exige que lo otorgue un OWNER activo (un CASHIER no puede
--     fabricarlo) y lo hace inmutable.
--
-- Instalaciones existentes: las tablas nacen vacías. No se inventan episodios ni strikes para la deuda
-- histórica: la primera evaluación (al abrir la ficha, vender o cobrar) registra el cierre si ya
-- corresponde, con su fecha de inicio teórica y la fecha real en que se detectó.

DROP TABLE IF EXISTS temp._fallas;
CREATE TEMP TABLE _fallas (motivo TEXT NOT NULL);
CREATE TEMP TRIGGER _abortar_por_falla AFTER INSERT ON _fallas
BEGIN
    SELECT RAISE(ABORT, 'Migración 040: verificación fallida: ' || NEW.motivo);
END;

INSERT INTO _fallas SELECT 'ya existe alguna tabla de la migración 040'
WHERE EXISTS (SELECT 1 FROM sqlite_master WHERE name IN ('episodios_credito', 'autorizaciones_credito'));

CREATE TABLE episodios_credito (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id               INTEGER NOT NULL REFERENCES clientes(id) ON DELETE RESTRICT,
    obligacion_id            INTEGER NOT NULL REFERENCES obligaciones_cliente(id) ON DELETE RESTRICT,
    fecha_inicio             TEXT NOT NULL CHECK (fecha_inicio GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    fecha_registro           TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    fecha_strike             TEXT NULL CHECK (fecha_strike IS NULL OR fecha_strike > fecha_inicio),
    strike_registrado_en     TEXT NULL,
    fecha_reapertura         TEXT NULL,
    reabierto_por_usuario_id INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    observacion_reapertura   TEXT NULL CHECK (observacion_reapertura IS NULL
                                              OR (length(trim(observacion_reapertura)) > 0 AND length(observacion_reapertura) <= 250)),
    CHECK ((fecha_strike IS NULL) = (strike_registrado_en IS NULL)),
    CHECK ((fecha_reapertura IS NULL) = (reabierto_por_usuario_id IS NULL)
           AND (fecha_reapertura IS NULL) = (observacion_reapertura IS NULL))
);

-- Un solo cierre vigente por cliente: dos evaluaciones simultáneas no abren dos episodios.
CREATE UNIQUE INDEX idx_episodios_credito_uno_abierto ON episodios_credito(cliente_id) WHERE fecha_reapertura IS NULL;
CREATE INDEX idx_episodios_credito_cliente ON episodios_credito(cliente_id, id);

CREATE TRIGGER trg_episodios_credito_alta BEFORE INSERT ON episodios_credito
WHEN NEW.fecha_strike IS NOT NULL OR NEW.fecha_reapertura IS NOT NULL
     OR NOT EXISTS (SELECT 1 FROM obligaciones_cliente WHERE id = NEW.obligacion_id AND cliente_id = NEW.cliente_id)
BEGIN
    SELECT RAISE(ABORT, 'Un episodio nace abierto, sin strike, por una obligación del mismo cliente.');
END;

-- Solo dos transiciones, cada una una única vez: registrar el strike y reabrir. Nada más cambia.
CREATE TRIGGER trg_episodios_credito_transiciones BEFORE UPDATE ON episodios_credito
WHEN NEW.id IS NOT OLD.id OR NEW.cliente_id IS NOT OLD.cliente_id OR NEW.obligacion_id IS NOT OLD.obligacion_id
     OR NEW.fecha_inicio IS NOT OLD.fecha_inicio OR NEW.fecha_registro IS NOT OLD.fecha_registro
     OR (OLD.fecha_strike IS NOT NULL AND (NEW.fecha_strike IS NOT OLD.fecha_strike
                                           OR NEW.strike_registrado_en IS NOT OLD.strike_registrado_en))
     OR (OLD.fecha_reapertura IS NOT NULL AND (NEW.fecha_reapertura IS NOT OLD.fecha_reapertura
                                               OR NEW.reabierto_por_usuario_id IS NOT OLD.reabierto_por_usuario_id
                                               OR NEW.observacion_reapertura IS NOT OLD.observacion_reapertura))
     OR (OLD.fecha_reapertura IS NOT NULL AND NEW.fecha_strike IS NOT OLD.fecha_strike)
BEGIN
    SELECT RAISE(ABORT, 'Un episodio de crédito solo registra una vez su strike y una vez su reapertura.');
END;

CREATE TRIGGER trg_episodios_credito_reapertura BEFORE UPDATE OF fecha_reapertura ON episodios_credito
WHEN OLD.fecha_reapertura IS NULL AND NEW.fecha_reapertura IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Solo un OWNER activo puede reabrir el crédito.')
    WHERE NOT EXISTS (SELECT 1 FROM usuarios WHERE id = NEW.reabierto_por_usuario_id AND rol = 'OWNER' AND activo = 1);
    SELECT RAISE(ABORT, 'Crédito vetado para siempre: el cliente acumuló 3 strikes y no puede reabrirse.')
    WHERE (SELECT COUNT(*) FROM episodios_credito WHERE cliente_id = OLD.cliente_id AND fecha_strike IS NOT NULL)
          + (CASE WHEN OLD.fecha_strike IS NULL AND NEW.fecha_strike IS NOT NULL THEN 1 ELSE 0 END) >= 3;
END;

CREATE TRIGGER trg_episodios_credito_no_borrable BEFORE DELETE ON episodios_credito
BEGIN
    SELECT RAISE(ABORT, 'Los episodios de crédito no se borran.');
END;

CREATE TABLE autorizaciones_credito (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id     INTEGER NOT NULL REFERENCES clientes(id) ON DELETE RESTRICT,
    compromiso     TEXT NOT NULL CHECK (length(trim(compromiso)) > 0 AND length(compromiso) <= 250),
    fecha_limite   TEXT NOT NULL CHECK (fecha_limite GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    usuario_id     INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fecha_creacion TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK (fecha_limite > substr(fecha_creacion, 1, 10))
);

CREATE INDEX idx_autorizaciones_credito_cliente ON autorizaciones_credito(cliente_id, id);

CREATE TRIGGER trg_autorizaciones_credito_solo_owner BEFORE INSERT ON autorizaciones_credito
WHEN NOT EXISTS (SELECT 1 FROM usuarios WHERE id = NEW.usuario_id AND rol = 'OWNER' AND activo = 1)
BEGIN
    SELECT RAISE(ABORT, 'Solo un OWNER activo puede autorizar crédito temporal.');
END;

CREATE TRIGGER trg_autorizaciones_credito_inmutable BEFORE UPDATE ON autorizaciones_credito
BEGIN
    SELECT RAISE(ABORT, 'Las autorizaciones de crédito no pueden modificarse.');
END;

CREATE TRIGGER trg_autorizaciones_credito_no_borrable BEFORE DELETE ON autorizaciones_credito
BEGIN
    SELECT RAISE(ABORT, 'Las autorizaciones de crédito no pueden borrarse.');
END;

-- Regla fuerte del libro: ningún CARGO nuevo para un cliente con el crédito cerrado o vetado.
CREATE TRIGGER trg_movimientos_cuenta_cargo_credito_cerrado BEFORE INSERT ON movimientos_cuenta
WHEN NEW.tipo = 'CARGO'
BEGIN
    SELECT RAISE(ABORT, 'Crédito vetado para siempre: el cliente acumuló 3 strikes.')
    WHERE (SELECT COUNT(*) FROM episodios_credito WHERE cliente_id = NEW.cliente_id AND fecha_strike IS NOT NULL) >= 3;
    SELECT RAISE(ABORT, 'Crédito cerrado por deuda crítica: solo el dueño puede reabrirlo.')
    WHERE EXISTS (SELECT 1 FROM episodios_credito WHERE cliente_id = NEW.cliente_id AND fecha_reapertura IS NULL);
END;

INSERT INTO _fallas SELECT 'foreign_key_check final'
WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;
INSERT INTO _fallas SELECT 'integrity_check final'
WHERE (SELECT integrity_check FROM pragma_integrity_check) <> 'ok';

DROP TRIGGER temp._abortar_por_falla;
DROP TABLE temp._fallas;
