-- Migración 037: tipo de cliente, condición fiscal y compradores autorizados (V1.14-A).
--
-- Agrega a `clientes`, como columnas NULLables (ALTER TABLE, sin reconstruir la tabla):
--   * `tipo_cliente` (PERSONA / EMPRESA) y `condicion_fiscal` (CONSUMIDOR_FINAL /
--     RESPONSABLE_INSCRIPTO / MONOTRIBUTO / EXENTO): conceptos INDEPENDIENTES, ninguno se
--     deduce del otro;
--   * `razon_social`, `cuit` (11 dígitos, único entre clientes) y `contacto`.
-- Una EMPRESA exige razón social, CUIT, dirección, teléfono, contacto y condición fiscal.
--
-- Clientes históricos: quedan con `tipo_cliente` y `condicion_fiscal` en NULL ("sin
-- clasificar") hasta que el OWNER los edite. Nunca se infiere un tipo ni una condición
-- fiscal: el modelo V1.3 no guardaba nada que permita deducirlos. Una vez clasificado, un
-- cliente no vuelve a quedar sin clasificar.
--
-- `compradores_autorizados`: personas que pueden comprar a nombre de una EMPRESA. Como
-- máximo 2 ACTIVAS por empresa. Cada fila es un período de autorización: solo admite la
-- baja (activo 1 -> 0, con fecha y usuario); una reactivación es una fila nueva, así el
-- historial nunca se reescribe ni se borra.

DROP TABLE IF EXISTS temp._fallas;
CREATE TEMP TABLE _fallas (motivo TEXT NOT NULL);
CREATE TEMP TRIGGER _abortar_por_falla AFTER INSERT ON _fallas
BEGIN
    SELECT RAISE(ABORT, 'Migración 037: verificación fallida: ' || NEW.motivo);
END;

INSERT INTO _fallas SELECT 'la tabla clientes no tiene las columnas esperadas'
WHERE (SELECT group_concat(name, ',') FROM (SELECT name FROM pragma_table_info('clientes') ORDER BY cid))
      IS NOT 'id,nombre,telefono,email,direccion,observaciones,activo,fecha_creacion';
INSERT INTO _fallas SELECT 'ya existe la tabla compradores_autorizados'
WHERE EXISTS (SELECT 1 FROM sqlite_master WHERE name = 'compradores_autorizados');

CREATE TEMP TABLE _antes AS
SELECT (SELECT COUNT(*) FROM clientes) AS clientes_n,
       (SELECT group_concat(id || '|' || nombre || '|' || activo, ';') FROM (SELECT * FROM clientes ORDER BY id)) AS huella;

ALTER TABLE clientes ADD COLUMN tipo_cliente TEXT NULL
    CHECK (tipo_cliente IS NULL OR tipo_cliente IN ('PERSONA', 'EMPRESA'));
ALTER TABLE clientes ADD COLUMN condicion_fiscal TEXT NULL
    CHECK (condicion_fiscal IS NULL
           OR condicion_fiscal IN ('CONSUMIDOR_FINAL', 'RESPONSABLE_INSCRIPTO', 'MONOTRIBUTO', 'EXENTO'));
ALTER TABLE clientes ADD COLUMN razon_social TEXT NULL
    CHECK (razon_social IS NULL OR (length(trim(razon_social)) > 0 AND length(razon_social) <= 150));
ALTER TABLE clientes ADD COLUMN cuit TEXT NULL
    CHECK (cuit IS NULL OR (length(cuit) = 11 AND cuit NOT GLOB '*[^0-9]*'));
ALTER TABLE clientes ADD COLUMN contacto TEXT NULL
    CHECK (contacto IS NULL OR (length(trim(contacto)) > 0 AND length(contacto) <= 120));

CREATE UNIQUE INDEX idx_clientes_cuit ON clientes(cuit) WHERE cuit IS NOT NULL;

-- Una EMPRESA tiene siempre su identidad comercial completa (alta y edición).
CREATE TRIGGER trg_clientes_empresa_completa_alta BEFORE INSERT ON clientes
WHEN NEW.tipo_cliente = 'EMPRESA'
     AND (NEW.razon_social IS NULL OR NEW.cuit IS NULL OR NEW.direccion IS NULL OR NEW.telefono IS NULL
          OR NEW.contacto IS NULL OR NEW.condicion_fiscal IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'Una empresa requiere razón social, CUIT, dirección, teléfono, contacto y condición fiscal.');
END;

CREATE TRIGGER trg_clientes_empresa_completa_edicion BEFORE UPDATE ON clientes
WHEN NEW.tipo_cliente = 'EMPRESA'
     AND (NEW.razon_social IS NULL OR NEW.cuit IS NULL OR NEW.direccion IS NULL OR NEW.telefono IS NULL
          OR NEW.contacto IS NULL OR NEW.condicion_fiscal IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'Una empresa requiere razón social, CUIT, dirección, teléfono, contacto y condición fiscal.');
END;

CREATE TRIGGER trg_clientes_clasificacion_irreversible BEFORE UPDATE OF tipo_cliente, condicion_fiscal ON clientes
WHEN (OLD.tipo_cliente IS NOT NULL AND NEW.tipo_cliente IS NULL)
     OR (OLD.condicion_fiscal IS NOT NULL AND NEW.condicion_fiscal IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'Un cliente clasificado no puede volver a quedar sin tipo o sin condición fiscal.');
END;

CREATE TABLE compradores_autorizados (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id      INTEGER NOT NULL REFERENCES clientes(id) ON DELETE RESTRICT,
    nombre          TEXT NOT NULL CHECK (length(trim(nombre)) > 0 AND length(nombre) <= 120),
    documento       TEXT NULL CHECK (documento IS NULL OR (length(trim(documento)) > 0 AND length(documento) <= 30)),
    contacto        TEXT NULL CHECK (contacto IS NULL OR (length(trim(contacto)) > 0 AND length(contacto) <= 120)),
    activo          INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
    fecha_alta      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    usuario_alta_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fecha_baja      TEXT NULL,
    usuario_baja_id INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    CHECK ((activo = 1 AND fecha_baja IS NULL AND usuario_baja_id IS NULL)
           OR (activo = 0 AND fecha_baja IS NOT NULL AND usuario_baja_id IS NOT NULL))
);

CREATE INDEX idx_compradores_autorizados_cliente ON compradores_autorizados(cliente_id, activo);

CREATE TRIGGER trg_compradores_autorizados_alta BEFORE INSERT ON compradores_autorizados
BEGIN
    SELECT RAISE(ABORT, 'Solo una empresa activa puede tener compradores autorizados.')
    WHERE NOT EXISTS (SELECT 1 FROM clientes WHERE id = NEW.cliente_id AND tipo_cliente = 'EMPRESA' AND activo = 1);
    SELECT RAISE(ABORT, 'Un comprador autorizado nace activo.')
    WHERE NEW.activo <> 1;
    SELECT RAISE(ABORT, 'La empresa ya tiene 2 compradores autorizados activos.')
    WHERE (SELECT COUNT(*) FROM compradores_autorizados WHERE cliente_id = NEW.cliente_id AND activo = 1) >= 2;
END;

-- Solo la baja: los datos y el período registrado son inmutables.
CREATE TRIGGER trg_compradores_autorizados_solo_baja BEFORE UPDATE ON compradores_autorizados
WHEN NOT (OLD.activo = 1 AND NEW.activo = 0)
     OR NEW.id IS NOT OLD.id OR NEW.cliente_id IS NOT OLD.cliente_id OR NEW.nombre IS NOT OLD.nombre
     OR NEW.documento IS NOT OLD.documento OR NEW.contacto IS NOT OLD.contacto
     OR NEW.fecha_alta IS NOT OLD.fecha_alta OR NEW.usuario_alta_id IS NOT OLD.usuario_alta_id
BEGIN
    SELECT RAISE(ABORT, 'Un comprador autorizado solo puede darse de baja: su historial no se modifica.');
END;

CREATE TRIGGER trg_compradores_autorizados_no_borrable BEFORE DELETE ON compradores_autorizados
BEGIN
    SELECT RAISE(ABORT, 'Los compradores autorizados no se borran: se dan de baja.');
END;

-- Una empresa con autorizados activos no deja de ser empresa sin darlos de baja antes.
CREATE TRIGGER trg_clientes_empresa_con_autorizados BEFORE UPDATE OF tipo_cliente ON clientes
WHEN OLD.tipo_cliente = 'EMPRESA' AND NEW.tipo_cliente IS NOT 'EMPRESA'
     AND EXISTS (SELECT 1 FROM compradores_autorizados WHERE cliente_id = OLD.id AND activo = 1)
BEGIN
    SELECT RAISE(ABORT, 'Dá de baja a los compradores autorizados antes de cambiar el tipo de la empresa.');
END;

-- Verificación: nada histórico cambió y ningún cliente quedó clasificado por inferencia.
INSERT INTO _fallas SELECT 'cambió la cantidad o los datos de los clientes'
WHERE (SELECT COUNT(*) FROM clientes) <> (SELECT clientes_n FROM _antes)
   OR (SELECT group_concat(id || '|' || nombre || '|' || activo, ';') FROM (SELECT * FROM clientes ORDER BY id))
      IS NOT (SELECT huella FROM _antes);
INSERT INTO _fallas SELECT 'se infirió una clasificación para un cliente histórico'
WHERE EXISTS (SELECT 1 FROM clientes WHERE tipo_cliente IS NOT NULL OR condicion_fiscal IS NOT NULL
              OR razon_social IS NOT NULL OR cuit IS NOT NULL OR contacto IS NOT NULL);
INSERT INTO _fallas SELECT 'foreign_key_check final'
WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;
INSERT INTO _fallas SELECT 'integrity_check final'
WHERE (SELECT integrity_check FROM pragma_integrity_check) <> 'ok';

DROP TRIGGER temp._abortar_por_falla;
DROP TABLE temp._fallas;
DROP TABLE temp._antes;
