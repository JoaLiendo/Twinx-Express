// @ts-check
/**
 * Auditoría visual (V1.17-B): captura pantallas reales de la aplicación contra un servidor E2E aislado
 * (`tests_e2e/servidor_pruebas.py`), en varios tamaños de ventana y en tema claro/oscuro, para comparar
 * ANTES y DESPUÉS de un cambio de diseño. No es un test: no afirma nada, solo deja archivos PNG.
 *
 * Uso (con el servidor E2E ya levantado):
 *
 *   node tests_e2e/capturas_visuales.js <carpeta_salida> [url_base] [ruta_navegador]
 *
 * - `carpeta_salida`: fuera del repositorio (p. ej. `..\capturas-v1.17\antes`).
 * - `url_base`: por defecto `http://127.0.0.1:8140`.
 * - `ruta_navegador`: ejecutable de Brave o Chrome; por defecto Brave y, si no existe, el Chromium de Playwright.
 *
 * Primero registra una venta real por el POS (así el dashboard, los reportes y la analítica muestran datos),
 * después recorre las pantallas. Las credenciales son las que siembra el servidor de pruebas.
 */
const fs = require('fs');
const path = require('path');
const { chromium } = require('@playwright/test');

const SALIDA = process.argv[2];
const BASE_URL = process.argv[3] || 'http://127.0.0.1:8140';
const NAVEGADOR = process.argv[4] || 'C:/Program Files/BraveSoftware/Brave-Browser/Application/brave.exe';

if (!SALIDA) {
  console.error('Uso: node tests_e2e/capturas_visuales.js <carpeta_salida> [url_base] [ruta_navegador]');
  process.exit(2);
}

const USUARIO = 'e2e_owner';
const PASSWORD = 'clave-e2e-12345';

const VIEWPORTS = [
  { nombre: '1366x768', width: 1366, height: 768 },
  { nombre: '1440x900', width: 1440, height: 900 },
  { nombre: '1920x1080', width: 1920, height: 1080 },
  { nombre: '375x812', width: 375, height: 812 },
];

const PANTALLAS = [
  ['dashboard', '/'],
  ['pos', '/ventas'],
  ['productos', '/productos'],
  ['inventario', '/inventario'],
  ['caja', '/caja'],
  ['compras-nueva', '/compras/nueva'],
  ['clientes', '/clientes'],
  ['proveedores', '/proveedores'],
  ['reportes', '/reportes'],
  ['analitica', '/analitica'],
  ['analitica-margen', '/analitica/margen'],
  ['reposicion', '/reposicion'],
];

async function login(page) {
  await page.goto(`${BASE_URL}/login`);
  await page.locator('input[name="nombre_usuario"]').fill(USUARIO);
  await page.locator('input[name="password"]').fill(PASSWORD);
  await page.getByRole('button', { name: 'Ingresar' }).click();
  await page.waitForURL((url) => !url.pathname.startsWith('/login'));
}

async function venderAlgo(page) {
  await page.goto(`${BASE_URL}/ventas`);
  const input = page.getByLabel('Código de barras');
  for (const codigo of ['7790000000001', '7790000000001', '7790000000002']) {
    await input.fill(codigo);
    await input.press('Enter');
  }
  await page.getByText('TARJETA', { exact: true }).click();
  await Promise.all([
    page.waitForResponse((r) => r.url().includes('/api/ventas') && r.request().method() === 'POST'),
    page.locator('#boton-cobrar').click(),
  ]);
}

async function capturar(page, carpeta, nombre) {
  await page.waitForLoadState('networkidle');
  await page.screenshot({ path: path.join(carpeta, `${nombre}.png`), fullPage: false });
}

(async () => {
  fs.mkdirSync(SALIDA, { recursive: true });
  const opciones = fs.existsSync(NAVEGADOR) ? { executablePath: NAVEGADOR } : {};
  const navegador = await chromium.launch({ headless: true, ...opciones });
  console.log(`Navegador: ${opciones.executablePath || 'Chromium de Playwright'}`);
  try {
    const contextoDatos = await navegador.newContext({ viewport: { width: 1440, height: 900 } });
    const paginaDatos = await contextoDatos.newPage();
    await login(paginaDatos);
    await venderAlgo(paginaDatos);
    await contextoDatos.close();

    for (const viewport of VIEWPORTS) {
      for (const tema of ['claro', 'oscuro']) {
        if (tema === 'oscuro' && viewport.width !== 1440) continue;
        const carpeta = path.join(SALIDA, `${viewport.nombre}-${tema}`);
        fs.mkdirSync(carpeta, { recursive: true });
        const contexto = await navegador.newContext({
          viewport: { width: viewport.width, height: viewport.height },
          colorScheme: tema === 'oscuro' ? 'dark' : 'light',
        });
        const page = await contexto.newPage();
        await page.goto(`${BASE_URL}/login`);
        await capturar(page, carpeta, 'login');
        await login(page);
        for (const [nombre, ruta] of PANTALLAS) {
          await page.goto(`${BASE_URL}${ruta}`);
          await capturar(page, carpeta, nombre);
        }
        // POS con carrito cargado: la pantalla que más se usa.
        await page.goto(`${BASE_URL}/ventas`);
        const input = page.getByLabel('Código de barras');
        await input.fill('7790000000001');
        await input.press('Enter');
        await input.fill('7790000000002');
        await input.press('Enter');
        await capturar(page, carpeta, 'pos-con-carrito');
        await contexto.close();
        console.log(`Listo: ${carpeta}`);
      }
    }
  } finally {
    await navegador.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
