-- Migración 050: DNI opcional de los clientes PERSONA (cierre operativo, B1).
--
--   * `clientes.dni`: 7 u 8 dígitos, sin puntos. Opcional; solo para personas (una EMPRESA se identifica
--     por CUIT). Único entre los clientes que lo tienen: dos fichas con el mismo DNI son la misma persona
--     cargada dos veces, y eso se resuelve editando, no duplicando.
--   * Los clientes históricos quedan en NULL: no se infiere ningún DNI (ni del CUIT, ni de observaciones).
--   * No toca comprobantes fiscales: el receptor de un comprobante autorizado conserva su snapshot.

ALTER TABLE clientes ADD COLUMN dni TEXT NULL
    CHECK (dni IS NULL OR (length(dni) BETWEEN 7 AND 8 AND dni NOT GLOB '*[^0-9]*'));

CREATE UNIQUE INDEX idx_clientes_dni ON clientes(dni) WHERE dni IS NOT NULL;

CREATE TRIGGER trg_clientes_empresa_sin_dni_alta BEFORE INSERT ON clientes
WHEN NEW.tipo_cliente = 'EMPRESA' AND NEW.dni IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Una empresa se identifica por CUIT, no por DNI.');
END;

CREATE TRIGGER trg_clientes_empresa_sin_dni_edicion BEFORE UPDATE OF tipo_cliente, dni ON clientes
WHEN NEW.tipo_cliente = 'EMPRESA' AND NEW.dni IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Una empresa se identifica por CUIT, no por DNI.');
END;
