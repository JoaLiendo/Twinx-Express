-- Migración 005: imagen de producto (Fase 3D).
-- Aditiva: no modifica ni elimina ningún dato existente. Los productos
-- actuales quedan con imagen_archivo=NULL (sin imagen), su estado
-- implícito actual. No toca stock, ventas, caja, usuarios, categorías
-- ni unidad_medida.
--
-- Solo se guarda el nombre del archivo, nunca una ruta (ver
-- services/servicio_imagenes.py y config.DIRECTORIO_IMAGENES_PRODUCTOS).
-- Las imágenes en sí viven en disco (data/imagenes_productos/), no como
-- BLOB en esta tabla (ver auditoría de Fase 3D).

ALTER TABLE productos ADD COLUMN imagen_archivo TEXT NULL;
