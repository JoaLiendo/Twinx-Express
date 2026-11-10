# Cierre operativo — campaña `feature/cierre-roadmap-operativo`

Auditoría de alcance, decisiones y estado de cada requisito del roadmap y de las necesidades originales del
negocio. Base: `feature/v1.18-asistente-ia` en `b913b6a` (certificado). Versión candidata **1.18.0** para un
**piloto no fiscal**: sin release ni tag, sin homologación real con ARCA, sin firma de código.

## 1. Matriz de alcance

| Requisito | Ya existía | Faltaba | Riesgo | Cambio necesario | Test de aceptación |
|---|---|---|---|---|---|
| 1 Vencimientos configurables | `/configuracion/vencimientos`, alerta global auditada (V1.12-D) | — | — | Ninguno | `test_vencimientos_v112d.py` |
| 2 Liquidación próximos a vencer | `/precios/liquidacion`, nunca cambia un precio solo (V1.12-E) | — | — | Ninguno | `test_liquidacion.py` |
| 3 Bajas de stock y motivos | VENCIMIENTO/ROTURA/ROBO/FALLA_FABRICA, reversas, OTRO legacy solo lectura | Motivo adicional controlado | Un motivo genérico vacía de sentido el reporte de pérdidas; agregarlo exige reconstruir `ajustes_stock` (CHECK) | **Decisión conservada** (ver §3) | `test_ajuste_stock.py`, `test_migracion_027.py` |
| 4 Inventario | Conteo a ciegas, confirmación con RECUENTO (V1.4) | — | — | Ninguno | `test_inventario*.py` |
| 5 Categorías jerárquicas | Árbol de 3 niveles sin ciclos (V1.11-E) | — | — | Ninguno | `test_servicio_categorias.py` |
| 6 Búsqueda y listado | Filtros por texto, categoría, stock, vencimiento, etiqueta | — | — | Ninguno | `test_productos_lista_v111.py` |
| 7 Proveedores con categorías | Muchos-a-muchos con el árbol (V1.13-A) | — | — | Ninguno | `test_proveedores_clasificacion.py` |
| 8 Evaluación de proveedores | 1–5 inmutables, aviso al comprar (V1.13-B) | — | — | Ninguno | `test_evaluaciones_proveedor.py` |
| 9 Reclamos | Estados por trigger, vínculo a compra/ajuste (V1.13-C) | — | — | Ninguno | `test_reclamos_proveedor.py` |
| 10 Clientes particulares y empresas | PERSONA/EMPRESA, CUIT, condición fiscal (V1.14-A) | DNI de personas; reportes por cliente | Inferir DNI de históricos | Migración 050 (`dni` único, solo PERSONA, NULL histórico); búsqueda; `/reportes/clientes` + CSV | `test_clientes_dni_y_reporte.py` |
| 11 Compradores autorizados | Máx. 2 activos, historial (V1.14-A) | — | — | Ninguno | `test_clientes_identidad_v114.py` |
| 12 Crédito y cobranza | Políticas, estados, strikes, imputación (V1.14) | — | — | Ninguno | `test_politicas_credito.py`, `test_estado_credito.py` |
| 13 Evolución de costos y precios | Historial real + `/analitica/precios` (V1.17-B) | — | — | Ninguno | `test_servicio_analitica.py` |
| 14 Comparación de proveedores | `/analitica/proveedores` (costos por producto y proveedor) (V1.17-G) | Vista lado a lado por producto | Bajo | No implementada (candidata) | — |
| 15 Márgenes y rentabilidad | Margen bruto por producto/categoría/período con 3 bases (V1.17-C) | Resultado operativo | Presentar ganancia neta inexistente | `/analitica/resultado` (margen − gastos, siempre «estimado») | `test_resultado_operativo.py` |
| 16 Reposición determinística | Explicación por fila, sin compras automáticas (V1.17-H) | — | — | Ninguno | `test_reposicion*.py` |
| 17 Dashboard | 4 accesos + resumen ejecutivo (V1.17-I) | Gastos y resultado | Saturar | 2 indicadores en la fila de 30 días | `test_gastos_web.py::TestDashboard` |
| 18 Backups y recuperación | Cierre de caja, 24 h al abrir, pre-update, retención, segundo destino, restore/rollback (V1.15) | Backup por horario; bypass del bloqueo fiscal con una versión vieja | Prometer backups con la PC apagada; perder CAE en silencio | `servicio_backup_programado` (solo app abierta); `servicio_custodia` (E5) | `test_backup_programado.py`, `test_custodia_historia_fiscal.py` |
| 19 Facturación | WSAA/WSFEv1/CAEA, ticket interno «no válido como factura», panel (V1.16) | Estado de preparación legible, borrador, listado de autorizados | Dar por disponible lo no homologado; inventar CAE/QR | `servicio_preparacion`, `armar_borrador`, `/fiscal/listado`; ninguna llamada a ARCA real | `test_fiscal_cierre_web.py` |
| 20 Soporte | `/soporte` + diagnóstico ZIP (V1.18-B) | — | — | Ninguno | `test_soporte_web.py` |
| 21 Manual | `/ayuda` sincronizada con `docs/MANUAL.md` | Secciones nuevas | Desincronizar | Secciones Pedidos, Gastos, Facturación, desinstalación, backup programado | `test_soporte_web.py::test_las_secciones_del_manual...` |
| 22 Pedidos | Cascarón oculto | Todo el módulo | Reservar stock o cobrar al crear; vender dos veces | Migración 051 + servicio + POS precargado + conversión atómica | `test_pedidos.py`, E2E 22 |
| 23 Gastos operativos | Nada (solo egresos manuales de caja) | Todo el módulo | Doble conteo con mercadería; dos egresos por gasto | Migración 048 + servicio + rutas + CSV | `test_servicio_gastos.py`, `test_gastos_web.py`, E2E 22 |
| 24 Reportes de clientes | Deuda y cobranzas | Ventas, frecuencia, ticket promedio por cliente | Atribuir ventas anónimas | `/reportes/clientes` (solo ventas con cliente identificado) | `test_clientes_dni_y_reporte.py::TestReporteClientes` |
| 25 Identificación de comprobantes de compra | Solo `observaciones` | Tipo, número, fecha del proveedor | Confundir con el id interno; duplicar compras | Migración 049 + validación de duplicados + búsqueda + edición auditada | `test_comprobante_proveedor.py` |
| 26 Productos agotados / demanda no satisfecha | Filtro sin stock, dashboard, reposición | Eventos de demanda no satisfecha | Inventar «ingreso perdido» | No se registra (no hay dato real); candidato futuro | — |
| 27 Movimientos de caja | `/reportes/caja` por sesión, `/caja` sesión actual | Listado entre sesiones con filtro por origen | Bajo | No implementado (candidato) | — |

## 2. Qué se creó y qué se aprovechó

- **Reutilizado**: `registrar_movimiento_en_conexion` (caja) para el egreso de un gasto y su reintegro (mismo
  patrón que el origen de pago de compras, migración 047); `lineas_de_venta_por_dia` y `ResumenMargen` para el
  resultado operativo; el POS completo (`pos.js`, `/api/ventas`, `registrar_venta`) para convertir pedidos;
  `preparar_principal` (snapshot fiscal) para el borrador; `HistoriaFiscal`/`no_cubierta_por` para la custodia;
  `crear_backup` + retención + segundo destino para el backup programado; `Instalar.ps1` como molde del
  desinstalador; `limites_del_periodo`, `generar_csv`, componentes del design system.
- **Creado**: migraciones 048–051; `domain/gasto.py`, `domain/resultado.py`, `domain/pedido.py`;
  `db/repositorios/gastos.py`, `db/repositorios/pedidos.py`; `services/servicio_gastos.py`,
  `services/servicio_pedidos.py`, `services/servicio_backup_programado.py`, `services/fiscal/servicio_custodia.py`,
  `services/fiscal/servicio_preparacion.py`; rutas `gastos`, `pedidos`, `/analitica/resultado`,
  `/reportes/clientes`, `/fiscal/listado`, `/fiscal/ventas/{id}/borrador`, `/compras/{id}/comprobante`,
  `/backup/hora-programada`; `Desinstalar.ps1/.bat`; `KioscoApp.exe --aceptar-historia-fiscal`.

## 3. Decisiones y límites

- **Motivo de baja adicional (C1)**: se conserva la regla de V1.11-A. Un motivo «otro» controlado exigiría
  reconstruir `ajustes_stock` (migraciones 027–029 muestran el costo) y, sobre todo, volvería a admitir pérdidas
  que no dicen qué pasó. Alternativa documentada en el manual: inventario físico (RECUENTO) con observaciones.
- **Resultado operativo**: siempre «estimado». No descuenta impuestos, ajustes contables, pérdidas de stock ni
  unidades sin costo conocido; la mercadería se reconoce por su costo al vender, nunca por las compras, así que no
  hay doble conteo entre compras, costo de mercadería vendida y gastos.
- **Pedidos**: sin reserva de stock (la arquitectura no contempla una reserva real y documentada); el stock y el
  precio se validan al cobrar. La venta puede diferir del pedido (el cajero edita el carrito): el pedido registra lo
  pedido y la venta lo vendido. La venta de un pedido con cliente queda asociada al cliente también al contado.
- **Custodia fiscal (E5)**: cota inferior de lo conocido, refrescada al arrancar, al cerrar, tras cada cambio de
  estado fiscal y tras un restore; vive junto al material fiscal (fuera de `data/` y de los backups). Un backup
  anterior a la facturación sigue pudiendo restaurarse con esta versión (bloqueo de `servicio_restore`); el bypass
  demostrado (ejecutable archivado de una versión anterior) ya no pasa inadvertido: la app no inicia.
- **Facturación**: no se tocaron WSAA, WSFEv1, CAEA, ENVIANDO, INCIERTO ni conciliación. Nada llama a ARCA real;
  el ARCA falso solo vive en tests y en el servidor E2E, siempre en ambiente de homologación («SIN VALIDEZ FISCAL»).
- **Backup programado**: solo mientras la aplicación está abierta; ningún servicio ni tarea de Windows. El hilo
  espera el primer intervalo (60 s) antes de consultar la base y el lifespan lo detiene y espera al cerrar: nunca
  compite con la migración de arranque ni sobrevive al proceso que lo creó.
- **Desinstalador**: nunca borra datos; se niega sobre la carpeta de datos, la raíz de una unidad, el perfil del
  usuario o con la app abierta. Se usa el `Desinstalar.bat` **de la carpeta instalada** (quita la carpeta donde
  está, después de pedir «SI» y mostrarla). Funciona por doble clic aunque cmd y PowerShell estén parados dentro de
  esa carpeta (sale a `%TEMP%`, ejecuta todo en una línea ya leída y cierra el lote con `(goto) 2>nul`).
- **Borrador fiscal sin ARCA**: la vista previa no exige las tablas de ARCA en caché ni el punto de venta validado
  (requisitos para emitir, no para previsualizar); los lista en el propio borrador como «Para emitir este
  comprobante de verdad falta». Sigue rechazando lo que impide armar el comprobante: producto sin IVA clasificado,
  cliente asociado sin condición fiscal, venta anulada. La emisión real valida exactamente igual que antes.
- **Custodia fiscal, criterio conservador**: la 041 obliga a que PENDIENTE y DESCARTADO no tengan número, así que
  una conciliación INCIERTO → PENDIENTE no deja rastro comparable con lo custodiado. Si el proceso se corta justo
  entre el commit de esa conciliación y el refresco de la custodia, el próximo arranque se bloquea aunque no haya
  pérdida real. Se acepta: la dirección segura es bloquear, el mensaje lo explica y el técnico lo resuelve con
  `--aceptar-historia-fiscal` (auditado). Distinguirlo de una base vieja donde el comprobante todavía era PENDIENTE
  no es posible con los datos persistidos.

## 4. Pendientes externos (no son código)

| Pendiente | Bloquea | Depende de |
|---|---|---|
| Homologación real con ARCA (WSAA, FEDummy, puntos de venta, FECAESolicitar) con certificado del emisor | Facturar con validez fiscal | Trámite del comercio / contador |
| Habilitación de producción (`--habilitar-produccion-fiscal`) | Idem | Técnico, después de homologar |
| Certificado de firma de código (`docs/FIRMA_EJECUTABLE.md`) | Instalar sin advertencia de SmartScreen | Compra externa |
| Piloto de Ollama (`docs/PILOTO_OLLAMA.md`) | Solo la IA local opcional; el asistente funciona sin ella | Autorización para instalar |

## 5. Candidatos técnicos (no bloquean el piloto)

Comparación lado a lado de proveedores por producto; listado de movimientos de caja entre sesiones con filtro por
origen; eventos de demanda no satisfecha (solo si el negocio define cómo registrarlos sin inventar ventas);
intereses/recargos/cuotas (fuera de alcance de V1.14).

## 6. Certificación del candidato (2026-11-09/10) — CERRADA

Código ejecutable congelado en `a4eb799`, verificado en secuencia estricta y sin suites pesadas en paralelo. Para
la suite se usó `build/snapshot-a4eb799`, regenerado limpio con `git archive` (690 archivos). Entre `a4eb799` y el
commit que cierra esta certificación solo cambian `PROGRESS.md`, `docs/CHANGELOG.md` y
`docs/CIERRE_OPERATIVO.md`: no hay cambios ejecutables posteriores. Por eso JS, E2E, build, instalación, upgrade,
rollback y desinstalación, todos sobre `a4eb799`, siguen valiendo.

Hubo dos corridas previas de la suite completa:

- sobre `7b7ab09`: 6502 en verde, 0 fallos (1 h 10 min 40 s);
- sobre `a4eb799`: se cortó al 68 % porque el equipo se quedó sin memoria (4518 en verde y 0 fallos hasta ese
  punto). Se repitió completa en solitario.

| Verificación | Resultado |
|---|---|
| Suite Python completa (`pytest -p no:cacheprovider -q --no-header --tb=short`) sobre `a4eb799` | **6510 tests: 6510 passed, 0 failed, 0 errores, 0 warnings, 1 h 11 min 38 s** (2026-11-10, 00:01–01:13). Son los 6502 de `7b7ab09` más los 8 de regresión de la auditoría visual |
| JS (`npm run test:js`) | 82/82 |
| E2E desktop (`npm run test:e2e`) | 61/61 (1,8 min) |
| E2E responsive (`npm run test:e2e:responsive`) | 36/36 (55 s) |
| Build (`Construir_entregable.ps1`) | Válido: 308 archivos, 48,6 MB, 51 migraciones; sin bases, ZIP, `data\`, imágenes ni backups; el `twinx.css` del bundle es idéntico al versionado |
| `KioscoApp.exe --version` | «Twinx Express 1.18.0», sin crear datos |
| Primer arranque (`LOCALAPPDATA` aislado) | Base nueva con 51 migraciones; pide la configuración inicial; CSS y JS servidos sin red; 114 MB de memoria |
| Recorrido de instalación nueva (`build/cert118/recorrido.js`) | 21/21: configuración inicial, comercio, caja, categoría, producto, compra con comprobante del proveedor, cliente con DNI, pedido sin tocar stock, venta del pedido desde el POS, ticket «no válido como factura», gasto con efectivo, resultado operativo, backup manual, estado de preparación fiscal, perfil fiscal sin ARCA, borrador sin validez y sin comprobante fiscal creado, cierre de caja con backup, ninguna conexión fuera de 127.0.0.1 |
| Segundo arranque sobre los mismos datos | Inicia; venta, gasto, pedido, cliente y 42 productos conservados; `integrity_check` ok; sin violaciones de claves foráneas |
| Upgrade 1.17.0 → 1.18.0 (`build/cert118/certificar.ps1`, ejecutables reales) | `Instalar.ps1` detecta la instalación y usa `--actualizar`: backup `PRE_UPDATE`, 47 → 51 migraciones, 41 productos, `integrity_check` ok, 1.17.0 y 1.18.0 archivadas en `versiones\` |
| Rollback (`versiones\1.18.0\KioscoApp.exe --rollback 1.17.0`) | Programa 1.17.0, base de vuelta en 47 migraciones sin la tabla `gastos`, 41 productos, copia de seguridad de los datos reemplazados |
| Re-actualización a 1.18.0 | 51 migraciones, 41 productos |
| Desinstalación (`Desinstalar.ps1` sobre la carpeta de prueba) | Programa y acceso directo eliminados; base y 3 backups conservados e informados |
| Capturas | 200 PNG (25 pantallas × 1366×768, 1440×900, 1920×1080 y 375×812 × claro/oscuro) con Brave headless en `..\capturas-v1.18-cierre\` |

**Corregido durante la certificación** (cada corrección con su test):

- 10 tests desactualizados por las migraciones 048–051 (`d69a1fc`).
- El hilo del backup programado consultaba la base antes de su primer intervalo y sobrevivía al cierre (`faf9f63`).
- La desinstalación por doble clic dentro de la carpeta instalada fallaba: Windows no borra la carpeta en la que
  está parado un proceso (`904d807`).
- El borrador fiscal exigía las tablas de parámetros de ARCA. Ahora se genera sin conexión y lista lo que falta
  para emitir; la emisión real las sigue exigiendo (`4b07288`).
- Auditoría visual (`1b6712a`). En tema oscuro:
  - la pestaña activa de Analítica y Reportes no se distinguía;
  - los íconos de calendario y reloj quedaban negros;
  - la tarjeta de resultado negativo perdía el rojo.

  Además, el panel de Backup mostraba el tipo interno («automatico») y los porcentajes no tenían punto de miles.

**Observado y aceptado sin cambios** (bajo, en su mayoría preexistente):

- en 375 px los títulos largos de la cabecera se recortan con «…»;
- la fila «Total» del detalle de un pedido queda unos 4 px corrida respecto de sus columnas;
- el botón «×» para quitar una línea del pedido se ve tenue cuando hay una sola línea (está deshabilitado);
- los pedidos y gastos muestran la marca de tiempo en formato `AAAA-MM-DD hh:mm`, igual que las ventas.
