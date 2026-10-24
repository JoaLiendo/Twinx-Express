# PROGRESS.md — Estado del proyecto

> Contexto resumido para retomar en la próxima sesión. Última actualización: release Twinx Express 1.13.0 (2026-10-24). El detalle por versión está en `README.md` y las novedades para el usuario en `LEEME.txt`.

## 1. Funcionalidad implementada

- **Base (V1.0–V1.1)**: login/sesiones con roles OWNER/CASHIER, POS con cobro y vuelto, caja, productos y categorías (alta, edición, baja/reactivación, imágenes), compras y proveedores, empleados, importación CSV/XLSX, ticket imprimible, backup manual y restore por línea de comandos, modo oscuro y layout responsive.
- **V1.2**: historial de precios y actualización masiva, auditoría, ajustes de stock, anulación de ventas, reportes de ventas, exportación de productos, configuración del ticket.
- **V1.3–V1.4**: caja por sesiones, clientes y cuenta corriente, proveedores con relación producto-proveedor, inventario físico con conteo a ciegas.
- **V1.5–V1.6**: robustez numérica y de escala, reposición integrada con compras, reportes operativos (caja por sesión, compras por proveedor, deuda y cobranzas de cuenta corriente).
- **V1.7**: reporte de rotación de stock con índices de fecha (migración 23), anulación segura de compras con trazabilidad de costo (migración 24) y kardex / movimientos de stock por producto (sin tabla nueva).
- **V1.8**: compras paginadas y filtrables (proveedor, fechas, estado, producto), ficha de proveedor con estado de cada compra, historial de compras y última compra válida por producto, exportaciones CSV operativas (compras, ventas, deuda de clientes, rotación, kardex) y protección contra inyección de fórmulas en todas las exportaciones, incluido el CSV/XLSX de productos. Sin migraciones.
- **V1.9**: cuentas a pagar a proveedores (migración 025). Compras CONTADO/CREDITO con `CARGO_COMPRA` automático al saldo del proveedor; anulación de compra a crédito genera `REVERSA_COMPRA` si no tuvo pagos posteriores (bloqueo conservador si los tuvo); pagos a proveedor en efectivo (origen de caja `PAGO_PROVEEDOR`) o transferencia, con idempotencia y sobrepago bloqueado; historial/ficha de proveedor con saldo. Fuera de alcance: vencimientos, órdenes de compra, imputación de pagos a compras concretas, saldos a favor, devoluciones.
- **V1.10**: vencimientos de compras a crédito y deuda a proveedores (migración 026). `compras.fecha_vencimiento` opcional (alta y edición auditada, informativa; históricas `NULL`); FIFO causal de lectura como estimación por antigüedad (los pagos NO se imputan a compras concretas; no se persiste); clasificación vencida/próxima (7 días)/vigente/sin vencimiento; reporte `/reportes/deuda-proveedores` con filtros y CSV; ficha del proveedor con resumen, próximo vencimiento y compras abiertas; columna «Vence» en compras y pendiente estimado en el detalle; libro imposible informado como «Datos inconsistentes» sin cifras FIFO.
- **V1.11**: trazabilidad de stock y catálogo (migraciones 027–031). Motivos de baja vigentes con snapshot de costo, reversas auditables (solo OWNER), `STOCK_INICIAL` trazable (sin inferir históricos), reporte de pérdidas con costo histórico, categorías jerárquicas (hasta 3 niveles en UI, sin ciclos) y etiquetas, productos en tabla con filtros, inventario por categoría, dashboard de cuatro accesos sin costos para CASHIER, índice `ajustes_stock(fecha)`. Detalle en `docs/ROADMAP.md`.
- **V1.12**: partidas de stock con libro de movimientos (migración 032; stock previo como partida `HEREDADA` sin datos inventados), vencimientos al ingresar mercadería, ventas FEFO trazables con anulación exacta, alertas próximo a vencer/vencido, baja por vencimiento desde la partida (también de productos dados de baja), venta de vencidos solo con autorización OWNER auditada, liquidación sugerida. Pérdidas valorizadas con el costo real de la partida o sin costo. Costo real de lo vendido: desde el libro (`detalle_venta.costo_unitario_centavos` es referencia). Detalle en `docs/V1.12_PARTIDAS.md`.
- **V1.13**: proveedores (migraciones 033–036). Clasificación muchos-a-muchos con el árbol de categorías existente, datos fiscales opcionales (CUIT único con dígito verificador), evaluaciones 1–5 inmutables con criterios opcionales y calificación actual derivada (aviso no bloqueante al comprar a 1–2 estrellas), reclamos con estados auditables por triggers y proveedor propuesto desde un ajuste FALLA_FABRICA solo si es identificable de verdad (partidas ELEGIDA/UNICA de un único proveedor), documentación fiscal declarada, ficha por secciones y listado con atajos por dimensión. Detalle en `README.md` («Proveedores (V1.13)»).

## 2. Pendiente / fuera de alcance actual

- **V1.14**: a definir (ver `docs/ROADMAP.md`). Limitaciones aceptadas de V1.12 en `docs/V1.12_PARTIDAS.md`. De V1.13: revertir un ajuste FALLA_FABRICA que ya originó un reclamo no lo bloquea ni lo marca en el reclamo (se resuelve cambiando el estado del reclamo); la documentación fiscal es solo metadata (sin archivos); no hay contador global de reclamos en el dashboard.
- **Pedidos**: solo cascarón visual (`interfaces/web/rutas/pedidos.py`), sin lógica de negocio; oculto del menú.
- **Órdenes de compra persistentes, imputación de pagos a compras concretas, notificaciones de vencimientos, saldos a favor y devoluciones a proveedor**: NO implementadas (candidatas de una versión futura).
- **Backup automático/programado**: no existe; solo el manual desde `/backup` y el preventivo previo a migrar una base existente.
- **CLI (`interfaces/cli/main.py`)**: interfaz secundaria no sincronizada con los flujos de la web.
- Anulación de ventas a cuenta corriente: no permitida (decisión de V1.3).
- Productos anteriores a V1.11 no tienen movimiento de stock inicial (no se infiere): su stock de alta sigue dentro del saldo inicial reconstruido del kardex.

## 3. Estado de tests

- Suite completa (`python -m pytest`): **4918 tests, 0 fallos** al cierre de V1.13 (36 migraciones). JS: 33 (`node --test tests_js/*.test.js`). E2E: 33 desktop + 16 responsive.
- Cobertura: `db/`, `domain/`, `services/` y rutas de la interfaz web; E2E con Playwright (`npm run test:e2e`, `npm run test:e2e:responsive`) y pruebas JS (`tests_js/`).
