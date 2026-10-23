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
