# Changelog — Twinx Express

Formato: una entrada por versión, de la más nueva a la más vieja. Lo que cada versión implementó en detalle está en
`README.md`; las novedades para el usuario final, en `LEEME.txt`.

## 1.17.0 — candidato (rama `dev/v1.17`)

Sin migraciones nuevas (la base sigue en la 047).

**Analítica del dueño (V1.17)**

- Nueva sección *Analítica* (solo OWNER, solo lectura): resumen de los últimos 30 días con decisiones a revisar,
  ventas por día/semana/mes con comparaciones y estacionalidad histórica, margen bruto por producto/categoría/período
  con costo de referencia, costo real de partidas y unidades sin costo conocido, evolución de precios y costos desde
  el historial, deuda de clientes por antigüedad y a proveedores por vencimiento con evolución de saldos, análisis
  de proveedores (compras, calificación, reclamos, costos, dependencia por categoría). CSV de margen y de precios.
- Pérdidas de stock: filtro por categoría y resúmenes por producto, categoría y mes.
- Reposición: cada sugerencia explica su cálculo (mínimo, rotación de 30 días, cobertura, vencimientos, calificación
  del proveedor). La regla por mínimo y la decisión del operador no cambian.
- Dashboard del dueño: bloque «Últimos 30 días» con ventas, margen, deuda y decisiones a revisar.

**Seguridad y operación**

- Política mínima de contraseñas, auditoría paginada y exportable, errores web legibles para el usuario.
- Instancia única por base de datos (lock del sistema operativo): una segunda ventana no toca los datos.

**Interfaz**

- Design system compilado y autohospedado: la aplicación ya no carga Tailwind desde Internet y abre sin
  conectividad. Componentes unificados (botones, tarjetas, campos, tablas, badges, navegación, KPI) en todas las
  pantallas, claro y oscuro.
- POS: navegación por teclado en la búsqueda manual (↑ ↓ eligen, Enter agrega, Esc vuelve al lector) y tabla con
  anchos fijos sin recortes. Productos e inventario con tablas de datos uniformes.

**Preparación comercial (V1.18, en esta misma rama)**

- `Instalar.bat` / `Instalar.ps1`: instalación nueva con accesos directos o actualización de una instalación
  existente a través de `KioscoApp.exe --actualizar` (backup previo verificado, copia de la versión anterior,
  migración con vuelta atrás automática). Nunca toca `%LOCALAPPDATA%\KioscoApp`.
- *Ayuda* integrada (manual completo, ambos roles) y *Soporte técnico* (OWNER): versión, ubicación de los datos,
  estado de la base y de los backups, últimos avisos del registro y diagnóstico exportable sanitizado.
- `KioscoApp.exe --version`.
- Procedimiento de firma del ejecutable documentado (`docs/FIRMA_EJECUTABLE.md`, `Firmar_entregable.ps1`); el
  entregable **no está firmado** hasta contar con un certificado de firma de código auténtico.

**Pendientes externos**: homologación fiscal con certificado real de ARCA (desde 1.16) y certificado de firma de
código.

## 1.16.0 — candidato a release (rc5, `dev/v1.16`)

Facturación fiscal ARCA (WSAA + WSFEv1, factura A/B/C con CAE, QR, PDF, notas de crédito, CAEA de contingencia,
otros impuestos nacionales indirectos). Migraciones 041–047. Homologación externa pendiente.

## 1.15.0 y anteriores

Ver `README.md` (secciones por versión) y `docs/ROADMAP.md`.
