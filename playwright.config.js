// @ts-check
const { randomUUID } = require('crypto');
const path = require('path');
const { defineConfig, devices } = require('@playwright/test');

// Puerto propio del servidor E2E, distinto del 8000 que usa el server de
// desarrollo (`python -m interfaces.web.app`) -- evita que Playwright
// choque con un servidor de desarrollo que ya esté corriendo, y deja
// clarísimo en cualquier log/URL que esto no es la app real.
//
// LIMITACIÓN CONOCIDA Y ACEPTADA (no resuelta en esta corrección, por
// pedido explícito): sigue siendo fijo. Dos `npm run test:e2e`
// simultáneos chocan al intentar bindear el mismo puerto -- eso es
// independiente de que la DB ahora sea única por ejecución (ver abajo):
// aunque cada corrida usara su propia base, seguirían compitiendo por
// este mismo puerto. Puertos dinámicos quedan fuera de alcance acá.
const PUERTO_E2E = 8130;
const BASE_URL = `http://127.0.0.1:${PUERTO_E2E}`;

// Corrección de auditoría (5E.1): un directorio único por ejecución de
// `npx playwright test`, no una ruta fija -- así dos corridas (aunque no
// puedan ejecutarse en paralelo hoy por el puerto fijo de arriba, ver
// limitación) nunca podrían llegar a compartir ni pisarse una base de
// datos, y una corrida anterior que haya terminado mal jamás puede ser
// "reencontrada" por la siguiente (no hay ninguna ruta fija que buscar).
//
// Se genera acá, en `playwright.config.js` -- un archivo JS que Node
// vuelve a evaluar de cero en cada invocación de `npx playwright test`
// -- y no dentro de `tests_e2e/servidor_pruebas.py`, que sigue exigiendo
// la ruta como argumento explícito obligatorio, sin ningún default (así
// es estructuralmente imposible que ese script caiga en `data/kiosco.db`
// si algún día se lo invoca a mano sin argumentos).
//
// `randomUUID()` (nativo de Node, sin dependencias nuevas) da un
// identificador único para fines prácticos; `path.join` arma la ruta
// con el separador correcto de Windows (`\`) en vez de concatenar
// strings a mano. La ruta resultante es relativa (sin espacios, dado
// que ni el proyecto ni el UUID los introducen), pero igual se la pasa
// entre comillas dobles al comando de `webServer` (ver más abajo) para
// no depender de que eso siga siendo cierto.
const ID_EJECUCION = randomUUID();
const RUTA_DB_E2E = path.join('tests_e2e', '.tmp', ID_EJECUCION, 'e2e.db');

module.exports = defineConfig({
  testDir: './tests_e2e/tests',
  timeout: 30_000,
  fullyParallel: false,
  // Fase 5E.2: todos los tests de una misma corrida comparten UN solo
  // servidor/DB temporal (un único `webServer` por invocación de
  // `npx playwright test`, ver más abajo). `workers: 1` obliga a que se
  // ejecuten estrictamente uno a la vez, sin importar en cuántos
  // archivos estén repartidos -- sin esto, dos tests corriendo en
  // paralelo podrían pisarse al leer/escribir stock del mismo producto
  // sembrado (ver tests_e2e/servidor_pruebas.py) y dar falsos negativos
  // por una carrera de datos, no por un bug real de la app.
  workers: 1,
  reporter: 'list',

  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },

  // `test:e2e` (ver package.json) corre todo excepto el archivo de
  // responsive en un único viewport (desktop-1440). `test:e2e:responsive`
  // corre EXCLUSIVAMENTE 07-responsive.spec.js contra los 4 proyectos de
  // abajo -- correr los 7 archivos x 4 viewports por defecto sería 4x
  // más lento sin aportar nada: los tests 1-6 no dependen del ancho de
  // pantalla, solo el 7 lo hace.
  projects: [
    { name: 'desktop-1440', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    { name: 'desktop-1024', use: { ...devices['Desktop Chrome'], viewport: { width: 1024, height: 768 } } },
    { name: 'tablet-768', use: { ...devices['Desktop Chrome'], viewport: { width: 768, height: 1024 } } },
    { name: 'mobile-375', use: { ...devices['Desktop Chrome'], viewport: { width: 375, height: 812 } } },
  ],

  // Levanta el servidor FastAPI real (ver tests_e2e/servidor_pruebas.py)
  // como proceso propio -- nada de `--reload` (un solo proceso,
  // determinista). `reuseExistingServer: false` a propósito: preferimos
  // un servidor y una DB nuevos en cada corrida antes que arriesgarnos a
  // reusar un proceso/estado de una corrida anterior.
  webServer: {
    // La ruta va entre comillas dobles: defensivo ante espacios en la
    // ruta absoluta del proyecto en otras máquinas (acá no los hay,
    // pero no hay que depender de eso). El puerto es siempre un número,
    // no necesita comillas.
    command: `python -m tests_e2e.servidor_pruebas "${RUTA_DB_E2E}" ${PUERTO_E2E}`,
    url: `${BASE_URL}/login`,
    timeout: 30_000,
    reuseExistingServer: false,
  },
});
