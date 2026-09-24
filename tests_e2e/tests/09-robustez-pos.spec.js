// @ts-check
// TEST 9 — ROBUSTEZ DEL POS (V1.1).
//
// Cubre en un navegador real: flujo del lector USB (código -> Enter ->
// producto), bloqueo del carrito durante un cobro en vuelo, "Precio no
// configurado", Enter para cobrar, stock de la grilla tras una venta,
// respuestas no JSON del servidor y el aviso antes de abandonar la página.
const { test, expect } = require('@playwright/test');
const {
  loginViaUI,
  agregarPorCodigoBarras,
  agregarPorTarjeta,
  filaDelCarrito,
  seleccionarMedioDePago,
  leerStockDeCard,
  registrarListenersDeErrores,
  PRODUCTO_1,
  PRODUCTO_2,
} = require('./helpers');

const PRODUCTO_SIN_PRECIO = { codigoBarras: '7790000000088', nombre: 'Sin Precio E2E' };

test('lector USB: el input arranca enfocado y código + Enter agrega el producto y devuelve el foco', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page);

  const input = page.getByLabel('Código de barras');
  await expect(input).toBeFocused();

  // Tipeo real tecla por tecla (así escribe un lector que emula teclado), sin `fill`.
  await page.keyboard.type(PRODUCTO_1.codigoBarras);
  await page.keyboard.press('Enter');

  await expect(filaDelCarrito(page, PRODUCTO_1.nombre)).toBeVisible();
  await expect(input).toBeFocused();
  await expect(input).toHaveValue('');

  // Segundo escaneo del mismo producto: suma cantidad, sin tocar el mouse.
  await page.keyboard.type(PRODUCTO_1.codigoBarras);
  await page.keyboard.press('Enter');
  await expect(filaDelCarrito(page, PRODUCTO_1.nombre)).toContainText('2');

  errores.afirmarSinErrores();
});

test('lector USB: un código inexistente avisa y no agrega nada', async ({ page }) => {
  await loginViaUI(page);

  await page.keyboard.type('0000000000000');
  await page.keyboard.press('Enter');

  await expect(page.locator('#toast-container')).toContainText('No se encontró ningún producto');
  await expect(page.locator('#carrito-lista > div')).toHaveCount(0);
});

test('producto sin precio: se ve como "Precio no configurado" y no entra al carrito', async ({ page }) => {
  await loginViaUI(page);

  const tarjeta = page.locator(`[data-producto-card][data-nombre="${PRODUCTO_SIN_PRECIO.nombre}"]`);
  await expect(tarjeta).toContainText('Precio no configurado');

  await tarjeta.click();
  await expect(page.locator('#toast-container')).toContainText('no tiene precio configurado');
  await expect(page.locator('#carrito-lista > div')).toHaveCount(0);

  await agregarPorCodigoBarras(page, PRODUCTO_SIN_PRECIO.codigoBarras);
  await expect(page.locator('#carrito-lista > div')).toHaveCount(0);
  await expect(page.locator('#boton-cobrar')).toBeDisabled();
});

test('cobro en vuelo: el carrito se bloquea y ningún producto se pierde en silencio', async ({ page }) => {
  await loginViaUI(page);

  // Demora la respuesta del cobro para poder actuar mientras está en vuelo.
  await page.route('**/api/ventas', async (route) => {
    await new Promise((resolver) => setTimeout(resolver, 1500));
    await route.continue();
  });

  await agregarPorCodigoBarras(page, PRODUCTO_1.codigoBarras);
  await seleccionarMedioDePago(page, 'TARJETA');
  await page.locator('#boton-cobrar').click();
  await expect(page.locator('#boton-cobrar')).toBeDisabled();

  // Mientras el cobro está en curso: escanear y tocar una card se rechaza con aviso.
  await agregarPorCodigoBarras(page, PRODUCTO_2.codigoBarras);
  await expect(page.locator('#toast-container')).toContainText('venta procesándose');
  await agregarPorTarjeta(page, PRODUCTO_2.nombre);

  // Al confirmarse el cobro, el carrito queda vacío y el producto bloqueado NO
  // aparece "vendido a medias": se puede escanear de nuevo como venta nueva.
  await expect(page.getByRole('button', { name: 'Nueva venta' })).toBeVisible({ timeout: 10_000 });
  await agregarPorCodigoBarras(page, PRODUCTO_2.codigoBarras);
  await expect(filaDelCarrito(page, PRODUCTO_2.nombre)).toBeVisible();
});

test('Enter en "Monto recibido" cobra, una sola vez, cuando el monto alcanza', async ({ page }) => {
  await loginViaUI(page);
  let cobros = 0;
  page.on('request', (request) => {
    if (request.url().includes('/api/ventas') && request.method() === 'POST') cobros += 1;
  });

  await agregarPorCodigoBarras(page, PRODUCTO_1.codigoBarras);
  await seleccionarMedioDePago(page, 'EFECTIVO');
  const monto = page.getByLabel('Monto recibido');

  // Monto insuficiente: Enter no cobra.
  await monto.fill('1.00');
  await monto.press('Enter');
  await expect(page.getByRole('button', { name: 'Nueva venta' })).toBeHidden();
  expect(cobros).toBe(0);

  // Monto suficiente: doble Enter seguido -> una única venta.
  await monto.fill('5.00');
  await monto.press('Enter');
  await monto.press('Enter').catch(() => {});
  await expect(page.getByRole('button', { name: 'Nueva venta' })).toBeVisible();
  expect(cobros).toBe(1);
});

test('tras una venta, el stock de la grilla se actualiza sin recargar', async ({ page }) => {
  await loginViaUI(page);
  const stockAntes = await leerStockDeCard(page, PRODUCTO_2.nombre);

  await agregarPorCodigoBarras(page, PRODUCTO_2.codigoBarras);
  await agregarPorCodigoBarras(page, PRODUCTO_2.codigoBarras);
  await seleccionarMedioDePago(page, 'TARJETA');
  await page.locator('#boton-cobrar').click();
  await expect(page.getByRole('button', { name: 'Nueva venta' })).toBeVisible();

  const tarjeta = page.locator(`[data-producto-card][data-nombre="${PRODUCTO_2.nombre}"]`);
  await expect(tarjeta).toContainText(`Stock: ${stockAntes - 2}`);
  expect(await leerStockDeCard(page, PRODUCTO_2.nombre)).toBe(stockAntes - 2);
});

test('un 500 con texto plano se informa como error del servidor, no como falla de conexión', async ({ page }) => {
  await loginViaUI(page);
  await page.route('**/api/ventas', (route) =>
    route.fulfill({ status: 500, contentType: 'text/plain', body: 'Internal Server Error' })
  );

  await agregarPorCodigoBarras(page, PRODUCTO_1.codigoBarras);
  await seleccionarMedioDePago(page, 'TARJETA');
  await page.locator('#boton-cobrar').click();

  const toasts = page.locator('#toast-container');
  await expect(toasts).toContainText('error interno');
  await expect(toasts).not.toContainText('conexión');
  // El carrito se conserva y se puede reintentar.
  await expect(filaDelCarrito(page, PRODUCTO_1.nombre)).toBeVisible();
  await expect(page.locator('#boton-cobrar')).toBeEnabled();
});

test('aviso antes de abandonar la página: solo con productos en el carrito', async ({ page }) => {
  await loginViaUI(page);

  const avisaConCarritoVacio = await page.evaluate(() => {
    const evento = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(evento);
    return evento.defaultPrevented;
  });
  expect(avisaConCarritoVacio).toBe(false);

  await agregarPorCodigoBarras(page, PRODUCTO_1.codigoBarras);
  await expect(filaDelCarrito(page, PRODUCTO_1.nombre)).toBeVisible(); // la búsqueda por código es asíncrona
  const avisaConProductos = await page.evaluate(() => {
    const evento = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(evento);
    return evento.defaultPrevented;
  });
  expect(avisaConProductos).toBe(true);
});
