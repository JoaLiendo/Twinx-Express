-- Migración 044: contingencia CAEA (V1.16-G).
--
--   * `caea_autorizaciones`: cada CAEA obtenido de ARCA (FECAEASolicitar / FECAEAConsultar), por ambiente,
--     CUIT, período (AAAAMM) y orden (1 = días 1-15, 2 = 16-fin), con su vigencia y fecha tope para
--     informar. Inmutable: es lo que ARCA otorgó.
--   * `caea_numeracion`: último número que ARCA tenía autorizado en el punto de venta CAEA, por tipo,
--     sincronizado con FECompUltimoAutorizado mientras ARCA respondía. Bajo CAEA no se puede consultar a
--     ARCA (por eso hay contingencia): el siguiente número es el mayor entre esa base y lo que Twinx ya
--     emitió en ese punto de venta exclusivo + 1.
--   * `caea_sin_movimiento`: informe de un punto de venta CAEA sin comprobantes en el período.
-- Un comprobante bajo CAEA (migración 041) referencia su CAEA: se exige que exista, sea del mismo
-- ambiente y CUIT, y que la fecha del comprobante caiga dentro de su vigencia.

CREATE TABLE caea_autorizaciones (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ambiente            TEXT NOT NULL CHECK (ambiente IN ('HOMOLOGACION', 'PRODUCCION')),
    cuit                TEXT NOT NULL CHECK (length(cuit) = 11 AND cuit NOT GLOB '*[^0-9]*'),
    codigo              TEXT NOT NULL CHECK (length(codigo) = 14 AND codigo NOT GLOB '*[^0-9]*'),
    periodo             INTEGER NOT NULL CHECK (periodo BETWEEN 200001 AND 299912 AND periodo % 100 BETWEEN 1 AND 12),
    orden               INTEGER NOT NULL CHECK (orden IN (1, 2)),
    vigente_desde       TEXT NOT NULL CHECK (date(vigente_desde) = vigente_desde),
    vigente_hasta       TEXT NOT NULL CHECK (date(vigente_hasta) = vigente_hasta AND vigente_hasta >= vigente_desde),
    tope_informe        TEXT NOT NULL CHECK (date(tope_informe) = tope_informe AND tope_informe >= vigente_hasta),
    fecha_proceso       TEXT NULL,
    observaciones_json  TEXT NULL CHECK (observaciones_json IS NULL OR json_valid(observaciones_json)),
    fecha_obtencion     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (ambiente, cuit, periodo, orden),
    UNIQUE (ambiente, codigo)
);

CREATE TRIGGER trg_caea_autorizaciones_inmutables BEFORE UPDATE ON caea_autorizaciones
BEGIN
    SELECT RAISE(ABORT, 'Un CAEA otorgado por ARCA no se modifica.');
END;

CREATE TRIGGER trg_caea_autorizaciones_no_borrables BEFORE DELETE ON caea_autorizaciones
BEGIN
    SELECT RAISE(ABORT, 'Un CAEA otorgado por ARCA no se borra.');
END;

CREATE TABLE caea_numeracion (
    ambiente          TEXT NOT NULL CHECK (ambiente IN ('HOMOLOGACION', 'PRODUCCION')),
    cuit              TEXT NOT NULL,
    punto_venta       INTEGER NOT NULL,
    tipo_comprobante  INTEGER NOT NULL,
    ultimo_arca       INTEGER NOT NULL CHECK (ultimo_arca >= 0),
    fecha             TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (ambiente, cuit, punto_venta, tipo_comprobante)
);

-- La base sincronizada solo avanza: un dato viejo de ARCA nunca hace retroceder la numeración.
CREATE TRIGGER trg_caea_numeracion_no_retrocede BEFORE UPDATE ON caea_numeracion
WHEN NEW.ultimo_arca < OLD.ultimo_arca
BEGIN
    SELECT RAISE(ABORT, 'La numeración CAEA sincronizada no retrocede.');
END;

CREATE TABLE caea_sin_movimiento (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    caea_id       INTEGER NOT NULL REFERENCES caea_autorizaciones(id) ON DELETE RESTRICT,
    punto_venta   INTEGER NOT NULL,
    resultado     TEXT NOT NULL CHECK (resultado IN ('A', 'R')),
    fecha_proceso TEXT NULL,
    detalle_json  TEXT NULL CHECK (detalle_json IS NULL OR json_valid(detalle_json)),
    fecha         TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE UNIQUE INDEX idx_caea_sin_movimiento_aceptado ON caea_sin_movimiento(caea_id, punto_venta) WHERE resultado = 'A';

CREATE TRIGGER trg_caea_sin_movimiento_inmutable BEFORE UPDATE ON caea_sin_movimiento
BEGIN
    SELECT RAISE(ABORT, 'Un informe sin movimiento no se modifica.');
END;

CREATE TRIGGER trg_caea_sin_movimiento_no_borrable BEFORE DELETE ON caea_sin_movimiento
BEGIN
    SELECT RAISE(ABORT, 'Un informe sin movimiento no se borra.');
END;

CREATE TRIGGER trg_comprobantes_fiscales_caea_valido BEFORE INSERT ON comprobantes_fiscales
WHEN NEW.modalidad = 'CAEA'
BEGIN
    SELECT RAISE(ABORT, 'El comprobante bajo CAEA debe usar un CAEA vigente de ese ambiente y CUIT, y su mismo código.')
    WHERE NOT EXISTS (SELECT 1 FROM caea_autorizaciones c
                      WHERE c.id = NEW.caea_id AND c.ambiente = NEW.ambiente AND c.cuit = NEW.cuit_emisor
                        AND c.codigo = NEW.codigo_autorizacion
                        AND NEW.fecha_comprobante BETWEEN c.vigente_desde AND c.vigente_hasta);
END;
