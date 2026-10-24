-- Migración 033: clasificación de proveedores por categorías del catálogo y datos fiscales (V1.13-A).
--
-- 1. Datos fiscales del proveedor, todos opcionales (un proveedor histórico no tiene ninguno y queda
--    exactamente como estaba): razón social, CUIT (11 dígitos, sin guiones), condición fiscal,
--    inscripción de Ingresos Brutos e indicador de Convenio Multilateral. Solo se almacenan y muestran:
--    ninguna regla del sistema deduce de ellos qué puede o no facturar un proveedor.
--    Un CUIT identifica a un único proveedor (activo o dado de baja): índice único parcial, así dos fichas
--    del mismo contribuyente no parten su deuda en dos. Sin CUIT no hay restricción.
--
-- 2. `proveedor_categorias`: muchos-a-muchos con el árbol de categorías YA existente (no hay una
--    taxonomía paralela). Es organizativo: nunca restringe qué se le puede comprar a un proveedor.
--    * proveedor ON DELETE CASCADE: si un proveedor sin ninguna historia se elimina físicamente, su
--      clasificación se va con él (no es historia).
--    * categoría ON DELETE RESTRICT: una categoría que clasifica a algún proveedor está en uso, así que
--      al darla de baja se desactiva en vez de borrarse (mismo criterio que con sus productos y
--      subcategorías, ver `db.repositorios.categorias.eliminar_categoria`).
--
-- Aditiva: no modifica ni reinterpreta ningún dato existente (se verifica abajo). El runner corre el
-- script en un único `BEGIN IMMEDIATE`: cualquier fila en `_fallas` revierte TODO.

DROP TABLE IF EXISTS temp._fallas;
DROP TABLE IF EXISTS temp._proveedores_copia;

CREATE TEMP TABLE _fallas (motivo TEXT NOT NULL);
CREATE TEMP TRIGGER _abortar_por_falla AFTER INSERT ON _fallas
BEGIN
    SELECT RAISE(ABORT, 'Migración 033: verificación fallida: ' || NEW.motivo);
END;

INSERT INTO _fallas SELECT 'la tabla proveedores no tiene las columnas esperadas'
WHERE (SELECT group_concat(name, ',') FROM (SELECT name FROM pragma_table_info('proveedores') ORDER BY cid))
      IS NOT 'id,nombre,contacto_nombre,telefono,email,direccion,notas,activo,fecha_creacion';
INSERT INTO _fallas SELECT 'ya existe la tabla proveedor_categorias'
WHERE EXISTS (SELECT 1 FROM sqlite_master WHERE name = 'proveedor_categorias');
INSERT INTO _fallas SELECT 'foreign_key_check previo'
WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;
INSERT INTO _fallas SELECT 'integrity_check previo'
WHERE (SELECT integrity_check FROM pragma_integrity_check) <> 'ok';
INSERT INTO _fallas SELECT 'las claves foráneas no están activas'
WHERE (SELECT foreign_keys FROM pragma_foreign_keys) <> 1;

CREATE TEMP TABLE _proveedores_copia AS
SELECT id, nombre, contacto_nombre, telefono, email, direccion, notas, activo, fecha_creacion FROM proveedores;

-- 1. Datos fiscales -------------------------------------------------------------------------

ALTER TABLE proveedores ADD COLUMN razon_social TEXT NULL
    CHECK (razon_social IS NULL OR length(trim(razon_social)) > 0);
ALTER TABLE proveedores ADD COLUMN cuit TEXT NULL
    CHECK (cuit IS NULL OR (length(cuit) = 11 AND cuit NOT GLOB '*[^0-9]*'));
ALTER TABLE proveedores ADD COLUMN condicion_fiscal TEXT NULL
    CHECK (condicion_fiscal IS NULL OR condicion_fiscal IN ('RESPONSABLE_INSCRIPTO', 'MONOTRIBUTO', 'EXENTO', 'OTRA'));
ALTER TABLE proveedores ADD COLUMN ingresos_brutos TEXT NULL
    CHECK (ingresos_brutos IS NULL OR length(trim(ingresos_brutos)) > 0);
ALTER TABLE proveedores ADD COLUMN convenio_multilateral INTEGER NOT NULL DEFAULT 0
    CHECK (convenio_multilateral IN (0, 1));

CREATE UNIQUE INDEX idx_proveedores_cuit ON proveedores(cuit) WHERE cuit IS NOT NULL;

-- 2. Clasificación por categorías -----------------------------------------------------------

CREATE TABLE proveedor_categorias (
    proveedor_id  INTEGER NOT NULL REFERENCES proveedores(id) ON DELETE CASCADE,
    categoria_id  INTEGER NOT NULL REFERENCES categorias(id) ON DELETE RESTRICT,
    PRIMARY KEY (proveedor_id, categoria_id)
) WITHOUT ROWID;

CREATE INDEX idx_proveedor_categorias_categoria_id ON proveedor_categorias(categoria_id);

-- 3. Verificación ---------------------------------------------------------------------------

INSERT INTO _fallas SELECT 'proveedores: una fila difiere de la original (o falta)'
WHERE EXISTS (
    SELECT id, nombre, contacto_nombre, telefono, email, direccion, notas, activo, fecha_creacion
    FROM _proveedores_copia
    EXCEPT
    SELECT id, nombre, contacto_nombre, telefono, email, direccion, notas, activo, fecha_creacion
    FROM proveedores
);
INSERT INTO _fallas SELECT 'proveedores: cantidad de filas'
WHERE (SELECT COUNT(*) FROM proveedores) <> (SELECT COUNT(*) FROM _proveedores_copia);
INSERT INTO _fallas SELECT 'se inventó un dato fiscal para un proveedor existente'
WHERE EXISTS (
    SELECT 1 FROM proveedores
    WHERE razon_social IS NOT NULL OR cuit IS NOT NULL OR condicion_fiscal IS NOT NULL
       OR ingresos_brutos IS NOT NULL OR convenio_multilateral <> 0
);
INSERT INTO _fallas SELECT 'faltan índices esperados'
WHERE (SELECT COUNT(*) FROM sqlite_master
       WHERE name IN ('idx_proveedores_cuit', 'idx_proveedor_categorias_categoria_id')) <> 2;
INSERT INTO _fallas SELECT 'foreign_key_check final' WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;
INSERT INTO _fallas SELECT 'integrity_check final'
WHERE (SELECT integrity_check FROM pragma_integrity_check) <> 'ok';

DROP TABLE temp._fallas;
DROP TABLE temp._proveedores_copia;
