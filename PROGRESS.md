# PROGRESS.md — Estado del proyecto

> Contexto resumido para retomar en la próxima sesión. Última actualización: release Twinx Express 1.10.0 (2026-09-30). El detalle por versión está en `README.md` y las novedades para el usuario en `LEEME.txt`.

## 1. Funcionalidad implementada

- **Base (V1.0–V1.1)**: login/sesiones con roles OWNER/CASHIER, POS con cobro y vuelto, caja, productos y categorías (alta, edición, baja/reactivación, imágenes), compras y proveedores, empleados, importación CSV/XLSX, ticket imprimible, backup manual y restore por línea de comandos, modo oscuro y layout responsive.
- **V1.2**: historial de precios y actualización masiva, auditoría, ajustes de stock, anulación de ventas, reportes de ventas, exportación de productos, configuración del ticket.
- **V1.3–V1.4**: caja por sesiones, clientes y cuenta corriente, proveedores con relación producto-proveedor, inventario físico con conteo a ciegas.
- **V1.5–V1.6**: robustez numérica y de escala, reposición integrada con compras, reportes operativos (caja por sesión, compras por proveedor, deuda y cobranzas de cuenta corriente).
- **V1.7**: reporte de rotación de stock con índices de fecha (migración 23), anulación segura de compras con trazabilidad de costo (migración 24) y kardex / movimientos de stock por producto (sin tabla nueva).
- **V1.8**: compras paginadas y filtrables (proveedor, fechas, estado, producto), ficha de proveedor con estado de cada compra, historial de compras y última compra válida por producto, exportaciones CSV operativas (compras, ventas, deuda de clientes, rotación, kardex) y protección contra inyección de fórmulas en todas las exportaciones, incluido el CSV/XLSX de productos. Sin migraciones.
- **V1.9**: cuentas a pagar a proveedores (migración 025). Compras CONTADO/CREDITO con `CARGO_COMPRA` automático al saldo del proveedor; anulación de compra a crédito genera `REVERSA_COMPRA` si no tuvo pagos posteriores (bloqueo conservador si los tuvo); pagos a proveedor en efectivo (origen de caja `PAGO_PROVEEDOR`) o transferencia, con idempotencia y sobrepago bloqueado; historial/ficha de proveedor con saldo. Fuera de alcance: vencimientos, órdenes de compra, imputación de pagos a compras concretas, saldos a favor, devoluciones.
- **V1.10**: vencimientos de compras a crédito y deuda a proveedores (migración 026). `compras.fecha_vencimiento` opcional (alta y edición auditada, informativa; históricas `NULL`); FIFO causal de lectura como estimación por antigüedad (los pagos NO se imputan a compras concretas; no se persiste); clasificación vencida/próxima (7 días)/vigente/sin vencimiento; reporte `/reportes/deuda-proveedores` con filtros y CSV; ficha del proveedor con resumen, próximo vencimiento y compras abiertas; columna «Vence» en compras y pendiente estimado en el detalle; libro imposible informado como «Datos inconsistentes» sin cifras FIFO.

## 2. Pendiente / fuera de alcance actual

- **Pedidos**: solo cascarón visual (`interfaces/web/rutas/pedidos.py`), sin lógica de negocio; oculto del menú.
- **Órdenes de compra persistentes, imputación de pagos a compras concretas, notificaciones de vencimientos, saldos a favor y devoluciones a proveedor**: NO implementadas (candidatas de una versión futura).
- **Backup automático/programado**: no existe; solo el manual desde `/backup` y el preventivo previo a migrar una base existente.
- **CLI (`interfaces/cli/main.py`)**: interfaz secundaria no sincronizada con los flujos de la web.
- Anulación de ventas a cuenta corriente: no permitida (decisión de V1.3).
- El stock cargado al dar de alta un producto no es un movimiento propio: el kardex lo refleja dentro del saldo inicial reconstruido.

## 3. Estado de tests

- Suite completa (`python -m pytest`): **4253 tests, 0 fallos** al cierre de V1.10 (26 migraciones).
- Cobertura: `db/`, `domain/`, `services/` y rutas de la interfaz web; E2E con Playwright (`npm run test:e2e`, `npm run test:e2e:responsive`) y pruebas JS (`tests_js/`).
