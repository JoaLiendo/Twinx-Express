# Changelog — Twinx Express

Formato: una entrada por versión, de la más nueva a la más vieja. Lo que cada versión implementó en detalle está en
`README.md`; las novedades para el usuario final, en `LEEME.txt`.

## 1.18.0 — candidato de cierre operativo para PILOTO NO FISCAL (rama `feature/cierre-roadmap-operativo`)

Migraciones 048–051 (gastos, comprobante del proveedor, DNI de clientes, pedidos). Sin release ni tag: la
homologación real con ARCA y la firma del ejecutable siguen pendientes y externas.

- **Gastos operativos y resultado**: módulo *Gastos* (OWNER) con categorías fijas, importes en centavos, medio de
  pago, referencia, responsable y observaciones; pagado con efectivo de la caja genera el egreso en la misma
  transacción (idempotente, un egreso por gasto, reintegro opcional al anular); lo económico es inmutable, lo
  descriptivo se corrige auditado. *Analítica › Resultado*: ventas, anulaciones, margen bruto, gastos por categoría y
  **resultado operativo estimado** por período, con CSV; indicadores en el dashboard. Nunca se presenta como
  ganancia neta.
- **Pedidos de clientes**: módulo operativo para ambos roles (antes era un cascarón): tomar, listar, ver, cancelar con
  motivo y **vender desde el POS** con el carrito precargado; el pedido queda convertido en esa venta en la misma
  transacción y no se vende dos veces. No reserva ni descuenta stock, no cobra ni factura.
- **Compras**: tipo, número y fecha del comprobante del proveedor (sin duplicados activos por proveedor, edición
  auditada, filtro por referencia, columnas en el CSV). Nunca se confunde con el id interno.
- **Clientes**: DNI opcional en personas (único, sin inferir históricos; búsqueda) y reporte *Ventas por cliente*
  (ventas, ticket promedio, frecuencia, deuda y cobros).
- **Facturación sin homologación**: panel *Fiscal* con **Estado de preparación** (qué falta y quién lo resuelve, sin
  consultar ARCA), **borrador** por venta marcado «BORRADOR — SIN VALIDEZ FISCAL» (sin número, CAE ni QR; no
  avanza numeración), listado informativo de comprobantes autorizados con CSV («no es el Libro IVA Ventas») y
  **custodia de la historia fiscal** fuera de `data/`: si la base se reemplaza por una copia anterior fuera del
  restore seguro, la aplicación no inicia (`KioscoApp.exe --aceptar-historia-fiscal` para el técnico, auditado).
  El ticket interno sigue diciendo «COMPROBANTE INTERNO · NO VÁLIDO COMO FACTURA».
- **Backup**: backup diario a una hora fija, solo mientras la aplicación está abierta (sin servicio ni tarea de
  Windows).
- **Desinstalación**: `Desinstalar.bat` / `Desinstalar.ps1` quitan el programa y los accesos directos sin tocar los
  datos, e informan dónde quedan.
- **Manual y ayuda** actualizados y sincronizados; menú con *Pedidos* (ambos roles) y *Gastos* (OWNER).
- **Correcciones de la certificación**: la desinstalación por doble clic dentro de `C:\KioscoApp` fallaba (Windows
  no borra la carpeta en la que está parado un proceso) y ahora funciona; el borrador fiscal se genera sin conexión
  con ARCA y lista lo que falta para emitir; el hilo del backup programado espera antes de consultar y se detiene al
  cerrar; tests desactualizados por las migraciones 048–051 alineados y huecos de cobertura cerrados (precio
  cambiado entre pedido y venta, recorrido del cajero, custodia ante una copia con el comprobante todavía incierto).
  Auditoría visual: en tema oscuro la pestaña activa de Analítica y Reportes vuelve a distinguirse, los íconos de
  calendario y reloj se ven y el resultado negativo sale en rojo; el panel de Backup nombra el tipo del último
  backup en castellano («automático», «cierre de caja») y los porcentajes llevan punto de miles.

## 1.17.0 — candidato (rama `dev/v1.17`)

Sin migraciones nuevas (la base sigue en la 047).

**Twinx Asistente (V1.18, rama `feature/v1.18-asistente-ia`)**

- Panel lateral de chat de solo lectura en todas las pantallas (botón *Asistente*), para ambos roles: responde
  cuánto se vendió, qué se vendió más, qué vence, qué reponer, márgenes negativos, reclamos de proveedores, deuda
  de clientes y a proveedores, y cómo hacer algo, con accesos a la pantalla relacionada. Historial corto solo en la
  pestaña del navegador. Las preguntas frecuentes se responden con la *ayuda integrada* (sin IA) y datos reales.
- Herramientas de consulta con argumentos validados, filas acotadas y permisos por rol decididos en el backend: el
  cajero no obtiene costos, márgenes, reposición ni proveedores aunque los pida por el chat.
- Motor de IA local opcional (Ollama en 127.0.0.1, sin dependencias nuevas): detección, modelo, timeout y contexto
  configurables en *Configuración › Asistente*; conexión exclusivamente local, sin telemetría ni descargas. Con el
  motor apagado el asistente sigue funcionando y lo dice. Detalle en `docs/ASISTENTE.md`.
- Auditoría adversaria del asistente: el adaptador de Ollama ya no usa el proxy del sistema ni sigue
  redirecciones (garantía técnica de «solo local»), valida puertos y caracteres de la URL, tolera respuestas
  malformadas o cortadas sin dar 500, limita a tres las llamadas por turno y rechaza argumentos que no son un
  objeto; una sola conversación con la IA por vez y un solo sondeo del motor a la vez; el historial de la pestaña
  se borra al cambiar de usuario y en el login; el saneado quita caracteres invisibles (bidi, ancho cero). Detección
  de la capacidad `tools` del modelo por `/api/show`. Plan y script del piloto en `docs/PILOTO_OLLAMA.md` y
  `scripts/piloto_ollama.py`; integración propuesta en `docs/INTEGRACION_V1.17_V1.18.md`.

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
