-- Migración 041: modelo fiscal ARCA (V1.16-A).
--
-- Separa tres conceptos que nunca se confunden:
--   VENTA INTERNA (`ventas`, sin cambios)  ≠  COMPROBANTE FISCAL (`comprobantes_fiscales`)  ≠
--   AUTORIZACIÓN ARCA (`codigo_autorizacion` + `vencimiento_autorizacion`, solo en estados autorizados).
--
--   * `perfil_fiscal`: el emisor (una sola fila). Nace en HOMOLOGACION; pasar a PRODUCCION exige una
--     habilitación de técnico registrada para ese mismo CUIT (`habilitaciones_produccion_fiscal`), así
--     que ningún UPDATE "accidental" puede activar producción.
--   * `productos_fiscal`: tratamiento de IVA de cada producto (GRAVADO + alícuota ARCA, EXENTO,
--     NO_GRAVADO). La ausencia de fila es SIN_CLASIFICAR: los productos históricos no reciben ninguna
--     clasificación inventada. Tabla aparte para no tocar `productos` ni su dominio.
--   * `comprobantes_fiscales`: snapshot inmutable de emisor, receptor e importes; líneas y alícuotas en
--     tablas propias. Máquina de estados validada por trigger. Un comprobante autorizado nunca se edita,
--     nunca se borra, nunca cambia de número ni pierde su autorización.
--   * `intentos_fiscales`: cada operación contra ARCA (append-only, se cierra una sola vez). Nunca guarda
--     Token, Sign ni el sobre SOAP: solo el pedido fiscal canónico (su hash) y la respuesta sin secretos.
--
-- Invariantes de base (además de los del dominio):
--   1. estado autorizado => código de 14 dígitos, número y vencimiento.
--   2. UNIQUE lógico ambiente + CUIT + punto de venta + tipo + número (salvo rechazados/descartados,
--      cuyo número ARCA no consumió).
--   3. a lo sumo un comprobante PRINCIPAL vivo por venta.
--   4. RECHAZADO/DESCARTADO/PENDIENTE no tienen código.
--   5. a lo sumo un comprobante ENVIANDO o INCIERTO por secuencia (punto de venta + tipo): una
--      incertidumbre bloquea la siguiente emisión de esa secuencia.
--   6. una nota de crédito referencia un PRINCIPAL autorizado de la misma venta, ambiente, CUIT y clase;
--      a lo sumo una NC viva por comprobante (las NC de V1.16 son totales).
--   7. una venta con comprobante principal vivo no se anula localmente salvo que su NC esté autorizada.
--
-- Sin datos históricos que migrar: se verifica que ninguna venta, producto, cliente ni caja cambie.

DROP TABLE IF EXISTS temp._fallas;
DROP TABLE IF EXISTS temp._antes;
CREATE TEMP TABLE _fallas (motivo TEXT NOT NULL);
CREATE TEMP TRIGGER _abortar_por_falla AFTER INSERT ON _fallas
BEGIN
    SELECT RAISE(ABORT, 'Migración 041: verificación fallida: ' || NEW.motivo);
END;

INSERT INTO _fallas SELECT 'ya existe alguna tabla del modelo fiscal'
WHERE EXISTS (SELECT 1 FROM sqlite_master
              WHERE name IN ('perfil_fiscal', 'habilitaciones_produccion_fiscal', 'productos_fiscal',
                             'comprobantes_fiscales', 'comprobante_fiscal_lineas', 'comprobante_fiscal_alicuotas',
                             'intentos_fiscales'));
INSERT INTO _fallas SELECT 'foreign_key_check previo'
WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;

CREATE TEMP TABLE _antes AS
SELECT (SELECT COUNT(*) FROM ventas)                                   AS ventas_n,
       (SELECT COALESCE(SUM(total_centavos), 0) FROM ventas)           AS ventas_total,
       (SELECT group_concat(id || '|' || estado, ';') FROM (SELECT id, estado FROM ventas ORDER BY id)) AS ventas_estados,
       (SELECT COUNT(*) FROM detalle_venta)                            AS detalle_n,
       (SELECT COUNT(*) FROM productos)                                AS productos_n,
       (SELECT COALESCE(SUM(stock_actual), 0) FROM productos)          AS stock_total,
       (SELECT COUNT(*) FROM clientes)                                 AS clientes_n,
       (SELECT COUNT(*) FROM movimientos_cuenta)                       AS libro_n,
       (SELECT COUNT(*) FROM caja_movimientos)                         AS caja_n,
       (SELECT COALESCE(SUM(monto_centavos), 0) FROM caja_movimientos) AS caja_total;

-- 1. Emisor ---------------------------------------------------------------------------------------

CREATE TABLE perfil_fiscal (
    id                          INTEGER PRIMARY KEY CHECK (id = 1),
    cuit                        TEXT NOT NULL CHECK (length(cuit) = 11 AND cuit NOT GLOB '*[^0-9]*'),
    razon_social                TEXT NOT NULL CHECK (length(trim(razon_social)) > 0 AND length(razon_social) <= 150),
    condicion_iva               TEXT NOT NULL CHECK (condicion_iva IN ('RESPONSABLE_INSCRIPTO', 'MONOTRIBUTO', 'EXENTO')),
    domicilio                   TEXT NOT NULL CHECK (length(trim(domicilio)) > 0 AND length(domicilio) <= 200),
    ingresos_brutos             TEXT NULL CHECK (ingresos_brutos IS NULL
                                                 OR (length(trim(ingresos_brutos)) > 0 AND length(ingresos_brutos) <= 30)),
    inicio_actividades          TEXT NULL CHECK (inicio_actividades IS NULL OR date(inicio_actividades) = inicio_actividades),
    -- Clase A "con leyenda" (RG 5762/2025, reemplaza a la ex clase M): Twinx V1.16 no la emite.
    habilitacion_clase_a        TEXT NOT NULL DEFAULT 'NORMAL'
                                CHECK (habilitacion_clase_a IN ('NORMAL', 'OPERACION_SUJETA_A_RETENCION', 'PAGO_EN_CBU_INFORMADA')),
    punto_venta_cae             INTEGER NULL CHECK (punto_venta_cae IS NULL OR punto_venta_cae BETWEEN 1 AND 99998),
    -- Ambiente en el que ARCA confirmó (FEParamGetPtosVenta) el punto de venta; NULL = sin validar.
    punto_venta_cae_validado    TEXT NULL CHECK (punto_venta_cae_validado IS NULL
                                                 OR punto_venta_cae_validado IN ('HOMOLOGACION', 'PRODUCCION')),
    punto_venta_caea            INTEGER NULL CHECK (punto_venta_caea IS NULL OR punto_venta_caea BETWEEN 1 AND 99998),
    punto_venta_caea_validado   TEXT NULL CHECK (punto_venta_caea_validado IS NULL
                                                 OR punto_venta_caea_validado IN ('HOMOLOGACION', 'PRODUCCION')),
    contingencia_caea           INTEGER NOT NULL DEFAULT 0 CHECK (contingencia_caea IN (0, 1)),
    ambiente                    TEXT NOT NULL DEFAULT 'HOMOLOGACION' CHECK (ambiente IN ('HOMOLOGACION', 'PRODUCCION')),
    fecha_actualizacion         TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK (punto_venta_caea IS NULL OR punto_venta_cae IS NULL OR punto_venta_caea <> punto_venta_cae),
    CHECK (punto_venta_cae IS NOT NULL OR punto_venta_cae_validado IS NULL),
    CHECK (punto_venta_caea IS NOT NULL OR punto_venta_caea_validado IS NULL)
);

-- Registro de técnico (append-only): la única llave para que el perfil pase a PRODUCCION.
CREATE TABLE habilitaciones_produccion_fiscal (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    cuit                    TEXT NOT NULL CHECK (length(cuit) = 11 AND cuit NOT GLOB '*[^0-9]*'),
    huella_certificado      TEXT NOT NULL CHECK (length(huella_certificado) = 64),
    detalle                 TEXT NOT NULL CHECK (length(trim(detalle)) > 0 AND length(detalle) <= 250),
    fecha                   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TRIGGER trg_habilitaciones_produccion_inmutables BEFORE UPDATE ON habilitaciones_produccion_fiscal
BEGIN
    SELECT RAISE(ABORT, 'Una habilitación de producción fiscal no se modifica.');
END;

CREATE TRIGGER trg_habilitaciones_produccion_no_borrables BEFORE DELETE ON habilitaciones_produccion_fiscal
BEGIN
    SELECT RAISE(ABORT, 'Una habilitación de producción fiscal no se borra.');
END;

CREATE TRIGGER trg_perfil_fiscal_produccion_alta BEFORE INSERT ON perfil_fiscal
WHEN NEW.ambiente = 'PRODUCCION'
BEGIN
    SELECT RAISE(ABORT, 'El perfil fiscal nace en homologación: producción requiere la habilitación del técnico.');
END;

CREATE TRIGGER trg_perfil_fiscal_produccion_habilitada BEFORE UPDATE OF ambiente, cuit ON perfil_fiscal
WHEN NEW.ambiente = 'PRODUCCION'
     AND NOT EXISTS (SELECT 1 FROM habilitaciones_produccion_fiscal WHERE cuit = NEW.cuit)
BEGIN
    SELECT RAISE(ABORT, 'Producción fiscal no habilitada por el técnico para este CUIT.');
END;

-- Cambiar de ambiente o de punto de venta invalida la validación previa contra ARCA.
CREATE TRIGGER trg_perfil_fiscal_validacion_por_ambiente BEFORE UPDATE ON perfil_fiscal
WHEN (NEW.punto_venta_cae_validado IS NOT NULL AND NEW.punto_venta_cae_validado <> NEW.ambiente)
     OR (NEW.punto_venta_caea_validado IS NOT NULL AND NEW.punto_venta_caea_validado <> NEW.ambiente)
BEGIN
    SELECT RAISE(ABORT, 'La validación del punto de venta corresponde a otro ambiente: validalo de nuevo.');
END;

-- 2. Tratamiento de IVA de los productos -------------------------------------------------------------

CREATE TABLE productos_fiscal (
    producto_id          INTEGER PRIMARY KEY REFERENCES productos(id) ON DELETE RESTRICT,
    tratamiento_iva      TEXT NOT NULL CHECK (tratamiento_iva IN ('GRAVADO', 'EXENTO', 'NO_GRAVADO')),
    -- Id de la alícuota según FEParamGetTiposIva (solo GRAVADO).
    alicuota_iva_id      INTEGER NULL CHECK (alicuota_iva_id IS NULL OR alicuota_iva_id > 0),
    usuario_id           INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fecha_actualizacion  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK ((tratamiento_iva = 'GRAVADO' AND alicuota_iva_id IS NOT NULL)
           OR (tratamiento_iva <> 'GRAVADO' AND alicuota_iva_id IS NULL))
);

-- 3. Comprobantes fiscales -------------------------------------------------------------------------

CREATE TABLE comprobantes_fiscales (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    venta_id                    INTEGER NOT NULL REFERENCES ventas(id) ON DELETE RESTRICT,
    rol                         TEXT NOT NULL CHECK (rol IN ('PRINCIPAL', 'NOTA_CREDITO', 'NOTA_DEBITO')),
    comprobante_asociado_id     INTEGER NULL REFERENCES comprobantes_fiscales(id) ON DELETE RESTRICT,
    ambiente                    TEXT NOT NULL CHECK (ambiente IN ('HOMOLOGACION', 'PRODUCCION')),
    modalidad                   TEXT NOT NULL CHECK (modalidad IN ('CAE', 'CAEA')),
    caea_id                     INTEGER NULL,
    cuit_emisor                 TEXT NOT NULL CHECK (length(cuit_emisor) = 11 AND cuit_emisor NOT GLOB '*[^0-9]*'),
    punto_venta                 INTEGER NOT NULL CHECK (punto_venta BETWEEN 1 AND 99998),
    tipo_comprobante            INTEGER NOT NULL CHECK (tipo_comprobante > 0),
    clase                       TEXT NOT NULL CHECK (clase IN ('A', 'B', 'C')),
    numero                      INTEGER NULL CHECK (numero IS NULL OR numero BETWEEN 1 AND 99999999),
    fecha_comprobante           TEXT NULL CHECK (fecha_comprobante IS NULL OR date(fecha_comprobante) = fecha_comprobante),
    -- Hora real de generación (obligatoria bajo CAEA: FECAEARegInformativo.CbteFchHsGen).
    fecha_hora_generacion       TEXT NULL,
    concepto                    INTEGER NOT NULL CHECK (concepto > 0),
    moneda                      TEXT NOT NULL CHECK (length(moneda) = 3),
    cotizacion                  TEXT NOT NULL CHECK (length(cotizacion) BETWEEN 1 AND 21),
    doc_tipo                    INTEGER NOT NULL,
    doc_nro                     TEXT NOT NULL CHECK (doc_nro NOT GLOB '*[^0-9]*' AND length(doc_nro) BETWEEN 1 AND 20),
    condicion_iva_receptor_id   INTEGER NOT NULL CHECK (condicion_iva_receptor_id > 0),
    imp_total_centavos          INTEGER NOT NULL CHECK (imp_total_centavos >= 0),
    imp_tot_conc_centavos       INTEGER NOT NULL CHECK (imp_tot_conc_centavos >= 0),
    imp_neto_centavos           INTEGER NOT NULL CHECK (imp_neto_centavos >= 0),
    imp_op_ex_centavos          INTEGER NOT NULL CHECK (imp_op_ex_centavos >= 0),
    imp_trib_centavos           INTEGER NOT NULL CHECK (imp_trib_centavos >= 0),
    imp_iva_centavos            INTEGER NOT NULL CHECK (imp_iva_centavos >= 0),
    snapshot_emisor_json        TEXT NOT NULL CHECK (json_valid(snapshot_emisor_json)),
    snapshot_receptor_json      TEXT NOT NULL CHECK (json_valid(snapshot_receptor_json)),
    contenido_hash              TEXT NOT NULL CHECK (length(contenido_hash) = 64),
    estado                      TEXT NOT NULL CHECK (estado IN ('PENDIENTE', 'ENVIANDO', 'AUTORIZADO', 'RECHAZADO', 'INCIERTO',
                                                                'DESCARTADO', 'CAEA_PENDIENTE_INFORMAR', 'CAEA_INFORMADO',
                                                                'CAEA_INFORME_RECHAZADO')),
    resultado                   TEXT NULL CHECK (resultado IS NULL OR resultado IN ('A', 'R')),
    codigo_autorizacion         TEXT NULL,
    vencimiento_autorizacion    TEXT NULL CHECK (vencimiento_autorizacion IS NULL
                                                 OR date(vencimiento_autorizacion) = vencimiento_autorizacion),
    fecha_proceso               TEXT NULL,
    observaciones_json          TEXT NULL CHECK (observaciones_json IS NULL OR json_valid(observaciones_json)),
    errores_json                TEXT NULL CHECK (errores_json IS NULL OR json_valid(errores_json)),
    usuario_id                  INTEGER NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
    fecha_creacion              TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    fecha_actualizacion         TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK (imp_total_centavos = imp_tot_conc_centavos + imp_neto_centavos + imp_op_ex_centavos
                                + imp_trib_centavos + imp_iva_centavos),
    CHECK ((rol = 'PRINCIPAL' AND comprobante_asociado_id IS NULL)
           OR (rol <> 'PRINCIPAL' AND comprobante_asociado_id IS NOT NULL)),
    CHECK ((modalidad = 'CAE' AND caea_id IS NULL) OR (modalidad = 'CAEA' AND caea_id IS NOT NULL
                                                       AND fecha_hora_generacion IS NOT NULL)),
    -- Autorizado (CAE o CAEA): código real de 14 dígitos, número, fecha y vencimiento.
    CHECK (estado NOT IN ('AUTORIZADO', 'CAEA_PENDIENTE_INFORMAR', 'CAEA_INFORMADO', 'CAEA_INFORME_RECHAZADO')
           OR (codigo_autorizacion IS NOT NULL AND length(codigo_autorizacion) = 14
               AND codigo_autorizacion NOT GLOB '*[^0-9]*' AND numero IS NOT NULL
               AND fecha_comprobante IS NOT NULL AND vencimiento_autorizacion IS NOT NULL)),
    -- Sin autorización no hay código ni vencimiento: nunca un CAE a medias.
    CHECK (estado IN ('AUTORIZADO', 'CAEA_PENDIENTE_INFORMAR', 'CAEA_INFORMADO', 'CAEA_INFORME_RECHAZADO')
           OR (codigo_autorizacion IS NULL AND vencimiento_autorizacion IS NULL)),
    CHECK (estado NOT IN ('CAEA_PENDIENTE_INFORMAR', 'CAEA_INFORMADO', 'CAEA_INFORME_RECHAZADO') OR modalidad = 'CAEA'),
    CHECK (estado <> 'AUTORIZADO' OR modalidad = 'CAE'),
    CHECK (estado NOT IN ('PENDIENTE', 'DESCARTADO') OR numero IS NULL),
    CHECK (estado NOT IN ('ENVIANDO', 'INCIERTO') OR (numero IS NOT NULL AND fecha_comprobante IS NOT NULL)),
    CHECK (estado <> 'RECHAZADO' OR resultado IS NULL OR resultado = 'R')
);

CREATE INDEX idx_comprobantes_fiscales_venta ON comprobantes_fiscales(venta_id);
CREATE INDEX idx_comprobantes_fiscales_estado ON comprobantes_fiscales(estado);

CREATE UNIQUE INDEX idx_comprobantes_fiscales_numero
    ON comprobantes_fiscales(ambiente, cuit_emisor, punto_venta, tipo_comprobante, numero)
    WHERE numero IS NOT NULL AND estado NOT IN ('RECHAZADO', 'DESCARTADO');

CREATE UNIQUE INDEX idx_comprobantes_fiscales_principal_vivo
    ON comprobantes_fiscales(venta_id)
    WHERE rol = 'PRINCIPAL' AND estado NOT IN ('RECHAZADO', 'DESCARTADO');

CREATE UNIQUE INDEX idx_comprobantes_fiscales_secuencia_en_vuelo
    ON comprobantes_fiscales(ambiente, cuit_emisor, punto_venta, tipo_comprobante)
    WHERE estado IN ('ENVIANDO', 'INCIERTO');

CREATE UNIQUE INDEX idx_comprobantes_fiscales_nota_viva
    ON comprobantes_fiscales(comprobante_asociado_id, rol)
    WHERE comprobante_asociado_id IS NOT NULL AND estado NOT IN ('RECHAZADO', 'DESCARTADO');

CREATE TRIGGER trg_comprobantes_fiscales_alta BEFORE INSERT ON comprobantes_fiscales
BEGIN
    SELECT RAISE(ABORT, 'Un comprobante nace pendiente (CAE) o emitido bajo CAEA pendiente de informar.')
    WHERE NOT ((NEW.modalidad = 'CAE' AND NEW.estado = 'PENDIENTE')
               OR (NEW.modalidad = 'CAEA' AND NEW.estado = 'CAEA_PENDIENTE_INFORMAR'));
    SELECT RAISE(ABORT, 'Solo una venta activa puede fiscalizarse.')
    WHERE NEW.rol = 'PRINCIPAL' AND NOT EXISTS (SELECT 1 FROM ventas WHERE id = NEW.venta_id AND estado = 'ACTIVA');
    SELECT RAISE(ABORT, 'La nota referencia un comprobante principal autorizado de la misma venta, ambiente, emisor y clase.')
    WHERE NEW.rol <> 'PRINCIPAL' AND NOT EXISTS (
        SELECT 1 FROM comprobantes_fiscales o
        WHERE o.id = NEW.comprobante_asociado_id AND o.rol = 'PRINCIPAL' AND o.venta_id = NEW.venta_id
          AND o.ambiente = NEW.ambiente AND o.cuit_emisor = NEW.cuit_emisor AND o.clase = NEW.clase
          AND o.estado IN ('AUTORIZADO', 'CAEA_PENDIENTE_INFORMAR', 'CAEA_INFORMADO', 'CAEA_INFORME_RECHAZADO'));
END;

-- Máquina de estados: solo transiciones explícitas.
CREATE TRIGGER trg_comprobantes_fiscales_transicion BEFORE UPDATE OF estado ON comprobantes_fiscales
WHEN NEW.estado IS NOT OLD.estado
     AND (OLD.estado || '>' || NEW.estado) NOT IN (
        'PENDIENTE>ENVIANDO', 'PENDIENTE>DESCARTADO',
        'ENVIANDO>AUTORIZADO', 'ENVIANDO>RECHAZADO', 'ENVIANDO>INCIERTO', 'ENVIANDO>PENDIENTE',
        'INCIERTO>AUTORIZADO', 'INCIERTO>RECHAZADO', 'INCIERTO>PENDIENTE',
        'CAEA_PENDIENTE_INFORMAR>CAEA_INFORMADO', 'CAEA_PENDIENTE_INFORMAR>CAEA_INFORME_RECHAZADO',
        'CAEA_INFORME_RECHAZADO>CAEA_INFORMADO')
BEGIN
    SELECT RAISE(ABORT, 'Transición de estado fiscal no permitida.');
END;

-- El snapshot fiscal es inmutable desde que nace; un autorizado además conserva número, fecha y autorización.
CREATE TRIGGER trg_comprobantes_fiscales_snapshot_inmutable BEFORE UPDATE ON comprobantes_fiscales
WHEN NEW.id IS NOT OLD.id OR NEW.venta_id IS NOT OLD.venta_id OR NEW.rol IS NOT OLD.rol
     OR NEW.comprobante_asociado_id IS NOT OLD.comprobante_asociado_id OR NEW.ambiente IS NOT OLD.ambiente
     OR NEW.modalidad IS NOT OLD.modalidad OR NEW.caea_id IS NOT OLD.caea_id OR NEW.cuit_emisor IS NOT OLD.cuit_emisor
     OR NEW.punto_venta IS NOT OLD.punto_venta OR NEW.tipo_comprobante IS NOT OLD.tipo_comprobante
     OR NEW.clase IS NOT OLD.clase OR NEW.concepto IS NOT OLD.concepto OR NEW.moneda IS NOT OLD.moneda
     OR NEW.cotizacion IS NOT OLD.cotizacion OR NEW.doc_tipo IS NOT OLD.doc_tipo OR NEW.doc_nro IS NOT OLD.doc_nro
     OR NEW.condicion_iva_receptor_id IS NOT OLD.condicion_iva_receptor_id
     OR NEW.imp_total_centavos IS NOT OLD.imp_total_centavos OR NEW.imp_tot_conc_centavos IS NOT OLD.imp_tot_conc_centavos
     OR NEW.imp_neto_centavos IS NOT OLD.imp_neto_centavos OR NEW.imp_op_ex_centavos IS NOT OLD.imp_op_ex_centavos
     OR NEW.imp_trib_centavos IS NOT OLD.imp_trib_centavos OR NEW.imp_iva_centavos IS NOT OLD.imp_iva_centavos
     OR NEW.snapshot_emisor_json IS NOT OLD.snapshot_emisor_json
     OR NEW.snapshot_receptor_json IS NOT OLD.snapshot_receptor_json
     OR NEW.contenido_hash IS NOT OLD.contenido_hash OR NEW.usuario_id IS NOT OLD.usuario_id
     OR NEW.fecha_creacion IS NOT OLD.fecha_creacion
     OR NEW.fecha_hora_generacion IS NOT OLD.fecha_hora_generacion
BEGIN
    SELECT RAISE(ABORT, 'El snapshot de un comprobante fiscal no puede modificarse.');
END;

CREATE TRIGGER trg_comprobantes_fiscales_autorizado_inmutable BEFORE UPDATE ON comprobantes_fiscales
WHEN OLD.estado IN ('AUTORIZADO', 'CAEA_PENDIENTE_INFORMAR', 'CAEA_INFORMADO', 'CAEA_INFORME_RECHAZADO')
     AND (NEW.numero IS NOT OLD.numero OR NEW.fecha_comprobante IS NOT OLD.fecha_comprobante
          OR NEW.codigo_autorizacion IS NOT OLD.codigo_autorizacion
          OR NEW.vencimiento_autorizacion IS NOT OLD.vencimiento_autorizacion
          OR NEW.resultado IS NOT OLD.resultado OR NEW.fecha_proceso IS NOT OLD.fecha_proceso
          OR NEW.observaciones_json IS NOT OLD.observaciones_json OR NEW.errores_json IS NOT OLD.errores_json)
BEGIN
    SELECT RAISE(ABORT, 'Un comprobante fiscal autorizado no puede modificarse.');
END;

CREATE TRIGGER trg_comprobantes_fiscales_no_borrables BEFORE DELETE ON comprobantes_fiscales
BEGIN
    SELECT RAISE(ABORT, 'Los comprobantes fiscales no se borran.');
END;

CREATE TABLE comprobante_fiscal_lineas (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    comprobante_id            INTEGER NOT NULL REFERENCES comprobantes_fiscales(id) ON DELETE RESTRICT,
    orden                     INTEGER NOT NULL CHECK (orden >= 1),
    producto_id               INTEGER NULL REFERENCES productos(id) ON DELETE RESTRICT,
    codigo                    TEXT NOT NULL,
    descripcion               TEXT NOT NULL CHECK (length(trim(descripcion)) > 0),
    cantidad                  INTEGER NOT NULL CHECK (cantidad > 0),
    precio_unitario_centavos  INTEGER NOT NULL CHECK (precio_unitario_centavos >= 0),
    subtotal_centavos         INTEGER NOT NULL CHECK (subtotal_centavos = precio_unitario_centavos * cantidad),
    tratamiento_iva           TEXT NULL CHECK (tratamiento_iva IS NULL OR tratamiento_iva IN ('GRAVADO', 'EXENTO', 'NO_GRAVADO')),
    alicuota_iva_id           INTEGER NULL,
    UNIQUE (comprobante_id, orden)
);

CREATE TABLE comprobante_fiscal_alicuotas (
    comprobante_id            INTEGER NOT NULL REFERENCES comprobantes_fiscales(id) ON DELETE RESTRICT,
    alicuota_iva_id           INTEGER NOT NULL CHECK (alicuota_iva_id > 0),
    base_imponible_centavos   INTEGER NOT NULL CHECK (base_imponible_centavos >= 0),
    importe_centavos          INTEGER NOT NULL CHECK (importe_centavos >= 0),
    PRIMARY KEY (comprobante_id, alicuota_iva_id)
);

-- Líneas y alícuotas son parte del snapshot: se escriben una vez, nunca se editan ni se borran.
CREATE TRIGGER trg_comprobante_fiscal_lineas_inmutables BEFORE UPDATE ON comprobante_fiscal_lineas
BEGIN
    SELECT RAISE(ABORT, 'Las líneas de un comprobante fiscal no pueden modificarse.');
END;

CREATE TRIGGER trg_comprobante_fiscal_lineas_no_borrables BEFORE DELETE ON comprobante_fiscal_lineas
BEGIN
    SELECT RAISE(ABORT, 'Las líneas de un comprobante fiscal no se borran.');
END;

CREATE TRIGGER trg_comprobante_fiscal_lineas_solo_pendiente BEFORE INSERT ON comprobante_fiscal_lineas
WHEN NOT EXISTS (SELECT 1 FROM comprobantes_fiscales
                 WHERE id = NEW.comprobante_id AND estado IN ('PENDIENTE', 'CAEA_PENDIENTE_INFORMAR'))
BEGIN
    SELECT RAISE(ABORT, 'Las líneas se cargan al crear el comprobante.');
END;

CREATE TRIGGER trg_comprobante_fiscal_alicuotas_inmutables BEFORE UPDATE ON comprobante_fiscal_alicuotas
BEGIN
    SELECT RAISE(ABORT, 'Las alícuotas de un comprobante fiscal no pueden modificarse.');
END;

CREATE TRIGGER trg_comprobante_fiscal_alicuotas_no_borrables BEFORE DELETE ON comprobante_fiscal_alicuotas
BEGIN
    SELECT RAISE(ABORT, 'Las alícuotas de un comprobante fiscal no se borran.');
END;

CREATE TRIGGER trg_comprobante_fiscal_alicuotas_solo_pendiente BEFORE INSERT ON comprobante_fiscal_alicuotas
WHEN NOT EXISTS (SELECT 1 FROM comprobantes_fiscales
                 WHERE id = NEW.comprobante_id AND estado IN ('PENDIENTE', 'CAEA_PENDIENTE_INFORMAR'))
BEGIN
    SELECT RAISE(ABORT, 'Las alícuotas se cargan al crear el comprobante.');
END;

-- 4. Intentos contra ARCA -------------------------------------------------------------------------

CREATE TABLE intentos_fiscales (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    comprobante_id   INTEGER NULL REFERENCES comprobantes_fiscales(id) ON DELETE RESTRICT,
    operacion        TEXT NOT NULL CHECK (length(operacion) BETWEEN 3 AND 60),
    ambiente         TEXT NOT NULL CHECK (ambiente IN ('HOMOLOGACION', 'PRODUCCION')),
    correlacion      TEXT NOT NULL UNIQUE CHECK (length(correlacion) = 32),
    numero           INTEGER NULL,
    request_hash     TEXT NULL CHECK (request_hash IS NULL OR length(request_hash) = 64),
    fecha_inicio     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now', 'localtime')),
    fecha_fin        TEXT NULL,
    duracion_ms      INTEGER NULL CHECK (duracion_ms IS NULL OR duracion_ms >= 0),
    resultado        TEXT NULL CHECK (resultado IS NULL OR resultado IN ('A', 'R', 'P', 'O', 'INCIERTO', 'NO_ENVIADO',
                                                                        'ENCONTRADO', 'NO_ENCONTRADO', 'ERROR')),
    detalle_json     TEXT NULL CHECK (detalle_json IS NULL OR json_valid(detalle_json))
);

CREATE INDEX idx_intentos_fiscales_comprobante ON intentos_fiscales(comprobante_id);

CREATE TRIGGER trg_intentos_fiscales_cierre_unico BEFORE UPDATE ON intentos_fiscales
WHEN OLD.fecha_fin IS NOT NULL
     OR NEW.id IS NOT OLD.id OR NEW.comprobante_id IS NOT OLD.comprobante_id OR NEW.operacion IS NOT OLD.operacion
     OR NEW.ambiente IS NOT OLD.ambiente OR NEW.correlacion IS NOT OLD.correlacion OR NEW.numero IS NOT OLD.numero
     OR NEW.request_hash IS NOT OLD.request_hash OR NEW.fecha_inicio IS NOT OLD.fecha_inicio
     OR NEW.fecha_fin IS NULL OR NEW.resultado IS NULL
BEGIN
    SELECT RAISE(ABORT, 'Un intento fiscal solo se cierra una vez, con su resultado.');
END;

CREATE TRIGGER trg_intentos_fiscales_no_borrables BEFORE DELETE ON intentos_fiscales
BEGIN
    SELECT RAISE(ABORT, 'Los intentos fiscales no se borran.');
END;

-- 5. La venta fiscalizada no se anula localmente -----------------------------------------------------

CREATE TRIGGER trg_ventas_fiscalizada_no_anulable BEFORE UPDATE OF estado ON ventas
WHEN NEW.estado = 'ANULADA' AND OLD.estado <> 'ANULADA'
     AND EXISTS (SELECT 1 FROM comprobantes_fiscales p
                 WHERE p.venta_id = OLD.id AND p.rol = 'PRINCIPAL' AND p.estado NOT IN ('RECHAZADO', 'DESCARTADO')
                   AND NOT EXISTS (SELECT 1 FROM comprobantes_fiscales n
                                   WHERE n.comprobante_asociado_id = p.id AND n.rol = 'NOTA_CREDITO'
                                     AND n.estado IN ('AUTORIZADO', 'CAEA_PENDIENTE_INFORMAR', 'CAEA_INFORMADO',
                                                      'CAEA_INFORME_RECHAZADO')))
BEGIN
    SELECT RAISE(ABORT, 'La venta tiene un comprobante fiscal: se cancela con una nota de crédito, no anulándola.');
END;

-- 6. Verificación ---------------------------------------------------------------------------------

INSERT INTO _fallas SELECT 'cambiaron ventas, detalle, productos, stock, clientes, libro o caja'
WHERE (SELECT COUNT(*) FROM ventas) <> (SELECT ventas_n FROM _antes)
   OR (SELECT COALESCE(SUM(total_centavos), 0) FROM ventas) <> (SELECT ventas_total FROM _antes)
   OR (SELECT group_concat(id || '|' || estado, ';') FROM (SELECT id, estado FROM ventas ORDER BY id))
      IS NOT (SELECT ventas_estados FROM _antes)
   OR (SELECT COUNT(*) FROM detalle_venta) <> (SELECT detalle_n FROM _antes)
   OR (SELECT COUNT(*) FROM productos) <> (SELECT productos_n FROM _antes)
   OR (SELECT COALESCE(SUM(stock_actual), 0) FROM productos) <> (SELECT stock_total FROM _antes)
   OR (SELECT COUNT(*) FROM clientes) <> (SELECT clientes_n FROM _antes)
   OR (SELECT COUNT(*) FROM movimientos_cuenta) <> (SELECT libro_n FROM _antes)
   OR (SELECT COUNT(*) FROM caja_movimientos) <> (SELECT caja_n FROM _antes)
   OR (SELECT COALESCE(SUM(monto_centavos), 0) FROM caja_movimientos) <> (SELECT caja_total FROM _antes);
INSERT INTO _fallas SELECT 'se inventó una clasificación de IVA o un comprobante histórico'
WHERE EXISTS (SELECT 1 FROM productos_fiscal) OR EXISTS (SELECT 1 FROM comprobantes_fiscales)
   OR EXISTS (SELECT 1 FROM perfil_fiscal);
INSERT INTO _fallas SELECT 'foreign_key_check final'
WHERE (SELECT COUNT(*) FROM pragma_foreign_key_check) <> 0;
INSERT INTO _fallas SELECT 'integrity_check final'
WHERE (SELECT integrity_check FROM pragma_integrity_check) <> 'ok';

DROP TRIGGER temp._abortar_por_falla;
DROP TABLE temp._fallas;
DROP TABLE temp._antes;
