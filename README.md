# Twinx Express App

Sistema de control de stock para un kiosco. Fase Beta local: Python + SQLite,
con soporte para lector de código de barras y búsqueda manual, y control
básico de caja.

Ver `CLAUDE.md` para las directrices de desarrollo del proyecto.

La versión vigente de la aplicación (Twinx Express) está definida en `version.py`.

Los precios y montos de dinero se manejan como **enteros en centavos**
(`int`), nunca `float`, para evitar errores de precisión binaria en
cálculos financieros. Ver `domain/dinero.py`.

## Requisitos

- Python 3.14 (versión con la que se desarrolló y se probó el proyecto).
- `pip` para instalar dependencias.
- Node.js (probado con Node 24) — solo necesario para los tests E2E con Playwright.
- SQLite viene incluido en la librería estándar de Python: no requiere
  instalación ni servidor de base de datos aparte.

## Instalación

Clonar el repositorio e instalar las dependencias de runtime:

```
git clone <url-del-repositorio>
cd stock_app
pip install -r requirements.txt
```

Esto es suficiente para correr la aplicación. Para desarrollo (tests) o para
generar el ejecutable con PyInstaller, ver las secciones correspondientes
más abajo.

## Cómo correr la aplicación en desarrollo

CLI:

```
python -m interfaces.cli.main
```

Interfaz web (FastAPI + Tailwind):

```
python -m interfaces.web.app
```

o, para desarrollo con recarga automática:

```
uvicorn interfaces.web.app:app --reload
```

En ambos casos la interfaz web queda disponible en:

```
http://127.0.0.1:8000
```

Ambas formas inicializan la base de datos automáticamente si no existe
(`data/kiosco.db`).

## Cómo correr los tests

`pytest` es una dependencia de desarrollo, separada de las dependencias de
runtime de `requirements.txt`. Instalarla:

```
pip install -r requirements-dev.txt
```

Correr la suite:

```
python -m pytest
```

Toda la suite corre contra una base de datos SQLite temporal y aislada por
test (ver `tests/conftest.py`): nunca toca `data/kiosco.db`.

## Tests E2E (Playwright, Fase 5E)

Requieren Node.js (probado con Node 24). Desde `stock_app/`:

```
npm install
npx playwright install chromium
npm run test:e2e
```

Cada corrida levanta un servidor FastAPI real en `http://127.0.0.1:8130`
contra una base de datos SQLite temporal y descartable en un subdirectorio
único generado por ejecución (`tests_e2e/.tmp/<uuid>/e2e.db`). Nunca
reutiliza ni pisa la DB temporal de una corrida anterior — y nunca usa ni
modifica `data/kiosco.db`.

`npm run test:e2e:ui` abre el modo interactivo de Playwright para
depurar un test paso a paso.

`npm run test:e2e:responsive` ejecuta los tests de layout responsive
(`07-responsive.spec.js`) contra los 4 viewports: `mobile-375`,
`tablet-768`, `desktop-1024` y `desktop-1440`. `npm run test:e2e` no
incluye los tests responsive.

## Build del ejecutable (PyInstaller)

PyInstaller es una dependencia de **build**, separada de las dependencias de
runtime de `requirements.txt` (no la necesita quien solo corre la app con
`python -m interfaces.web.app`). Su versión queda fijada en
`requirements-build.txt`.

Instalar la dependencia de build:

```
pip install -r requirements-build.txt
```

Generar el entregable con el script único de build (desde la raíz del repo):

```
powershell -ExecutionPolicy Bypass -File .\Construir_entregable.ps1
```

El script borra solo `build\KioscoApp` y `dist\KioscoApp`, ejecuta PyInstaller
con `KioscoApp.spec`, copia `LEEME.txt` y `Restaurar_backup.bat` junto al
ejecutable y **valida el resultado**: falla (código de salida distinto de 0) si
`dist\KioscoApp` no tiene el `.exe`, `_internal`, los archivos de entrega y las
migraciones completas, o si contiene bases de datos (`*.db`, `*.sqlite`),
ZIPs, `data\`, `imagenes_productos\` o `backups\`. El entregable queda en
`dist\KioscoApp\` (`KioscoApp.exe` junto con `_internal\`); otras carpetas dentro
de `dist\` no forman parte de él.

`data/` **nunca** forma parte del build: los datos de cada cliente viven en
`%LOCALAPPDATA%\KioscoApp\data\` y se crean en su primera ejecución. Un `data/`
embebido dentro del bundle se ignora, nunca se copia a la instalación del cliente.

## Imágenes de productos

Cada producto puede tener una imagen opcional (JPG, PNG o WEBP, máximo 2MB,
validada por la firma real del archivo — magic bytes —, nunca solo por su
extensión o por el `Content-Type` que mande el navegador). Se guardan como
archivos sueltos en `data/imagenes_productos/` (nunca como BLOB dentro de
`kiosco.db`); la base solo guarda el nombre del archivo generado por el
servidor, nunca una ruta ni el nombre original subido.

Reemplazar una imagen guarda la nueva primero y recién borra la anterior si
esa escritura tuvo éxito, para no dejar un producto sin imagen ante una
falla a mitad de camino. Dar de baja un producto (lógica o reactivándolo)
conserva su imagen; eliminarlo físicamente borra también el archivo.

**Backup**: como las imágenes no viven dentro de `kiosco.db`, un backup
completo de la aplicación debe incluir también la carpeta
`data/imagenes_productos/`, no solo el archivo `.db`.

## Backup y restore

**Backup**: desde la propia aplicación web, en `/backup` (solo accesible para el
rol OWNER; no tiene enlace en el menú: se abre escribiendo la dirección
`http://127.0.0.1:8000/backup`). Genera un único archivo
`KioscoApp_backup_YYYY-MM-DD_HHMMSS.zip` con `kiosco.db` +
`imagenes_productos/`, guardado en `backups/` junto a `data/` (es decir,
en modo frozen, `%LOCALAPPDATA%\KioscoApp\backups\`). Mientras se genera,
la aplicación rechaza brevemente nuevas escrituras (ventas, altas de
producto, etc. devuelven un 503 transitorio) para garantizar que la DB y
las imágenes queden consistentes entre sí; las lecturas nunca se ven
afectadas.

**Retención**: tras cada backup se conservan los 30 más recientes (manuales y
automáticos) y, aparte, los 5 más recientes de migración; el resto se borra
(`config.BACKUPS_A_CONSERVAR`, `BACKUPS_PREMIGRACION_A_CONSERVAR`). El backup recién
creado nunca se borra y solo se consideran archivos con el nombre exacto de
los backups de la aplicación. Si dos backups caen en el mismo segundo, el nombre
lleva un sufijo (`_2`, `_3`) y nunca se pisa uno anterior.

**Registro (logs)**: el ejecutable escribe `%LOCALAPPDATA%\KioscoApp\logs\twinx_express.log`
(rotativo, 1 MB × 5) con timestamp y nivel, incluidos los errores de arranque. No
registra contraseñas, hashes, cookies ni claves de idempotencia (ver `logging_config.py`).

**Backup preventivo antes de migrar**: si al iniciar la aplicación la base de
datos ya existe y tiene migraciones pendientes, se genera antes un backup
(`KioscoApp_backup_pre_migracion_<fecha>.zip`, mismo formato y misma carpeta).
Si ese backup falla, no se migra. Una instalación nueva no genera ninguno.

**Restore**: no tiene interfaz web -- requiere la aplicación **cerrada**:

```
KioscoApp.exe --restore "C:\ruta\al\backup.zip"
```

Valida el ZIP por completo (estructura, integridad de SQLite, rutas
dentro del archivo) antes de tocar la instalación actual, y nunca deja
la instalación a medio modificar: si algo falla durante el reemplazo,
revierte automáticamente a los datos anteriores. Al terminar, se
recomienda reiniciar la aplicación.

## Estructura general del proyecto

```
config.py              Configuración centralizada (rutas, etc.)
excepciones.py          Excepciones de dominio del proyecto
lanzador.py             Punto de entrada del ejecutable empaquetado (PyInstaller)
db/                     Acceso a datos: conexión, migraciones (db/migraciones/) y repositorios
domain/                 Entidades y reglas de negocio puras (sin SQL, sin I/O)
services/               Casos de uso que orquestan db/ + domain/
interfaces/
  cli/                  Interfaz de consola
  web/                  Interfaz web (FastAPI + Jinja2 + Tailwind), rutas, templates y estáticos
tests/                  Tests automatizados (pytest) de dominio, servicios y rutas web
tests_e2e/              Tests end-to-end (Playwright)
tests_js/               Tests unitarios de JavaScript
data/                   Base de datos SQLite e imágenes de producto (no versionado, ver abajo)
```

Ver `CLAUDE.md` para el detalle de la separación en capas y las convenciones
de código del proyecto.

## Persistencia de datos

En modo desarrollo, todos los datos de la aplicación (base de datos SQLite
`kiosco.db` e imágenes de producto) viven en la carpeta local `data/`, la
cual **no se versiona** (ver `.gitignore`): cada instalación mantiene sus
propios datos, y clonar el repositorio no trae datos de ningún kiosco real.
Esa carpeta se crea automáticamente al ejecutar la aplicación si no existe.

En el ejecutable empaquetado (PyInstaller), los datos persisten fuera del
bundle, en `%LOCALAPPDATA%\KioscoApp\data`, para sobrevivir a
actualizaciones (ver `config.py` y la sección de Build más abajo).

### Catálogo inicial de distribución

Una instalación **nueva** del ejecutable arranca con ~41 productos genéricos de
kiosco (10 categorías, sin marcas ni códigos EAN) y **sin usuarios**: el dueño se
crea en `/configuracion-inicial`. El catálogo vive en `db/seed/catalogo_inicial.json`
(versionado, independiente de `data/kiosco.db`) y se carga en una única
transacción por `services/servicio_catalogo_inicial.py`.

- Solo se siembra en el ejecutable congelado (`config.SEMBRAR_CATALOGO_INICIAL`):
  nunca en desarrollo, en tests ni en E2E.
- Se siembra una sola vez: si ya hay usuarios, productos o categorías, o el
  marcador ya está puesto, no se toca nada. Una actualización del ejecutable
  nunca vuelve a cargar el catálogo. El marcador es `PRAGMA user_version`
  (versión del catálogo inicial; **no** es la versión del esquema, que se
  lleva en `schema_migraciones`) y viaja dentro de `kiosco.db`, así que los
  backups y restores lo conservan.
- Todos los productos nacen con precio de venta, costo y stock en 0 y códigos
  internos `TX-...`. Un producto sin precio de venta no se puede vender, y un
  producto sin stock no aparece como alerta de stock crítico.
- Si el catálogo distribuido es inválido, la carga se revierte por completo, el
  error queda registrado y la aplicación no arranca.

## Control comercial y trazabilidad (V1.2)

Todas estas pantallas son **solo OWNER** (los CASHIER reciben 403; sin sesión se
redirige a `/login`).

| Ruta | Qué hace |
|---|---|
| `GET /precios` | Formulario de actualización masiva de precios de venta y últimas actualizaciones. |
| `GET /precios/vista-previa` | Calcula (sin escribir) el precio actual y el nuevo de cada producto. |
| `POST /precios/aplicar` | Confirma la actualización: transaccional, todo o nada, idempotente. |
| `GET /productos/{id}/precios` | Historial de cambios de precio de venta y de costo de un producto. |
| `GET /auditoria` | Registro de auditoría con filtros por acción, usuario y fechas. |
| `GET/POST /configuracion` | Datos comerciales del ticket (nombre, dirección, teléfono, leyenda de pie). |
| `GET /reposicion` | Productos por debajo del stock mínimo y cantidad sugerida (solo consulta). |
| `POST /productos/importar` | Importación CSV/XLSX en modo crear o crear y actualizar (muestra el reporte completo). |

**Historial de precios.** Todo cambio de precio de venta o de costo queda en
`historial_precios` (valor anterior y nuevo, usuario, fecha y origen: `EDICION`,
`COMPRA`, `MASIVA` o `IMPORTACION`), solo si el valor realmente cambió, dentro de la
misma transacción que el cambio. Empieza en V1.2 (sin relleno retroactivo) y el
precio inicial de un producto no es un cambio. Hay dos únicos puntos de escritura
de precios (`actualizar_datos_en_conexion` y `cambiar_precio_en_conexion`, en
`db/repositorios/productos.py`) y `tests/test_estructura_precios.py` falla si
aparece otro. Las ventas históricas conservan el precio y el costo con que se
registraron (`detalle_venta`). Un producto con historial de precios no se
elimina físicamente: se da de baja (lógica), igual que con ventas, compras o
ajustes, y el aviso indica el motivo.

**Actualización masiva.** Solo el precio de venta, por porcentaje (hasta ±1000 %)
o monto fijo, sobre todos los productos activos con precio, una categoría o los
sin categoría. Sin redondeo por defecto (opción explícita "al peso más cercano").
Flujo: vista previa → confirmación; la confirmación revalida en el servidor el
alcance, el criterio, los límites, la selección, el estado y el precio de cada
producto (nunca confía en la vista previa) y rechaza toda la operación si algún
producto seleccionado es inválido o su precio cambió entre la vista previa y la
confirmación. Cada operación crea un lote (`lotes_precios`) con clave de
idempotencia única.

**Auditoría.** `auditoria` registra usuario, fecha, acción, entidad, id y un
resumen corto de: alta/edición/baja/reactivación de productos, actualización masiva
de precios, importaciones, ajustes de stock, compras, anulaciones, movimientos de
caja, alta/rol/activación/desactivación/reseteo de contraseña de usuarios y cambios
de configuración. La operación y su registro se escriben **en la misma
transacción** (`db.repositorios.auditoria.registrar_en_conexion`): si la auditoría
falla, la operación hace rollback. Solo se audita con un usuario autenticado (CLI,
siembra inicial y tests no generan registros). Nunca guarda contraseñas, hashes,
cookies ni claves de idempotencia.

**Importación.** Modos `CREAR` (omite los códigos existentes) y `CREAR_Y_ACTUALIZAR`
(actualiza los existentes: una celda vacía conserva el valor, el stock nunca se
modifica, un producto dado de baja se rechaza). **Por defecto es todo o nada**
(formulario, ruta y servicio coinciden): una fila inválida cancela la importación
completa; la importación parcial requiere marcar explícitamente "Permitir importación
parcial" (`todo_o_nada=False` en el servicio). Un valor numérico extremo (`inf`,
`1e400`, fuera del rango de SQLite) es un error de fila controlado.

**Ticket.** El encabezado y el pie se configuran en `/configuracion` (límites: nombre
60, dirección 100, teléfono 30, leyenda 120 caracteres; el texto se sanea y se
escapa). Sin configuración imprime "Kiosco". No hay datos fiscales (CUIT, condición
frente al IVA) ni texto fijo agregado. Los nombres largos sin espacios se parten
(`overflow-wrap`) para no romper el ancho de 80 mm.

**Reposición.** Entra un producto activo con `stock_minimo > 0` y
`stock_actual < stock_minimo` (el stock 0 entra; el stock igual al mínimo no). La
cantidad sugerida es exactamente `stock_minimo - stock_actual` y se puede editar
en pantalla antes de imprimir. No crea compras ni modifica stock. La alerta del
dashboard (`listar_stock_critico`) no cambia.

**Migraciones nuevas** (`db/migraciones/`, mismo mecanismo atómico y versionado):

| Migración | Contenido |
|---|---|
| `014_idempotencia_operaciones.sql` | `clave_idempotencia` (UNIQUE) en caja, ajustes y compras. |
| `015_historial_precios.sql` | Tabla `historial_precios`. |
| `016_lotes_precios.sql` | Tabla `lotes_precios` y `historial_precios.lote_id`. |
| `017_auditoria.sql` | Tabla `auditoria`. |
| `018_configuracion.sql` | Tabla `configuracion` (clave/valor). |

## Caja por sesiones, clientes y cuenta corriente (V1.3)

**Sesiones de caja.** Cada apertura de caja inicia una sesión y su cierre la termina.
Las ventas y los movimientos de caja (ingresos, egresos y cobros de cuenta corriente)
quedan asociados a la sesión en la que se hicieron, y el arqueo se calcula por
sesión. Una venta solo puede anularse mientras su sesión sigue abierta: las ventas
de sesiones cerradas no se anulan (sus datos no se modifican).

**Clientes y cuenta corriente.** Los clientes se gestionan desde el menú *Clientes*
(visible para OWNER y CASHIER). El saldo de un cliente es lo cargado a cuenta menos
lo cobrado. Los clientes no se borran: se desactivan.

| Ruta | Quién | Qué hace |
|---|---|---|
| `GET /clientes` | OWNER, CASHIER | Lista con saldo; búsqueda por nombre o teléfono y filtro de estado (activos por defecto). |
| `GET/POST /clientes/nuevo` | OWNER, CASHIER | Alta de cliente. |
| `GET /clientes/{id}` | OWNER, CASHIER | Ficha: datos, estado, saldo, total de cargos y de cobros, y movimientos (el más reciente primero, con enlace a cada venta). |
| `GET/POST /clientes/{id}/editar` | solo OWNER | Edición de datos. |
| `POST /clientes/{id}/desactivar` y `/reactivar` | solo OWNER | Baja y reactivación lógicas. La baja se rechaza con saldo pendiente. |
| `GET/POST /clientes/{id}/cobro` | OWNER, CASHIER | Cobro de la deuda, solo en efectivo, parcial o total, idempotente. |
| `GET /api/clientes/buscar?q=` | OWNER, CASHIER | Buscador del POS: solo clientes activos, máximo 20. |

Los CASHIER reciben 403 en editar, desactivar y reactivar (el permiso lo exige el
servidor, no solo el menú); sin sesión se redirige a `/login`.

**Reglas de la cuenta corriente.**
- Una venta a cuenta usa el medio de pago `CUENTA_CORRIENTE`, exige un cliente activo
  y una caja abierta, y suma su total al saldo del cliente. No puede anularse.
- Un cliente inactivo no recibe ventas nuevas; un cliente con saldo pendiente no puede
  desactivarse.
- Un cobro es solo en efectivo, mayor a cero y no superior al saldo. Requiere una caja
  abierta y entra a la caja como ingreso (se identifica como *Cobro cta. cte.* en Caja y
  en el Dashboard). Un cobro **no es una venta**: no suma a los reportes de ventas.
- La venta a cuenta sí es una venta como las demás: cuenta en ventas, facturación y
  productos vendidos, y aparece en Reportes como medio de pago `CUENTA_CORRIENTE`.
- Las ventas y los cobros son idempotentes (una clave por intento). La venta a cuenta,
  el cobro y el alta, edición, baja y reactivación de clientes se auditan en la misma
  transacción que la operación.

**Punto de venta.** `EFECTIVO` sigue siendo el medio marcado por defecto. `CUENTA_CORRIENTE`
es una opción aparte, que nunca queda marcada sola: al elegirla hay que seleccionar un
cliente activo, y sin cliente no se puede cobrar. Si se cambia a otro medio de pago, el
cliente elegido se descarta y al volver hay que elegirlo de nuevo.

**Historial de ventas.** Muestra el cliente de cada venta, permite filtrar por
`CUENTA_CORRIENTE` y no ofrece anular una venta a cuenta (el servidor la rechaza igual).

**Actualizar desde V1.2.** Las migraciones se aplican solas al abrir la nueva versión,
con un backup preventivo previo. Se conservan todas las ventas, sus detalles y los
movimientos de caja; las cajas anteriores se reconstruyen como sesiones históricas (la
última, si no tenía cierre, queda sin fecha de cierre). **Después de actualizar no hay
ninguna caja abierta**: hay que abrir una nueva antes de vender. Las ventas de las
sesiones anteriores no pueden anularse, y las ventas anteriores quedan sin cliente.

**Migraciones nuevas** (`db/migraciones/`, mismo mecanismo atómico y versionado):

| Migración | Contenido |
|---|---|
| `019_sesiones_caja.sql` | Sesiones de caja, con reconstrucción histórica de las cajas de V1.2. |
| `020_clientes_cuenta_corriente.sql` | Clientes, ventas a cuenta y libro de cuenta corriente (cargos y cobros). |

## Proveedores e inventario físico (V1.4)

### Proveedores (solo OWNER)

Además del alta, edición, baja y reactivación que ya existían, el menú *Proveedores* suma:

- **Búsqueda** por nombre, persona de contacto, teléfono o email (activos por defecto; también en la
  vista de dados de baja).
- **Ficha** (`GET /proveedores/{id}`, también para proveedores dados de baja): datos, productos
  vinculados e historial de compras del proveedor, la más reciente primero.
- **Relación producto-proveedor.** Un producto puede tener varios proveedores. Cada vínculo tiene un
  *código del proveedor* opcional y, como máximo, un vínculo por producto es el **principal**. El
  vínculo no guarda costos: el costo sale de las compras (`detalle_compra`) y del costo vigente del
  producto.
- **Vínculo automático.** Al registrar una compra se crea el vínculo de cada producto con el proveedor
  si no existía. Si el producto todavía no tenía proveedor principal, ese vínculo pasa a ser el
  principal; si ya tenía uno, **una compra nunca lo reemplaza**. El cambio de principal es siempre una
  acción explícita del OWNER desde la ficha del proveedor («Marcar como principal»); quitar un vínculo
  principal tampoco promueve a otro.
- Desde la ficha el OWNER también puede vincular un producto activo a mano y quitar un vínculo.
- **Reposición.** Cada sugerencia muestra el proveedor principal del producto (marcado *Inactivo* si
  está dado de baja) y usa como costo el de la última línea de compra a ese proveedor; si no hay
  proveedor principal o nunca se le compró, usa el costo vigente del producto. Sigue siendo solo de
  consulta: no crea compras.
- Un proveedor o un producto con vínculos no se borra: se desactiva. Alta, edición, baja y reactivación
  de proveedores, y alta, baja y cambio de principal de vínculos, quedan en la auditoría en la misma
  transacción que la operación.

### Inventario físico

El menú *Inventario* (visible para OWNER y CASHIER) compara lo **contado** físicamente con el stock que
el sistema tenía **en el momento del conteo**.

| Ruta | Quién | Qué hace |
|---|---|---|
| `GET /inventario` | OWNER (el CASHIER es llevado al conteo) | Historial de inventarios y acceso al abierto. |
| `GET/POST /inventario/nuevo` | solo OWNER | Inicia un inventario: todos los productos activos, o una selección manual. |
| `GET /inventario/conteo` | OWNER, CASHIER | Pantalla de conteo del inventario abierto, **sin stock esperado ni diferencia**. |
| `POST /inventario/conteo/{producto_id}` y `POST /api/inventario/conteo/{producto_id}` | OWNER, CASHIER | Registra (o reemplaza) el conteo de un producto. |
| `GET /inventario/{id}` | solo OWNER | Revisión: esperado, contado, diferencia, costo, quién contó y ajuste asociado. |
| `POST /inventario/{id}/confirmar` y `/cancelar` | solo OWNER | Confirma o cancela el inventario. |

**Reglas.**
- Un inventario está `ABIERTO`, `CONFIRMADO` o `CANCELADO`. Solo puede haber uno abierto a la vez y un
  inventario cerrado no se reabre: para corregir, se cancela y se inicia otro. Las líneas (productos)
  quedan fijadas al iniciarlo. La selección manual puede incluir productos inactivos que todavía tengan
  stock.
- El conteo es a ciegas: las pantallas y las respuestas de conteo nunca incluyen el stock esperado ni la
  diferencia (esos datos solo los ve el OWNER en la revisión). Al contar, el servidor guarda de una vez el
  stock esperado, su versión, la cantidad contada, el costo, quién contó y cuándo. Volver a contar un
  producto reemplaza su conteo anterior.
- **Confirmar** valida, para todas las líneas contadas (también las que no tienen diferencia), que ni el
  stock ni la versión del producto hayan cambiado desde que se contó (`productos.version_stock`, que sube
  con cada cambio real de stock: venta, anulación, compra o ajuste, incluso si el stock vuelve al mismo
  número). Si alguna cambió, **no se confirma nada**: se informa qué productos cambiaron y hay que volver a
  contarlos. El inventario nunca sobrescribe movimientos posteriores al conteo.
- Si todo coincide, en una única transacción se generan los ajustes de stock, se audita y se cierra el
  inventario, o no se hace nada. Una línea contada **sin diferencia no genera ajuste**; una con diferencia
  genera un ajuste con motivo `RECUENTO` asociado al inventario (queda en el historial de ajustes del
  producto y en la auditoría). Los productos **sin contar no se modifican** (jamás se toman como cero); hace
  falta al menos un producto contado para confirmar.
- La confirmación es idempotente: un doble clic o un reenvío no duplica ajustes ni auditoría.
- Cancelar no toca el stock. La creación, confirmación y cancelación se auditan
  (`INVENTARIO_CREADO`, `INVENTARIO_CONFIRMADO`, `INVENTARIO_CANCELADO`).

**Actualizar desde V1.3.** Las migraciones se aplican solas al abrir la nueva versión, con un backup
preventivo previo. No se pierde ni se modifica ningún dato: la 021 crea los vínculos producto-proveedor a
partir de las compras ya registradas (el proveedor principal de cada producto es el de su compra más
reciente, por orden de registro) y la 022 agrega el contador de versión de stock (en 0) y las tablas del
inventario.

**Migraciones nuevas** (`db/migraciones/`, mismo mecanismo atómico y versionado):

| Migración | Contenido |
|---|---|
| `021_producto_proveedor.sql` | Tabla `producto_proveedor` (un principal por producto) y vínculos históricos a partir de las compras. |
| `022_inventario_fisico.sql` | `productos.version_stock` con su trigger, tablas `inventarios` e `inventario_lineas` y `ajustes_stock.inventario_id`, con triggers que impiden estados inválidos. |

## Robustez y escala (V1.5)

Sin funcionalidad nueva ni migraciones: endurece lo existente.

- **Robustez numérica.** Montos, cantidades, stock y porcentajes se validan contra el rango que soporta
  SQLite (enteros de 64 bits): `NaN`, `Infinity`, exponentes gigantes y valores fuera de rango se rechazan como
  dato inválido (mensaje claro, nada se guarda) en vez de producir un error del servidor. Un identificador de
  categoría o de usuario no numérico también se resuelve de forma controlada.
- **Escala acotada.** El Historial de ventas se pagina en SQL (`LIMIT/OFFSET`, 100 por página) conservando los
  filtros; la pantalla de Caja lista solo los movimientos de la sesión relevante (la abierta o, si no hay, la
  última cerrada) y el dashboard consulta únicamente los últimos 5 movimientos. Arqueo y cierre de caja no cambian.
- **Cobertura E2E del rol CASHIER.** El servidor de pruebas E2E siembra un CASHIER y se verifica en el navegador
  que cuenta el inventario a ciegas pero no crea, revisa, confirma ni cancela, y que las rutas solo-OWNER
  responden 403 también por acceso directo (URL y POST).

**Actualizar desde V1.4.** Sin migraciones: los datos quedan intactos.

## Cuenta corriente, reposición y reportes (V1.6)

Sin migraciones ni dependencias nuevas.

- **Cuenta corriente.** El historial de ventas se filtra por cliente; el ticket de una venta a cuenta muestra el
  cliente y su saldo posterior al cargo; el listado de clientes muestra la deuda total.
- **Reposición integrada con compras.** La reposición agrupa por proveedor principal, permite editar cantidades,
  se puede imprimir y precarga una compra que no se guarda hasta confirmarla. Un proveedor inactivo se informa
  en lugar de fallar.
- **Reportes operativos.** Caja por sesión, compras por proveedor, deuda de cuenta corriente y cobranzas de
  cuenta corriente, calculados con agregaciones SQL.

**Actualizar desde V1.5.** Sin migraciones: los datos quedan intactos.

## Rotación, anulación de compras y movimientos de stock (V1.7)

Dos migraciones nuevas (23 y 24, aditivas; 24 migraciones en total), sin dependencias nuevas.

- **Rotación de stock** (`/reportes/rotacion`). Productos con stock y sus ventas activas del período, en una única
  consulta SQL: unidades vendidas, última venta, valorización a costo actual y valor inmovilizado. Migración 023:
  índices `ventas(fecha)` y `compras(fecha)` para los reportes por rango.
- **Anulación segura de compras** (solo OWNER). Todo o nada dentro de una transacción: exige `stock_actual >=
  cantidad` en todas las líneas y revierte el stock. El costo anterior se restaura solo si se demuestra (la línea
  guarda el evento de `historial_precios` que produjo su cambio de costo, ese evento sigue siendo el último y no hay
  otra compra activa posterior); si no, se conserva y la auditoría (`COMPRA_ANULADA`) registra la causa. Las
  compras anteriores a V1.7 (`costo_trazable = 0`) revierten stock pero nunca restauran costo: no se infiere ningún
  evento. Migración 024: estado y datos de anulación en `compras`, `detalle_compra.historial_precio_id`, origen
  `ANULACION_COMPRA` en `historial_precios` (la tabla se reconstruye con verificación). Los reportes por proveedor y
  el último costo de reposición ignoran las compras anuladas.
- **Movimientos de stock por producto** (`/productos/{id}/movimientos`, solo OWNER). Kardex de solo lectura armado
  con los eventos reales (ventas, compras, sus anulaciones como movimientos aparte, ajustes y recuentos); sin
  tabla nueva. El saldo inicial es reconstruido desde `stock_actual`, coherente con cualquier rango de fechas.

**Actualizar desde V1.6.** Se crea un backup automático y se aplican las migraciones 23 y 24; los datos quedan
intactos y las compras existentes quedan `ACTIVA`.

## Estado actual

- [x] Estructura de carpetas y configuración base
- [x] Fase 1: Base de datos (esquema, conexión, migraciones)
- [x] Fase 2: Lógica de negocio (dominio + servicios de stock)
- [x] Fase 3: Servicio de ventas transaccional (carrito + descuento de stock atómico)
- [x] Fase 4: Control de caja (apertura, cierre, ingresos/egresos)
- [x] Interfaz de consola (CLI): productos, ventas, caja, alertas de stock crítico
- [x] Suite de tests automatizados (pytest) sobre dominio y servicios
- [x] Fase 5: Interfaz web local (FastAPI + Jinja2 + Tailwind CDN) — POS, productos, caja, dashboard
- [x] Autenticación por sesión (OWNER/CASHIER), categorías, unidad de medida e imágenes de producto
- [x] Compras, proveedores y empleados (alta/edición), reactivación de productos y categorías dados de baja
- [x] Backup manual y restore (ver sección "Backup y restore" más arriba)
- [x] Modo oscuro y layout responsive (375/768/1024/1440)
- [x] Reportes de ventas, precios (historial y actualización masiva), auditoría, importación, configuración del ticket y reposición (V1.2, ver la sección «Control comercial y trazabilidad (V1.2)»)
- [x] Caja por sesiones, clientes y cuenta corriente (ventas a cuenta y cobros en efectivo) (V1.3, ver sección anterior)
- [x] Proveedores (búsqueda, ficha, relación producto-proveedor) e inventario físico con conteo a ciegas (V1.4, ver sección anterior)
- [x] Robustez numérica, historial/caja/dashboard acotados y cobertura E2E del rol CASHIER (V1.5, ver «Robustez y escala (V1.5)»)
- [x] Cuenta corriente completa, reposición integrada con compras y reportes operativos (V1.6, ver «Cuenta corriente, reposición y reportes (V1.6)»)
- [x] Rotación de stock, anulación segura de compras y movimientos de stock por producto (V1.7, ver «Rotación, anulación de compras y movimientos de stock (V1.7)»)
- [ ] Pedidos: solo cascarón visual, sin lógica de negocio todavía (oculto del menú)
- [ ] Exportación de datos y backup automático/programado
