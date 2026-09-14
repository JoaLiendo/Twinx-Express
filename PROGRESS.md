# PROGRESS.md — Estado del proyecto

> Contexto ultra-resumido para retomar en la próxima sesión. Última actualización: sesión del 2026-09-11 (salida de fase Beta: CRUD completo, importación masiva, arqueo de caja).

## 1. Funcionalidad que quedó funcionando

- **CRUD completo de productos**: alta (ya existía) + **editar** (`GET/POST /productos/{id}/editar`, no toca stock) + **eliminar** (`POST /productos/{id}/eliminar`).
  - Eliminar hace **DELETE físico** si el producto no tiene ventas; si tiene, hace **baja lógica** (`activo=0`) para no romper el historial de ventas (FK `ON DELETE RESTRICT`).
  - Productos con `activo=0` quedan invisibles para listados/búsquedas/venta (como si no existieran).
- **Importación masiva** de productos vía `.csv` o `.xlsx`: `GET/POST /productos/importar`. Reutiliza `servicio_stock.registrar_producto` fila por fila → misma validación que el alta manual. Omite códigos de barras duplicados y reporta errores por fila sin abortar el resto. Requiere `openpyxl` (agregado a `requirements.txt`).
- **Arqueo de caja del día**: `GET /caja/arqueo` — total vendido hoy, ventas en efectivo, efectivo estimado (apertura + ingresos + ventas efectivo − egresos) y detalle de ventas del día.
- **Alertas de stock crítico**: ya existían en dashboard/lista; se agregó banner adicional en el listado de productos.
- **Sistema de migraciones versionadas**: `db/conexion.py` ahora aplica todos los `.sql` de `db/migraciones/` una sola vez cada uno (tabla `schema_migraciones`), en vez de re-ejecutar siempre el mismo script único. Permite `ALTER TABLE` no idempotentes a futuro.
- Todo probado manualmente end-to-end contra la app real levantada (crear/editar/eliminar/baja lógica/importar/arqueo) y con 91 tests automatizados en verde (`python -m pytest`).

## 2. Pendientes abiertos (no se tocaron / quedaron fuera de alcance)

- **`sembrar_datos.py` está roto**: referencia `domain.modelos.Producto` y `repositorio_productos.guardar`, que no existen (la API real es `domain.producto.Producto` y `crear_producto`). No se arregló por estar fuera del pedido de esta sesión.
- **No hay reactivación** de un producto dado de baja lógica (una vez desactivado, no hay UI para volver a activarlo).
- **CLI (`interfaces/cli/main.py`) no tiene** los flujos nuevos (editar/eliminar/importar/arqueo): solo se implementó en la interfaz web, como pidió el usuario.
- **Sin tests de las rutas FastAPI/templates**: los tests nuevos cubren la capa `services/`; las rutas y templates solo se verificaron manualmente con curl en esta sesión.
- **Importación no actualiza productos existentes**, solo los omite como duplicado (decisión de diseño, podría pedirse "upsert" a futuro).
- **Sin paginación** en el listado de productos (no es problema al tamaño actual de catálogo de un kiosco).
- Bases de datos `data/kiosco.db` preexistentes en otras máquinas necesitan correr la migración (pasa automático al levantar la app vía `inicializar_base_datos()` en el lifespan de FastAPI).

## 3. Archivos principales modificados/creados

**Dominio / excepciones**
- `excepciones.py` (+`ArchivoImportacionInvalidoError`)
- `domain/producto.py` (+campo `activo`)

**Datos**
- `db/migraciones/002_productos_activo.sql` (nuevo)
- `db/conexion.py` (runner de migraciones versionadas)
- `db/repositorios/productos.py` (+`obtener_por_id`, `eliminar_producto`, filtros `activo=1`)
- `db/repositorios/ventas.py` (+`listar_ventas_del_dia`)
- `db/repositorios/caja.py` (+`listar_movimientos_del_dia`)

**Servicios**
- `services/servicio_stock.py` (+`obtener_por_id`, `actualizar_producto`, `eliminar_producto`)
- `services/servicio_caja.py` (+`ArqueoCaja`, `calcular_arqueo_del_dia`)
- `services/servicio_importacion.py` (nuevo)

**Web**
- `interfaces/web/rutas/productos.py` (+editar, eliminar, importar)
- `interfaces/web/rutas/caja.py` (+arqueo)
- `interfaces/web/templates/productos/{lista,nuevo}.html` (editados), `{editar,importar}.html` (nuevos)
- `interfaces/web/templates/caja/panel.html` (editado), `arqueo.html` (nuevo)

**Config / tests**
- `requirements.txt` (+`openpyxl`)
- `tests/test_servicio_stock.py`, `tests/test_servicio_caja.py` (casos nuevos)
- `tests/test_services/test_servicio_importacion.py` (nuevo)
