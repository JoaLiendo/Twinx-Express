-- Migración 004: categorías de productos y unidad de medida (Fase 3C).
-- Aditiva: no modifica ni elimina ningún dato existente. Los productos
-- actuales quedan con categoria_id=NULL (sin categoría) y
-- unidad_medida='UNIDAD' -- su estado implícito actual, ahora explícito.
--
-- La unidad de medida es, por ahora, puramente descriptiva: no cambia
-- cómo se guarda ni se opera `stock_actual` (sigue siendo un conteo
-- entero de "unidades" tal cual, sin conversión a gramos/mililitros).

CREATE TABLE IF NOT EXISTS categorias (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre          TEXT NOT NULL UNIQUE,
    activa          INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0, 1)),
    fecha_creacion  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_categorias_activa ON categorias(activa);

ALTER TABLE productos ADD COLUMN categoria_id INTEGER NULL REFERENCES categorias(id) ON DELETE RESTRICT;
CREATE INDEX IF NOT EXISTS idx_productos_categoria_id ON productos(categoria_id);

ALTER TABLE productos ADD COLUMN unidad_medida TEXT NOT NULL DEFAULT 'UNIDAD'
    CHECK (unidad_medida IN ('UNIDAD', 'KG', 'G', 'LITRO', 'ML'));
