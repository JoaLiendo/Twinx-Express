// @ts-check
const { expect } = require('@playwright/test');

// Mismas credenciales/productos que siembra tests_e2e/servidor_pruebas.py
// (Fase 5E.1/5E.2) -- centralizados acá para no repetir "números mágicos"
// en cada archivo de test.
const USUARIO = 'e2e_owner';
const PASSWORD = 'clave-e2e-12345';
const USUARIO_CASHIER = 'e2e_cashier'; // misma contraseña que el OWNER

const PRODUCTO_1 = {
  codigoBarras: '7790000000001',
  nombre: 'Alfajor E2E',
  precioCentavos: 250, // $2.50
};

const PRODUCTO_2 = {
  codigoBarras: '7790000000002',
  nombre: 'Gaseosa E2E',
  precioCentavos: 500, // $5.00
};

// Fase 5E.3: producto exclusivo del test de regresión de XSS
// (08-seguridad-xss.spec.js). Mismo nombre exacto que siembra
// tests_e2e/servidor_pruebas.py -- combina, en un solo string, los
// vectores que la vulnerabilidad original permitía (comilla doble +
// atributo nuevo, <img onerror=...>, comilla simple, `&`, etiqueta
// genérica).
const PRODUCTO_XSS = {
  codigoBarras: '7790000000099',
  nombre:
    '<img src=x onerror="window.__xssEjecutado = true">' +
    'Nombre" onmouseover="window.__xssEjecutado = true" raro\' & <b>negrita</b>',
  precioCentavos: 100, // $1.00
};

/**
 * Login real vía la UI (nunca inyección de cookie): navega a `irA`
 * (que redirige a /login si no hay sesión, igual que le pasaría a
 * cualquier usuario real) y completa el formulario real.
 *
 * `input[name="nombre_usuario"]`/`input[name="password"]` se ubican por
 * su atributo `name` -- login.html no asocia sus <label> con `for`/`id`
 * (deuda de accesibilidad documentada en la auditoría de 5E.1, no
 * corregida a propósito acá), así que `getByLabel` no los encuentra.
 * `name` es un atributo semántico real del formulario (lo usa también
 * `interfaces/web/rutas/autenticacion.py::procesar_login`), no una
 * clase de presentación.
 */
async function loginViaUI(page, { irA = '/ventas', usuario = USUARIO } = {}) {
  await page.goto(irA);
  await page.locator('input[name="nombre_usuario"]').fill(usuario);
  await page.locator('input[name="password"]').fill(PASSWORD);
  await page.getByRole('button', { name: 'Ingresar' }).click();
}

/**
 * Selecciona un medio de pago tocando el texto visible de la pill
 * (`{{tipo}}` dentro del <span> del <label>), no el <input type="radio">
 * en sí: ese input tiene `class="peer sr-only"` (visualmente oculto,
 * patrón de accesibilidad válido -- el <input> vive DENTRO del
 * <label>, así que sigue siendo un control con nombre accesible real),
 * pero por su tamaño casi nulo Playwright puede negarse a considerarlo
 * "clickeable" directamente. Tocar el texto visible es además lo que
 * haría un usuario real.
 */
async function seleccionarMedioDePago(page, tipoPago) {
  await page.getByText(tipoPago, { exact: true }).click();
}

/**
 * Agrega un producto al carrito por el flujo real del lector de
 * código de barras: tipear el código en el input y presionar Enter
 * (ver interfaces/web/static/js/pos.js, listener de `keydown`).
 */
async function agregarPorCodigoBarras(page, codigoBarras) {
  const input = page.getByLabel('Código de barras');
  await input.fill(codigoBarras);
  await input.press('Enter');
}

/**
 * Agrega un producto al carrito por el flujo real de selección manual:
 * click en su card de la grilla (`[data-producto-card]`). Se ubica por
 * `data-nombre`, un atributo funcional que el propio `pos.js` ya lee
 * (`tarjeta.dataset.nombre`) -- no una clase de Tailwind.
 */
async function agregarPorTarjeta(page, nombre) {
  await page.locator(`[data-producto-card][data-nombre="${nombre}"]`).click();
}

/** Fila del carrito que contiene `nombre` (hijo directo de #carrito-lista). */
function filaDelCarrito(page, nombre) {
  return page.locator('#carrito-lista > div', { hasText: nombre });
}

/**
 * Lee el stock mostrado en la card de un producto (`data-stock`, el
 * mismo atributo que ya lee `pos.js` para validar cantidades) -- nunca
 * se consulta la base de datos directamente desde el proceso de test
 * (evita agregar un lector de SQLite en Node solo para esto).
 */
async function leerStockDeCard(page, nombre) {
  const valor = await page.locator(`[data-producto-card][data-nombre="${nombre}"]`).getAttribute('data-stock');
  return Number(valor);
}

/**
 * Registra listeners de errores reales de navegador/red durante un
 * test. Criterio (Fase 5E.2):
 *  - pageerror / console.error (nunca console.warn) -> siempre error.
 *  - response con status >= 400 -> error. 3xx (el 303 real de login,
 *    o el 303 tras un cobro exitoso) NUNCA cuenta como error.
 *  - requestfailed (DNS/conexión rechazada/timeout de red) -> error.
 * `afirmarSinErrores()` se llama al final de cada test.
 */
function registrarListenersDeErrores(page) {
  const erroresDeNavegador = [];
  const respuestasHttpInesperadas = [];

  page.on('pageerror', (error) => {
    erroresDeNavegador.push(`pageerror: ${error.message}`);
  });
  page.on('console', (mensaje) => {
    if (mensaje.type() === 'error') {
      erroresDeNavegador.push(`console.error: ${mensaje.text()}`);
    }
  });
  page.on('response', (respuesta) => {
    if (respuesta.status() >= 400) {
      respuestasHttpInesperadas.push(`${respuesta.status()} ${respuesta.url()}`);
    }
  });
  page.on('requestfailed', (request) => {
    respuestasHttpInesperadas.push(`request fallida: ${request.url()} (${request.failure()?.errorText})`);
  });

  return {
    erroresDeNavegador,
    respuestasHttpInesperadas,
    afirmarSinErrores() {
      expect(respuestasHttpInesperadas, respuestasHttpInesperadas.join('\n')).toEqual([]);
      expect(erroresDeNavegador, erroresDeNavegador.join('\n')).toEqual([]);
    },
  };
}

/**
 * Flujo completo mínimo para dejar UNA venta ya confirmada, para tests
 * que necesitan "una venta que ya existe" como punto de partida (ticket,
 * idempotencia) sin depender de que otro test haya corrido antes --
 * cada test que la usa arma la suya propia, con datos previsibles.
 *
 * Por defecto cobra con TARJETA (no requiere cargar un monto recibido,
 * es el camino más simple para "solo necesito una venta real").
 */
async function realizarVentaSimple(page, { codigoBarras = PRODUCTO_1.codigoBarras, tipoPago = 'TARJETA', montoRecibido = null } = {}) {
  await loginViaUI(page);
  await agregarPorCodigoBarras(page, codigoBarras);

  if (tipoPago !== 'EFECTIVO') {
    await seleccionarMedioDePago(page, tipoPago);
  } else if (montoRecibido) {
    await page.getByLabel('Monto recibido').fill(montoRecibido);
  }

  const [respuesta] = await Promise.all([
    page.waitForResponse((r) => r.url().includes('/api/ventas') && r.request().method() === 'POST'),
    page.locator('#boton-cobrar').click(),
  ]);
  const datos = await respuesta.json();
  return { respuesta, datos, ventaId: datos.id };
}

module.exports = {
  USUARIO,
  USUARIO_CASHIER,
  PASSWORD,
  PRODUCTO_1,
  PRODUCTO_2,
  PRODUCTO_XSS,
  loginViaUI,
  seleccionarMedioDePago,
  agregarPorCodigoBarras,
  agregarPorTarjeta,
  filaDelCarrito,
  leerStockDeCard,
  registrarListenersDeErrores,
  realizarVentaSimple,
};
