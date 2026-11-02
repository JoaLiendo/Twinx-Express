# ROADMAP.md — Twinx Express V1.11–V1.18

> Roadmap vivo, no retrospectivo (el detalle de lo ya implementado por versión está en `README.md`,
> `LEEME.txt` y `PROGRESS.md`). Se actualiza a medida que cada versión se define o se cierra.
> V1.11–V1.15 están cerradas; V1.16 es candidato a release (falta la homologación externa con ARCA); V1.17–V1.18 tienen el alcance acordado que se detalla abajo.

## V1.11 — Trazabilidad de ajustes de stock

- **V1.11-A (cerrado)**: nuevos motivos manuales de pérdida (`VENCIMIENTO`, `ROTURA`, `ROBO`,
  `FALLA_FABRICA`, todos solo-resta); `MERMA`/`PERDIDA`/`OTRO` pasan a legacy (solo lectura de
  historial); `RECUENTO` sigue siendo el código interno del ajuste de inventario físico (se
  muestra como "Ajuste de inventario" en UI); snapshot nullable de `costo_unitario_centavos` por
  ajuste (migración 027).
- **V1.11-B (cerrado)**: reversas explícitas y auditables de un ajuste manual erróneo (motivo
  `REVERSA`, `reversa_de_id`, una por original, solo OWNER; migración 028). RECUENTO no se revierte.
- **V1.11-C (cerrado)**: stock inicial trazable. Un alta con stock > 0 (web, importación CSV/XLSX,
  CLI) registra un movimiento `STOCK_INICIAL` en `ajustes_stock` (usuario y costo solo si existen
  realmente; migración 029). Productos históricos sin inferencias. Kardex: "Stock inicial".
- **V1.11-D (cerrado)**: reporte de pérdidas de stock (`/reportes/perdidas` + CSV): filtros por
  fecha, producto y motivo; costo histórico, responsable y reversa; totales vigentes, valor conocido
  a costo y filas con costo histórico desconocido (nunca valorizadas).
- **V1.11-E (cerrado)**: categorías jerárquicas (`categorias.categoria_padre_id`, sin ciclos por
  trigger, nombre único global; la interfaz admite hasta 3 niveles y filtrar por una categoría
  incluye sus subcategorías; un producto tiene una sola categoría de cualquier nivel) y etiquetas
  planas (`etiquetas`, `producto_etiquetas`; migración 030). Productos en tabla con filtros
  (texto/código, categoría, stock crítico/sin/con stock, etiqueta, activos/dados de baja; costo solo
  OWNER). Inventario por categoría (con o sin subcategorías). Dashboard con cuatro accesos (venta,
  consulta, mercadería o caja según rol, actividad); el CASHIER no recibe costos ni valorizaciones.
  Índice `ajustes_stock(fecha)` (migración 031). Kardex CSV con "Ajuste de inventario"/"Stock
  inicial" en vez de códigos internos. La actualización masiva de precios por categoría sigue siendo
  exacta (no incluye subcategorías).

## V1.12 — Partidas, vencimientos y liquidación

Diseño e invariantes: `docs/V1.12_PARTIDAS.md`.

- **V1.12-A (cerrado)**: `partidas_stock` + `movimientos_partida` (migración 032). Existencias previas
  como una partida `HEREDADA` por producto (sin vencimiento, costo ni fecha). Alta con stock, compra y
  su anulación, venta y su anulación, ajustes (pérdidas, reversas, RECUENTO) mueven partidas en la
  misma transacción y verifican `stock_actual = SUM(partidas)` (si no, se revierte todo). FEFO
  determinístico; anulación de venta y reversa devuelven exactamente a las mismas partidas; venta
  anterior a V1.12 anulada vuelve como partida `ANULACION_VENTA` sin datos inventados. La base
  impide escribir el disponible sin un movimiento y vuelve inmutables partidas y movimientos.
- **V1.12-B (cerrado)**: vencimiento de producto opcional al ingresar mercadería (una fecha, o varias
  por línea con `cantidad@fecha`; lo no asignado queda sin vencimiento conocido), validado contra el
  día de la compra, incluido en la comparación idempotente y visible en el detalle de la compra. La
  anulación retira primero las partidas de esa compra. Sin migración.
- **V1.12-C (cerrado)**: ventas FEFO con trazabilidad causal (un movimiento por línea de venta y
  partida, en la misma transacción). `detalle_venta.costo_unitario_centavos` conserva su semántica
  (costo vigente al vender: márgenes históricos comparables); el costo real de lo que salió queda en
  el libro y se muestra al OWNER en el detalle de la venta ("Partidas consumidas"). Sin migración.
- **V1.12-D (cerrado)**: aviso "Próximo a vencer" (10 días por defecto, global, configurable por
  OWNER y auditado) y "Vencido"; filtros del listado (próximo a vencer, vencido, sin vencimiento
  conocido) y próximo vencimiento por producto; alertas en el dashboard. Pantalla de vencimientos por
  producto (orden FEFO) con baja por VENCIMIENTO desde la partida, valorizada con su costo real (o
  sin costo si no se conoce, nunca el actual); en otras pérdidas se puede elegir la partida. Una venta
  que solo se completa con mercadería vencida exige autorización de un OWNER (el propio, o sus
  credenciales en el puesto de un CASHIER), auditada como `VENTA_VENCIDOS_AUTORIZADA`. Sin migración.
- **V1.12-E (cerrado)**: liquidación por vencimiento en `/precios/liquidacion` (OWNER): para lo
  próximo a vencer, precio sugerido = vigente menos un descuento objetivo, nunca por debajo de costo ×
  (1 + margen mínimo), con el costo real de las partidas que vencen (o el vigente, avisado). Parámetros
  locales configurables (20 % / 10 % por defecto). Nunca cambia un precio solo: el OWNER aplica,
  modifica o descarta; el cambio queda en el historial de precios (edición) y en la auditoría
  (`PRECIO_LIQUIDACION_APLICADO`), sin pisar un precio que cambió mientras tanto. Sin migración ni
  dependencias externas.
- **Auditoría de cierre (1.12.0)**: toda pérdida manual se valoriza con el costo de su partida cuando se
  conoce (elegida o única) y queda sin costo si hay varias sin elegir (nunca el vigente); la reversa de
  una baja anterior a V1.12 devuelve una partida sin costo inventado; lo vencido de un producto dado de
  baja se puede retirar desde su pantalla de vencimientos; un producto con partidas (ej. existencia
  heredada sin otro historial) se conserva como baja lógica sin borrar su imagen. Semántica de costos y
  limitaciones aceptadas en `docs/V1.12_PARTIDAS.md`. Sin migración.

## V1.13 — Proveedores

- **V1.13-A (cerrado)**: clasificación proveedor↔categorías (árbol existente, muchos-a-muchos, sin
  restringir compras) y datos fiscales opcionales con CUIT único (migración 033).
- **V1.13-B (cerrado)**: evaluaciones 1–5 inmutables con comentario obligatorio y criterios opcionales;
  calificación actual derivada; aviso no bloqueante al comprar a 1–2 estrellas (migración 034).
- **V1.13-C (cerrado)**: reclamos con estados auditables (transiciones y estado garantizados por la base),
  relaciones reales a producto/compra/línea/ajuste FALLA_FABRICA, sin tocar estrellas ni deuda (035).
- **V1.13-D (cerrado)**: documentación fiscal declarada (036), ficha por secciones y listado con atajos
  por dimensión (baja calificación, reclamos abiertos, deuda).

Limitaciones aceptadas de V1.12 en `docs/V1.12_PARTIDAS.md`; de V1.13, en `PROGRESS.md` (sección 2).

---

## V1.14 — Clientes y crédito (cerrado, release 1.14.0)

- **V1.14-A**: PERSONA / EMPRESA y condición fiscal independientes (migración 037); empresa con identidad
  completa y CUIT único; compradores autorizados (máx. 2 activos, historial por períodos). Históricos sin
  clasificar.
- **V1.14-B**: políticas de crédito GENERAL → TIPO → INDIVIDUAL (migración 038), sin crédito si no hay
  política; versión optimista; solo OWNER.
- **V1.14-C**: venta 100% contado, 100% crédito o mixta (efectivo + crédito) atómica; obligación causal por
  cada porción financiada (migración 039); comprador de empresa (autorizado o excepcional).
- **V1.14-D**: cobros con imputación FIFO persistida; imputación manual solo OWNER, observada y auditada.
- **V1.14-E**: estado objetivo por antigüedad (NORMAL/REGULAR/DEMORADO/CRÍTICA), autorización temporal de
  DEMORADO, cierre al entrar en CRÍTICA, reapertura explícita, un strike por episodio, veto permanente con
  3 strikes (migración 040), sin scheduler.
- **V1.14-F**: ficha por secciones y crédito en el POS.

Fuera de alcance (no implementado): intereses, recargos, refinanciaciones, cuotas, vencimientos por venta,
saldo a favor, cobranza automática, notificaciones externas, scoring, calificación subjetiva de clientes.

## V1.15 — Backups y actualizaciones (cerrada, release 1.15.0)

Sin migraciones nuevas. Reutiliza y endurece el backup/restore existente (no hay un sistema paralelo).

- **V1.15-A**: manifiesto en cada ZIP (versión, tipo, id, fecha, migraciones, integridad, SHA-256 por archivo)
  y validación completa antes de publicar (ZIP, nombres seguros, symlinks, tamaño, `integrity_check`,
  `foreign_key_check`, coherencia del manifiesto). El backup copia siempre la base activa.
- **V1.15-B**: backup `CIERRE_CAJA` tras confirmar el cierre; si falla la caja sigue cerrada y queda una alerta
  auditada hasta un backup correcto. Idempotente por sesión.
- **V1.15-C**: retención 14 diarios / 8 semanales / 12 mensuales (días, semanas ISO y meses *con backups*),
  10 manuales, 3 pre-update y 3 de seguridad; nunca borra archivos ajenos ni pre-update protegidos.
- **V1.15-D**: segundo destino opcional solo para copias, verificado por tamaño y SHA-256; un fallo nunca
  afecta al primario.
- **V1.15-E**: `PRE_UPDATE` validado obligatorio antes de migrar una base existente; versión de origen/destino
  en el manifiesto; una base de una versión más nueva no se abre.
- **V1.15-F**: `--actualizar`, `--rollback` y `--versiones` (técnico, app cerrada); rollback = programa
  archivado + su pre-update, con backup de seguridad previo; restore con compatibilidad de esquema, backup de
  seguridad y rechazo con la app abierta.

Fuera de alcance: actualización por Internet, integración con APIs de nube, rollback desde la web.

## V1.16 — Facturación fiscal ARCA (candidato a release; homologación externa pendiente)

- **V1.16-A** (041): perfil del emisor (producción bloqueada por trigger sin habilitación del técnico),
  tratamiento de IVA por producto en tabla aparte (sin clasificar lo histórico), comprobantes con snapshot
  inmutable, líneas, alícuotas e intentos; máquina de estados y unicidades exigidas por la base; clase A/B/C,
  umbral fechado de consumidor final (RG 5700/2025) y bloqueo de clase A con leyenda (RG 5762/2025, ex M).
- **V1.16-B**: certificados por ambiente con clave privada cifrada con DPAPI fuera de `data/` y backups;
  WSAA (TRA, CMS SignedData adjunto SHA-256, LoginCms, faults) con TA en memoria + copia DPAPI; transporte
  HTTPS con timeouts que distingue "no enviado" de "incierto"; producción bloqueada en tests.
- **V1.16-C** (042): cliente WSFEv1 según el WSDL vigente, caché de tablas `FEParam*` por ambiente con fuente y
  fecha, validación del punto de venta contra ARCA, gateway real y ARCA falso determinista para tests.
- **V1.16-D**: CAE con número de `FECompUltimoAutorizado`, ENVIANDO persistido antes del envío, INCIERTO
  conciliado con `FECompConsultar` (nunca reenvío ciego), bloqueo de la secuencia, recuperación al arrancar.
- **V1.16-E**: POS con estado fiscal, QR oficial, factura A4/80 mm/PDF, ticket interno «no válido como
  factura», historial y detalle, panel del dueño, permisos; `--habilitar-produccion-fiscal` por consola.
- **V1.16-F** (043): nota de crédito total con cancelación comercial aplicada una única vez y solo con la NC
  autorizada; la venta fiscalizada no se anula localmente.
- **V1.16-G** (044): CAEA real (solicitar/consultar, emisión solo si el CAE no se envió, informe posterior,
  sin movimiento, alertas persistentes).

Fuera de alcance: WSMTXCA, notas de débito, NC parciales/devoluciones parciales, clase A con leyenda, otras
monedas, exportación, multi-sucursal, «Otros Impuestos Nacionales Indirectos» discriminados. Para declararla
lista para producción falta el smoke real contra ARCA homologación con un certificado de homologación.

## V1.17 — Analítica

- Evolución de precios y costos.
- Costo real desde partidas.
- Margen bruto.
- Ventas por producto/período/temporada.
- Pérdidas.
- Deuda.
- Análisis de proveedores.
- Reposición sugerida determinística.
- Dashboard avanzado OWNER.

## V1.18 — Preparación comercial / piloto

- Instalación.
- Soporte.
- Ayuda/manual.
- Firma del ejecutable.
- Pruebas de recuperación.
- Hardening comercial.
- Release candidate para clientes reales.
