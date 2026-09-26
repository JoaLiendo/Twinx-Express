# PROGRESS.md — Estado del proyecto

> Contexto resumido para retomar en la próxima sesión. Última actualización: release Twinx Express 1.7.0 (2026-09-26). El detalle por versión está en `README.md` y las novedades para el usuario en `LEEME.txt`.

## 1. Funcionalidad implementada

- **Base (V1.0–V1.1)**: login/sesiones con roles OWNER/CASHIER, POS con cobro y vuelto, caja, productos y categorías (alta, edición, baja/reactivación, imágenes), compras y proveedores, empleados, importación CSV/XLSX, ticket imprimible, backup manual y restore por línea de comandos, modo oscuro y layout responsive.
- **V1.2**: historial de precios y actualización masiva, auditoría, ajustes de stock, anulación de ventas, reportes de ventas, exportación de productos, configuración del ticket.
- **V1.3–V1.4**: caja por sesiones, clientes y cuenta corriente, proveedores con relación producto-proveedor, inventario físico con conteo a ciegas.
- **V1.5–V1.6**: robustez numérica y de escala, reposición integrada con compras, reportes operativos (caja por sesión, compras por proveedor, deuda y cobranzas de cuenta corriente).
- **V1.7**: reporte de rotación de stock con índices de fecha (migración 23), anulación segura de compras con trazabilidad de costo (migración 24) y kardex / movimientos de stock por producto (sin tabla nueva).

## 2. Pendiente / fuera de alcance actual

- **Pedidos**: solo cascarón visual (`interfaces/web/rutas/pedidos.py`), sin lógica de negocio; oculto del menú.
- **Cuentas a pagar a proveedores** y **órdenes de compra** persistentes: diseñadas como candidatas de una versión futura, no implementadas.
- **Backup automático/programado**: no existe; solo el manual desde `/backup` y el preventivo previo a migrar una base existente.
- **CLI (`interfaces/cli/main.py`)**: interfaz secundaria no sincronizada con los flujos de la web.
- Anulación de ventas a cuenta corriente: no permitida (decisión de V1.3).
- El stock cargado al dar de alta un producto no es un movimiento propio: el kardex lo refleja dentro del saldo inicial reconstruido.

## 3. Estado de tests

- Suite completa (`python -m pytest`): **2999 tests, 0 fallos** al cierre de V1.7-C (24 migraciones).
- Cobertura: `db/`, `domain/`, `services/` y rutas de la interfaz web; E2E con Playwright (`npm run test:e2e`, `npm run test:e2e:responsive`) y pruebas JS (`tests_js/`).
