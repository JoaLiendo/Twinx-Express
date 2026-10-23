-- Migración 030: categorías jerárquicas y etiquetas de productos (V1.11-E).
--
-- 1. `categorias.categoria_padre_id` (NULL = categoría principal). El modelo admite un árbol de
--    cualquier profundidad; el límite de niveles que ofrece la interfaz es una regla de servicio
--    (`domain.categoria.NIVELES_MAXIMOS`), no del esquema. La base garantiza lo que nunca puede
--    romperse: el padre existe (FK, ON DELETE RESTRICT: no se borra una categoría con
--    subcategorías) y no hay ciclos (una categoría no puede ser su propio ancestro, trigger).
--    El nombre sigue siendo único en todo el catálogo: la importación CSV/XLSX resuelve la
--    categoría por nombre y así nunca es ambigua.
--    Categorías existentes: quedan todas como principales (NULL), su estado plano actual.
--
-- 2. Etiquetas independientes de las categorías (`etiquetas`, `producto_etiquetas`): planas, un
--    producto puede tener varias. No son historial: se borran en cascada con su producto (si este
--    se elimina físicamente) o con la etiqueta.
--
-- Aditiva: no modifica ni reinterpreta ningún dato existente (se verifica abajo). El runner corre
-- el script en un único `BEGIN IMMEDIATE`: cualquier fila en `_fallas` revierte TODO.

DROP TABLE IF EXISTS temp._fallas;
DROP TABLE IF EXISTS temp._antes;
DROP TABLE IF EXISTS temp._categorias_copia;

CREATE TEMP TABLE _fallas (motivo TEXT NOT NULL);
CREATE TEMP TRIGGER _abortar_por_falla AFTER INSERT ON _fallas
BEGIN
    SELECT RAISE(ABORT, 'Migración 030: verificación fallida: ' || NEW.motivo);
END;

INSERT INTO _fallas SELECT 'la tabla categorias no tiene las columnas esperadas'
WHERE (SELECT group_concat(name, ',') FROM (SELECT name FROM pragma_table_info('categorias') ORDER BY cid))
      IS NOT 'id,nombre,activa,fecha_creacion';
INSERT INTO _fallas SELECT 'ya existe una tabla de etiquetas'
WHERE EXISTS (SELECT 1 FROM sqlite_master WHERE name IN ('etiquetas', 'producto_etiquetas'));
INSERT INTO _fallas SELECT 'foreign_key_check previo'
WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;
INSERT INTO _fallas SELECT 'integrity_check previo'
WHERE (SELECT integrity_check FROM pragma_integrity_check) <> 'ok';
INSERT INTO _fallas SELECT 'las claves foráneas no están activas'
WHERE (SELECT foreign_keys FROM pragma_foreign_keys) <> 1;

CREATE TEMP TABLE _antes AS
SELECT
    (SELECT COUNT(*) FROM productos)                                    AS productos_n,
    (SELECT COALESCE(SUM(stock_actual), 0) FROM productos)              AS productos_stock,
    (SELECT COUNT(*) FROM productos WHERE categoria_id IS NOT NULL)     AS productos_con_categoria_n;
CREATE TEMP TABLE _categorias_copia AS SELECT id, nombre, activa, fecha_creacion FROM categorias;

-- 1. Árbol de categorías -----------------------------------------------------------------

ALTER TABLE categorias ADD COLUMN categoria_padre_id INTEGER NULL REFERENCES categorias(id) ON DELETE RESTRICT;
CREATE INDEX idx_categorias_categoria_padre_id ON categorias(categoria_padre_id);

-- Un alta nueva no puede generar un ciclo (su id todavía no es padre de nadie) salvo que se
-- elija a sí misma con un id explícito.
CREATE TRIGGER trg_categorias_padre_valido_alta
BEFORE INSERT ON categorias
WHEN NEW.categoria_padre_id IS NOT NULL AND NEW.categoria_padre_id = NEW.id
BEGIN
    SELECT RAISE(ABORT, 'Una categoría no puede estar dentro de sí misma.');
END;

-- Mover una categoría: el nuevo padre no puede ser ella misma ni ninguna de sus descendientes.
CREATE TRIGGER trg_categorias_sin_ciclos
BEFORE UPDATE OF categoria_padre_id ON categorias
WHEN NEW.categoria_padre_id IS NOT NULL
     AND (
         NEW.categoria_padre_id = NEW.id
         OR NEW.categoria_padre_id IN (
             WITH RECURSIVE descendientes(id) AS (
                 SELECT id FROM categorias WHERE categoria_padre_id = NEW.id
                 UNION
                 SELECT c.id FROM categorias c JOIN descendientes d ON c.categoria_padre_id = d.id
             )
             SELECT id FROM descendientes
         )
     )
BEGIN
    SELECT RAISE(ABORT, 'Una categoría no puede quedar dentro de sí misma ni de una de sus subcategorías.');
END;

-- 2. Etiquetas ------------------------------------------------------------------------------

CREATE TABLE etiquetas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre          TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (length(trim(nombre)) > 0),
    fecha_creacion  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE producto_etiquetas (
    producto_id  INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
    etiqueta_id  INTEGER NOT NULL REFERENCES etiquetas(id) ON DELETE CASCADE,
    PRIMARY KEY (producto_id, etiqueta_id)
) WITHOUT ROWID;

CREATE INDEX idx_producto_etiquetas_etiqueta_id ON producto_etiquetas(etiqueta_id);

-- 3. Verificación ---------------------------------------------------------------------------

INSERT INTO _fallas SELECT 'categorias: una fila difiere de la original (o falta)'
WHERE EXISTS (
    SELECT id, nombre, activa, fecha_creacion FROM _categorias_copia
    EXCEPT
    SELECT id, nombre, activa, fecha_creacion FROM categorias
);
INSERT INTO _fallas SELECT 'categorias: cantidad de filas'
WHERE (SELECT COUNT(*) FROM categorias) <> (SELECT COUNT(*) FROM _categorias_copia);
INSERT INTO _fallas SELECT 'se asignó una categoría superior a una categoría existente'
WHERE EXISTS (SELECT 1 FROM categorias WHERE categoria_padre_id IS NOT NULL);
INSERT INTO _fallas SELECT 'productos: cantidad, stock o categorías cambiaron'
WHERE (SELECT COUNT(*) FROM productos) <> (SELECT productos_n FROM _antes)
   OR (SELECT COALESCE(SUM(stock_actual), 0) FROM productos) <> (SELECT productos_stock FROM _antes)
   OR (SELECT COUNT(*) FROM productos WHERE categoria_id IS NOT NULL) <> (SELECT productos_con_categoria_n FROM _antes);
INSERT INTO _fallas SELECT 'faltan índices/triggers esperados'
WHERE (SELECT COUNT(*) FROM sqlite_master
       WHERE name IN ('idx_categorias_categoria_padre_id', 'trg_categorias_padre_valido_alta',
                      'trg_categorias_sin_ciclos', 'idx_producto_etiquetas_etiqueta_id')) <> 4;
INSERT INTO _fallas SELECT 'foreign_key_check final' WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;
INSERT INTO _fallas SELECT 'integrity_check final'
WHERE (SELECT integrity_check FROM pragma_integrity_check) <> 'ok';

DROP TABLE temp._fallas;
DROP TABLE temp._antes;
DROP TABLE temp._categorias_copia;
