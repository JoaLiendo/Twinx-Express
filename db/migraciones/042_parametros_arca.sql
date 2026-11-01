-- Migración 042: caché local de las tablas de parámetros de ARCA (V1.16-C).
--
-- ARCA publica sus tablas (tipos de comprobante, documento, IVA, monedas, condición IVA del receptor...)
-- por los métodos FEParamGet* de WSFEv1. Twinx no las copia en el código: guarda la última versión
-- obtenida, por ambiente, con su fecha y fuente. Si un refresco falla se sigue usando la copia vigente;
-- sin ninguna copia, la fiscalización se bloquea (no se adivina una tabla).

CREATE TABLE parametros_arca (
    ambiente             TEXT NOT NULL CHECK (ambiente IN ('HOMOLOGACION', 'PRODUCCION')),
    tabla                TEXT NOT NULL CHECK (length(tabla) BETWEEN 3 AND 60),
    contenido_json       TEXT NOT NULL CHECK (json_valid(contenido_json) AND json_type(contenido_json) = 'array'),
    fuente               TEXT NOT NULL CHECK (length(fuente) BETWEEN 3 AND 120),
    fecha_actualizacion  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (ambiente, tabla)
);
