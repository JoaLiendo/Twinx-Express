# Kiosco Stock App

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

**Backup**: desde la propia aplicación web, en `/backup` (solo visible/accesible
para el rol OWNER). Genera un único archivo
`KioscoApp_backup_YYYY-MM-DD_HHMM.zip` con `kiosco.db` +
`imagenes_productos/`, guardado en `backups/` junto a `data/` (es decir,
en modo frozen, `%LOCALAPPDATA%\KioscoApp\backups\`). Mientras se genera,
la aplicación rechaza brevemente nuevas escrituras (ventas, altas de
producto, etc. devuelven un 503 transitorio) para garantizar que la DB y
las imágenes queden consistentes entre sí; las lecturas nunca se ven
afectadas.

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
- [ ] Pedidos, precios y reportes: solo cascarón visual, sin lógica de negocio todavía
- [ ] Exportación de datos y backup automático/programado
