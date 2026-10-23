# ROADMAP.md — Twinx Express V1.11–V1.18

> Roadmap vivo, no retrospectivo (el detalle de lo ya implementado por versión está en `README.md`,
> `LEEME.txt` y `PROGRESS.md`). Se actualiza a medida que cada versión se define o se cierra.
> V1.14 en adelante es **provisional**: orden y alcance pueden cambiar según lo que surja en V1.11–V1.13.

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

## V1.13 — a definir

---

### V1.14–V1.18 (provisional)

Sin alcance definido todavía. Candidatas ya mencionadas en `PROGRESS.md` como pendientes/fuera de
alcance (a priorizar cuando corresponda, no comprometidas a ninguna versión puntual):

- Lotes y vencimientos de productos (no de compras), promociones, proveedores con rating.
- Órdenes de compra persistentes, imputación de pagos a compras concretas, notificaciones de
  vencimientos, saldos a favor y devoluciones a proveedor.
- Backup automático/programado.
- Sincronizar `interfaces/cli/main.py` con los flujos de la web, o retirarlo.
