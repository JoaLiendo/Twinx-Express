-- Migración 021: relación producto-proveedor (V1.4).
-- Aditiva: no modifica ni elimina ninguna tabla existente ni toca compras.
--
-- `producto_proveedor` dice qué proveedores le venden un producto y cuál es el
-- principal. No guarda costos: el costo de compra sigue viniendo de
-- `detalle_compra` (último costo por proveedor) y de `productos.precio_costo_centavos`
-- (costo vigente), para no duplicar fuentes de verdad.
--
-- `ON DELETE RESTRICT` en ambas FK, igual que compras: ni productos ni proveedores
-- se borran si hay un vínculo; se desactivan (baja lógica).
--
-- Máximo un principal por producto, garantizado por el índice único parcial.
--
-- Backfill desde las compras históricas: un vínculo por cada par
-- (producto, proveedor) que ya compró alguna vez. El principal de cada producto
-- es el proveedor de su compra de MAYOR `compras.id` (no de `compras.fecha`: es
-- texto con resolución de un segundo y puede empatar; el id es AUTOINCREMENT y
-- nunca se reutiliza). Si ese proveedor está inactivo, el vínculo se conserva.
-- Un producto sin compras no recibe ningún vínculo. No modifica compras.

CREATE TABLE IF NOT EXISTS producto_proveedor (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id       INTEGER NOT NULL REFERENCES productos(id) ON DELETE RESTRICT,
    proveedor_id      INTEGER NOT NULL REFERENCES proveedores(id) ON DELETE RESTRICT,
    codigo_proveedor  TEXT NULL CHECK (codigo_proveedor IS NULL OR length(trim(codigo_proveedor)) > 0),
    es_principal      INTEGER NOT NULL DEFAULT 0 CHECK (es_principal IN (0, 1)),
    fecha_creacion    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (producto_id, proveedor_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_producto_proveedor_principal
    ON producto_proveedor(producto_id) WHERE es_principal = 1;
CREATE INDEX IF NOT EXISTS idx_producto_proveedor_proveedor_id ON producto_proveedor(proveedor_id);

INSERT INTO producto_proveedor (producto_id, proveedor_id, es_principal)
SELECT par.producto_id,
       par.proveedor_id,
       CASE WHEN par.proveedor_id = (
                SELECT c2.proveedor_id
                FROM detalle_compra d2
                JOIN compras c2 ON c2.id = d2.compra_id
                WHERE d2.producto_id = par.producto_id
                ORDER BY c2.id DESC
                LIMIT 1
            )
            THEN 1 ELSE 0 END
FROM (
    SELECT DISTINCT d.producto_id, c.proveedor_id
    FROM detalle_compra d
    JOIN compras c ON c.id = d.compra_id
) AS par
WHERE NOT EXISTS (
    SELECT 1 FROM producto_proveedor x
    WHERE x.producto_id = par.producto_id AND x.proveedor_id = par.proveedor_id
)
ORDER BY par.producto_id, par.proveedor_id;
