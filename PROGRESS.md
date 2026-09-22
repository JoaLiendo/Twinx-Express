# PROGRESS.md — Estado del proyecto

> Contexto ultra-resumido para retomar en la próxima sesión. Última actualización: 2026-09-22 (auditoría de estado + pulido de Fase A: identidad visual, firma de desarrolladores, esta actualización de documento).

## 1. Funcionalidad implementada y funcionando

- **Autenticación y sesiones**: login/logout reales (`interfaces/web/rutas/autenticacion.py`), cookie de sesión httponly, expiración vía `services.servicio_auth`.
- **Roles OWNER/CASHIER**: `requiere_rol` conectado como dependencia en todos los routers de la interfaz web (productos, ventas, caja, compras, proveedores, empleados, backup, pedidos/precios/reportes-cascarón). No es infraestructura sin usar: restringe accesos reales hoy.
- **Dark mode**: toggle persistente (`localStorage`) con fallback a `prefers-color-scheme`, aplicado en `base.html` y `login.html`.
- **Responsive**: layout adaptado a 375/768/1024/1440 (sidebar desktop, rail tablet, drawer mobile).
- **Productos**: alta, edición, baja lógica/eliminación física según tenga ventas asociadas, y **reactivación** de productos y categorías dados de baja.
- **Stock**: control de stock con alertas de stock crítico (dashboard y listado).
- **POS / Ventas**: carrito, cobro en efectivo con cálculo de vuelto, emisión de venta, idempotencia.
- **Caja**: apertura, cierre, movimientos, arqueo del día.
- **Compras**: alta y detalle de compras a proveedores, con impacto en stock.
- **Proveedores**: alta, edición, listado.
- **Categorías**: alta, edición, baja/reactivación.
- **Empleados / usuarios**: alta, edición, gestión de cuenta propia (cambio de contraseña).
- **Backup / restore**: backup manual desde la web (`/backup`, solo OWNER), coordinado con un lock de escrituras (`services/control_escrituras.py`) para evitar backups inconsistentes. Restore solo por CLI con la app cerrada (`KioscoApp.exe --restore <zip>`), con validación de estructura de zip e integridad de SQLite antes de aplicar.
- **Importación CSV/XLSX** de productos (`/productos/importar`), reutilizando la misma validación que el alta manual.
- **Impresión de ticket**: vista de ticket con estilos `@media print` y botón "Imprimir".

## 2. Pendiente / fuera de alcance actual

- **Pedidos**: solo cascarón visual (`interfaces/web/rutas/pedidos.py` + `templates/pedidos.html`), sin lógica de negocio. Protegido por rol OWNER, marcado "Próximamente" en la interfaz.
- **Precios**: solo cascarón visual, mismo estado que Pedidos.
- **Reportes**: solo cascarón visual, mismo estado que Pedidos.
- **Exportación de datos**: no existe (solo hay importación de productos).
- **Backup automático/programado**: no existe; el backup depende de que el OWNER lo dispare manualmente desde `/backup`.
- **CLI (`interfaces/cli/main.py`)**: interfaz secundaria que **no está sincronizada** con los flujos modernos de la web (editar/eliminar/importar productos, arqueo de caja, autenticación, roles, dark mode).
- **Sin paginación** en el listado de productos (no es problema al tamaño actual de catálogo de un kiosco).
- **Importación no actualiza productos existentes**, solo omite duplicados por código de barras (decisión de diseño, no bug).
- `sembrar_datos.py` no fue revisado en esta actualización (ver estado previo si se necesita).

## 3. Estado de tests

- Suite completa (`python -m pytest`): **668 tests, 0 fallos** (última corrida: 2026-09-22).
- Cobertura: `db/`, `domain/`, `services/` y rutas de la interfaz web (incluye autenticación, compras, proveedores, empleados, backup).
- Suite E2E (`tests_e2e/`, Playwright) no se ejecutó en esta actualización.
